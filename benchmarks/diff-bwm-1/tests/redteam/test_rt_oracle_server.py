"""RT-O: adversarial traffic against a real per-trial OracleServer, for every eval.

A real ``OracleServer`` is started per eval with that eval's real handlers and
a prepared sandbox, then hit with: mode/channel/backend/world/path overrides,
oversized payloads, path-traversal strings in every field a handler might
open, malformed JSON, the wrong token, budget exhaustion, deeply nested JSON,
and concurrent calls. The invariants: the sealed mode never changes, override
keys never reach the handler and are logged, no absolute host path ever leaks
in a reply, and no handler bug crashes the server.
"""

from __future__ import annotations

import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dbwm.core import paths
from dbwm.core.oracle import client
from dbwm.core.oracle.server import OVERRIDE_KEYS, OracleServer, read_calls_log
from dbwm.core.types import Cell, OracleContext

from .conftest import EVAL_IDS, load_eval

TOKEN = "k" * 40
# The operator-home probe is built from Path.home() at run time: a literal
# home path would be both an operator leak and a `dbwm hygiene` hit.
TRAVERSALS = ["../../etc/passwd", "....//....//etc/passwd", "/etc/passwd",
              str(Path.home() / ".claude" / "CLAUDE.md"), "~/.ssh/id_rsa", "world.json",
              "private/reference.json", "\x00/etc/passwd", "a/../../../../etc/hostname"]


def _oracle_arms():
    """Every (eval, arm) that serves oracle tools -- all sealed modes, not one per eval."""
    out = []
    for e in EVAL_IDS:
        _m, ev = load_eval(e)
        out += [(e, name) for name, a in ev.arms.items() if a.tools.oracle_tools]
    return out


def _write_deliverables(ev, cell, sb: Path) -> None:
    """Give the handler something plausible to read (eval4/eval5 read the sandbox)."""
    names = ev.deliverables_for(cell) if hasattr(ev, "deliverables_for") else ev.deliverables
    for name in names:
        if any(ch in name for ch in "*?["):
            continue
        p = sb / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".json"):
            p.write_text("{}", encoding="utf-8")
        elif name.endswith((".yaml", ".yml")):
            p.write_text("x: 1\n", encoding="utf-8")
        elif name.endswith(".csv"):
            p.write_text("a,b\n1,2\n", encoding="utf-8")
        else:
            p.write_text("draft\n", encoding="utf-8")


class RunningOracle:
    def __init__(self, ev, arm_name, sb: Path):
        self.ev = ev
        self.arm_name = arm_name
        cell = Cell(eval_id=ev.eval_id, instance=ev.instances()[0], arm=arm_name, rep=0)
        arm = ev.arms[arm_name]
        self.sealed = arm.oracle_mode
        self.ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=sb, state={})
        self.log = sb.parent / "oracle_calls.jsonl"
        self.srv = OracleServer(ev.oracle_handlers(cell), self.ctx, TOKEN, self.log,
                                query_budget=None, specs=arm.tools.oracle_tools,
                                scrub_paths=[str(sb)])
        self.ops = [s.name for s in arm.tools.oracle_tools]
        self.addr = self.srv.start()

    def stop(self):
        self.srv.stop()


ORACLE_ARMS = _oracle_arms()
ALL_OPS = sorted({s.name for e in EVAL_IDS for a in load_eval(e)[1].arms.values() for s in a.tools.oracle_tools})


@pytest.fixture(params=ORACLE_ARMS, ids=[f"{e}:{a}" for e, a in ORACLE_ARMS])
def oracle(request, tmp_path):
    eval_id, arm = request.param
    _mod, ev = load_eval(eval_id)
    sb = tmp_path / "work"
    sb.mkdir()
    _write_deliverables(ev, Cell(eval_id=ev.eval_id, instance=ev.instances()[0], arm=arm, rep=0), sb)
    o = RunningOracle(ev, arm, sb)
    yield o
    o.stop()


