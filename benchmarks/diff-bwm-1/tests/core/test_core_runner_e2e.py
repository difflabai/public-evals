"""The runner end to end on the toy eval (HOST and HOST_UNSAFE; the container path is in
test_core_container_e2e.py): sandbox -> oracle -> adapter -> classify -> collect -> score ->
verdict -> audit, plus resume, infra retry, halts, rails, re-scoring and reports."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from dbwm.core import regime as regime_mod
from dbwm.core.attempts import RunStore
from dbwm.core.runner import RunnerError, RunOptions, Runner
from dbwm.core.types import CANARY_GUID, Cell
from dbwm.core.verdict import read_verdict, validate_verdict

ANSWER_ALPHA, ANSWER_BETA = 73.1415, 62.8318

MCP_SCRIPT = {"actions": [
    {"op": "read", "path": "input.json"},
    {"op": "mcp", "tool": "run_model", "arguments": {"plan": {"x": 10}}},
    {"op": "write", "path": "answer.json", "json": {"y": "$last.y", "claimed_success": True}},
]}


def runner(toy, iso_dirs, overrides, fake=None, run_id="r1", **opt):
    _, runs = iso_dirs
    ov = {"model": "haiku", "auth_mode": "none", **overrides}
    regime = regime_mod.build_regime(toy, ov)
    akw = {"claude_bin": str(fake.bin)} if fake is not None else {}
    if regime.adapter == "replay":
        akw = {"replay_dir": opt.pop("replay_dir")}
    if regime.adapter == "command":
        akw = {"command": regime.extra["command"]}
    o = RunOptions(run_id=run_id, runs_root=runs, install_signal_handlers=False, adapter_kwargs=akw,
                   log=lambda m: None, **opt)
    return Runner(toy, regime, o)


def verdicts(run_dir: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in sorted(run_dir.glob("cells/*/attempt-*/verdict.json")):
        v = read_verdict(p)
        out.setdefault(v["cell"]["cell_id"], []).append(v)
    return out


def test_stub_host_full_pipeline_and_resume(iso_dirs, toy):
    r = runner(toy, iso_dirs, {"adapter": "stub", "model": "dbwm-stub"})
    res = r.run()
    assert res.status == "complete" and res.cells_total == 4 and res.cells_scored == 4
    vs = verdicts(res.run_dir)
    assert set(vs) == {"alpha__no-tool__r0", "alpha__mcp-oracle__r0", "beta__no-tool__r0", "beta__mcp-oracle__r0"}
    for cid, (v,) in vs.items():
        assert validate_verdict(v) == []
        assert v["outcome"] == "completed" and v["scored"] and v["ruleset"] == "v1"
        assert v["success"] is ("mcp-oracle" in cid)
        assert v["overclaim"] is ("no-tool" in cid)  # claimed success, wrong answer
        assert v["process"]["oracle_calls"] == (1 if "mcp-oracle" in cid else 0)
        assert v["audit"].get("flagged") is False, v["audit"]
        assert len(v["engine_hash"]) == 64 and v["regime_hash"] == r.regime_hash
        adir = res.run_dir / "cells" / cid / "attempt-1"
        for f in ("transcript.jsonl", "agent_result.json", "deliverables.json", "sandbox.json", "audit.json",
                  "prompt.txt", "deliverables/answer.json"):
            assert (adir / f).exists(), f
    m = json.loads((res.run_dir / "manifest.json").read_text())
    for k in ("regime", "regime_hash", "engine_hash", "harness_version", "python", "platform", "isolation",
              "conforming", "cells", "order"):
        assert k in m, k
    assert m["status"] == "complete" and m["conforming"] is True and len(m["cells"]) == 4
    assert m["effective_isolation"] == "in-process"
    st = RunStore(res.run_dir)
    assert len(st.attempt_records()) == 4
    # sandboxes deleted
    assert list(iso_dirs[0].iterdir()) == []
    # resume: nothing re-run
    res2 = runner(toy, iso_dirs, {"adapter": "stub", "model": "dbwm-stub"}).run()
    assert res2.status == "complete" and res2.attempts_this_invocation == 0
    assert len(RunStore(res.run_dir).attempt_records()) == 4
    assert len(json.loads((res.run_dir / "manifest.json").read_text())["invocations"]) == 2
    # resume with a different regime is refused
    with pytest.raises(RunnerError, match="different regime"):
        runner(toy, iso_dirs, {"adapter": "stub", "model": "dbwm-stub", "max_turns": 99}).run()


def test_claude_code_fake_mcp_arm_host(iso_dirs, toy, fake_claude):
    fake_claude.script(MCP_SCRIPT)
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["mcp-oracle"], "instances": ["alpha"]},
                 fake=fake_claude).run()
    assert res.status == "complete"
    (v,) = verdicts(res.run_dir)["alpha__mcp-oracle__r0"]
    assert v["outcome"] == "completed" and v["success"] is True and v["claimed_success"] is True
    assert v["models_served"] == ["claude-haiku-4-5-20251001"] and v["model_requested"] == "haiku"
    assert v["process"]["cli_version"].startswith("2.1.287-fake") and v["process"]["oracle_calls"] == 1
    assert v["audit"]["flagged"] is False, v["audit"]
    m = json.loads((res.run_dir / "manifest.json").read_text())
    assert m["cli_version"].startswith("2.1.287-fake") and m["claude_bin"] == str(fake_claude.bin)


def test_bash_arm_host_unsafe_nonconforming(iso_dirs, toy, fake_claude):
    fake_claude.script({"actions": [
        {"op": "bash", "command": "./toy_model input.json", "stdout_only": True},
        {"op": "write", "path": "answer.json", "json": {"y": "$last.y", "claimed_success": True}}]})
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["bash-oracle"], "instances": ["beta"],
                                 "isolation": "host-unsafe"}, fake=fake_claude).run()
    (v,) = verdicts(res.run_dir)["beta__bash-oracle__r0"]
    assert v["success"] is True and v["details"]["conforming"] is False
    m = json.loads((res.run_dir / "manifest.json").read_text())
    assert m["conforming"] is False and "host-unsafe" in m["nonconforming_reasons"][0]


def test_infra_retry_then_scored(iso_dirs, toy, fake_claude):
    fail = {"actions": [{"op": "result", "is_error": True, "result": "API Error: 529 overloaded",
                         "num_turns": 2, "total_cost_usd": 0.1, "api_error_status": 529}]}
    fake_claude.script({"sequence": [fail, MCP_SCRIPT]})
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["mcp-oracle"], "instances": ["alpha"]},
                 fake=fake_claude).run()
    vs = verdicts(res.run_dir)["alpha__mcp-oracle__r0"]
    assert [v["outcome"] for v in vs] == ["infra_api", "completed"]
    assert vs[0]["scored"] is False and vs[0]["success"] is None
    assert vs[1]["success"] is True
    assert RunStore(res.run_dir).scored_attempt("alpha__mcp-oracle__r0")["k"] == 2
    assert res.outcomes == {"infra_api": 1, "completed": 1}


def test_infra_attempts_exhausted_left_unscored(iso_dirs, toy, fake_claude):
    fake_claude.script({"actions": [{"op": "exit", "code": 1}]})
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"], "instances": ["alpha"],
                                 "max_attempts_per_cell": 2}, fake=fake_claude).run()
    assert res.cells_exhausted == ["alpha__no-tool__r0"] and res.cells_scored == 0
    assert [v["outcome"] for v in verdicts(res.run_dir)["alpha__no-tool__r0"]] == ["crash", "crash"]
    assert len(fake_claude.calls()) == 2


def test_clock_rail_is_scored_not_retried(iso_dirs, toy, fake_claude):
    fake_claude.script({"actions": [{"op": "sleep", "seconds": 60}]})
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"], "instances": ["alpha"],
                                 "wall_clock_s": 2}, fake=fake_claude).run()
    (v,) = verdicts(res.run_dir)["alpha__no-tool__r0"]
    assert v["outcome"] == "clock_stop" and v["scored"] is True
    assert v["deliverable_present"] is False and v["success"] is False  # missing deliverable = fail
    assert len(fake_claude.calls()) == 1


def test_three_invalid_regime_attempts_halt(iso_dirs, toy, fake_claude):
    fake_claude.script({"init_overrides": {"tools": ["Read", "Write", "Bash"]}, "actions": []})
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"], "reps": 3},
                 fake=fake_claude).run()
    assert res.status == "halted" and "3 consecutive infra_invalid_regime" in res.halt_reason
    assert res.outcomes == {"infra_invalid_regime": 3}
    m = json.loads((res.run_dir / "manifest.json").read_text())
    assert m["status"] == "halted" and m["halt_reason"] == res.halt_reason
    (v, *_) = next(iter(verdicts(res.run_dir).values()))
    assert any("init.tools mismatch" in f for f in v["details"]["assertion_failures"])


def test_auth_failure_halts_without_consuming_attempts(iso_dirs, toy, fake_claude):
    fake_claude.script({"actions": [{"op": "result", "is_error": True, "result": "Not logged in · Please run /login",
                                     "num_turns": 1, "total_cost_usd": 0}]})
    r = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"]}, fake=fake_claude)
    res = r.run()
    assert res.status == "halted" and "authentication" in res.halt_reason
    assert res.outcomes == {"infra_auth": 1}
    cid = next(iter(verdicts(res.run_dir)))
    assert r.counted_attempts(cid) == 0
    # after "re-login" the same run resumes and completes
    fake_claude.script(MCP_SCRIPT | {"actions": [{"op": "write", "path": "answer.json", "content": "{}"}]})
    res2 = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"]}, fake=fake_claude).run()
    assert res2.status == "complete" and res2.cells_scored == 2


def test_cap_halt_and_cap_wait(iso_dirs, toy, fake_claude):
    def cap():
        return {"actions": [{"op": "rate_limit", "status": "rejected", "resetsAt": time.time() + 1},
                            {"op": "result", "is_error": True, "result": "You've hit your limit · resets 5pm",
                             "num_turns": 1, "total_cost_usd": 0}]}

    fake_claude.script(cap())
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"], "instances": ["alpha"]},
                 fake=fake_claude).run()
    assert res.status == "halted" and "usage cap" in res.halt_reason
    fake_claude.script({"sequence": [cap(), {"actions": [{"op": "write", "path": "answer.json", "content": "{}"}]}]})
    fake_claude.log.with_suffix(".jsonl.count").unlink(missing_ok=True)
    (fake_claude.dir / "fake_log.jsonl.count").unlink(missing_ok=True)
    t0 = time.time()
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"], "instances": ["alpha"]},
                 fake=fake_claude, run_id="r2", cap_policy="wait", cap_wait_buffer_s=0.5).run()
    assert res.status == "complete" and res.outcomes == {"infra_cap": 1, "completed": 1}
    assert time.time() - t0 >= 1.0


def test_precheck_leak_blocks_launch(iso_dirs, toy, fake_claude):
    toy.leak_value_into_task = True
    fake_claude.script({})
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"], "reps": 2},
                 fake=fake_claude).run()
    assert fake_claude.calls() == []  # the agent was never started
    assert res.status == "halted" and set(res.outcomes) == {"infra_invalid_regime"}
    v = next(iter(verdicts(res.run_dir).values()))[0]
    assert any("answer-key value" in p for p in v["details"]["sandbox_precheck"])


def test_scorer_exception_is_unscorable_not_fail(iso_dirs, toy, fake_claude):
    fake_claude.script({"actions": [{"op": "write", "path": "answer.json", "content": '{"raise": true}'}]})
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"], "instances": ["alpha"]},
                 fake=fake_claude).run()
    (v,) = verdicts(res.run_dir)["alpha__no-tool__r0"]
    assert v["scored"] is True and v["success"] is None and "score_error" in v["details"]
    assert (res.run_dir / "cells" / "alpha__no-tool__r0" / "attempt-1" / "score_error.txt").exists()


def test_audit_flags_canary_in_deliverable(iso_dirs, toy, fake_claude):
    fake_claude.script({"actions": [{"op": "write", "path": "answer.json",
                                     "content": json.dumps({"y": 1, "note": CANARY_GUID})}]})
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["no-tool"], "instances": ["alpha"]},
                 fake=fake_claude).run()
    (v,) = verdicts(res.run_dir)["alpha__no-tool__r0"]
    assert v["audit"]["flagged"] is True and "canary" in v["audit"]["reasons"]


def test_replay_adapter(iso_dirs, toy, tmp_path):
    root = tmp_path / "fixtures"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "answer.json").write_text(json.dumps({"y": ANSWER_ALPHA, "claimed_success": True}))
    res = runner(toy, iso_dirs, {"adapter": "replay", "model": "dbwm-replay", "arms": ["no-tool"],
                                 "extra": {"replay_dir": str(root)}}, replay_dir=str(root)).run()
    vs = verdicts(res.run_dir)
    assert vs["alpha__no-tool__r0"][0]["success"] is True
    assert [v["outcome"] for v in vs["beta__no-tool__r0"]] == ["crash"] * 3  # no fixture: crash, never "fail"


def test_command_adapter(iso_dirs, toy, tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(
        "import json, os, subprocess, sys\n"
        "assert os.getcwd() == os.environ['DBWM_SANDBOX']\n"
        "prompt = open(os.environ['DBWM_PROMPT_FILE']).read()\n"
        "print(json.dumps({'type': 'assistant'}))\n"
        "print(json.dumps({'type': 'tool_use', 'name': 'run_model', 'input': {'plan': {'x': 10}}}))\n"
        "sys.path.insert(0, %r)\n"
        "from dbwm.core.oracle.client import call\n"
        "y = call(os.environ['DBWM_ORACLE'], os.environ['DBWM_ORACLE_TOKEN'], 'run_model', {'plan': {'x': 10}})['y']\n"
        "open('answer.json', 'w').write(json.dumps({'y': y, 'claimed_success': True, "
        "'tools': os.environ['DBWM_TOOLS'], 'max_turns': os.environ['DBWM_MAX_TURNS']}))\n"
        % str(Path(__file__).resolve().parents[2]))
    cmd = f"{sys.executable} {agent}"
    res = runner(toy, iso_dirs, {"adapter": "command", "model": "my-agent", "arms": ["mcp-oracle"],
                                 "instances": ["alpha"], "extra": {"command": cmd}}).run()
    (v,) = verdicts(res.run_dir)["alpha__mcp-oracle__r0"]
    assert v["outcome"] == "completed" and v["success"] is True, v["details"]
    ans = json.loads((res.run_dir / "cells/alpha__mcp-oracle__r0/attempt-1/deliverables/answer.json").read_text())
    assert ans["tools"] == "read,write,run_model" and ans["max_turns"] == "6"
    assert v["process"]["tool_calls"] == 1 and v["process"]["oracle_calls"] == 1
    # Nothing confines an external command at HOST: the run is recorded as non-conforming.
    m = json.loads((res.run_dir / "manifest.json").read_text())
    assert m["conforming"] is False and any("adapter command at isolation host" in r
                                            for r in m["nonconforming_reasons"])
    assert v["details"]["conforming"] is False


def test_concurrency_runs_every_cell_once(iso_dirs, toy):
    res = runner(toy, iso_dirs, {"adapter": "stub", "model": "dbwm-stub", "reps": 3, "concurrency": 4}).run()
    assert res.status == "complete" and res.cells_scored == 12
    st = RunStore(res.run_dir)
    assert sorted(r["cell_id"] for r in st.attempt_records()) == sorted({r["cell_id"] for r in st.attempt_records()})


def test_preflight_refuses_missing_cli(iso_dirs, toy, tmp_path):
    regime = regime_mod.build_regime(toy, {"model": "haiku", "auth_mode": "none"})
    r = Runner(toy, regime, RunOptions(run_id="x", runs_root=iso_dirs[1], install_signal_handlers=False,
                                       adapter_kwargs={"claude_bin": str(tmp_path / "nope")}, log=lambda m: None))
    with pytest.raises(RunnerError, match="claude --version"):
        r.run()


def test_rescore_and_report_cli(iso_dirs, toy, monkeypatch, capsys):
    from dbwm import cli

    res = runner(toy, iso_dirs, {"adapter": "stub", "model": "dbwm-stub"}).run()
    assert cli.main(["score", "--run", str(res.run_dir), "--ruleset", "paper"]) == 0
    paper = sorted(res.run_dir.glob("cells/*/attempt-1/verdict.paper.json"))
    assert len(paper) == 4
    pv = read_verdict(paper[0])
    assert pv["ruleset"] == "paper" and pv["details"]["rescored_from"]["ruleset"] == "v1"
    assert cli.main(["report", "--run", str(res.run_dir)]) == 0
    rep = json.loads((res.run_dir / "report.json").read_text())
    assert [s["ruleset"] for s in rep["sections"]] == ["paper", "v1"]
    assert "eval_report" in rep
    out = capsys.readouterr().out
    assert "mcp-oracle" in out
    assert "_Eval-specific report skipped: no eval-specific report" in (res.run_dir / "report.md").read_text()
    assert cli.main(["audit", "--run", str(res.run_dir)]) == 0


def test_report_md_includes_eval_section(iso_dirs, toy, monkeypatch):
    """report.md carries the eval's own markdown (evals 3/4 return it as eval_report['markdown'])."""
    import toy_eval

    from dbwm import cli

    res = runner(toy, iso_dirs, {"adapter": "stub", "model": "dbwm-stub"}).run()
    monkeypatch.setattr(toy_eval.ToyEval, "report",
                        lambda self, run_dir: {"markdown": "## Toy eval section\n\n| TOY-CONTRAST | 1 |\n", "n": 1})
    assert cli.main(["report", "--run", str(res.run_dir)]) == 0
    md = (res.run_dir / "report.md").read_text()
    assert md.index("## Ruleset") < md.index("## Toy eval section") and "| TOY-CONTRAST | 1 |" in md
    rep = json.loads((res.run_dir / "report.json").read_text())
    assert rep["eval_report"]["n"] == 1


