"""Per-trial oracle server: the only place an eval's private model runs during a trial.

Why out of process: in the paper's harness the "run the model" tools were scripts the
agent executed from inside a directory that also held the world spec, the
answer key and the optimizer (coupled_planner.py:243-244, world_query.py,
bwm_query.py). The channel (verdict-only, figures-only, ...) was a command-line
flag the agent could change (world_query.py:87-88, bwm_query.py:108-109), and the "boolean" channel printed figures anyway. Here the
handlers run in the harness (HOST) or in the sidecar container (CONTAINER);
the sandbox holds only a thin client with no paths and no mode argument.

Rules enforced for every request:
  * per-trial token (constant-time compare); wrong token -> "unauthorized";
  * only the arm's ops (plus ``describe``) are served;
  * payload keys in OVERRIDE_KEYS are removed before the handler runs and the
    attempt is logged as ``override_attempt`` -- the mode is sealed in the
    OracleContext and nothing in a payload can change it;
  * ``query_budget`` (None = unlimited) counts every authorized call of a real
    op; call budget+1 is refused with "query budget exhausted";
  * every request is appended to ``oracle_calls.jsonl``
    {ts, op, payload_sha256, ok, override_attempt, override_keys,
     result_sha256, error, calls_remaining, call_index}; this log, not a
    substring count over the transcript, is the "ran the model" metric.

Error handling for eval authors: raise ValueError (or KeyError/TypeError) with
an agent-facing message for bad input; the message is passed back with any
absolute paths redacted. Any other exception returns a generic error to the
agent and the traceback goes to ``oracle_errors.log`` beside the call log.

Wire protocol: newline-delimited JSON over TCP, one reply per request line;
a connection may carry several requests.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib
import json
import os
import re
import signal
import socketserver
import sys
import threading
import time
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from dbwm.core import netaddr, paths
from dbwm.core.types import Cell, OracleContext, OracleHandler, OracleToolSpec

OVERRIDE_KEYS = frozenset({"mode", "channel", "backend", "world", "path"})
MAX_JSON_DEPTH = 64  # far beyond any eval payload (eval 3 models nest < 10 levels)
NESTED_SCAN_DEPTH = 6


def _nesting_depth(obj: Any) -> int:
    """Container nesting depth of a decoded JSON value, without recursion."""
    deepest = 0
    stack = [(obj, 1)]
    while stack:
        node, d = stack.pop()
        if isinstance(node, dict):
            deepest = max(deepest, d)
            if d <= MAX_JSON_DEPTH:
                stack.extend((v, d + 1) for v in node.values())
        elif isinstance(node, list):
            deepest = max(deepest, d)
            if d <= MAX_JSON_DEPTH:
                stack.extend((v, d + 1) for v in node)
    return deepest
NESTED_SCAN_MAX = 20


def nested_override_paths(payload: Any, depth: int = NESTED_SCAN_DEPTH) -> list[str]:
    """Paths ("plan.mode", "items[0].world") of OVERRIDE_KEYS nested below the top level.

    Bounded (depth, number of paths, nodes visited) so a hostile payload cannot
    make the scan expensive. Logged as override attempts; never stripped.
    """
    out: list[str] = []
    budget = [10000]

    def walk(node: Any, prefix: str, level: int) -> None:
        if level > depth or len(out) >= NESTED_SCAN_MAX or budget[0] <= 0:
            return
        budget[0] -= 1
        if isinstance(node, dict):
            for k, v in node.items():
                if level == 0 and k in OVERRIDE_KEYS:
                    continue  # top-level override keys are stripped and logged by the caller
                key = f"{prefix}.{k}" if prefix else str(k)
                if level > 0 and k in OVERRIDE_KEYS and len(out) < NESTED_SCAN_MAX:
                    out.append(key)
                walk(v, key, level + 1)
        elif isinstance(node, list):
            for i, v in enumerate(node[:200]):
                walk(v, f"{prefix}[{i}]", level + 1)

    walk(payload, "", 0)
    return out
RESERVED_OPS = frozenset({"describe"})
MAX_REQUEST_BYTES = 16 * 1024 * 1024


def spec_to_json(spec: OracleToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": json.loads(json.dumps(dict(spec.input_schema))),
        "cli_name": spec.cli_name,
        "cli_usage": spec.cli_usage,
        "cli_args": spec.cli_args,
        "cli_payload_key": spec.cli_payload_key,
    }


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_json_default)


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


_ABS_PATH_RE = re.compile(r"(?:/Users|/home|/private|/var/folders|/tmp|/dbwm|/harness|/work-ro)/[^\s'\"]*")


def scrub_message(msg: str, extra_paths: Sequence[str] = ()) -> str:
    """Remove absolute paths from an agent-facing error message."""
    out = msg
    known = [str(paths.PKG_ROOT), str(paths.private_root()), str(paths.data_root()),
             str(Path.home()), *extra_paths]
    for p in sorted({k for k in known if k and len(k) > 1}, key=len, reverse=True):
        out = out.replace(p, "<redacted>")
    return _ABS_PATH_RE.sub("<redacted>", out)


class OracleServer:
    """Threaded TCP JSON-lines server for one trial.

    ``handlers`` maps op -> OracleHandler. If ``specs`` is given, exactly the
    ops named by the specs are served (each must have a handler); handlers for
    other ops are ignored, so an eval cannot accidentally expose a richer
    channel to an arm. ``ctx.mode`` is the sealed channel.
    """

    def __init__(self, handlers: Mapping[str, OracleHandler], ctx: OracleContext, token: str,
                 log_path: Path, query_budget: int | None = None, *,
                 specs: Sequence[OracleToolSpec] = (), host: str = "127.0.0.1", port: int = 0,
                 scrub_paths: Sequence[str] = (), allow_clients: Sequence[str] = ()):
        if not token or len(token) < 16:
            raise ValueError("oracle token must be at least 16 characters")
        if specs:
            ops = [s.name for s in specs]
            missing = [o for o in ops if o not in handlers]
            if missing:
                raise ValueError(f"no handler for oracle op(s) {missing}")
            self.handlers = {o: handlers[o] for o in ops}
        else:
            self.handlers = dict(handlers)
        clash = RESERVED_OPS & set(self.handlers)
        if clash:
            raise ValueError(f"reserved op name(s) {sorted(clash)}")
        self.specs = tuple(specs)
        self.ctx = ctx
        self._sealed_mode = ctx.mode
        self._token = token.encode("utf-8")
        self.log_path = Path(log_path)
        self.errors_path = self.log_path.with_name("oracle_errors.log")
        self.query_budget = query_budget
        self.calls_made = 0
        self.override_attempts = 0
        self.requests = 0
        self._scrub = tuple(scrub_paths) + (str(ctx.sandbox),)
        self._budget_lock = threading.Lock()
        self._handler_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._host = host
        self._port = port
        # Client networks allowed to connect (empty: any). The sidecar sets it when
        # it must listen beyond the trial network (review m1); see dbwm.core.netaddr.
        self.allow_clients = netaddr.parse_networks(allow_clients)
        self.rejected_clients = 0
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> str:
        server_self = self

        class _Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:  # noqa: D401 - socketserver API
                while True:
                    try:
                        line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
                    except OSError:
                        return
                    if not line:
                        return
                    if len(line) > MAX_REQUEST_BYTES:
                        # Discard the rest of the oversized line first, so it is not read as
                        # a second request (one reply per request, on every platform).
                        rest = line
                        while not rest.endswith(b"\n"):
                            try:
                                rest = self.rfile.readline(MAX_REQUEST_BYTES + 1)
                            except OSError:
                                return
                            if not rest:
                                break
                        server_self._log({"op": None, "ok": False, "error": "request too large"})
                        reply = {"ok": False, "result": None, "error": "request too large",
                                 "calls_remaining": None}
                    elif not line.strip():
                        continue
                    else:
                        reply = server_self.handle_line(line)
                    try:
                        self.wfile.write((canonical_reply(reply) + "\n").encode("utf-8"))
                        self.wfile.flush()
                    except OSError:
                        return

        class _Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True
            # socketserver's default listen backlog is 5; parallel tool calls
            # (or the CLI's parallel HTTPS connections) must not be reset.
            request_queue_size = 128

            def verify_request(self, request: Any, client_address: Any) -> bool:
                if netaddr.client_allowed(str(client_address[0]), server_self.allow_clients):
                    return True
                server_self.rejected_clients += 1
                return False

        self._server = _Server((self._host, self._port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="dbwm-oracle", daemon=True)
        self._thread.start()
        return self.address

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("server not started")
        return self._server.server_address[1]

    @property
    def address(self) -> str:
        host = self._host
        if host in ("0.0.0.0", "", "::"):
            host = "127.0.0.1"
        return f"tcp://{host}:{self.port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "OracleServer":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -- request handling ----------------------------------------------------

    def describe(self) -> dict[str, Any]:
        return {"tools": [spec_to_json(s) for s in self.specs]}

    def handle_line(self, line: bytes) -> dict[str, Any]:
        try:
            req = json.loads(line.decode("utf-8"))
            if _nesting_depth(req) > MAX_JSON_DEPTH:
                raise ValueError("nested too deeply")
        except (UnicodeDecodeError, ValueError, RecursionError):
            # RecursionError / MAX_JSON_DEPTH: deeply nested JSON (red-team RT-O1) gets the
            # same structured refusal and log line as any other malformed request. The
            # explicit depth cap makes the answer the same on every platform (the json
            # module's own limit is ~1000 on macOS and much higher on Linux builds).
            self._log({"op": None, "ok": False, "error": "malformed request"})
            return _reply(False, error="malformed request")
        try:
            return self.dispatch(req)
        except RecursionError:
            self._log({"op": None, "ok": False, "error": "malformed request"})
            return _reply(False, error="malformed request")

    def dispatch(self, req: Any) -> dict[str, Any]:
        """Process one decoded request and return the reply dict."""
        self.requests += 1
        if not isinstance(req, dict):
            self._log({"op": None, "ok": False, "error": "malformed request"})
            return _reply(False, error="malformed request")
        token = req.get("token")
        op = req.get("op")
        payload = req.get("payload", {})
        if payload is None:
            payload = {}
        if not isinstance(token, str) or not hmac.compare_digest(token.encode("utf-8"), self._token):
            self._log({"op": op if isinstance(op, str) else None, "ok": False,
                       "error": "unauthorized", "auth_failed": True})
            return _reply(False, error="unauthorized")
        if not isinstance(op, str) or not isinstance(payload, dict):
            self._log({"op": op if isinstance(op, str) else None, "ok": False,
                       "error": "malformed request"})
            return _reply(False, error="malformed request: need op (string) and payload (object)")
        payload_sha = sha256_json(payload)
        # Top-level override keys are stripped below; nested ones (e.g.
        # {"plan": {"mode": "raw"}}, seen in a live session; red-team RT-O2) are
        # only logged, by path: no handler reads them, and stripping inside an
        # agent's model/plan could change what is evaluated.
        override_keys = sorted(k for k in payload if k in OVERRIDE_KEYS) + sorted(nested_override_paths(payload))
        if override_keys:
            self.override_attempts += 1
        base = {"op": op, "payload_sha256": payload_sha, "override_attempt": bool(override_keys),
                "override_keys": override_keys}
        if op == "describe":
            result = self.describe()
            self._log({**base, "ok": True, "result_sha256": sha256_json(result)})
            return _reply(True, result=result, calls_remaining=self._remaining())
        if op not in self.handlers:
            self._log({**base, "ok": False, "error": "unknown op"})
            return _reply(False, error=f"unknown tool: {op}")
        clean = {k: v for k, v in payload.items() if k not in OVERRIDE_KEYS}
        with self._budget_lock:
            if self.query_budget is not None and self.calls_made >= self.query_budget:
                self._log({**base, "ok": False, "error": "query budget exhausted",
                           "budget_exhausted": True, "calls_remaining": 0})
                return _reply(False, error="query budget exhausted", calls_remaining=0)
            self.calls_made += 1
            call_index = self.calls_made
            remaining = self._remaining()
        try:
            with self._handler_lock:
                try:
                    result = self.handlers[op](clean, self.ctx)
                finally:
                    # The channel is sealed for the trial; a handler must not change it.
                    self.ctx.mode = self._sealed_mode
        except (ValueError, KeyError, TypeError) as e:
            msg = scrub_message(f"{e}" if str(e) else type(e).__name__, self._scrub)
            self._log({**base, "ok": False, "error": msg, "call_index": call_index,
                       "calls_remaining": remaining})
            self._log_error(op, call_index)
            return _reply(False, error=msg, calls_remaining=remaining)
        except Exception:  # noqa: BLE001 - never let a handler bug reach the agent verbatim
            self._log({**base, "ok": False, "error": "internal error", "call_index": call_index,
                       "calls_remaining": remaining})
            self._log_error(op, call_index)
            return _reply(False, error="internal error in tool", calls_remaining=remaining)
        try:
            result_sha = sha256_json(result)
            result = json.loads(canonical_json(result))
        except (TypeError, ValueError):
            self._log({**base, "ok": False, "error": "unserializable result",
                       "call_index": call_index, "calls_remaining": remaining})
            self._log_error(op, call_index)
            return _reply(False, error="internal error in tool", calls_remaining=remaining)
        self._log({**base, "ok": True, "result_sha256": result_sha, "call_index": call_index,
                   "calls_remaining": remaining})
        return _reply(True, result=result, calls_remaining=remaining)

    def _remaining(self) -> int | None:
        if self.query_budget is None:
            return None
        return max(0, self.query_budget - self.calls_made)

    def _log(self, rec: dict[str, Any]) -> None:
        full = {"ts": round(time.time(), 6), "op": rec.get("op"),
                "payload_sha256": rec.get("payload_sha256"), "ok": rec.get("ok", False),
                "override_attempt": rec.get("override_attempt", False),
                "override_keys": rec.get("override_keys", []),
                "result_sha256": rec.get("result_sha256")}
        for k, v in rec.items():
            if k not in full:
                full[k] = v
        line = json.dumps(full, sort_keys=True) + "\n"
        with self._log_lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()

    def _log_error(self, op: str, call_index: int) -> None:
        with self._log_lock:
            with open(self.errors_path, "a", encoding="utf-8") as fh:
                fh.write(f"--- {time.strftime('%Y-%m-%dT%H:%M:%S')} op={op} call={call_index}\n")
                fh.write(traceback.format_exc())


def _reply(ok: bool, *, result: Any = None, error: str | None = None,
           calls_remaining: int | None = None) -> dict[str, Any]:
    return {"ok": ok, "result": result if ok else None, "error": None if ok else error,
            "calls_remaining": calls_remaining}


def canonical_reply(reply: Mapping[str, Any]) -> str:
    return json.dumps(reply, sort_keys=True, default=_json_default)


def read_calls_log(log_path: Path) -> list[dict[str, Any]]:
    """Parse an oracle_calls.jsonl (missing file -> [])."""
    p = Path(log_path)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def summarize_calls(log_path: Path) -> dict[str, int]:
    """Counts for verdict.process: successful real-op calls, override attempts, refusals."""
    recs = read_calls_log(log_path)
    return {
        "oracle_calls": sum(1 for r in recs if r.get("call_index") is not None),
        "oracle_calls_ok": sum(1 for r in recs if r.get("ok") and r.get("call_index") is not None),
        "oracle_override_attempts": sum(1 for r in recs if r.get("override_attempt")),
        "oracle_budget_refusals": sum(1 for r in recs if r.get("budget_exhausted")),
        "oracle_auth_failures": sum(1 for r in recs if r.get("auth_failed")),
    }


# -- sealed-config entry point (sidecar) ----------------------------------------


def load_eval_module(module_name: str, module_path: str | None = None):
    """Import the eval package named in a sealed config and return its EVAL."""
    if module_path:
        p = Path(module_path)
        if not p.is_absolute():
            p = paths.PKG_ROOT / p
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    mod = importlib.import_module(module_name)
    ev = getattr(mod, "EVAL", None)
    if ev is None:
        raise RuntimeError(f"{module_name} exposes no EVAL")
    return ev


def server_from_config(cfg: Mapping[str, Any]) -> OracleServer:
    """Build (not start) an OracleServer from a sealed config dict.

    Config keys: eval_id, eval_module (default ``dbwm.<eval_id>``),
    eval_module_path (optional dir added to sys.path; PKG-relative allowed),
    cell {eval_id, instance, arm, rep}, mode, token, sandbox, log_path,
    host, port, query_budget, allow_clients (optional CIDR list). The mode must equal the arm's declared
    oracle_mode; a mismatch is refused rather than silently served.
    """
    ev = load_eval_module(cfg.get("eval_module") or f"dbwm.{cfg['eval_id']}",
                          cfg.get("eval_module_path"))
    c = cfg["cell"]
    cell = Cell(eval_id=c["eval_id"], instance=c["instance"], arm=c["arm"], rep=int(c["rep"]))
    arm = ev.arms[cell.arm]
    mode = cfg.get("mode")
    if mode != arm.oracle_mode:
        raise RuntimeError(f"sealed mode {mode!r} does not match arm {arm.name!r} "
                           f"oracle_mode {arm.oracle_mode!r}")
    ctx = OracleContext(cell=cell, arm=arm, mode=mode, sandbox=Path(cfg["sandbox"]), state={})
    handlers = ev.oracle_handlers(cell)
    return OracleServer(handlers, ctx, cfg["token"], Path(cfg["log_path"]),
                        query_budget=cfg.get("query_budget", arm.query_budget),
                        specs=arm.tools.oracle_tools, host=cfg.get("host", "127.0.0.1"),
                        port=int(cfg.get("port", 0)), allow_clients=cfg.get("allow_clients") or ())


def write_ready(path: Path | None, info: Mapping[str, Any]) -> None:
    if not path:
        return
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(dict(info), sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dbwm.core.oracle.server",
                                 description="Serve one trial's oracle tools from a sealed config.")
    ap.add_argument("--config", required=True, help="sealed JSON config (written by the harness)")
    args = ap.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    server = server_from_config(cfg)
    server.start()
    write_ready(cfg.get("ready_file"), {"oracle_port": server.port, "pid": os.getpid()})
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.wait(0.5):
        pass
    server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
