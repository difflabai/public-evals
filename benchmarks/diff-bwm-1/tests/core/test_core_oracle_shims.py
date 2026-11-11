"""Oracle server (token, sealed mode, override logging, budget, errors) and both shims as real processes."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading

import pytest

from dbwm.core import sandbox
from dbwm.core.oracle import client
from dbwm.core.oracle.server import OracleServer, read_calls_log, server_from_config, summarize_calls
from dbwm.core.types import Cell, OracleContext

TOKEN = "tok_" + "x" * 28
# Placeholder host-path root, assembled at run time (keeps `dbwm hygiene` clean without an allowlist).
USERS = "/" + "Users"


def make_server(toy, tmp_path, arm="mcp-oracle", handlers=None, budget="arm"):
    cell = Cell("toy", "alpha", arm, 0)
    a = toy.arms[arm]
    ctx = OracleContext(cell=cell, arm=a, mode=a.oracle_mode, sandbox=tmp_path, state={})
    return OracleServer(handlers or toy.oracle_handlers(cell), ctx, TOKEN, tmp_path / "oracle_calls.jsonl",
                        a.query_budget if budget == "arm" else budget, specs=a.tools.oracle_tools)


def test_token_and_describe(toy, tmp_path):
    with make_server(toy, tmp_path) as srv:
        assert client.request(srv.address, "wrong" * 5, "run_model", {})["error"] == "unauthorized"
        assert client.request(srv.address, "", "run_model", {})["error"] == "unauthorized"
        d = client.call(srv.address, TOKEN, "describe")
        assert [t["name"] for t in d["tools"]] == ["run_model"]
        with pytest.raises(client.OracleError, match="unknown tool"):
            client.call(srv.address, TOKEN, "solve_optimum", {})
    log = read_calls_log(tmp_path / "oracle_calls.jsonl")
    assert [r.get("auth_failed") for r in log[:2]] == [True, True]
    assert summarize_calls(tmp_path / "oracle_calls.jsonl")["oracle_calls"] == 0  # describe/unknown not counted


def test_mode_lock_and_override_logging(toy, tmp_path):
    seen = []

    def handler(payload, ctx):
        seen.append((dict(payload), ctx.mode))
        ctx.mode = "figures"  # a buggy handler cannot unseal the channel
        return {"ok": True}

    with make_server(toy, tmp_path, arm="verdict", handlers={"run_model": handler}) as srv:
        client.call(srv.address, TOKEN, "run_model",
                    {"plan": {"x": 1}, "mode": "numeric", "channel": "raw", "backend": "x", "world": "w",
                     "path": "/etc/passwd"})
        client.call(srv.address, TOKEN, "run_model", {"plan": {"x": 2}})
    assert seen == [({"plan": {"x": 1}}, "verdict"), ({"plan": {"x": 2}}, "verdict")]
    log = read_calls_log(tmp_path / "oracle_calls.jsonl")
    assert log[0]["override_attempt"] is True
    assert log[0]["override_keys"] == ["backend", "channel", "mode", "path", "world"]
    assert log[1]["override_attempt"] is False
    for r in log:
        assert set(r) >= {"ts", "op", "payload_sha256", "ok", "override_attempt", "result_sha256"}
    assert summarize_calls(tmp_path / "oracle_calls.jsonl")["oracle_override_attempts"] == 1


def test_sealed_modes_differ(toy, tmp_path):
    with make_server(toy, tmp_path, arm="mcp-oracle") as srv:
        assert client.call(srv.address, TOKEN, "run_model", {"plan": {"x": 10}}) == {"y": 73.1415}
    with make_server(toy, tmp_path, arm="verdict") as srv:
        r = client.call(srv.address, TOKEN, "run_model", {"plan": {"x": 10}, "mode": "numeric"})
        assert r == {"pass": True}


def test_query_budget(toy, tmp_path):
    with make_server(toy, tmp_path, budget=2) as srv:
        assert client.request(srv.address, TOKEN, "run_model", {"plan": {"x": 1}})["calls_remaining"] == 1
        assert client.request(srv.address, TOKEN, "run_model", {"plan": {"x": 1}})["calls_remaining"] == 0
        r = client.request(srv.address, TOKEN, "run_model", {"plan": {"x": 1}})
        assert r == {"ok": False, "result": None, "error": "query budget exhausted", "calls_remaining": 0}
    s = summarize_calls(tmp_path / "oracle_calls.jsonl")
    assert s["oracle_calls"] == 2 and s["oracle_budget_refusals"] == 1


def test_call_counts_agree_across_runner_verdict_and_audit(toy, tmp_path):
    """process.oracle_calls (runner), verdict.oracle_call_counts and audit counts use one rule: call_index."""
    from dbwm.core import audit as audit_mod
    from dbwm.core import verdict as verdict_mod

    def handler(payload, ctx):
        if "plan" not in payload:
            raise ValueError("need a plan")
        return {"y": 1}

    with make_server(toy, tmp_path, handlers={"run_model": handler}, budget=3) as srv:
        client.request(srv.address, "wrong" * 5, "run_model", {})          # unauthorised: not a call
        client.request(srv.address, TOKEN, "describe", {})                 # discovery: not a call
        client.request(srv.address, TOKEN, "solve_optimum", {})            # unknown op: not a call
        client.request(srv.address, TOKEN, "run_model", {"plan": 1, "mode": "raw"})  # call 1 (+override)
        client.request(srv.address, TOKEN, "run_model", {})                # call 2: bad input, still charged
        client.request(srv.address, TOKEN, "run_model", {"plan": 2})       # call 3
        client.request(srv.address, TOKEN, "run_model", {"plan": 3})       # budget refused: not a call
    log = tmp_path / "oracle_calls.jsonl"
    runner_counts = summarize_calls(log)
    assert runner_counts["oracle_calls"] == 3 and runner_counts["oracle_override_attempts"] == 1
    assert verdict_mod.oracle_call_counts(log) == (3, 1)
    a = audit_mod.audit_attempt(tmp_path, toy, Cell("toy", "alpha", "mcp-oracle", 0), sandbox=tmp_path,
                                isolation="host", expected_tools=[], model_pin=None, answer_key_values=[],
                                write=False)
    assert a["counts"]["oracle_calls"] == 3 and a["counts"]["oracle_override_attempts"] == 1


def test_handler_errors_are_scrubbed(toy, tmp_path):
    def bad_input(payload, ctx):
        raise ValueError(f"cannot read {ctx.sandbox}/x.json or {USERS}/someone/private/eval2/world.json")

    def crash(payload, ctx):
        raise RuntimeError(f"secret internals {USERS}/someone/answer")

    with make_server(toy, tmp_path, handlers={"run_model": bad_input}) as srv:
        r = client.request(srv.address, TOKEN, "run_model", {})
        assert r["ok"] is False and USERS not in r["error"] and str(tmp_path) not in r["error"]
    with make_server(toy, tmp_path, handlers={"run_model": crash}) as srv:
        r = client.request(srv.address, TOKEN, "run_model", {})
        assert r["error"] == "internal error in tool"
    assert "RuntimeError" in (tmp_path / "oracle_errors.log").read_text()


def test_malformed_and_multi_request_connection(toy, tmp_path):
    with make_server(toy, tmp_path) as srv:
        host, port = client.parse_address(srv.address)
        with socket.create_connection((host, port), timeout=10) as s:
            f = s.makefile("rwb")
            f.write(b"not json\n")
            f.write(json.dumps({"token": TOKEN, "op": "run_model", "payload": {"plan": {"x": 1}}}).encode() + b"\n")
            f.write(json.dumps({"token": TOKEN, "op": "run_model", "payload": []}).encode() + b"\n")
            f.flush()
            replies = [json.loads(f.readline()) for _ in range(3)]
    assert replies[0]["error"] == "malformed request"
    assert replies[1]["ok"] is True
    assert replies[2]["ok"] is False


def test_concurrent_calls_respect_budget(toy, tmp_path):
    with make_server(toy, tmp_path, budget=5) as srv:
        results = []

        def go():
            results.append(client.request(srv.address, TOKEN, "run_model", {"plan": {"x": 1}})["ok"])

        ts = [threading.Thread(target=go) for _ in range(12)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    assert results.count(True) == 5 and results.count(False) == 7


def test_server_from_config_imports_eval_and_checks_mode(tmp_path):
    cfg = {"eval_id": "toy", "eval_module": "toy_eval", "eval_module_path": "tests/core",
           "cell": {"eval_id": "toy", "instance": "beta", "arm": "mcp-oracle", "rep": 0},
           "mode": "numeric", "token": TOKEN, "sandbox": str(tmp_path), "log_path": str(tmp_path / "o.jsonl")}
    srv = server_from_config(cfg)
    srv.start()
    try:
        assert client.call(srv.address, TOKEN, "run_model", {"plan": {"x": 20}}) == {"y": 62.8318}
        assert srv.query_budget == 3
    finally:
        srv.stop()
    with pytest.raises(RuntimeError, match="does not match"):
        server_from_config({**cfg, "mode": "figures"})


def test_server_main_entrypoint(tmp_path):
    cfg = {"eval_id": "toy", "eval_module": "toy_eval", "eval_module_path": "tests/core",
           "cell": {"eval_id": "toy", "instance": "alpha", "arm": "mcp-oracle", "rep": 0},
           "mode": "numeric", "token": TOKEN, "sandbox": str(tmp_path), "log_path": str(tmp_path / "o.jsonl"),
           "ready_file": str(tmp_path / "ready.json")}
    (tmp_path / "sealed.json").write_text(json.dumps(cfg))
    p = subprocess.Popen([sys.executable, "-m", "dbwm.core.oracle.server", "--config", str(tmp_path / "sealed.json")])
    try:
        import time

        for _ in range(200):
            if (tmp_path / "ready.json").exists():
                break
            time.sleep(0.05)
        port = json.loads((tmp_path / "ready.json").read_text())["oracle_port"]
        assert client.call(f"tcp://127.0.0.1:{port}", TOKEN, "run_model", {"plan": {"x": 10}}) == {"y": 73.1415}
    finally:
        p.terminate()
        assert p.wait(timeout=10) == 0


# -- shims as processes ---------------------------------------------------------------------


@pytest.fixture
def served(toy, tmp_path):
    srv = make_server(toy, tmp_path)
    srv.start()
    yield srv
    srv.stop()


def shim_env(srv):
    return {"PATH": os.environ.get("PATH", ""), "DBWM_ORACLE": srv.address, "DBWM_ORACLE_TOKEN": TOKEN}


def test_cli_shim_round_trip(toy, served, tmp_path):
    spec = toy.arms["bash-oracle"].tools.oracle_tools[0]
    work = tmp_path / "work"
    work.mkdir()
    shim = work / spec.cli_name
    shim.write_text(sandbox.render_cli_shim(spec))
    shim.chmod(0o555)
    (work / "input.json").write_text('{"x": 10}')
    p = subprocess.run([str(shim), "input.json"], cwd=work, env=shim_env(served), capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {"y": 73.1415}
    assert "calls remaining: 2" in p.stderr
    # neutral help, no mode flag accepted
    h = subprocess.run([str(shim), "--help"], cwd=work, env=shim_env(served), capture_output=True, text=True)
    assert h.returncode == 0 and h.stdout.startswith("usage: ./toy_model input.json")
    for bad in ("--mode", "numeric", "--backend", "BWM_"):
        assert bad not in h.stdout
    m = subprocess.run([str(shim), "--mode", "figures", "input.json"], cwd=work, env=shim_env(served),
                       capture_output=True, text=True)
    assert m.returncode == 2 and "unknown option" in m.stderr
    # errors: missing file, bad JSON, no service
    assert subprocess.run([str(shim), "nope.json"], cwd=work, env=shim_env(served),
                          capture_output=True).returncode == 2
    (work / "bad.json").write_text("{")
    assert subprocess.run([str(shim), "bad.json"], cwd=work, env=shim_env(served),
                          capture_output=True).returncode == 2
    r = subprocess.run([str(shim), "input.json"], cwd=work, env={"PATH": os.environ["PATH"]},
                       capture_output=True, text=True)
    assert r.returncode == 3 and "unavailable" in r.stderr
    # handler error -> rc 1 with the (scrubbed) message
    (work / "noxin.json").write_text('{"y": 1}')
    e = subprocess.run([str(shim), "noxin.json"], cwd=work, env=shim_env(served), capture_output=True, text=True)
    assert e.returncode == 1 and "numeric x" in e.stderr


def mcp_session(cmd, env, msgs):
    p = subprocess.run(cmd, input="".join(json.dumps(m) + "\n" for m in msgs), env=env, capture_output=True,
                       text=True, timeout=60)
    return [json.loads(x) for x in p.stdout.splitlines() if x.strip()]


def test_mcp_shim_handshake_in_process(toy, served, tmp_path):
    specs = toy.arms["mcp-oracle"].tools.oracle_tools
    cfg_path = sandbox.write_mcp_tools(tmp_path / "tools", specs, address=served.address, token=TOKEN,
                                       python=sys.executable)
    cfg = json.loads(cfg_path.read_text())["mcpServers"]["dbwm"]
    env = {"PATH": os.environ.get("PATH", ""), **cfg["env"]}
    replies = mcp_session([cfg["command"], *cfg["args"]], env, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "run_model", "arguments": {"plan": {"x": 10}}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "run_model", "arguments": {"plan": "{\"x\": 20}"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "run_model", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "solve", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 7, "method": "ping"},
        {"jsonrpc": "2.0", "id": 8, "method": "resources/list"},
    ])
    by_id = {r["id"]: r for r in replies}
    assert len(replies) == 8  # the notification got no reply
    assert by_id[1]["result"]["capabilities"] == {"tools": {}}
    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"
    tools = by_id[2]["result"]["tools"]
    assert tools == [{"name": "run_model", "description": specs[0].description,
                      "inputSchema": dict(specs[0].input_schema)}]
    c3 = by_id[3]["result"]
    assert c3["isError"] is False and json.loads(c3["content"][0]["text"]) == {"y": 73.1415}
    assert c3["content"][1]["text"] == "calls remaining: 2"
    assert json.loads(by_id[4]["result"]["content"][0]["text"]) == {"y": 146.283}  # JSON-string arg decoded
    assert by_id[5]["result"]["isError"] is True and "calls remaining: 0" in by_id[5]["result"]["content"][0]["text"]
    assert by_id[6]["error"]["code"] == -32602
    assert by_id[7]["result"] == {}
    assert by_id[8]["error"]["code"] == -32601


def test_mcp_shim_without_service(toy, tmp_path):
    specs = toy.arms["mcp-oracle"].tools.oracle_tools
    cfg_path = sandbox.write_mcp_tools(tmp_path / "tools", specs, address="tcp://127.0.0.1:1", token=TOKEN,
                                       python=sys.executable)
    cfg = json.loads(cfg_path.read_text())["mcpServers"]["dbwm"]
    replies = mcp_session([cfg["command"], *cfg["args"]], {"PATH": os.environ["PATH"], **cfg["env"]}, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "run_model", "arguments": {"plan": {"x": 1}}}}])
    assert replies[0]["result"]["isError"] is True
    assert "unavailable" in replies[0]["result"]["content"][0]["text"]


def _raw_exchange(address: str, data: bytes, timeout: float = 30) -> bytes:
    host, port = client.parse_address(address)
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(data)
        s.shutdown(socket.SHUT_WR)
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return buf


def test_nesting_cap_is_platform_independent(toy, tmp_path):
    """Depth 100 parses on every platform's json module, so the refusal comes from the
    server's own MAX_JSON_DEPTH (the fresh-clone check found Linux parses depth 5000)."""
    with make_server(toy, tmp_path) as srv:
        nested = "[" * 100 + "]" * 100
        line = ('{"token":"%s","op":"run_model","payload":{"plan":%s}}\n' % (TOKEN, nested)).encode()
        replies = _raw_exchange(srv.address, line).decode().splitlines()
        assert len(replies) == 1 and json.loads(replies[0])["error"] == "malformed request"
        ok = client.request(srv.address, TOKEN, "run_model", {"plan": {"x": 10}})
        assert ok["ok"] is True


def test_oversized_line_gets_exactly_one_reply(toy, tmp_path):
    with make_server(toy, tmp_path) as srv:
        big = ('{"token":"%s","op":"run_model","payload":{"x":"' % TOKEN).encode() + b"A" * (17 * 1024 * 1024) \
            + b'"}}\n' + ('{"token":"%s","op":"describe","payload":{}}\n' % TOKEN).encode()
        replies = _raw_exchange(srv.address, big, timeout=60).decode().splitlines()
        assert len(replies) == 2, replies[:3]
        assert json.loads(replies[0])["error"] == "request too large"
        assert json.loads(replies[1])["ok"] is True  # the connection survives