def test_per_cell_deliverables_hook(iso_dirs, toy, monkeypatch):
    """EVAL.deliverables_for(cell) (eval 5: names differ per scenario) overrides EVAL.deliverables."""
    from dbwm.core.runner import deliverables_for

    def per_cell(cell):
        if cell.instance == "alpha":
            return ["answer.json", f"notes-{cell.instance}.md"]
        return None  # beta: fall back to the eval-wide list

    assert deliverables_for(toy, Cell("toy", "beta", "no-tool", 0)) == ["answer.json"]
    monkeypatch.setattr(toy, "deliverables_for", per_cell, raising=False)
    res = runner(toy, iso_dirs, {"adapter": "stub", "model": "dbwm-stub"}).run()
    assert res.cells_scored == 4
    for cdir in sorted((res.run_dir / "cells").iterdir()):
        d = json.loads((cdir / "attempt-1" / "deliverables.json").read_text())
        if cdir.name.startswith("alpha"):
            assert d["declared"] == ["answer.json", "notes-alpha.md"] and d["missing"] == ["notes-alpha.md"]
        else:
            assert d["declared"] == ["answer.json"] and d["missing"] == []
    monkeypatch.setattr(toy, "deliverables_for", lambda cell: "answer.json", raising=False)
    with pytest.raises(RunnerError, match="sequence of relative paths"):
        deliverables_for(toy, Cell("toy", "beta", "no-tool", 0))


