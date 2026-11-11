"""Credential-injecting relay for the Messages API: the agent never holds the real key.

Why (red-team finding RT-E, verified with claude 2.1.287): the CLI's Bash tool
inherits the CLI's environment, so at CONTAINER isolation with ``api_key`` or
``oauth_token`` auth a bash-arm agent could read ``ANTHROPIC_API_KEY`` /
``CLAUDE_CODE_OAUTH_TOKEN``, print it into the transcript, and call the API
itself through the allowlisted ``api.anthropic.com`` tunnel: other models
(past the model pin) and spend the USD rail never sees.

With the relay, the sidecar holds the real credential. The agent container gets
``ANTHROPIC_BASE_URL=http://dbwm-sidecar:<port>`` and a per-trial PLACEHOLDER in
the auth variable; the CONNECT allowlist no longer includes the API host. The
relay:

  * accepts clients from the trial network only (like the egress proxy);
  * requires the placeholder (``x-api-key`` or ``Authorization: Bearer``),
    replaces it with the real credential, and forwards to the upstream
    (``https://api.anthropic.com`` unless the operator overrides it);
  * forwards only ``POST /v1/messages`` and ``POST /v1/messages/count_tokens``
    (query strings allowed; ``HEAD /api/hello``, the CLI's connectivity probe,
    is answered locally) whose JSON ``model`` matches the regime's pin
    (dbwm.core.stream_json.model_matches) or an operator-listed extra model;
    everything else gets a 403 in the API's error shape;
  * streams the response through unchanged and tallies usage from it
    (``message_start`` / ``message_delta`` events, or the JSON body), so the
    harness has an account of API spend that does not come from the CLI;
  * appends one line per request to ``api_relay.jsonl``: {ts, client, method,
    path, model, allowed, reason, status, stream, served_model, usage,
    bytes_up, bytes_down, duration_s}. Never a header, never a credential.

  * refuses any request that would make the API itself reach the network or run
    code on the agent's behalf: a ``tools`` entry that is not a client tool (one
    with a ``type`` other than ``custom``, e.g. the server-side web search, web
    fetch or code execution tools) and the ``mcp_servers`` / ``container`` body
    fields. Without this a bash agent could route around the egress allowlist
    through the relay (PR #6 review). Claude Code's own requests carry only
    client tools when its WebSearch/WebFetch tools are excluded by ``--tools``.

What an agent can still do: send ``/v1/messages`` requests for the pinned model
with the placeholder (it is in its environment), using client tools only. Those
are logged with their usage; the audit records the relay's request and usage
tally beside the CLI's own turn count so a reviewer can compare them, but
nothing flags extra requests automatically. They cannot reach another model,
another endpoint, a server-side tool, or carry the real credential away.
Stdlib only; runs in the sidecar (``dbwm.core.sidecar``) or, in tests, on the host.
"""

from __future__ import annotations

import http.client
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from dbwm.core import netaddr

DEFAULT_UPSTREAM = "https://api.anthropic.com"
DEFAULT_PORT = 8089
ALLOWED_PATHS = ("/v1/messages", "/v1/messages/count_tokens")
# Connectivity probes the CLI sends before its first request (seen with the real
# claude 2.1.287: ``HEAD /api/hello``): answered 200 by the relay itself, nothing
# forwarded, so they do not show up as denials in every audit.
LOCAL_OK = {("HEAD", "/api/hello"), ("GET", "/api/hello")}
# Body fields that make the API act on the network on the agent's behalf.
FORBIDDEN_BODY_FIELDS = ("mcp_servers", "container")
MAX_BODY_BYTES = 64 * 1024 * 1024
UPSTREAM_TIMEOUT_S = 600
# Request headers never forwarded: hop-by-hop, the client's credential, the host.
_DROP_REQUEST = {"host", "connection", "keep-alive", "proxy-connection", "proxy-authorization", "te", "trailer",
                 "transfer-encoding", "upgrade", "content-length", "x-api-key", "authorization",
                 "accept-encoding"}
_DROP_RESPONSE = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "upgrade", "content-length",
                  "trailer"}
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _error_body(kind: str, message: str) -> bytes:
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode()


class UsageTally:
    """Usage from a Messages API response, streamed (SSE) or not."""

    def __init__(self, stream: bool) -> None:
        self.stream = stream
        self.served_model: str | None = None
        self.usage: dict[str, int] = {}
        self._buf = b""
        self._body = b""

    def _add(self, usage: Mapping[str, Any] | None, replace: bool = False) -> None:
        for k in USAGE_KEYS:
            v = (usage or {}).get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                # message_delta carries cumulative output_tokens: keep the max
                self.usage[k] = max(self.usage.get(k, 0), v) if not replace else v

    def feed(self, chunk: bytes) -> None:
        if not self.stream:
            if len(self._body) < 8 * 1024 * 1024:
                self._body += chunk
            return
        self._buf += chunk
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            try:
                ev = json.loads(line[5:].strip() or b"null")
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "message_start" and isinstance(ev.get("message"), dict):
                self.served_model = ev["message"].get("model") or self.served_model
                self._add(ev["message"].get("usage"))
            elif ev.get("type") == "message_delta":
                self._add(ev.get("usage"))

    def finish(self) -> None:
        if self.stream or not self._body:
            return
        try:
            body = json.loads(self._body)
        except ValueError:
            return
        if isinstance(body, dict):
            self.served_model = body.get("model") if isinstance(body.get("model"), str) else None
            self._add(body.get("usage"))
            if isinstance(body.get("input_tokens"), int):  # count_tokens
                self.usage["counted_input_tokens"] = body["input_tokens"]


