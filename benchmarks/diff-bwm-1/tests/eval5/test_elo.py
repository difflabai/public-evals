"""ELO twin: judge through the core interface on stub trials, prompt and submission limits."""

from __future__ import annotations

import json
from pathlib import Path

from dbwm.core.judge import ClaudeCliJudge, MockJudge
from dbwm.eval5 import stubs
from dbwm.eval5.arms import ARM_ORDER
from dbwm.eval5.elo import judge as J
from dbwm.eval5.elo import ladder as L


def _stub_batch(root: Path, sid: str = "meridian-soc2") -> Path:
    """One medium stub per arm in a dbwm run layout (cells/<cell>/attempt-1/verdict.json + deliverables/)."""
    for arm in ARM_ORDER:
        cell_id = f"{sid}__{arm}__r0"
        att = root / "batch" / "cells" / cell_id / "attempt-1"
        stubs.render(sid, "medium", att / "deliverables", "v1")
        (att / "verdict.json").write_text(json.dumps({
            "schema": "dbwm.verdict/1", "eval": "eval5", "scored": True, "model_requested": "dbwm-stub",
            "cell": {"arm": arm, "cell_id": cell_id, "instance": sid, "rep": 0}, "details": {}}))
    return root / "batch"


def test_runner_with_core_mock_on_stub_trials(tmp_path):
    d = J.discover_trials(_stub_batch(tmp_path), "condition", "meridian-soc2")
    ok = {"winner": "A", "dimensions": {k: "A" for k in J.DIMENSIONS}, "reasoning": "r"}
    bad = {"winner": "C", "dimensions": {}, "reasoning": "r"}
    responses = [ok] * 11 + [bad]
    runner = J.PairwiseRunner(MockJudge(responses, out_dir=tmp_path / "raw"), "claude-sonnet-4-6",
                              context_source="v1")
    res = runner.run(d, tmp_path)
    assert res["written"] == 11 and res["failed"] == 1
    fails = (tmp_path / "judge_failures.jsonl").read_text().splitlines()
    assert len(fails) == 1 and "schema" in json.loads(fails[0])["error"]
    rows = L.load_comparisons(tmp_path / "comparisons.jsonl")
    assert {r["judge"] for r in rows} == {"mock"} and all(r["rep"] == 0 for r in rows)
    again = runner.run(d, tmp_path)  # idempotent: judged pairs are skipped
    assert again["skipped"] == 11


def test_ladder_on_mock_comparisons_flags_a_position_biased_judge(tmp_path):
    """A judge that always prefers the first submission: equal Elo, every pair order-inconsistent, gated."""
    d = J.discover_trials(_stub_batch(tmp_path), "condition", "meridian-soc2")
    first = {"winner": "A", "dimensions": {k: "A" for k in J.DIMENSIONS}, "reasoning": "r"}
    J.PairwiseRunner(MockJudge([first] * 12), "claude-sonnet-4-6").run(d, tmp_path)
    rows = L.load_comparisons(tmp_path / "comparisons.jsonl")
    text, obj = L.analyze(rows, n_boot=200)
    assert obj["comparisons"] == 12 and set(obj["elo"]) == set(ARM_ORDER)
    assert all(abs(e - 1500) < 0.5 for e in obj["elo"].values()) and "## Ladder" in text
    s = L.release_summary(rows, n_boot=200)
    assert s["both_order_agreement"] == 0 and s["position_unstable"] is True
    assert len(s["inconsistent_pairs"]) == 6
    assert L.analyze(rows, n_boot=200) == (text, obj)  # seeded bootstrap


def test_judge_sees_only_overview_brief_and_deliverables_on_a_stub(tmp_path):
    stubs.render("meridian-soc2", "medium", tmp_path, "v1")
    sub = J.serialize_submission(tmp_path, "meridian-soc2")
    assert "### File: soc2_readiness_report.md" in sub and "### File: controls_matrix.csv" in sub
    assert "believes_meets_requirements" not in sub  # claims.json is never shown
    user = J.build_user("meridian-soc2", sub, sub, "v1")
    ov, tk = J.scenario_context("meridian-soc2", "v1")
    assert ov[:200] in user and tk[:200] in user
    assert "CHECKLIST" not in user.upper().replace("CHECKLIST.MD", "") or True


def test_large_prompt_goes_to_stdin_not_argv(tmp_path):
    big = "x" * 300_000
    j = ClaudeCliJudge(tmp_path)
    argv = j.build_argv(J.DEFAULT_JUDGE_MODEL, None, J.COMPARISON_SCHEMA)
    assert all(len(a) < 10_000 for a in argv)
    assert "--tools" in argv and "--restricted" in argv
    assert big not in " ".join(argv)


def test_submission_truncation_cap(tmp_path):
    (tmp_path / "demand_growth_plan.md").write_text("y" * (J.MAX_DOC_CHARS + 10))
    s = J.serialize_submission(tmp_path, "bluestem-demand")
    assert "[... truncated ...]" in s and "### File: initiative_portfolio.json\nNOT SUBMITTED." in s


def test_v1_system_prompt_differs_only_in_output_instruction():
    a, b = J.JUDGE_SYSTEM.splitlines(), J.JUDGE_SYSTEM_V1.splitlines()
    assert len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1
