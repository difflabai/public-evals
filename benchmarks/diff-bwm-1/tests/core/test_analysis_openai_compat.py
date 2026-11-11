"""dbwm.core.adapters.openai_compat against a local fake chat-completions server (no network)."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from analysis_fixtures.helpers import ORACLE_SPEC, FakeChatAPI, ToyEval, chat_response
from dbwm.core import audit as A
from dbwm.core import outcomes, stream_json
from dbwm.core.adapters import openai_compat as OC
from dbwm.core.oracle.server import OracleServer
from dbwm.core.types import (
    AgentRequest,
    ArmSpec,
    Budget,
    Cell,
    Isolation,
    OracleContext,
    Outcome,
    ToolPolicy,
)

KEY_ENV = "DBWM_TEST_OC_KEY"
KEY = "test-oc-key-" + "Z" * 12
NOBASH = ToolPolicy(frozenset({"read", "write", "edit", "glob", "grep"}), (ORACLE_SPEC,))
BASH = ToolPolicy(frozenset({"read", "write", "bash"}), (ORACLE_SPEC,))
PLAIN = ToolPolicy(frozenset({"read", "write"}))


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)


def _req(tmp_path: Path, api: FakeChatAPI | None, *, policy=PLAIN, isolation=Isolation.HOST, max_turns=10,
         wall=60, usd=None, oracle=(None, None), extra=None, **cfg) -> AgentRequest:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "TASK.md").write_text("Write plan.json.\n")
    out = tmp_path / "attempt"
    out.mkdir(exist_ok=True)
    conf = {"base_url": api.base_url if api else None, "api_key_env": KEY_ENV, **cfg}
    ex = {"regime_extra": {"openai_compat": conf}}
    ex.update(extra or {})
    return AgentRequest(cell=Cell("evalX", "w1", "arm", 0), prompt="Write plan.json with {\"x\": 1}.",
                        sandbox=sandbox, tools=policy, budget=Budget(max_turns, wall, usd), model="acme/model-1",
                        effort=None, isolation=isolation, harness_out=out, oracle_address=oracle[0],
                        oracle_token=oracle[1], auth_mode="none", env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                        extra=ex)


def _events(res):
    return [json.loads(line) for line in Path(res.transcript_path).read_text().splitlines()]


def test_tool_loop_writes_deliverable_and_transcript_is_normalized(tmp_path):
    script = [chat_response("I'll write it.", [("write_file", {"path": "plan.json", "content": '{"x": 1}'})]),
              chat_response("", [("read_file", {"path": "plan.json"})]),
              chat_response("Done.")]
    with FakeChatAPI(script) as api:
        req = _req(tmp_path, api, pricing={"input": 1.4, "cached_input": 0.26, "output": 4.4})
        res = OC.OpenAICompatAdapter().run(req)
    assert (req.sandbox / "plan.json").read_text() == '{"x": 1}'
    assert outcomes.classify(res) == Outcome.COMPLETED
    assert res.turns == 3 and res.assistant_messages == 3 and res.result_subtype == "success"
    assert res.models_served == ["acme/model-1"] and res.tools_available == ["read_file", "write_file"]
    per_turn = (600 * 1.4 + 400 * 0.26 + 100 * 4.4) / 1e6
    assert res.cost_usd == pytest.approx(3 * per_turn, abs=1e-6)
    assert [t.name for t in res.tool_calls] == ["write_file", "read_file"]
    evs = _events(res)
    assert evs[0]["type"] == "system" and evs[0]["adapter"] == "openai_compat" and evs[0]["cwd"] == str(
        req.sandbox.resolve())
    assert evs[-1]["type"] == "result" and evs[-1]["num_turns"] == 3
    summary = stream_json.parse_file(res.transcript_path)
    assert summary.turns_counted == 3
    # Request shape: model, both tools, honest UA, bearer key; the key appears nowhere on disk.
    body, headers = api.requests[0], api.headers[0]
    assert body["model"] == "acme/model-1"
    assert [t["function"]["name"] for t in body["tools"]] == ["read_file", "write_file"]
    assert headers["user-agent"] == OC.USER_AGENT and "mozilla" not in headers["user-agent"].lower()
    assert headers["authorization"] == f"Bearer {KEY}"
    assert body["messages"][0]["role"] == "system" and "turn" not in body["messages"][0]["content"].lower()
    for p in req.harness_out.iterdir():
        assert KEY not in p.read_text(errors="replace"), p.name
    # The assistant tool call is echoed back with its id, then the tool result.
    second = api.requests[1]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "call_0" and second[-1]["role"] == "tool"


def test_audit_reads_the_transcript(tmp_path):
    script = [chat_response("", [("write_file", {"path": "plan.json", "content": "{}"})]), chat_response("ok")]
    with FakeChatAPI(script) as api:
        req = _req(tmp_path, api)
        res = OC.OpenAICompatAdapter().run(req)
    assert outcomes.classify(res) == Outcome.COMPLETED
    ev = ToyEval()
    ev.arms["arm"] = ArmSpec("arm", "plain", PLAIN)
    a = A.audit_attempt(req.harness_out, ev, req.cell, isolation=Isolation.HOST, model_pin="acme/model-1",
                        sandbox=req.sandbox)
    assert not a["flagged"], a["findings"]
    assert a["checks"]["regime"] == "ok"


def test_path_escapes_are_refused(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("hidden")
    calls = [("read_file", {"path": "../secret.txt"}), ("read_file", {"path": str(outside)}),
             ("write_file", {"path": "../escape.txt", "content": "x"}),
             ("read_file", {"path": "link/secret.txt"}), ("list_dir", {"path": ".."}),
             ("grep_files", {"pattern": "hidden", "path": ".."}),
             ("edit_file", {"path": "../secret.txt", "old_string": "h", "new_string": "H"})]
    script = [chat_response("", calls), chat_response("done")]
    with FakeChatAPI(script) as api:
        req = _req(tmp_path, api, policy=NOBASH)
        (req.sandbox / "link").symlink_to(tmp_path)
        res = OC.OpenAICompatAdapter().run(req)
    results = [b for e in _events(res) if e["type"] == "user" for b in e["message"]["content"]]
    assert len(results) == len(calls)
    assert all(r["is_error"] and "outside the working directory" in r["content"] for r in results), results
    assert not (tmp_path / "escape.txt").exists() and outside.read_text() == "hidden"
    assert all("hidden" not in r["content"] for r in results)
    # No host path is revealed to the model (except the one it typed itself, echoed in call 1).
    assert str(tmp_path) not in json.dumps(results[:1] + results[2:])


def test_file_tools(tmp_path):
    calls = [("write_file", {"path": "sub/dir/a.txt", "content": "alpha beta\nalpha\n"}),
             ("edit_file", {"path": "sub/dir/a.txt", "old_string": "beta", "new_string": "gamma"}),
             ("edit_file", {"path": "sub/dir/a.txt", "old_string": "alpha", "new_string": "x"}),
             ("list_dir", {"pattern": "**/*.txt"}), ("grep_files", {"pattern": "^alpha", "glob": "*.txt"}),
             ("read_file", {"path": "missing.txt"}), ("write_file", {"path": "bad"}),
             ("nope", {}), ("read_file", "{not json")]
    script = [chat_response("", calls), chat_response("done")]
    with FakeChatAPI(script) as api:
        req = _req(tmp_path, api, policy=NOBASH)
        res = OC.OpenAICompatAdapter().run(req)
    out = [b for e in _events(res) if e["type"] == "user" for b in e["message"]["content"]]
    texts = [o["content"] for o in out]
    assert (req.sandbox / "sub/dir/a.txt").read_text() == "alpha gamma\nalpha\n"
    assert not out[1]["is_error"] and out[2]["is_error"] and "occurs 2 times" in texts[2]
    assert "sub/dir/a.txt" in texts[3]
    assert texts[4].splitlines() == ["sub/dir/a.txt:1:alpha gamma", "sub/dir/a.txt:2:alpha"]
    assert out[5]["is_error"] and "does not exist" in texts[5]
    assert out[6]["is_error"] and "bad arguments" in texts[6]
    assert out[7]["is_error"] and "unknown tool" in texts[7]
    assert out[8]["is_error"] and "not valid JSON" in texts[8]


def test_max_turns_is_budget_exhausted(tmp_path):
    loop = [chat_response("", [("read_file", {"path": "TASK.md"})]) for _ in range(5)]
    with FakeChatAPI(loop) as api:
        req = _req(tmp_path, api, max_turns=3)
        res = OC.OpenAICompatAdapter().run(req)
    assert len(api.requests) == 3 and res.result_subtype == "error_max_turns"
    assert outcomes.classify(res) == Outcome.BUDGET_EXHAUSTED


def test_length_cutoff_continues_neutrally(tmp_path):
    script = [chat_response("partial", finish_reason="length"), chat_response("Done.")]
    with FakeChatAPI(script) as api:
        res = OC.OpenAICompatAdapter().run(_req(tmp_path, api))
    assert res.turns == 2 and api.requests[1]["messages"][-1]["content"] == OC.CONTINUE_AFTER_LENGTH


def test_api_errors_classify_as_infra(tmp_path):
    sleeps: list[float] = []
    with FakeChatAPI([(401, {"error": {"message": "invalid key"}})]) as api:
        res = OC.OpenAICompatAdapter(sleep=sleeps.append).run(_req(tmp_path / "a", api))
    assert outcomes.classify(res) == Outcome.INFRA_AUTH and res.cost_usd == 0.0
    with FakeChatAPI([(503, "busy"), (503, "busy"), (503, "busy")]) as api:
        res = OC.OpenAICompatAdapter(sleep=sleeps.append).run(_req(tmp_path / "b", api, retries=2))
    assert outcomes.classify(res) == Outcome.INFRA_API and len(api.requests) == 3 and len(sleeps) == 2
    with FakeChatAPI([(429, "slow down", {"retry-after": "7"}), chat_response("ok")]) as api:
        res = OC.OpenAICompatAdapter(sleep=sleeps.append).run(_req(tmp_path / "c", api))
    assert outcomes.classify(res) == Outcome.COMPLETED and sleeps[-1] == 7.0
    api_dead = _req(tmp_path / "d", None)
    api_dead.extra["regime_extra"]["openai_compat"]["base_url"] = "http://127.0.0.1:9/v1"
    res = OC.OpenAICompatAdapter(sleep=lambda s: None).run(api_dead)
    assert outcomes.classify(res) == Outcome.INFRA_API


def test_missing_key_and_config_errors(tmp_path, monkeypatch):
    monkeypatch.delenv(KEY_ENV)
    with FakeChatAPI([]) as api:
        res = OC.OpenAICompatAdapter().run(_req(tmp_path / "a", api))
    assert outcomes.classify(res) == Outcome.INFRA_AUTH and api.requests == []
    monkeypatch.setenv(KEY_ENV, KEY)
    res = OC.OpenAICompatAdapter().run(_req(tmp_path / "b", None))
    assert outcomes.classify(res) == Outcome.INFRA_INVALID_REGIME and "base_url" in res.assertion_failures[0]
    with FakeChatAPI([]) as api:
        res = OC.OpenAICompatAdapter().run(_req(tmp_path / "c", api, temperature=0))
    assert outcomes.classify(res) == Outcome.INFRA_INVALID_REGIME
    with FakeChatAPI([]) as api:
        res = OC.OpenAICompatAdapter().run(_req(tmp_path / "e", api, policy=BASH, isolation=Isolation.HOST))
    assert outcomes.classify(res) == Outcome.INFRA_INVALID_REGIME and api.requests == []
    with FakeChatAPI([]) as api:
        res = OC.OpenAICompatAdapter().run(_req(tmp_path / "f", api, policy=BASH, isolation=Isolation.CONTAINER))
    assert outcomes.classify(res) == Outcome.INFRA_INVALID_REGIME  # no container session


def test_clock_and_usd_rails(tmp_path):
    t = [0.0]

    def clock():
        return t[0]

    def advance(body):
        t[0] += 40
        return chat_response("", [("read_file", {"path": "TASK.md"})])

    with FakeChatAPI([advance, advance, advance]) as api:
        res = OC.OpenAICompatAdapter(clock=clock).run(_req(tmp_path / "a", api, wall=60))
    assert res.killed_by_harness == "clock" and outcomes.classify(res) == Outcome.CLOCK_STOP
    assert len(api.requests) == 2
    big = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    with FakeChatAPI([chat_response("", [("read_file", {"path": "TASK.md"})], usage=big)] * 3) as api:
        res = OC.OpenAICompatAdapter().run(_req(tmp_path / "b", api, usd=1.0, pricing={"input": 1.5, "output": 2}))
    assert res.killed_by_harness == "cost" and outcomes.classify(res) == Outcome.COST_STOP and len(api.requests) == 1


def test_host_unsafe_bash_never_sees_the_key(tmp_path):
    script = [chat_response("", [("run_bash", {"command": "env; pwd; echo hi > out.txt"})]), chat_response("ok")]
    with FakeChatAPI(script) as api:
        req = _req(tmp_path, api, policy=BASH, isolation=Isolation.HOST_UNSAFE, oracle=("tcp://127.0.0.1:1", "t" * 16))
        res = OC.OpenAICompatAdapter().run(req)
    out = [b for e in _events(res) if e["type"] == "user" for b in e["message"]["content"]][0]["content"]
    assert KEY not in out and KEY_ENV not in out
    assert "DBWM_ORACLE=tcp://127.0.0.1:1" in out and str(req.sandbox.resolve()) in out
    assert (req.sandbox / "out.txt").read_text() == "hi\n"
    # Bash arms reach the oracle through the staged CLI shim, never as a function.
    assert [t["function"]["name"] for t in api.requests[0]["tools"]] == ["read_file", "write_file", "run_bash"]


def test_bash_timeout(tmp_path):
    script = [chat_response("", [("run_bash", {"command": "sleep 30"})]), chat_response("ok")]
    with FakeChatAPI(script) as api:
        req = _req(tmp_path, api, policy=BASH, isolation=Isolation.HOST_UNSAFE, bash_timeout_s=1)
        res = OC.OpenAICompatAdapter().run(req)
    out = [b for e in _events(res) if e["type"] == "user" for b in e["message"]["content"]][0]
    assert out["is_error"] and "[exit 124]" in out["content"] and "timed out" in out["content"]


def test_eval_command_timeout_overrides_regime_bash_timeout(tmp_path):
    """EVAL.command_timeout_s (runner: extra["command_timeout_s"]) wins over the regime's bash_timeout_s."""
    script = [chat_response("", [("run_bash", {"command": "sleep 30"})]), chat_response("ok")]
    with FakeChatAPI(script) as api:
        req = _req(tmp_path, api, policy=BASH, isolation=Isolation.HOST_UNSAFE, bash_timeout_s=600,
                   extra={"command_timeout_s": 1})
        assert OC.OpenAICompatAdapter().config(req)["bash_timeout_s"] == 1.0
        res = OC.OpenAICompatAdapter().run(req)
    out = [b for e in _events(res) if e["type"] == "user" for b in e["message"]["content"]][0]
    assert out["is_error"] and "[exit 124]" in out["content"]
    bad = _req(tmp_path, None, policy=BASH, isolation=Isolation.HOST_UNSAFE, extra={"command_timeout_s": 0})
    bad.extra["regime_extra"]["openai_compat"]["base_url"] = "http://127.0.0.1:9"
    with pytest.raises(OC.ConfigError, match="command_timeout_s"):
        OC.OpenAICompatAdapter().config(bad)