def test_wrong_token_is_refused(oracle):
    r = client.request(oracle.addr, "wrong" * 10, oracle.ops[0], {})
    assert r["ok"] is False and r["error"] == "unauthorized"


def test_describe_does_not_consume_budget_or_change_mode(oracle):
    r = client.request(oracle.addr, TOKEN, "describe")
    assert r["ok"] is True and "tools" in r["result"]
    assert oracle.ctx.mode == oracle.sealed


def test_override_keys_are_stripped_and_logged(oracle):
    for op in oracle.ops:
        for value in ("raw", "figures", "live", "../../etc/passwd", {"mode": "raw"}, ["raw"]):
            payload = {"candidate": {}, "plan": {}, "model": {}}
            payload.update({k: value for k in OVERRIDE_KEYS})
            r = client.request(oracle.addr, TOKEN, op, payload)
            assert isinstance(r.get("ok"), bool)
            assert oracle.ctx.mode == oracle.sealed, "sealed mode changed after an override attempt"
            recs = [x for x in read_calls_log(oracle.log) if x.get("op") == op]
            assert recs and recs[-1]["override_attempt"] is True
            assert set(recs[-1]["override_keys"]) == set(OVERRIDE_KEYS)


def test_ops_outside_the_arm_are_refused(oracle):
    """An arm's server serves exactly its own ops: a richer op of the same eval
    (e.g. eval3 model_test on the gen-figures arm) is 'unknown tool'."""
    for op in ALL_OPS + ["../describe", "describe ", "DESCRIBE", "__init__", "handlers", "run_model\x00"]:
        if op in oracle.ops:
            continue
        r = client.request(oracle.addr, TOKEN, op, {})
        assert r["ok"] is False and r["error"].startswith("unknown tool"), (op, r)
    tools = client.request(oracle.addr, TOKEN, "describe")["result"]["tools"]
    assert [t["name"] for t in tools] == oracle.ops
    blob = json.dumps(tools).lower()
    if oracle.sealed:
        assert f'"{oracle.sealed.lower()}"' not in blob


def test_path_traversal_in_every_field_never_leaks_a_host_path(oracle):
    roots = [str(paths.PKG_ROOT), str(paths.private_root()), str(Path.home()), "/etc/passwd", "/etc/hostname"]
    for op, field, bad in [(o, f, b) for o in oracle.ops
                           for f in ("file", "candidate", "model", "path", "plan", "workbook", "submission")
                           for b in TRAVERSALS]:
            r = client.request(oracle.addr, TOKEN, op, {field: bad})
            blob = json.dumps(r)
            for root in roots:
                # the raw attacker string may echo back; a resolved host path must not
                if root in ("/etc/passwd", "/etc/hostname"):
                    continue
                assert root not in blob, f"{oracle.ev.eval_id}: reply leaked {root} for {field}={bad!r}"
            # the server must never crash: either ok, or a structured ok:false
            assert isinstance(r.get("ok"), bool)
    assert oracle.ctx.mode == oracle.sealed


def test_oversized_payload_is_refused_cleanly(oracle):
    host, port = client.parse_address(oracle.addr)
    big = b'{"token":"' + TOKEN.encode() + b'","op":"' + oracle.ops[0].encode() + \
          b'","payload":{"candidate":"' + b"A" * (17 * 1024 * 1024) + b'"}}\n'
    with socket.create_connection((host, port), timeout=30) as s:
        s.sendall(big)
        s.shutdown(socket.SHUT_WR)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    reply = json.loads(buf.decode())
    assert reply["ok"] is False and "too large" in (reply["error"] or "")


def test_malformed_json_is_refused_and_connection_survives(oracle):
    host, port = client.parse_address(oracle.addr)
    with socket.create_connection((host, port), timeout=30) as s:
        s.sendall(b"{ this is not json }\n")
        s.sendall(b"not even close\n")
        good = json.dumps({"token": TOKEN, "op": "describe"}).encode() + b"\n"
        s.sendall(good)
        s.settimeout(30)
        buf = b""
        lines = []
        while len(lines) < 3:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    lines.append(json.loads(line.decode()))
    assert lines[0]["error"] == "malformed request"
    assert lines[1]["error"] == "malformed request"
    assert lines[2]["ok"] is True  # the same connection still serves a good request