def test_command_timeout_hook_reaches_agent_env(iso_dirs, toy, fake_claude, monkeypatch):
    """EVAL.command_timeout_s(cell) -> BASH_DEFAULT/MAX_TIMEOUT_MS (ms) + background tasks off, for bash arms only."""
    from dbwm.core.runner import command_timeout_for

    monkeypatch.setattr(toy, "command_timeout_s", lambda cell: 120, raising=False)
    assert command_timeout_for(toy, Cell("toy", "beta", "mcp-oracle", 0)) is None  # no bash: no limit
    fake_claude.script({"actions": [
        {"op": "bash", "command": "./toy_model input.json", "stdout_only": True},
        {"op": "write", "path": "answer.json", "json": {"y": "$last.y", "claimed_success": True}}]})
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["bash-oracle", "mcp-oracle"],
                                 "instances": ["beta"], "isolation": "host-unsafe"}, fake=fake_claude).run()
    envs = {c["env"].get("DBWM_ORACLE") is not None: c["env"] for c in fake_claude.calls() if "env" in c}
    bash_env, mcp_env = envs[True], envs[False]
    assert bash_env["BASH_DEFAULT_TIMEOUT_MS"] == bash_env["BASH_MAX_TIMEOUT_MS"] == "120000"
    assert bash_env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert "BASH_MAX_TIMEOUT_MS" not in mcp_env
    m = json.loads((res.run_dir / "manifest.json").read_text())
    assert m["command_timeout_s"] == {"bash-oracle": [120]}
    vs = verdicts(res.run_dir)
    assert vs["beta__bash-oracle__r0"][0]["details"]["command_timeout_s"] == 120
    assert vs["beta__mcp-oracle__r0"][0]["details"]["command_timeout_s"] is None