class _FakeContainer:
    def __init__(self, tools_dir: Path):
        self.tools_dir = tools_dir
        self.env = None

    def ensure_idle_agent(self, env):
        self.env = dict(env)
        return "dbwm-agent-x"


def test_container_bash_goes_through_exec_in_agent(tmp_path, monkeypatch):
    from dbwm.core import container as core_container

    seen = []

    def fake_exec(name, argv, timeout_s):
        seen.append((name, list(argv)))
        return 0, "ran\n", ""

    monkeypatch.setattr(core_container, "exec_in_agent", fake_exec)
    fc = _FakeContainer(tmp_path / "tools")
    script = [chat_response("", [("run_bash", {"command": "./run_model plan.json"}),
                                 ("read_file", {"path": "/work/TASK.md"})]), chat_response("ok")]
    with FakeChatAPI(script) as api:
        req = _req(tmp_path, api, policy=BASH, isolation=Isolation.CONTAINER, oracle=("tcp://dbwm-sidecar:7700",
                                                                                     "t" * 16),
                   extra={"container": fc})
        req.env["ANTHROPIC_API_KEY"] = "should-not-pass"
        res = OC.OpenAICompatAdapter().run(req)
    assert seen == [("dbwm-agent-x", ["bash", "-lc", "./run_model plan.json"])]
    assert fc.env["DBWM_ORACLE"] == "tcp://dbwm-sidecar:7700" and "ANTHROPIC_API_KEY" not in fc.env
    assert KEY not in json.dumps(fc.env)
    results = [b for e in _events(res) if e["type"] == "user" for b in e["message"]["content"]]
    assert results[1]["content"].startswith("Write plan.json")  # /work maps to the sandbox
    assert _events(res)[0]["cwd"] == "/work"


