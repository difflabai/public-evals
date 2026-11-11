"""stream_json parsing, runtime init assertions, model pin matching, outcome fixtures, reset parsing."""

from __future__ import annotations

import datetime
import json

import pytest

from dbwm.core import outcomes, stream_json
from dbwm.core.stream_json import InitExpectations, check_init, model_matches
from dbwm.core.types import AgentResult, Outcome, OracleToolSpec, ToolPolicy

SPEC = OracleToolSpec("run_model", "d", {"type": "object"}, "run_model", "./run_model x.json")


def test_claude_tool_names():
    assert stream_json.claude_tool_names(ToolPolicy(frozenset({"write", "read"}))) == ["Read", "Write"]
    assert stream_json.claude_tool_names(ToolPolicy(frozenset({"read", "write"}), (SPEC,))) == [
        "Read", "Write", "mcp__dbwm__run_model"]
    assert stream_json.claude_tool_names(ToolPolicy(frozenset({"bash", "read"}), (SPEC,))) == ["Read", "Bash"]
    assert stream_json.claude_tool_names(ToolPolicy(frozenset())) == []


def ev_assistant(mid, blocks, model="claude-haiku-4-5-20251001"):
    return {"type": "assistant", "message": {"id": mid, "model": model, "content": blocks}}


def test_turns_counted_by_distinct_message_id():
    s = stream_json.StreamSummary()
    for e in [
        {"type": "system", "subtype": "init", "tools": []},
        ev_assistant("m1", [{"type": "thinking", "thinking": ""}]),
        ev_assistant("m1", [{"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": "a"}}]),
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                  "content": [{"type": "text", "text": "ok"}]}]}},
        ev_assistant("m2", [{"type": "text", "text": "done"}]),
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": 123}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "total_cost_usd": 0.01,
         "modelUsage": {"claude-haiku-4-5-20251001": {}}},
    ]:
        s.feed(e)
    assert s.turns_counted == 2 and s.events == 7
    assert [t.name for t in s.tool_calls] == ["Write"]
    assert s.tool_results[0]["text"] == "ok"
    assert s.rate_limit == {"status": "allowed", "resetsAt": 123}
    res = stream_json.fill_result(s, AgentResult(None, None, 0))
    assert res.turns == 2 and res.assistant_messages == 2 and res.cost_usd == 0.01
    assert res.models_served == ["claude-haiku-4-5-20251001"]


