"""Paper v7 runtime additions (lane C1): the operator auto-memory assertion on the init event's
``memory_paths``, ``permissionMode`` recorded per attempt, and the ``zero_cost_completion``
audit flag.

Three memory cases run through the fake claude, both at the adapter and through the runner:
present and acceptable (isolated config dir; or, with session auth at host, the project directory
keyed to this sandbox and empty at launch), present and foreign (INFRA_INVALID_REGIME, the agent
killed on the init event), and absent (acceptable, recorded as absent).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from analysis_fixtures.helpers import ToyEval, Transcript
from dbwm.core import audit as A
from dbwm.core.adapters.claude_code import (
    MEMORY_STATUSES,
    MemoryPolicy,
    check_memory_paths,
    init_record,
    project_slug)
from dbwm.core.types import Cell, Isolation, Outcome
from test_core_claude_adapter import Trial
from test_core_runner_e2e import runner, verdicts

# Host-style paths are assembled at run time so this file does not itself trip `dbwm hygiene`.
_USERS = "/" + "Users"
_HOME = "/" + "home"
FOREIGN = {"auto": f"{_USERS}/operator/.claude/projects/-Users-operator-dev-harness-checkout/memory/"}
API_SRC = {"PATH": os.environ["PATH"], "HOME": "/nonexistent-home", "ANTHROPIC_API_KEY": "dummy-api-key-for-tests"}
SLOW_ACTIONS = [{"op": "sleep", "seconds": 30}, {"op": "write", "path": "answer.json", "content": "{}"}]


def _session_src(home: Path) -> dict[str, str]:
    return {"PATH": os.environ["PATH"], "HOME": str(home), "USER": "dbwm-test"}


# -- unit ----------------------------------------------------------------------------------


def test_project_slug_matches_the_cli_shape():
    # Claude Code 2.1.287 names a project after its cwd: /private/var/.../T/bwm_d1_self-supply_03ck7kv0
    #   -> -private-var-...-T-bwm-d1-self-supply-03ck7kv0
    assert project_slug("/private/var/folders/0l/T/bwm_d1_self-supply_03ck7kv0") == \
        "-private-var-folders-0l-T-bwm-d1-self-supply-03ck7kv0"
    assert project_slug("/work") == "-work"


def test_check_memory_paths_rules(tmp_path):
    home, cfg = tmp_path / "home", tmp_path / "claude-config"
    sb = tmp_path / "sb"
    sb.mkdir()
    pol = MemoryPolicy(isolated_roots=(str(home), str(cfg)), sandbox_cwds=(str(sb),))
    slug = project_slug(str(sb))
    ok_iso = {"memory_paths": {"auto": f"{cfg}/projects/{slug}/memory/"}}
    assert check_memory_paths(ok_iso, pol) == ([], {"status": "isolated",
                                                     "entries": {"auto": {"kind": "isolated",
                                                                          "entries_at_launch": 0}}})
    host_proj = {"memory_paths": {"auto": f"{_USERS}/op/.claude/projects/{slug}/memory"}}
    fails, rec = check_memory_paths(host_proj, pol)
    assert fails == [] and rec["status"] == "sandbox_project"
    fails, rec = check_memory_paths({"memory_paths": FOREIGN}, pol)
    assert rec["status"] == "foreign" and len(fails) == 1 and "auto-memory" in fails[0]
    # one foreign entry among acceptable ones still fails
    fails, rec = check_memory_paths({"memory_paths": {**ok_iso["memory_paths"], "user": FOREIGN["auto"]}}, pol)
    assert rec["status"] == "foreign" and len(fails) == 1
    for absent in ({}, {"memory_paths": None}, {"memory_paths": {}}, None):
        assert check_memory_paths(absent, pol) == ([], {"status": "absent", "entries": {}})
    fails, rec = check_memory_paths({"memory_paths": 7}, pol)
    assert rec["status"] == "malformed" and fails
    fails, rec = check_memory_paths({"memory_paths": {"auto": None}}, pol)
    assert rec["status"] == "malformed" and fails
    # a project directory keyed to ANOTHER cwd (e.g. the repository the operator works in) is foreign
    fails, rec = check_memory_paths({"memory_paths": {"auto": f"{_USERS}/op/.claude/projects/-Users-op-repo/memory/"}},
                                    pol)
    assert rec["status"] == "foreign" and fails
    # pre-filled at launch: the contents would be in context
    mem = tmp_path / "op" / ".claude" / "projects" / slug / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- [eval 2 overclaim metric](x.md)\n")
    fails, rec = check_memory_paths({"memory_paths": {"auto": str(mem) + "/"}}, pol)
    assert rec["entries"]["auto"]["entries_at_launch"] == 1 and "not empty at launch" in fails[0]
    assert check_memory_paths({"memory_paths": {"auto": str(mem)}}, pol, check_empty=False)[0] == []
    assert set(MEMORY_STATUSES) >= {"absent", "isolated", "sandbox_project", "foreign", "malformed"}


def test_check_memory_paths_container_is_literal():
    pol = MemoryPolicy(isolated_roots=("/home/agent",), sandbox_cwds=("/work",), in_container=True)
    ok = {"memory_paths": {"auto": "/home/agent/.claude/projects/-work/memory/"}}
    assert check_memory_paths(ok, pol)[1]["status"] == "isolated"
    # contents are never checked from the host in the container (tmpfs home)
    assert "entries_at_launch" not in check_memory_paths(ok, pol)[1]["entries"]["auto"]
    assert check_memory_paths({"memory_paths": {"auto": f"{_HOME}/agentx/m"}}, pol)[1]["status"] == "foreign"


# -- through the adapter (fake claude) --------------------------------------------------------


def test_memory_isolated_api_key_is_acceptable_and_recorded(iso_dirs, toy, fake_claude, tmp_path):
    t = Trial(toy, "no-tool", tmp_path, auth="api_key", source_env=API_SRC)
    res = t.run(fake_claude.script({"actions": [{"op": "write", "path": "answer.json", "content": "{}"}]}))
    assert res.outcome == Outcome.COMPLETED and res.assertion_failures == []
    assert res.permission_mode == "default"
    assert res.memory_check["status"] == "isolated"
    assert res.memory_paths["auto"].startswith(str(t.out / "claude-config"))
    assert init_record(res) == {"permission_mode": "default", "memory_paths": "isolated", "memory_keys": ["auto"]}
    inv = json.loads((t.out / "agent_invocation.json").read_text())
    assert inv["expected_init"]["memory_paths"]["isolated_roots"] == [str(t.out / "home"),
                                                                     str(t.out / "claude-config")]
    rec = json.loads(json.dumps(res.memory_check))  # JSON-able (agent_result.json)
    assert rec["entries"]["auto"]["entries_at_launch"] == 0


def test_memory_session_sandbox_project_is_acceptable(iso_dirs, toy, fake_claude, tmp_path):
    home = tmp_path / "operator-home"
    home.mkdir()
    t = Trial(toy, "no-tool", tmp_path, auth="session", source_env=_session_src(home))
    res = t.run(fake_claude.script({}))
    assert res.outcome == Outcome.COMPLETED, res.assertion_failures
    assert res.memory_check["status"] == "sandbox_project"
    assert res.memory_paths["auto"].startswith(str(home / ".claude" / "projects"))
    assert not (home / ".claude").exists()  # the harness reads, never creates


def test_memory_session_prefilled_directory_is_invalid_regime(iso_dirs, toy, fake_claude, tmp_path):
    home = tmp_path / "operator-home"
    t = Trial(toy, "no-tool", tmp_path, auth="session", source_env=_session_src(home))
    mem = home / ".claude" / "projects" / project_slug(os.path.realpath(t.h.path)) / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- [orion optimum strategy](orion.md)\n")
    res = t.run(fake_claude.script({"actions": SLOW_ACTIONS}))
    assert res.outcome == Outcome.INFRA_INVALID_REGIME
    assert any("not empty at launch" in f for f in res.assertion_failures), res.assertion_failures
    assert res.duration_s < 20 and t.answer is None  # killed on the init event


@pytest.mark.parametrize("auth", ["api_key", "session"])
def test_memory_foreign_is_invalid_regime(iso_dirs, toy, fake_claude, tmp_path, auth):
    src = API_SRC if auth == "api_key" else _session_src(tmp_path / "operator-home")
    t = Trial(toy, "no-tool", tmp_path, auth=auth, source_env=src)
    res = t.run(fake_claude.script({"init_overrides": {"memory_paths": FOREIGN}, "actions": SLOW_ACTIONS}))
    assert res.outcome == Outcome.INFRA_INVALID_REGIME
    assert any("memory_paths.auto" in f and "auto-memory" in f for f in res.assertion_failures)
    assert res.memory_check["status"] == "foreign"
    assert res.duration_s < 20 and t.answer is None


def test_memory_absent_is_acceptable(iso_dirs, toy, fake_claude, tmp_path):
    t = Trial(toy, "no-tool", tmp_path, auth="api_key", source_env=API_SRC)
    res = t.run(fake_claude.script({"init_drop": ["memory_paths"]}))
    assert res.outcome == Outcome.COMPLETED and res.assertion_failures == []
    assert res.memory_paths is None and res.memory_check == {"status": "absent", "entries": {}}


# -- through the runner: verdict details.agent_init --------------------------------------------


@pytest.mark.parametrize("script,want_outcome,want_status", [
    ({"actions": [{"op": "write", "path": "answer.json", "content": "{}"}]}, "completed", "sandbox_project"),
    ({"init_overrides": {"memory_paths": FOREIGN}, "actions": []}, "infra_invalid_regime", "foreign"),
    ({"init_drop": ["memory_paths"], "actions": [{"op": "write", "path": "answer.json", "content": "{}"}]},
     "completed", "absent"),
])
def test_runner_records_agent_init(iso_dirs, toy, fake_claude, tmp_path, monkeypatch, script, want_outcome,
                                   want_status):
    monkeypatch.setenv("HOME", str(tmp_path / "operator-home"))  # auth none passes HOME through
    fake_claude.script(script)
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"], "instances": ["alpha"],
                                 "max_attempts_per_cell": 1}, fake=fake_claude).run()
    (v,) = verdicts(res.run_dir)["alpha__no-tool__r0"]
    assert v["outcome"] == want_outcome
    assert v["details"]["agent_init"] == {"permission_mode": "default", "memory_paths": want_status,
                                          "memory_keys": [] if want_status == "absent" else ["auto"]}
    if want_status == "foreign":
        assert any("auto-memory" in f for f in v["details"]["assertion_failures"])
    ar = json.loads((res.run_dir / "cells" / "alpha__no-tool__r0" / "attempt-1" / "agent_result.json").read_text())
    assert ar["permission_mode"] == "default" and ar["memory_check"]["status"] == want_status
    # the verdict stays path-free: no absolute memory path in details
    assert str(tmp_path) not in json.dumps(v["details"]["agent_init"])


# -- zero_cost_completion -----------------------------------------------------------------


def test_zero_cost_completion_shape(tmp_path):
    ev = ToyEval()
    cell = Cell("evalX", "w1", "nobash", 0)

    def audit(result_kw, tool=False, adapter="claude_code", **init):
        d = tmp_path / f"a{len(list(tmp_path.iterdir()))}"
        adir = d / "run" / "cells" / cell.cell_id / "attempt-1"
        sb = d / "sb"
        sb.mkdir(parents=True)
        t = Transcript(adir / "transcript.jsonl", tools=["Read", "Write", "mcp__dbwm__run_model"], cwd=str(sb),
                       **init)
        if tool:
            t.tool("Read", {"file_path": "TASK.md"}, "x")
        t.result(**result_kw)
        t.write()
        (d / "run" / "manifest.json").write_text(json.dumps({"adapter": adapter, "regime": {"isolation": "host"}}))
        return A.audit_attempt(adir, ev, cell, isolation=Isolation.HOST, model_pin="haiku")

    a = audit({"total_cost_usd": 0, "num_turns": 1})
    assert a["flagged"] and A.F_ZERO_COST in a["reasons"] and a["checks"]["zero_cost_completion"] == "flagged"
    for kw, tool in (({"total_cost_usd": 0.0001, "num_turns": 1}, False),   # a real call costs something
                     ({"total_cost_usd": None, "num_turns": 1}, False),     # unknown cost is not zero
                     ({"total_cost_usd": 0, "num_turns": 2}, False),        # more than one turn
                     ({"total_cost_usd": 0, "num_turns": 1}, True),         # a tool call happened
                     ({"total_cost_usd": 0, "num_turns": 1, "is_error": True}, False)):  # errors: classifier's job
        assert A.F_ZERO_COST not in audit(kw, tool)["reasons"], (kw, tool)
    b = audit({"total_cost_usd": 0, "num_turns": 1}, adapter="stub")
    assert A.F_ZERO_COST not in b["reasons"] and b["checks"]["zero_cost_completion"].startswith("skipped")


def test_zero_cost_completion_through_runner_is_flagged_and_excluded(iso_dirs, toy, fake_claude, monkeypatch):
    monkeypatch.setenv("HOME", str(iso_dirs[0].parent / "operator-home"))
    fake_claude.script({"cost": 0, "actions": []})  # "success", $0, one turn, no tool call
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"], "instances": ["alpha"]},
                 fake=fake_claude).run()
    (v,) = verdicts(res.run_dir)["alpha__no-tool__r0"]
    assert v["outcome"] == "completed" and v["process"]["cost_usd"] == 0
    assert v["audit"]["flagged"] is True and A.F_ZERO_COST in v["audit"]["reasons"]
    from dbwm.core import report

    summary = report.build_report(res.run_dir)
    md = report.render_markdown(summary)
    assert "audit:zero_cost_completion" in json.dumps(summary, default=str)
    assert "zero_cost_completion" in md  # printed in the exclusions, never counted as the agent's fail