def test_unverified_cli_version_is_warned_and_recorded(iso_dirs, toy, fake_claude):
    """Review M2: a CLI version outside VERIFIED_CLI_VERSIONS runs, but says so everywhere."""
    fake_claude.script(MCP_SCRIPT)  # the test double reports "2.1.287-fake ...": not a verified version
    logs: list[str] = []
    _, runs = iso_dirs
    regime = regime_mod.build_regime(toy, {"model": "haiku", "auth_mode": "none", "adapter": "claude_code",
                                           "arms": ["mcp-oracle"], "instances": ["alpha"]})
    o = RunOptions(run_id="r-cli", runs_root=runs, install_signal_handlers=False,
                   adapter_kwargs={"claude_bin": str(fake_claude.bin)}, log=logs.append)
    res = Runner(toy, regime, o).run()
    m = json.loads((res.run_dir / "manifest.json").read_text())
    assert m["cli_version"].startswith("2.1.287-fake") and m["cli_version_verified"] is False
    (v,) = verdicts(res.run_dir)["alpha__mcp-oracle__r0"]
    assert v["details"]["cli_version_verified"] is False
    assert sum("is not a verified version" in line for line in logs) == 1


def test_stub_adapter_has_no_cli_verification(iso_dirs, toy):
    res = runner(toy, iso_dirs, {"adapter": "stub", "model": "dbwm-stub"}).run()
    m = json.loads((res.run_dir / "manifest.json").read_text())
    assert m["cli_version_verified"] is None
    for vs in verdicts(res.run_dir).values():
        assert "cli_version_verified" not in vs[0]["details"]