def test_parse_tolerates_garbage(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('not json\n{"type":"assistant","message":{"content":[]}}\n\n{"type":"assistant"}\n')
    s = stream_json.parse_file(p)
    assert s.parse_errors == 1 and s.turns_counted == 2  # anonymous messages count separately
    assert stream_json.parse_file(tmp_path / "missing").events == 0


@pytest.mark.parametrize("pin,served,ok", [
    ("haiku", "claude-haiku-4-5-20251001", True),
    ("sonnet", "claude-haiku-4-5-20251001", False),
    ("claude-sonnet-4-6", "claude-sonnet-4-6", True),
    ("claude-sonnet-4-6", "claude-sonnet-4-6-20260101", True),
    ("claude-sonnet-4-6", "claude-sonnet-4-5", False),
    ("claude-opus-4-8[1m]", "claude-opus-4-8", True),
    ("claude-sonnet-4-6", "us.anthropic.claude-sonnet-4-6-v1:0", True),
    ("haiku", None, False),
    ("default", "claude-sonnet-4-6", False),
])
def test_model_matches(pin, served, ok):
    assert model_matches(pin, served) is ok


def good_init(cwd_, **kw):
    ev = {"type": "system", "subtype": "init", "tools": ["Read", "Write", "mcp__dbwm__run_model"], "cwd": cwd_,
          "model": "claude-haiku-4-5-20251001", "skills": [],
          "mcp_servers": [{"name": "dbwm", "status": "connected"}],
          "plugins": [{"name": "x", "path": "builtin", "source": "x@builtin"}]}
    ev.update(kw)
    return ev


def exp(cwd, **kw):
    d = dict(tools=["Read", "Write", "mcp__dbwm__run_model"], cwd=cwd, model="haiku", mcp_servers=["dbwm"])
    d.update(kw)
    return InitExpectations(**d)


def test_check_init_passes_and_resolves_symlinked_cwd(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert check_init(good_init(str(real)), exp(str(link))) == []
    # literal comparison inside a container
    assert check_init(good_init("/work"), exp("/work", resolve_cwd=False)) == []


@pytest.mark.parametrize("mutation,needle", [
    ({"tools": ["Read", "Write", "Bash", "mcp__dbwm__run_model"]}, "init.tools mismatch: extra=['Bash']"),
    ({"tools": ["Read", "Write"]}, "missing=['mcp__dbwm__run_model']"),
    ({"cwd": "/somewhere/else"}, "init.cwd"),
    ({"model": "claude-sonnet-4-6"}, "does not match pinned model"),
    ({"skills": ["operator-skill"]}, "init.skills not empty"),
    ({"mcp_servers": [{"name": "dbwm", "status": "failed"}]}, "not connected"),
    ({"mcp_servers": [{"name": "dbwm", "status": "connected"}, {"name": "github", "status": "connected"}]},
     "init.mcp_servers"),
    ({"plugins": [{"name": "op", "path": "/" + "Users/x/.claude/plugins/op", "source": "op@market"}]}, "non-builtin"),
    ({"tools": "Read"}, "not a list"),
])
def test_check_init_failures(tmp_path, mutation, needle):
    fails = check_init(good_init(str(tmp_path), **mutation), exp(str(tmp_path)))
    assert any(needle in f for f in fails), fails


def test_check_init_missing():
    assert check_init(None, exp("/x")) == ["no system/init event in the stream"]


def test_served_models_checked():
    s = stream_json.parse_lines([json.dumps(ev_assistant("a", [], model="claude-sonnet-4-6")),
                                 json.dumps(ev_assistant("b", [], model="<synthetic>"))])
    assert stream_json.check_served_models(s, "haiku") == [
        "served model 'claude-sonnet-4-6' does not match pinned model 'haiku'"]
    assert stream_json.check_served_models(s, "sonnet") == []


# -- outcomes ------------------------------------------------------------------------------


def R(**kw) -> AgentResult:
    base = dict(transcript_path=None, stderr_path=None, returncode=0)
    base.update(kw)
    return AgentResult(**base)


def result_ev(**kw):
    ev = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 5, "total_cost_usd": 0.5,
          "result": "ok"}
    ev.update(kw)
    return ev


def from_result(**kw):
    ev = result_ev(**kw)
    return R(raw_result_event=ev, result_subtype=ev["subtype"], is_error=ev["is_error"], turns=ev["num_turns"],
             cost_usd=ev["total_cost_usd"])


@pytest.mark.parametrize("res,want", [
    (from_result(), Outcome.COMPLETED),
    (from_result(result="You've hit your usage limit, I will stop here"), Outcome.COMPLETED),
    (from_result(subtype="error_max_turns", is_error=True), Outcome.BUDGET_EXHAUSTED),
    (from_result(subtype="error_max_budget_usd", is_error=True), Outcome.COST_STOP),
    (from_result(subtype="success", is_error=True, result="Not logged in · Please run /login", num_turns=1,
                 total_cost_usd=0), Outcome.INFRA_AUTH),
    (from_result(is_error=True, result="Invalid API key", total_cost_usd=0), Outcome.INFRA_AUTH),
    (from_result(is_error=True, result="x", api_error_status=401, total_cost_usd=0), Outcome.INFRA_AUTH),
    (from_result(is_error=True, result="You've hit your limit · resets 4:30am (America/Denver)", num_turns=1,
                 total_cost_usd=0), Outcome.INFRA_CAP),
    # capped AFTER doing work: not a cap (it would hide a real partial trial); retryable infra instead
    (from_result(is_error=True, result="You've hit your limit · resets 4:30am", num_turns=7,
                 total_cost_usd=1.2), Outcome.CRASH),
    (from_result(is_error=True, result="API Error: 529 Overloaded", num_turns=3), Outcome.INFRA_API),
    (from_result(is_error=True, result="x", api_error_status=500), Outcome.INFRA_API),
    (from_result(is_error=True, result="something odd"), Outcome.CRASH),
    (R(killed_by_harness="clock"), Outcome.CLOCK_STOP),
    (R(killed_by_harness="turns"), Outcome.BUDGET_EXHAUSTED),
    (R(killed_by_harness="invalid_regime"), Outcome.INFRA_INVALID_REGIME),
    (R(killed_by_harness="interrupt"), Outcome.CRASH),
    (R(returncode=1), Outcome.CRASH),
    (R(error_text="Not logged in"), Outcome.INFRA_AUTH),
    (R(assertion_failures=["x"], killed_by_harness="clock"), Outcome.INFRA_INVALID_REGIME),
])
def test_classify_fixtures(res, want):
    assert outcomes.classify(res) == want


def test_classify_cap_from_rate_limit_event(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(e) for e in [
        {"type": "system", "subtype": "init"},
        {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 2_000_000_000}},
        result_ev(is_error=True, result="request rejected", num_turns=1, total_cost_usd=0)]) + "\n")
    res = R(transcript_path=t, raw_result_event=result_ev(is_error=True, result="request rejected", num_turns=1,
                                                          total_cost_usd=0),
            result_subtype="success", is_error=True, turns=1, cost_usd=0.0)
    assert outcomes.classify(res) == Outcome.INFRA_CAP
    assert outcomes.cap_reset_seconds(res, now=2_000_000_000 - 90) == 90


def test_classify_auth_from_stderr_without_result(tmp_path):
    e = tmp_path / "stderr.txt"
    e.write_text("Error: Invalid API key · Please run /login\n")
    assert outcomes.classify(R(stderr_path=e, returncode=1)) == Outcome.INFRA_AUTH


def test_reset_parser():
    now = datetime.datetime(2026, 9, 17, 3, 0, 0)
    assert outcomes.parse_reset_seconds("You've hit your limit · resets 4:30am (America/Denver)", now) == 5400
    assert outcomes.parse_reset_seconds("resets 2am", now) == 23 * 3600
    assert outcomes.parse_reset_seconds("You've hit your limit · resets Sep 18 at 1am (America/Denver)",
                                        now) == 22 * 3600
    assert outcomes.parse_reset_seconds("resets Sep 18 at 1:30 pm", now) == 34.5 * 3600
    assert outcomes.parse_reset_seconds("resets Jan 2 at 1am", now) > 100 * 24 * 3600  # next year
    assert outcomes.parse_reset_seconds("no time here", now) is None
    assert outcomes.parse_reset_seconds(None, now) is None