def test_deeply_nested_json_does_not_crash_the_server(oracle):
    host, port = client.parse_address(oracle.addr)
    depth = 20000
    nested = "[" * depth + "]" * depth
    line = ('{"token":"%s","op":"%s","payload":{"candidate":%s}}\n' % (TOKEN, oracle.ops[0], nested)).encode()
    if len(line) < 16 * 1024 * 1024:
        with socket.create_connection((host, port), timeout=30) as s:
            s.sendall(line)
            s.settimeout(30)
            buf = b""
            try:
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            except socket.timeout:
                pass
    # whatever happened to that request, the server is still alive and sealed
    r = client.request(oracle.addr, TOKEN, "describe")
    assert r["ok"] is True
    assert oracle.ctx.mode == oracle.sealed


def test_concurrent_calls_all_answer_and_keep_mode_sealed(oracle):
    def one(_i):
        return client.request(oracle.addr, TOKEN, oracle.ops[0], {"candidate": {}, "plan": {}, "model": {}})

    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(one, range(64)))
    assert all(isinstance(r.get("ok"), bool) for r in results)
    assert oracle.ctx.mode == oracle.sealed


def test_deeply_nested_json_gets_a_structured_reply(oracle):
    host, port = client.parse_address(oracle.addr)
    nested = "[" * 5000 + "]" * 5000
    line = ('{"token":"%s","op":"%s","payload":{"candidate":%s}}\n' % (TOKEN, oracle.ops[0], nested)).encode()
    with socket.create_connection((host, port), timeout=15) as s:
        s.sendall(line)
        s.settimeout(10)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    assert buf, "server sent no reply for deeply nested JSON (RecursionError escaped handle_line)"
    reply = json.loads(buf.decode())
    assert reply["ok"] is False and reply["error"] == "malformed request"


def test_budget_exhaustion_refuses_then_logs(tmp_path):
    _mod, ev = load_eval("eval1")
    sb = tmp_path / "work"
    sb.mkdir()
    cell = Cell(eval_id="eval1", instance=ev.instances()[0], arm="grounded-readonly", rep=0)
    arm = ev.arms["grounded-readonly"]
    ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=sb, state={})
    log = tmp_path / "oracle_calls.jsonl"
    srv = OracleServer(ev.oracle_handlers(cell), ctx, TOKEN, log, query_budget=3,
                       specs=arm.tools.oracle_tools)
    addr = srv.start()
    try:
        rem = []
        for _ in range(5):
            r = client.request(addr, TOKEN, "run_model", {"plan": {"bindings": {}}})
            rem.append(r.get("calls_remaining"))
        assert rem[:3] == [2, 1, 0]
        assert all(x["ok"] is False and x["error"] == "query budget exhausted"
                   for x in [client.request(addr, TOKEN, "run_model", {"plan": {}})])
    finally:
        srv.stop()
    recs = read_calls_log(log)
    assert any(r.get("budget_exhausted") for r in recs)
    # at most `budget` calls carry a call_index (reached the handler)
    assert sum(1 for r in recs if r.get("call_index") is not None) == 3


def test_nested_override_keys_are_logged(tmp_path):
    _mod, ev = load_eval("eval1")
    sb = tmp_path / "work"
    sb.mkdir()
    o = RunningOracle(ev, "grounded-iterate", sb)
    try:
        client.request(o.addr, TOKEN, "run_model", {"plan": {"bindings": {}, "mode": "raw", "backend": "live"}})
        assert o.ctx.mode == o.sealed
    finally:
        o.stop()
    recs = [r for r in read_calls_log(o.log) if r.get("op") == "run_model"]
    assert recs and recs[-1]["override_attempt"] is True
    # FIXED 2026-10-02 (integration): nested keys are logged by path, not stripped
    assert recs[-1]["override_keys"] == ["plan.backend", "plan.mode"]
