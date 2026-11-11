"""Core self-checks for ``dbwm selftest`` (no LLM, no network beyond loopback).

Each check exercises a runtime piece the way a trial does: the oracle server
(token, sealed mode, override logging, query budget), both shims as separate
processes, the sandbox pre-check, the stream parser and outcome classifier,
the egress proxy on loopback, and the presence of the optional external tools
(Claude Code CLI, Docker image). A missing optional tool is SKIPPED with the
reason, never reported as ok.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from dbwm.core import canary, container, outcomes, paths, sandbox, stream_json
from dbwm.core.egress_proxy import EgressProxy
from dbwm.core.oracle.client import call, request
from dbwm.core.oracle.server import OracleServer, read_calls_log
from dbwm.core.types import (
    CANARY_GUID,
    AgentResult,
    ArmSpec,
    Cell,
    CheckResult,
    OracleContext,
    OracleToolSpec,
    Outcome,
    StagedFile,
    StagingManifest,
    ToolPolicy,
)

ECHO = OracleToolSpec(name="echo", description="Echo the payload.",
                      input_schema={"type": "object", "properties": {"plan": {"type": "object"}}},
                      cli_name="echo_tool", cli_usage="./echo_tool input.json")
_ARM = ArmSpec("echo", "selfcheck arm", ToolPolicy(frozenset({"read", "write"}), (ECHO,)), oracle_mode="sealed",
               query_budget=2)
# A placeholder host path the pre-check must reject, assembled at run time so the
# package source itself never carries a home-directory path (dbwm hygiene).
_HOST_PATH = "/" + "Users/someone/private/x"
_CELL = Cell("selfcheck", "i0", "echo", 0)


def _echo(payload, ctx):  # noqa: ANN001 - OracleHandler
    return {"payload": dict(payload), "mode": ctx.mode}


def _server(tmp: Path) -> tuple[OracleServer, str]:
    token = secrets.token_urlsafe(24)
    ctx = OracleContext(cell=_CELL, arm=_ARM, mode="sealed", sandbox=tmp, state={})
    srv = OracleServer({"echo": _echo}, ctx, token, tmp / "oracle_calls.jsonl", query_budget=2, specs=(ECHO,))
    srv.start()
    return srv, token


def check_oracle_server() -> list[CheckResult]:
    out = []
    with tempfile.TemporaryDirectory(prefix="dbwm_selfcheck_") as t:
        tmp = Path(t)
        srv, token = _server(tmp)
        try:
            bad = request(srv.address, "x" * 32, "echo", {})
            out.append(CheckResult("oracle: wrong token refused", bad.get("error") == "unauthorized", str(bad)))
            r = call(srv.address, token, "echo", {"plan": {"a": 1}, "mode": "figures"})
            out.append(CheckResult("oracle: payload 'mode' ignored, sealed mode served",
                                   r == {"payload": {"plan": {"a": 1}}, "mode": "sealed"}, json.dumps(r)))
            call(srv.address, token, "echo", {})
            over = request(srv.address, token, "echo", {})
            out.append(CheckResult("oracle: query budget enforced",
                                   over.get("error") == "query budget exhausted", str(over)))
            log = read_calls_log(tmp / "oracle_calls.jsonl")
            out.append(CheckResult("oracle: calls logged with override attempt",
                                   any(x.get("override_attempt") for x in log) and len(log) == 4,
                                   f"{len(log)} log lines"))
        finally:
            srv.stop()
    return out


def check_shims() -> list[CheckResult]:
    out = []
    with tempfile.TemporaryDirectory(prefix="dbwm_selfcheck_") as t:
        tmp = Path(t)
        srv, token = _server(tmp)
        env = {"PATH": os.environ.get("PATH", ""), "DBWM_ORACLE": srv.address, "DBWM_ORACLE_TOKEN": token}
        try:
            shim = tmp / "echo_tool"
            shim.write_text(sandbox.render_cli_shim(ECHO), encoding="utf-8")
            (tmp / "input.json").write_text('{"x": 1}', encoding="utf-8")
            p = subprocess.run([sys.executable, str(shim), "input.json"], cwd=tmp, env=env,
                               capture_output=True, text=True, timeout=60)
            ok = p.returncode == 0 and json.loads(p.stdout) == {"mode": "sealed", "payload": {"plan": {"x": 1}}}
            out.append(CheckResult("cli shim round trip", ok, (p.stdout + p.stderr).strip()[:200]))
            h = subprocess.run([sys.executable, str(shim), "--help"], env=env, capture_output=True, text=True,
                               timeout=60)
            neutral = "mode" not in h.stdout.lower() and "dbwm" not in h.stdout.lower()
            out.append(CheckResult("cli shim --help is neutral", h.returncode == 0 and neutral, h.stdout[:120]))
            mcp_cfg = sandbox.write_mcp_tools(tmp / "tools", (ECHO,), address=srv.address, token=token,
                                              python=sys.executable)
            cfg = json.loads(mcp_cfg.read_text())["mcpServers"]["dbwm"]
            proc = subprocess.Popen([cfg["command"], *cfg["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    env={**env, **cfg["env"]})
            msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "echo", "arguments": {"plan": {"y": 2}}}}]
            stdout, _ = proc.communicate("".join(json.dumps(m) + "\n" for m in msgs).encode(), timeout=60)
            replies = [json.loads(x) for x in stdout.decode().splitlines() if x.strip()]
            ok = (len(replies) == 3 and replies[0]["result"]["capabilities"] == {"tools": {}}
                  and [t["name"] for t in replies[1]["result"]["tools"]] == ["echo"]
                  and json.loads(replies[2]["result"]["content"][0]["text"])["payload"] == {"plan": {"y": 2}})
            out.append(CheckResult("mcp shim handshake + tools/call", ok, json.dumps(replies)[:200]))
        finally:
            srv.stop()
    return out


def check_sandbox() -> list[CheckResult]:
    out = []
    man = StagingManifest(files=[StagedFile(dst="data/input.json", content=b'{"x": 1}')],
                          task_md="Read data/input.json and write out.json.\n", writable_dirs=["work"])
    pol = ToolPolicy(frozenset({"read", "write"}))
    h = sandbox.build_sandbox(man, _CELL, pol)
    try:
        clean = sandbox.precheck(h, answer_values=["123456.789"], prompt="Follow TASK.md.")
        out.append(CheckResult("sandbox: clean build passes pre-check", not clean, "; ".join(clean)))
        (h.path / "work" / "note.txt").write_text(CANARY_GUID)
        dirty = sandbox.precheck(h, answer_values=["123456.789"], prompt="See " + _HOST_PATH)
        reasons = " | ".join(dirty)
        ok = "canary" in reasons and "listing differs" in reasons and "absolute path" in reasons
        out.append(CheckResult("sandbox: pre-check catches canary, extra file, host path", ok, reasons[:200]))
    finally:
        sandbox.cleanup(h)
    try:
        paths.assert_outside_repos(paths.sandbox_root())
        out.append(CheckResult("sandbox root outside any repo", True, str(paths.sandbox_root())))
    except paths.InsideRepoError as e:
        out.append(CheckResult("sandbox root outside any repo", False, str(e)))
    return out


def check_stream_outcomes() -> list[CheckResult]:
    lines = [
        json.dumps({"type": "system", "subtype": "init", "tools": ["Read"], "cwd": "/w", "model": "claude-x-1",
                    "mcp_servers": [], "skills": []}),
        json.dumps({"type": "assistant", "message": {"id": "m1", "model": "claude-x-1",
                                                     "content": [{"type": "thinking"}]}}),
        json.dumps({"type": "assistant", "message": {"id": "m1", "model": "claude-x-1", "content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a"}}]}}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                    "total_cost_usd": 0.1, "result": "I hit my usage limit of patience"}),
    ]
    s = stream_json.parse_lines(lines)
    res = stream_json.fill_result(s, AgentResult(transcript_path=None, stderr_path=None, returncode=0))
    out = [CheckResult("stream: turns by distinct message id", s.turns_counted == 1 and len(s.tool_calls) == 1)]
    out.append(CheckResult("outcome: completed despite 'usage limit' in final text",
                           outcomes.classify(res) == Outcome.COMPLETED))
    auth = AgentResult(transcript_path=None, stderr_path=None, returncode=1, is_error=True, cost_usd=0.0, turns=1,
                       result_subtype="success", raw_result_event={"result": "Not logged in · Please run /login"})
    out.append(CheckResult("outcome: logged-out CLI is INFRA_AUTH", outcomes.classify(auth) == Outcome.INFRA_AUTH))
    return out


def check_egress_proxy() -> list[CheckResult]:
    up = socket.socket()
    up.bind(("127.0.0.1", 0))
    up.listen(1)
    port = up.getsockname()[1]

    def serve() -> None:
        try:
            c, _ = up.accept()
            c.sendall(b"hello")
            c.close()
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    out = []
    with tempfile.TemporaryDirectory(prefix="dbwm_selfcheck_") as t:
        log = Path(t) / "egress.jsonl"
        with EgressProxy(allow=[f"127.0.0.1:{port}"], log_path=log, host="127.0.0.1", port=0) as px:
            def connect(target: str) -> bytes:
                with socket.create_connection(("127.0.0.1", px.port), timeout=10) as s:
                    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
                    data = b""
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    return data

            ok_resp = connect(f"127.0.0.1:{port}")
            denied = connect("example.com:443")
        recs = [json.loads(x) for x in log.read_text().splitlines()]
    up.close()
    out.append(CheckResult("egress proxy: allowlisted CONNECT tunnels",
                           ok_resp.startswith(b"HTTP/1.1 200") and ok_resp.endswith(b"hello"), ok_resp[:60].decode()))
    out.append(CheckResult("egress proxy: other hosts get 403 and are logged",
                           denied.startswith(b"HTTP/1.1 403") and any(r.get("status") == 403 for r in recs)))
    return out


def check_canaries() -> list[CheckResult]:
    missing = canary.missing_canaries()
    root = paths.private_root()
    if not root.is_dir():
        return [CheckResult("private/ canaries", True, skipped=True, detail=f"no private dir at {root.name}/")]
    return [CheckResult("private/<evalN>/CANARY.txt present", not missing,
                        ", ".join(p.name for p in missing) or "all present")]


def check_external_tools() -> list[CheckResult]:
    out = []
    claude = os.environ.get("DBWM_CLAUDE_BIN") or shutil.which("claude")
    if not claude:
        out.append(CheckResult("Claude Code CLI", True, skipped=True,
                               detail="claude not on PATH (needed only for --adapter claude_code)"))
    else:
        try:
            p = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=60)
            out.append(CheckResult("Claude Code CLI", p.returncode == 0, p.stdout.strip() or p.stderr.strip()))
        except (OSError, subprocess.TimeoutExpired) as e:
            out.append(CheckResult("Claude Code CLI", False, str(e)))
    if not shutil.which(container.docker_bin()):
        out.append(CheckResult("Docker", True, skipped=True,
                               detail="docker not on PATH (needed only for --isolation container)"))
    elif not container.docker_available():
        out.append(CheckResult("Docker", True, skipped=True,
                               detail="docker daemon not reachable (needed only for --isolation container)"))
    elif not container.image_exists():
        out.append(CheckResult(f"Docker image {container.IMAGE}", True, skipped=True,
                               detail="image not built; run `dbwm image build` before --isolation container"))
    else:
        out.append(CheckResult(f"Docker image {container.IMAGE}", True, container.image_id() or ""))
    return out


CHECKS: list[tuple[str, Callable[[], list[CheckResult]]]] = [
    ("oracle server", check_oracle_server),
    ("shims", check_shims),
    ("sandbox", check_sandbox),
    ("stream/outcomes", check_stream_outcomes),
    ("egress proxy", check_egress_proxy),
    ("canaries", check_canaries),
    ("external tools", check_external_tools),
]


def run_core_checks() -> list[CheckResult]:
    out: list[CheckResult] = []
    for name, fn in CHECKS:
        try:
            out.extend(fn())
        except Exception as e:  # noqa: BLE001 - a crashing check is a failed check
            out.append(CheckResult(name, False, f"{type(e).__name__}: {e}"))
    return out