class ApiRelay:
    """See the module docstring. ``credential`` is the real secret; ``placeholder`` what the agent holds."""

    def __init__(self, *, credential: str, placeholder: str, auth_header: str = "x-api-key",
                 pin: str | None, extra_models: Iterable[str] = (), upstream: str = DEFAULT_UPSTREAM,
                 log_path: Path | str = "api_relay.jsonl", host: str = "0.0.0.0", port: int = DEFAULT_PORT,
                 allow_clients: Iterable[str] = ()):
        if auth_header not in ("x-api-key", "bearer"):
            raise ValueError("auth_header must be 'x-api-key' or 'bearer'")
        if not credential or not placeholder or credential == placeholder:
            raise ValueError("the relay needs a credential and a different placeholder")
        u = urlsplit(upstream)
        if u.scheme not in ("https", "http") or not u.hostname:
            raise ValueError(f"upstream must be an http(s) URL, got {upstream!r}")
        self._credential = credential
        self._placeholder = placeholder
        self.auth_header = auth_header
        self.pin = pin
        self.extra_models = tuple(str(m) for m in extra_models if str(m).strip())
        self.upstream = u
        self.log_path = Path(log_path)
        self._host = host
        self._port = port
        self.allow_clients = netaddr.parse_networks(allow_clients)
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- policy ----------------------------------------------------------------------

    def model_allowed(self, model: Any) -> bool:
        from dbwm.core.stream_json import model_matches

        if not isinstance(model, str) or not model:
            return False
        return model_matches(self.pin, model) or model in self.extra_models

    def _client_credential_ok(self, headers: Mapping[str, str]) -> bool:
        if self.auth_header == "x-api-key":
            got = headers.get("x-api-key")
        else:
            a = headers.get("authorization") or ""
            got = a[7:].strip() if a.lower().startswith("bearer ") else None
        return got == self._placeholder

    def _log(self, rec: dict[str, Any]) -> None:
        rec = {"ts": time.time(), **rec}
        line = json.dumps(rec, sort_keys=True, default=str)
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # -- server ----------------------------------------------------------------------

    def start(self) -> int:
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "dbwm-relay"
            sys_version = ""

            def log_message(self, *a: Any) -> None:
                pass

            def _any(self) -> None:
                relay._handle(self)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _any  # noqa: N815

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True
            request_queue_size = 128

            def verify_request(self, request: Any, client_address: Any) -> bool:
                if netaddr.client_allowed(str(client_address[0]), relay.allow_clients):
                    return True
                relay._log({"client": str(client_address[0]), "allowed": False,
                            "reason": "client not on the trial network"})
                return False

        self._server = Server((self._host, self._port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="dbwm-api-relay", daemon=True)
        self._thread.start()
        return self.port

    @property
    def port(self) -> int:
        return int(self._server.server_address[1]) if self._server else self._port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # -- one request -----------------------------------------------------------------

    def _deny(self, h: BaseHTTPRequestHandler, rec: dict[str, Any], status: int, kind: str, reason: str) -> None:
        body = _error_body(kind, f"dbwm relay: {reason}")
        rec.update({"allowed": False, "reason": reason, "status": status})
        self._log(rec)  # before replying, so the record exists once the client has its answer
        h.send_response(status)
        h.send_header("content-type", "application/json")
        h.send_header("content-length", str(len(body)))
        h.end_headers()
        if h.command != "HEAD":
            h.wfile.write(body)

    def _handle(self, h: BaseHTTPRequestHandler) -> None:
        t0 = time.monotonic()
        path = h.path.split("?", 1)[0]
        rec: dict[str, Any] = {"client": str(h.client_address[0]), "method": h.command, "path": path}
        headers = {k.lower(): v for k, v in h.headers.items()}
        try:
            n = int(headers.get("content-length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY_BYTES or "chunked" in (headers.get("transfer-encoding") or "").lower():
            h.close_connection = True
            return self._deny(h, rec, 413, "request_too_large", "request body missing a valid length or too large")
        body = h.rfile.read(n) if n else b""
        rec["bytes_up"] = len(body)
        if (h.command, path) in LOCAL_OK:
            rec.update({"allowed": True, "reason": "answered by the relay", "status": 200, "local": True})
            self._log(rec)
            h.send_response(200)
            h.send_header("content-length", "0")
            h.end_headers()
            return
        if h.command != "POST" or path not in ALLOWED_PATHS:
            return self._deny(h, rec, 403, "permission_error", f"{h.command} {path} is not relayed")
        if not self._client_credential_ok(headers):
            return self._deny(h, rec, 401, "authentication_error", "missing or wrong placeholder credential")
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return self._deny(h, rec, 400, "invalid_request_error", "body is not JSON")
        model = payload.get("model") if isinstance(payload, dict) else None
        rec["model"] = model if isinstance(model, str) else None
        if not self.model_allowed(model):
            return self._deny(h, rec, 403, "permission_error", "model is not the pinned model")
        refusal = server_side_refusal(payload)
        if refusal:
            return self._deny(h, rec, 403, "permission_error", refusal)
        stream = bool(isinstance(payload, dict) and payload.get("stream"))
        rec["stream"] = stream
        fwd = {k: v for k, v in h.headers.items() if k.lower() not in _DROP_REQUEST}
        if self.auth_header == "x-api-key":
            fwd["x-api-key"] = self._credential
        else:
            fwd["authorization"] = f"Bearer {self._credential}"
        fwd["host"] = self.upstream.netloc
        fwd["content-length"] = str(len(body))
        fwd["accept-encoding"] = "identity"
        port = self.upstream.port or (443 if self.upstream.scheme == "https" else 80)
        if self.upstream.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                self.upstream.hostname, port, timeout=UPSTREAM_TIMEOUT_S, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(self.upstream.hostname, port, timeout=UPSTREAM_TIMEOUT_S)
        target = (self.upstream.path.rstrip("/") or "") + h.path
        tally = UsageTally(stream)
        down = 0
        try:
            try:
                conn.request("POST", target, body=body, headers=fwd)
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as e:
                return self._deny(h, rec, 502, "api_error", f"upstream unreachable ({type(e).__name__})")
            rec["status"] = resp.status
            h.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in _DROP_RESPONSE:
                    h.send_header(k, v)
            h.send_header("transfer-encoding", "chunked")
            h.end_headers()
            while True:
                chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                if not chunk:
                    break
                down += len(chunk)
                tally.feed(chunk)
                h.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                h.wfile.flush()
            h.wfile.write(b"0\r\n\r\n")
            h.wfile.flush()
        except OSError as e:  # client went away mid-stream
            rec["error"] = type(e).__name__
            h.close_connection = True
        finally:
            conn.close()
        tally.finish()
        rec.update({"allowed": True, "reason": None, "bytes_down": down, "served_model": tally.served_model,
                    "usage": tally.usage, "duration_s": round(time.monotonic() - t0, 3)})
        self._log(rec)


def server_side_refusal(payload: Any) -> str | None:
    """Why a Messages request must not be relayed, or None if it may.

    Only client tools are allowed: a tool definition without ``type`` or with
    ``type: "custom"`` is executed by the caller, so it cannot reach anything the
    agent container cannot. Any other ``type`` names a server-side or
    provider-defined tool (web search, web fetch, code execution, ...), which the
    API would run outside the sandbox. ``mcp_servers`` and ``container`` likewise
    make the API act on the network for the caller.
    """
    if not isinstance(payload, dict):
        return None
    for key in FORBIDDEN_BODY_FIELDS:
        if key in payload:
            return f"request field {key!r} is not relayed (the API would act outside the sandbox)"
    tools = payload.get("tools")
    if tools is None:
        return None
    if not isinstance(tools, list):
        return "request field 'tools' must be a list of client tools"
    for t in tools:
        if not isinstance(t, dict):
            return "request field 'tools' must be a list of client tools"
        kind = t.get("type")
        if kind not in (None, "custom"):
            return f"server-side tool type {str(kind)[:60]!r} is not relayed (only client tools are)"
    return None


def read_log(path: Path | str) -> list[dict[str, Any]]:
    """The relay's records (missing file -> [])."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def summarize(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts and summed usage over relay records (for verdicts and the audit)."""
    s: dict[str, Any] = {"requests": 0, "allowed": 0, "denied": 0, "denied_reasons": {},
                         "usage": {k: 0 for k in USAGE_KEYS}, "models": []}
    models: set[str] = set()
    for r in records:
        if r.get("local"):
            continue  # connectivity probes answered by the relay itself
        s["requests"] += 1
        if r.get("allowed"):
            s["allowed"] += 1
            for k in USAGE_KEYS:
                v = (r.get("usage") or {}).get(k)
                if isinstance(v, int):
                    s["usage"][k] += v
            if r.get("served_model"):
                models.add(str(r["served_model"]))
        else:
            s["denied"] += 1
            reason = str(r.get("reason"))
            s["denied_reasons"][reason] = s["denied_reasons"].get(reason, 0) + 1
    s["models"] = sorted(models)
    return s