def _oracle_server(tmp_path, sandbox):
    def handler(payload, ctx):
        return {"revenue": 100 * len(json.dumps(payload["plan"])), "mode": ctx.mode}

    arm = ArmSpec("arm", "nobash", NOBASH, oracle_mode="numeric", query_budget=2)
    ctx = OracleContext(cell=Cell("evalX", "w1", "arm", 0), arm=arm, mode="numeric", sandbox=sandbox, state={})
    token = secrets.token_hex(16)
    srv = OracleServer({"run_model": handler}, ctx, token, tmp_path / "attempt" / "oracle_calls.jsonl", 2,
                       specs=(ORACLE_SPEC,))
    return srv, token


def test_oracle_functions_for_mcp_transport_arms(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    srv, token = _oracle_server(tmp_path, sandbox)
    address = srv.start()
    try:
        calls = [("run_model", {"plan": {"a": 1}, "mode": "verdict"}), ("run_model", {"plan": {}}),
                 ("run_model", {"plan": {}})]
        script = [chat_response("", calls), chat_response("done")]
        with FakeChatAPI(script) as api:
            req = _req(tmp_path, api, policy=NOBASH, oracle=(address, token))
            res = OC.OpenAICompatAdapter().run(req)
    finally:
        srv.stop()
    names = [t["function"]["name"] for t in api.requests[0]["tools"]]
    assert names[-1] == "run_model" and api.requests[0]["tools"][-1]["function"]["parameters"] == dict(
        ORACLE_SPEC.input_schema)
    out = [b for e in _events(res) if e["type"] == "user" for b in e["message"]["content"]]
    first = out[0]["content"]
    assert '"mode": "numeric"' in first and "calls remaining: 1" in first  # sealed mode, not "verdict"
    assert out[2]["is_error"] and "budget" in out[2]["content"]
    log = [json.loads(x) for x in (tmp_path / "attempt" / "oracle_calls.jsonl").read_text().splitlines()]
    assert log[0]["override_attempt"] is True


def test_expected_tool_names_and_registry():
    assert OC.expected_tool_names(NOBASH) == ["read_file", "write_file", "edit_file", "list_dir", "grep_files",
                                              "run_model"]
    assert OC.expected_tool_names(BASH) == ["read_file", "write_file", "run_bash"]
    assert A.expected_tool_names(BASH, "openai_compat", Isolation.CONTAINER) == set(OC.expected_tool_names(BASH))
    from dbwm.core.adapters import registry

    ad = registry.get_adapter("openai_compat", base_url="http://x/v1", model="m", api_key_env="K")
    assert isinstance(ad, OC.OpenAICompatAdapter) and ad.defaults["base_url"] == "http://x/v1"


def test_abort_event_and_adapter_errors_are_crashes(tmp_path, monkeypatch):
    import threading

    ev = threading.Event()

    def stop_after_first(body):
        ev.set()
        return chat_response("", [("read_file", {"path": "TASK.md"})])

    with FakeChatAPI([stop_after_first, chat_response("never")]) as api:
        res = OC.OpenAICompatAdapter().run(_req(tmp_path / "a", api, extra={"abort_event": ev}))
    assert res.killed_by_harness == "interrupt" and outcomes.classify(res) == Outcome.CRASH
    assert len(api.requests) == 1

    def boom(self, name, args, cfg):
        raise RuntimeError("bug")

    monkeypatch.setattr(OC._Session, "_exec", boom)
    with FakeChatAPI([chat_response("", [("read_file", {"path": "TASK.md"})])]) as api:
        res = OC.OpenAICompatAdapter().run(_req(tmp_path / "b", api))
    assert outcomes.classify(res) == Outcome.CRASH and "adapter error" in res.error_text
    assert "RuntimeError" in Path(res.stderr_path).read_text()


def test_container_oracle_without_published_port_calls_from_inside(tmp_path, monkeypatch):
    from dbwm.core import container as core_container

    seen = []

    def fake_exec(name, argv, timeout_s):
        seen.append(list(argv))
        payload = json.loads((tmp_path / "tools" / Path(argv[-1]).name).read_text())
        return 0, json.dumps({"ok": True, "result": {"echo": payload}, "calls_remaining": None}) + "\n", ""

    monkeypatch.setattr(core_container, "exec_in_agent", fake_exec)
    fc = _FakeContainer(tmp_path / "tools")
    script = [chat_response("", [("run_model", {"plan": {"a": 1}})]), chat_response("ok")]
    with FakeChatAPI(script) as api:
        req = _req(tmp_path, api, policy=NOBASH, isolation=Isolation.CONTAINER,
                   oracle=("tcp://dbwm-sidecar:7700", "t" * 16), extra={"container": fc})
        res = OC.OpenAICompatAdapter().run(req)
    assert seen and seen[0][:2] == ["python3", f"{OC.TOOLS_MOUNT}/{OC.ORACLE_CALL_SCRIPT}"]
    assert (tmp_path / "tools" / OC.ORACLE_CALL_SCRIPT).exists() and fc.env["DBWM_ORACLE_TOKEN"] == "t" * 16
    out = [b for e in _events(res) if e["type"] == "user" for b in e["message"]["content"]][0]
    assert not out["is_error"] and json.loads(out["content"]) == {"echo": {"plan": {"a": 1}}}


def test_staged_oracle_call_script_round_trips(tmp_path):
    import subprocess
    import sys

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    srv, token = _oracle_server(tmp_path, sandbox)
    address = srv.start()
    try:
        script = tmp_path / OC.ORACLE_CALL_SCRIPT
        script.write_text(OC.ORACLE_CALL_SOURCE)
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({"plan": {"k": [1, 2]}}))
        p = subprocess.run([sys.executable, str(script), "run_model", str(payload)], capture_output=True,
                           text=True, env={"DBWM_ORACLE": address, "DBWM_ORACLE_TOKEN": token}, timeout=30)
    finally:
        srv.stop()
    reply = json.loads(p.stdout)
    assert reply["ok"] and reply["result"]["mode"] == "numeric" and reply["calls_remaining"] == 1