def test_oracle_token_is_redacted_after_the_attempt(iso_dirs, toy, fake_claude, tmp_path):
    """Review m8: the per-trial token must not stay in runs/ (mcp.json keeps a redacted record)."""
    from dbwm.core import sandbox as sandbox_mod

    fake_claude.script(MCP_SCRIPT)
    res = runner(toy, iso_dirs, {"adapter": "claude_code", "arms": ["mcp-oracle"], "instances": ["alpha"]},
                 fake=fake_claude).run()
    adir = res.run_dir / "cells" / "alpha__mcp-oracle__r0" / "attempt-1"
    cfg = json.loads((adir / "mcp.json").read_text())
    assert cfg["mcpServers"]["dbwm"]["env"]["DBWM_ORACLE_TOKEN"] == sandbox_mod.REDACTED
    (v,) = verdicts(res.run_dir)["alpha__mcp-oracle__r0"]
    assert v["process"]["oracle_calls"] == 1  # the call went through before the redaction
    # unit: every known token file, nothing else touched
    (tmp_path / "tools").mkdir()
    for rel in sandbox_mod.TOKEN_FILES + ("transcript.jsonl",):
        (tmp_path / rel).write_text('{"t": "SECRET-TOKEN-123456"}')
    assert sorted(sandbox_mod.redact_token_files(tmp_path, "SECRET-TOKEN-123456")) == sorted(sandbox_mod.TOKEN_FILES)
    assert all("SECRET" not in (tmp_path / rel).read_text() for rel in sandbox_mod.TOKEN_FILES)
    assert "SECRET-TOKEN-123456" in (tmp_path / "transcript.jsonl").read_text()
    assert sandbox_mod.redact_token_files(tmp_path, None) == []


