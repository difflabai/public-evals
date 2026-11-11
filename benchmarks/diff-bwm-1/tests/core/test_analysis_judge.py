"""dbwm.core.judge: isolated claude_cli judge (via the fake claude), anthropic_api judge, mock, panels."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from analysis_fixtures.helpers import FakeChatAPI, chat_response
from dbwm.core import judge as J

HERE = Path(__file__).resolve().parent
FAKE_CLAUDE = HERE / "fake_claude" / "claude"  # core-runtime's test double of `claude -p`

SCHEMA = {"type": "object", "properties": {"passed": {"type": "boolean"}, "reasoning": {"type": "string"}},
          "required": ["passed", "reasoning"], "additionalProperties": False}


def _fake_bin() -> Path:
    if not FAKE_CLAUDE.exists():
        pytest.fail(f"fake claude missing: {FAKE_CLAUDE.name} (tests/core/fake_claude/)")
    return FAKE_CLAUDE


def _judge(tmp_path, script, **kw):
    sp = tmp_path / "script.json"
    sp.write_text(json.dumps(script))
    log = tmp_path / "fake_log.jsonl"
    j = J.ClaudeCliJudge(tmp_path / "judge_out", claude_bin=str(_fake_bin()),
                         extra_env={"DBWM_FAKE_LOG": str(log), "DBWM_FAKE_SCRIPT": str(sp)},
                         tmp_root=tmp_path / "jtmp", **kw)
    (tmp_path / "jtmp").mkdir(exist_ok=True)
    return j, log


def _log(log: Path) -> dict:
    return json.loads(log.read_text().splitlines()[-1])


def test_claude_cli_judge_argv_cwd_env_and_valid_judgment(tmp_path):
    verdict_text = 'Looks fine. {"passed": true, "reasoning": "has {braces} inside"}'
    j, log = _judge(tmp_path, {"cost": 0.002, "actions": [{"op": "result", "result": verdict_text}]})
    res = j.judge("You are a strict grader.", "Grade this.", SCHEMA, "claude-sonnet-4-6", label="w1 t2/pass#1")
    assert res.ok, res.error
    assert res.parsed == {"passed": True, "reasoning": "has {braces} inside"}
    assert res.tool_use_count == 0 and res.cost_usd == 0.002 and res.init_tools == []
    rec = _log(log)
    argv = rec["argv"]
    i = argv.index("--tools")
    assert argv[i + 1] == ""
    for flag in ("-p", "--restricted", "--strict-mcp-config", "--disable-slash-commands",
                 "--no-session-persistence", "--verbose"):
        assert flag in argv
    assert argv[argv.index("--max-turns") + 1] == "2"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-6"
    assert "--allowedTools" not in argv and "--dangerously-skip-permissions" not in argv
    assert Path(argv[argv.index("--system-prompt-file") + 1]).read_text() == "You are a strict grader."
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA
    assert rec["stdin"] == "Grade this."
    cwd = Path(rec["cwd"])
    assert cwd.name.startswith("dbwm_judge_") and not cwd.exists()  # fresh, then removed
    assert "ANTHROPIC_API_KEY" not in rec["env"]
    assert res.raw_path.exists() and res.raw_path.name.startswith("w1_t2_pass_1-")
    rj = json.loads(res.raw_path.with_name(res.raw_path.name.replace(".stream.jsonl", ".result.json")).read_text())
    assert rj["ok"] is True


def test_structured_output_field_is_preferred(tmp_path):
    j, _ = _judge(tmp_path, {"actions": [{"op": "result", "result": "ignored text",
                                          "structured_output": {"passed": False, "reasoning": "no"}}]})
    res = j.judge("", "Grade.", SCHEMA, "haiku")
    assert res.ok and res.parsed == {"passed": False, "reasoning": "no"}


def test_tool_use_makes_the_pass_a_judge_error(tmp_path):
    j, _ = _judge(tmp_path, {"actions": [{"op": "bash", "command": "true"},
                                         {"op": "result", "result": '{"passed": true, "reasoning": "x"}'}]})
    res = j.judge("", "Grade.", SCHEMA, "haiku")
    assert not res.ok and res.error_class == J.ERR_ISOLATION and res.tool_use_count == 1
    assert res.parsed is None


def test_nonempty_init_tools_or_skills_is_a_judge_error(tmp_path):
    j, _ = _judge(tmp_path, {"init_overrides": {"tools": ["Bash", "Read"]},
                             "actions": [{"op": "result", "result": '{"passed": true, "reasoning": "x"}'}]})
    res = j.judge("", "Grade.", SCHEMA, "haiku")
    assert res.error_class == J.ERR_ISOLATION and "init.tools" in res.error
    j, _ = _judge(tmp_path, {"init_overrides": {"skills": ["operator-skill"]},
                             "actions": [{"op": "result", "result": '{"passed": true, "reasoning": "x"}'}]})
    assert j.judge("", "Grade.", SCHEMA, "haiku").error_class == J.ERR_ISOLATION
    j, _ = _judge(tmp_path, {"init_overrides": {"plugins": [{"name": "mine", "path": "/x/plugins/mine"}]},
                             "actions": [{"op": "result", "result": '{"passed": true, "reasoning": "x"}'}]})
    assert j.judge("", "Grade.", SCHEMA, "haiku").error_class == J.ERR_ISOLATION


def test_structured_output_tool_tolerated_only_with_schema(tmp_path):
    script = {"init_overrides": {"tools": ["StructuredOutput"]},
              "actions": [{"op": "result", "result": '{"passed": true, "reasoning": "x"}'}]}
    j, _ = _judge(tmp_path, script)
    assert j.judge("", "Grade.", SCHEMA, "haiku").ok
    j, _ = _judge(tmp_path, script)
    assert j.judge("", "Grade.", None, "haiku").error_class == J.ERR_ISOLATION


def test_infra_failures_are_errors_never_fails(tmp_path):
    j, _ = _judge(tmp_path, {"cost": 0, "actions": [
        {"op": "result", "is_error": True, "subtype": "success", "result": "Not logged in · Please run /login",
         "total_cost_usd": 0, "num_turns": 1}]})
    res = j.judge("", "Grade.", SCHEMA, "haiku")
    assert res.error_class == J.ERR_INFRA and res.error.startswith("infra_auth")
    j, _ = _judge(tmp_path, {"actions": [{"op": "exit", "code": 3}]})
    res = j.judge("", "Grade.", SCHEMA, "haiku")
    assert res.error_class == J.ERR_INFRA and "no result event" in res.error
    j, _ = _judge(tmp_path, {"actions": [{"op": "result", "result": "I think it passes."}]})
    res = j.judge("", "Grade.", SCHEMA, "haiku")
    assert res.error_class == J.ERR_PARSE
    j, _ = _judge(tmp_path, {"actions": [{"op": "result", "result": '{"passed": "yes", "reasoning": "x"}'}]})
    assert j.judge("", "Grade.", SCHEMA, "haiku").error_class == J.ERR_PARSE
    missing = J.ClaudeCliJudge(tmp_path / "o2", claude_bin=str(tmp_path / "no-such-claude"), env={"PATH": "/usr/bin"})
    assert missing.judge("", "Grade.", SCHEMA, "haiku").error_class == J.ERR_INFRA


def test_timeout_kills_the_judge(tmp_path):
    j, _ = _judge(tmp_path, {"actions": [{"op": "sleep", "seconds": 30}]}, timeout_s=1.5)
    res = j.judge("", "Grade.", SCHEMA, "haiku")
    assert res.error_class == J.ERR_INFRA and "timed out" in res.error
    assert res.duration_s < 20


def test_concurrent_repeated_passes_never_share_files(tmp_path):
    j, _ = _judge(tmp_path, {"actions": [{"op": "result", "result": '{"passed": true, "reasoning": "x"}'}]})
    results = []

    def go():
        results.append(j.judge("", "Same prompt.", SCHEMA, "haiku", label="rep"))

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.ok for r in results)
    assert len({r.raw_path for r in results}) == 4


def test_build_argv_options():
    j = J.ClaudeCliJudge("/tmp/unused", auth_mode="api_key", effort="high", max_budget_usd=0.5, use_json_schema=False)
    argv = j.build_argv("m", None, SCHEMA)
    assert "--bare" in argv and "--json-schema" not in argv and "--system-prompt-file" not in argv
    assert argv[argv.index("--effort") + 1] == "high" and argv[argv.index("--max-budget-usd") + 1] == "0.5"
    with pytest.raises(ValueError):
        j.build_argv("", None, None)


# -- anthropic_api -------------------------------------------------------------------------------

def _messages_reply(content, stop_reason="tool_use", model="claude-sonnet-4-6"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": stop_reason, "usage": {"input_tokens": 100, "output_tokens": 20}}


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("DBWM_TEST_ANTHROPIC_KEY", "test-key-not-real")
    return "DBWM_TEST_ANTHROPIC_KEY"


def test_anthropic_api_forced_tool(tmp_path, api_key):
    reply = _messages_reply([{"type": "tool_use", "id": "t1", "name": J.JUDGMENT_TOOL,
                              "input": {"passed": True, "reasoning": "ok"}}])
    with FakeChatAPI([reply]) as api:
        j = J.AnthropicApiJudge(tmp_path, api_key_env=api_key, base_url=api.base_url[:-3],
                                pricing={"claude-sonnet-4-6": (3.0, 15.0)})
        res = j.judge("sys", "Grade.", SCHEMA, "claude-sonnet-4-6")
    assert res.ok and res.parsed == {"passed": True, "reasoning": "ok"}
    assert res.cost_usd == pytest.approx((100 * 3 + 20 * 15) / 1e6)
    body, headers = api.requests[0], api.headers[0]
    assert body["tool_choice"] == {"type": "tool", "name": J.JUDGMENT_TOOL}
    assert body["tools"][0]["input_schema"] == SCHEMA and body["system"] == "sys"
    assert headers["x-api-key"] == "test-key-not-real" and headers["anthropic-version"] == "2023-06-01"
    assert headers["user-agent"].startswith("diff-bwm-1/")
    assert "test-key-not-real" not in res.raw_path.read_text()


def test_anthropic_api_falls_back_when_forced_tool_rejected(tmp_path, api_key):
    err = (400, {"type": "error", "error": {"type": "invalid_request_error", "message":
           'tool_choice: type "tool" and "any" are not supported for this model.'}})
    ok = _messages_reply([{"type": "text", "text": '{"passed": false, "reasoning": "missing"}'}], "end_turn")
    with FakeChatAPI([err, ok]) as api:
        j = J.AnthropicApiJudge(tmp_path, api_key_env=api_key, base_url=api.base_url[:-3])
        res = j.judge("", "Grade.", SCHEMA, "claude-opus-5-5")
    assert res.ok and res.parsed["passed"] is False
    assert api.requests[1]["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert "tool_choice" not in api.requests[1]


def test_anthropic_api_errors(tmp_path, api_key, monkeypatch):
    sleeps = []
    with FakeChatAPI([(529, {"error": "overloaded"}), (529, {"error": "overloaded"}),
                      _messages_reply([{"type": "text", "text": "I decline."}], "refusal")]) as api:
        j = J.AnthropicApiJudge(tmp_path, api_key_env=api_key, base_url=api.base_url[:-3], sleep=sleeps.append)
        res = j.judge("", "Grade.", SCHEMA, "m")
    assert res.error_class == J.ERR_REFUSAL and len(sleeps) == 2
    with FakeChatAPI([(401, {"error": "bad key"})]) as api:
        j = J.AnthropicApiJudge(tmp_path, api_key_env=api_key, base_url=api.base_url[:-3])
        res = j.judge("", "Grade.", SCHEMA, "m")
    assert res.error_class == J.ERR_INFRA and res.error.startswith("infra_auth")
    with FakeChatAPI([_messages_reply([{"type": "tool_use", "id": "t", "name": "bash", "input": {}}])]) as api:
        j = J.AnthropicApiJudge(tmp_path, api_key_env=api_key, base_url=api.base_url[:-3])
        assert j.judge("", "Grade.", SCHEMA, "m").error_class == J.ERR_ISOLATION
    monkeypatch.delenv(api_key)
    res = J.AnthropicApiJudge(tmp_path, api_key_env=api_key).judge("", "Grade.", SCHEMA, "m")
    assert res.error_class == J.ERR_INFRA and "is not set" in res.error


# -- openai_compat (eval-6 GLM D2 judge) ----------------------------------------------------------------

RANK_SCHEMA = {"type": "object", "properties": {"ranking": {"type": "array", "items": {"type": "string"}},
                                                "reasoning": {"type": "string"}}, "required": ["ranking"]}


@pytest.fixture
def oc_key(monkeypatch):
    monkeypatch.setenv("DBWM_TEST_OC_KEY", "oc-key-not-real")
    return "DBWM_TEST_OC_KEY"


def test_openai_compat_judge_paper_shaped_request(tmp_path, oc_key):
    text = 'Thinking {"x": {"y": 1}} ... final: {"ranking": ["B", "A"], "reasoning": "B {dominates}"}'
    with FakeChatAPI([chat_response(text, model="accounts/fireworks/models/glm-5p1")]) as api:
        j = J.get_judge("openai_compat", tmp_path, base_url=api.base_url, api_key_env=oc_key,
                        pricing={"input": 1.0, "cached_input": 0.5, "output": 3.0})
        res = j.judge("You are a judge.", "Rank A and B.", RANK_SCHEMA, "accounts/fireworks/models/glm-5p1",
                      label="d2/w1")
    assert res.ok and res.parsed == {"ranking": ["B", "A"], "reasoning": "B {dominates}"}
    assert res.adapter == "openai_compat" and res.turns == 1 and res.tool_use_count == 0
    assert res.model_served == ["accounts/fireworks/models/glm-5p1"]
    assert res.cost_usd == pytest.approx((600 * 1.0 + 400 * 0.5 + 100 * 3.0) / 1e6)
    body, headers = api.requests[0], api.headers[0]
    assert body == {"model": "accounts/fireworks/models/glm-5p1", "max_tokens": 4000, "tools": [],
                    "messages": [{"role": "system", "content": "You are a judge."},
                                 {"role": "user", "content": "Rank A and B."}]}
    assert headers["authorization"] == "Bearer oc-key-not-real"
    assert headers["user-agent"].startswith("diff-bwm-1/") and "Mozilla" not in headers["user-agent"]
    saved = "".join(f.read_text() for f in tmp_path.iterdir())
    assert "oc-key-not-real" not in saved and "Rank A and B." in res.raw_path.read_text()
    rec = json.loads(next(tmp_path.glob("d2_w1-*.result.json")).read_text())
    assert rec["ok"] is True and rec["adapter"] == "openai_compat"


def test_openai_compat_judge_errors_are_never_fails(tmp_path, oc_key, monkeypatch):
    sleeps = []
    script = [(503, {"error": "busy"}), chat_response("no json here", finish_reason="length"),
              chat_response("", [("bash", {"command": "ls"})]),
              chat_response('{"ranking": "A"}'),
              (401, {"error": "bad key"}),
              {"choices": [{"message": {"role": "assistant", "content": None, "refusal": "no"},
                            "finish_reason": "stop"}]}]
    with FakeChatAPI(script) as api:
        j = J.OpenAICompatJudge(tmp_path, base_url=api.base_url, api_key_env=oc_key, sleep=sleeps.append,
                                empty_tools=False, extra_body={"temperature": 0})
        r1 = j.judge("", "Rank.", RANK_SCHEMA, "m")
        assert r1.error_class == J.ERR_PARSE and "length" in r1.error and sleeps == [2.0]
        assert "tools" not in api.requests[0] and api.requests[0]["temperature"] == 0
        assert j.judge("", "Rank.", RANK_SCHEMA, "m").error_class == J.ERR_ISOLATION
        r3 = j.judge("", "Rank.", RANK_SCHEMA, "m")
        assert r3.error_class == J.ERR_PARSE and "schema" in r3.error
        r4 = j.judge("", "Rank.", RANK_SCHEMA, "m")
        assert r4.error_class == J.ERR_INFRA and r4.error.startswith("infra_auth")
        assert j.judge("", "Rank.", RANK_SCHEMA, "m").error_class == J.ERR_REFUSAL
    assert J.panel_majority([None, None, True]) is None  # judge errors never become votes
    monkeypatch.delenv(oc_key)
    res = J.OpenAICompatJudge(tmp_path, base_url="http://127.0.0.1:9/v1", api_key_env=oc_key).judge(
        "", "Rank.", RANK_SCHEMA, "m")
    assert res.error_class == J.ERR_INFRA and "is not set" in res.error
    with pytest.raises(ValueError, match="base_url"):
        J.OpenAICompatJudge(tmp_path)
    monkeypatch.setenv("DBWM_JUDGE_BASE_URL", "https://example.invalid/v1")
    assert J.OpenAICompatJudge(tmp_path).base_url == "https://example.invalid/v1"
    with pytest.raises(ValueError, match="extra_body"):
        J.OpenAICompatJudge(tmp_path, extra_body={"tools": [{"type": "function"}]})


# -- mock, panels, parsing ------------------------------------------------------------------------

def test_mock_judge(tmp_path):
    m = J.MockJudge({"Grade A.": {"passed": True, "reasoning": "a"}}, out_dir=tmp_path)
    assert m.judge("", "Grade A.", SCHEMA, "m").parsed["passed"] is True
    r = m.judge("", "Grade B.", SCHEMA, "m")
    assert r.error_class == J.ERR_INFRA
    assert len(list(tmp_path.glob("*.result.json"))) == 2
    seq = J.MockJudge([{"passed": "x"}, {"passed": False, "reasoning": "b"}])
    assert seq.judge("", "u", SCHEMA, "m").error_class == J.ERR_PARSE
    assert seq.judge("", "u", SCHEMA, "m").ok
    assert seq.judge("", "u", SCHEMA, "m").error_class == J.ERR_INFRA
    fn = J.MockJudge(lambda s, u, sc, mo: {"passed": len(u) > 3, "reasoning": mo})
    assert fn.judge("", "long", SCHEMA, "m").parsed == {"passed": True, "reasoning": "m"}
    assert isinstance(J.get_judge("mock", {}), J.MockJudge)
    with pytest.raises(ValueError):
        J.get_judge("gpt", {})


@pytest.mark.parametrize("votes,want", [
    ([True, True, False], True), ([False, False, True], False), ([True, False], None),
    ([True, None, None], None), ([True, True, None], True), ([], None), ([None, None, None], None),
])
def test_panel_majority(votes, want):
    assert J.panel_majority(votes) is want


def test_panel_majority_rejects_non_bool():
    with pytest.raises(TypeError):
        J.panel_majority([1, 0, 1])
    assert J.panel_majority([True], min_valid=1) is True


def test_extract_last_json_object():
    t = 'first {"passed": false} then reasoning {"a": {"b": "}"}} final {"passed": true, "r": "x{y}"}'
    assert J.extract_last_json_object(t, "passed") == {"passed": True, "r": "x{y}"}
    assert J.extract_last_json_object(t) == {"passed": True, "r": "x{y}"}
    assert J.extract_last_json_object('{"a": 1} trailing {broken', "a") == {"a": 1}
    assert J.extract_last_json_object("no json", "a") is None
    assert J.extract_last_json_object('{"outer": {"passed": true}}', "passed") == {"passed": True}


def test_validate_against_schema():
    assert J.validate_against_schema({"passed": True, "reasoning": "x"}, SCHEMA) == []
    assert J.validate_against_schema({"passed": True}, SCHEMA)
    assert J.validate_against_schema({"passed": True, "reasoning": "x", "z": 1}, SCHEMA)
    assert J.validate_against_schema([1, "a"], {"type": "array", "items": {"type": "integer"}})
    assert J.validate_against_schema("b", {"enum": ["a"]})
    assert J.validate_against_schema(None, None) == []