def test_run_level_usd_rail_stops_new_attempts(iso_dirs, toy, fake_claude):
    """Review m4: --max-total-usd stops launching once the run's attempts cost that much (also on resume)."""
    fake_claude.script({"cost": 0.6, "actions": [
        {"op": "write", "path": "answer.json", "json": {"y": 1, "claimed_success": True}}]})
    ov = {"adapter": "claude_code", "arms": ["no-tool"], "instances": ["alpha"], "reps": 4}
    res = runner(toy, iso_dirs, ov, fake=fake_claude, run_id="usd", max_total_usd=1.0).run()
    assert res.status == "halted" and res.halt_reason.startswith("run-level USD rail")
    assert len(fake_claude.calls()) == 2 and res.cells_scored == 2
    m = json.loads((res.run_dir / "manifest.json").read_text())
    assert m["max_total_usd"] == 1.0 and m["spent_usd"] == pytest.approx(1.2)
    again = runner(toy, iso_dirs, ov, fake=fake_claude, run_id="usd", max_total_usd=1.0).run()
    assert again.status == "halted" and len(fake_claude.calls()) == 2  # nothing new launched


def test_auth_mode_hosts_join_the_egress_allowlist(iso_dirs, toy, monkeypatch):
    """Review m7: Bedrock/Vertex endpoints are pre-populated (reasoned, not verified live)."""
    from dbwm.core.egress_proxy import auth_mode_hosts

    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    r = runner(toy, iso_dirs, {"adapter": "stub", "model": "dbwm-stub", "auth_mode": "bedrock"})
    assert r.egress_allow[:3] == ["api.anthropic.com", "bedrock-runtime.eu-west-1.amazonaws.com",
                                  "bedrock.eu-west-1.amazonaws.com"]
    assert auth_mode_hosts("vertex", {"CLOUD_ML_REGION": "global"}) == ["aiplatform.googleapis.com",
                                                                        "oauth2.googleapis.com"]
    assert auth_mode_hosts("vertex", {"CLOUD_ML_REGION": "us-east5",
                                      "ANTHROPIC_VERTEX_BASE_URL": "https://gw.example.com:8443/v1"}) == [
        "us-east5-aiplatform.googleapis.com", "oauth2.googleapis.com", "gw.example.com:8443"]
    assert auth_mode_hosts("bedrock", {}) == [] and auth_mode_hosts("api_key", {"AWS_REGION": "x"}) == []
    assert auth_mode_hosts("oauth_token", {}) == []
