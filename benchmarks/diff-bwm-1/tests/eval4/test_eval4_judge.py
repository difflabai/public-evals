"""The judge step: serialization text and truncation, prompt filling,
judge_error semantics (never a fail), panel majority, and judge_run over a
synthetic run directory with a mock judge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from ._support import FAKE_MATERIALS, ARMS, HAVE_FORMULAS, RECALC_UNUSABLE, cell, needs_private

from dbwm.core import verdict as core_verdict
from dbwm.core.judge import JudgeResult, MockJudge
from dbwm.core.types import Outcome
from dbwm.eval4 import EVAL, judge as J, stub


# sha256 of serialize_workbook_text() (the paper judge's serialize_workbook(), judge.py
# L46-93) on stub workbooks under openpyxl 3.1.5 + formulas 1.3.4; the port matched the
# paper judge's digests before these were captured. name -> (defects, cached, sha256).
SERIALIZATION_SHA256 = {
    "clean": ((), True, "97683444bfd7413dc5a08f58164783ac5bba249f6827959e2e63aaaca47a3060"),
    "all-defects": (stub.DEFECT_ORDER, True, "5175f57b8b5a95388062bb2eba508051b36c0c76f8554c87d686b80035744b32"),
    "clean-uncached": ((), False, "35b2a58a12218483571c5a7acf3cabadf4562b5bed78ae3a0992287620a26974"),
    "all-defects-uncached": (stub.DEFECT_ORDER, False,
                             "695da81e28c9ef6b20a074a12a4bf10bd0c24582ebcf50e0fc7436868462f24c"),
}


@pytest.mark.parametrize("name", sorted(SERIALIZATION_SHA256))
def test_serialization_identical_to_paper(name, tmp_path):
    defects, cached, sha = SERIALIZATION_SHA256[name]
    if not cached and not HAVE_FORMULAS:
        pytest.skip(f"needs formula recalculation: {RECALC_UNUSABLE}")
    stub.build_workbook(tmp_path / "m.xlsx", set(defects), cached=cached)
    assert hashlib.sha256(J.serialize_workbook_text(tmp_path / "m.xlsx").encode()).hexdigest() == sha


def test_serialization_truncates_at_160k(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "MAX_ARTIFACT_CHARS", 500)
    stub.build_workbook(tmp_path / "m.xlsx", set())
    t = J.serialize_workbook_text(tmp_path / "m.xlsx")
    assert t.endswith("\n[... artifact truncated ...]") and len(t) == 500 + len("\n[... artifact truncated ...]")


def test_prompt_roles_v1_and_paper():
    c = FAKE_MATERIALS["checks"][0]
    s, u = J.build_messages("SYS", FAKE_MATERIALS["user_tpl"], "TASK", c, "ART", "v1")
    assert s == "SYS" and "<task>TASK</task>" in u and "<a>ART</a>" in u and "OUTPUT" not in u
    s2, u2 = J.build_messages("SYS", FAKE_MATERIALS["user_tpl"], "TASK", c, "ART", "paper")
    assert s2 is None and u2.startswith("SYS\n\n") and u2.endswith(J.PAPER_OUTPUT_INSTRUCTION)


def test_paper_regex_parse_and_its_brace_defect():
    assert J.parse_paper_judge_text('blah {"passed": true, "reasoning": "ok"}') == {"passed": True,
                                                                                     "reasoning": "ok"}
    # the published regex cannot match an object whose reasoning contains braces
    assert J.parse_paper_judge_text('{"passed": false, "reasoning": "no {legend} tab"}') is None


class _Seq:
    """A JudgeAdapter returning queued JudgeResults (and counting calls)."""
    name = "seq"

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def judge(self, system, user, schema, model, *, label=None):
        self.calls += 1
        return self.results.pop(0)


def _jr(parsed=None, error=None, tools=0):
    return JudgeResult(parsed=parsed, raw_path=None, tool_use_count=tools, cost_usd=0.01, turns=1, error=error)


def test_judge_one_retries_then_succeeds():
    a = _Seq([_jr(error="API Error: 500"), _jr(parsed={"passed": False, "reasoning": "x"})])
    r = J.judge_one(a, "s", "u", "m", retries=2)
    assert r["passed"] is False and r["attempts"] == 2 and r["judge_error"] is None


def test_tool_use_is_judge_error_never_a_fail():
    a = _Seq([_jr(parsed={"passed": False, "reasoning": "x"}, tools=1)] * 3)
    r = J.judge_one(a, "s", "u", "m", retries=2)
    assert r["passed"] is None and r["error_class"] == "isolation" and a.calls == 3


def test_unparseable_is_judge_error():
    a = _Seq([_jr(parsed={"verdict": "yes"})])
    r = J.judge_one(a, "s", "u", "m", retries=0)
    assert r["passed"] is None and r["error_class"] == "parse"


def test_adapter_exception_is_infra_error():
    class Boom:
        name = "boom"

        def judge(self, *a, **k):
            raise OSError("no CLI")
    r = J.judge_one(Boom(), "s", "u", "m", retries=1)
    assert r["passed"] is None and r["error_class"] == "infra" and r["attempts"] == 2


def test_panel_majority_and_verdict():
    assert J.panel_majority([True, True, False]) is True
    assert J.panel_majority([True, False]) is None          # tie -> judge_error
    assert J.panel_majority([True, None, None]) is None     # fewer than 2 valid votes
    assert J.panel_majority([False, None, False]) is False
    ids = [c["check_id"] for c in FAKE_MATERIALS["checks"]]

    def mk(votes):
        return {"checks": [{"check_id": i, "passed": v} for i, v in zip(ids, votes, strict=True)]}
    passes = [mk([True] * 11 + [None]), mk([True] * 11 + [None]), mk([False] + [True] * 11)]
    pv = J.panel_verdict(passes, {"believes_meets_requirements": True,
                                  "checklist_self_assessment": {"K-SCEN": True, "K-INTEG": True}}, ids)
    assert pv["items"][ids[0]] is True and pv["items"][ids[-1]] is None
    assert pv["item_pass"] == 11 and pv["item_valid"] == 11 and pv["judge_error_items"] == 1
    assert pv["coarse_overclaim_vs_judge"] is False  # no decided item failed
    # K-INTEG maps to A-07 and is excluded; K-SCEN maps to A-01 (pass) and A-09 (pass)
    assert pv["per_item_overclaim_vs_judge_excl_A07"] == {"claimed_true": 1, "overclaimed": 0, "overclaimed_ids": []}
    nw = J.panel_verdict([J.no_workbook_pass("m", p) for p in range(3)], None, ids)
    assert nw["no_workbook"] and nw["item_pass"] == 0 and nw["item_valid"] == 12


def test_judge_workbook_with_mock_materials(tmp_path):
    stub.build_workbook(tmp_path / "m.xlsx", set())
    mj = MockJudge(lambda s, u, sc, m: {"passed": "A-07" not in u, "reasoning": "mock"})
    r = J.judge_workbook(tmp_path / "m.xlsx", mj, "mock-model", 0, retries=0, materials=FAKE_MATERIALS)
    assert r["total"] == 12 and r["passed"] == 11 and r["errors"] == 0 and r["schema"] == J.SCHEMA_JUDGED
    assert [c["check_id"] for c in r["checks"]] == [c["check_id"] for c in FAKE_MATERIALS["checks"]]


@needs_private
def test_judge_materials_from_dataset():
    m = J.judge_materials()
    assert len(m["checks"]) == 12 and m["checks"][0]["check_id"] == "W1-T2-A-01"
    assert "{task_markdown}" in m["user_tpl"] and "<<<SUBMISSION CONTENT MESSAGES>>>" in m["user_tpl"]


# ---------------------------------------------------------------------------
# judge_run over a synthetic run directory
# ---------------------------------------------------------------------------

def make_run(root: Path, reps: int = 2, no_workbook_cell: str | None = None, excluded_cell: str | None = None) -> Path:
    """A run directory in the core layout: manifest.json + cells/<cid>/attempt-1/
    {verdict.json, deliverables/} built with the stub agent and core build_verdict."""
    from dbwm.eval4.selftest import run_stub_cell

    run = root / "run"
    (run / "cells").mkdir(parents=True)
    regime = {**EVAL.default_regime(), "model": "stub-model", "reps": reps}
    (run / "manifest.json").write_text(json.dumps({"eval": "eval4", "regime": regime}))
    for arm in ARMS:
        for rep in range(reps):
            c = cell(arm, rep)
            adir = run / "cells" / c.cell_id / "attempt-1"
            adir.mkdir(parents=True)
            v1, _paper, state = run_stub_cell(EVAL, c, adir / "_work")
            (adir / "_work" / "deliverables").rename(adir / "deliverables")
            if c.cell_id == no_workbook_cell:
                (adir / "deliverables" / "market_model.xlsx").unlink()
                v1 = EVAL.score(adir / "deliverables", c, "v1")
            audit = {"flagged": True, "reasons": ["canary"]} if c.cell_id == excluded_cell else {}
            v = core_verdict.build_verdict(cell=c, attempt=1, outcome=Outcome.COMPLETED, score=v1,
                                           regime_hash="a" * 64, engine_hash="e" * 64,
                                           oracle_calls=(state.get("qa_check") or {}).get("calls", 0),
                                           audit=audit)
            core_verdict.write_verdict(adir / "verdict.json", v)
    return run


def test_judge_run_with_mock_judge(tmp_path):
    nowb = cell("checklist-prose", 1).cell_id
    excl = cell("baseline", 1).cell_id
    run = make_run(tmp_path, reps=2, no_workbook_cell=nowb, excluded_cell=excl)
    calls = []

    def fn(system, user, schema, model):
        calls.append(model)
        return {"passed": "W1-T2-A-07" not in user, "reasoning": "mock"}
    mj = MockJudge(fn, out_dir=tmp_path / "raw")
    s = J.judge_run(run, mj, model="mock-judge", passes=3, materials=FAKE_MATERIALS)
    judged_cells = len(ARMS) * 2 - 2  # one excluded, one without a workbook (not sent to the judge)
    assert len(calls) == judged_cells * 3 * 12 and set(calls) == {"mock-judge"}
    assert excl in s["not_judged"] and excl not in s["cells"]
    assert s["cells"][nowb]["no_workbook"] is True and s["cells"][nowb]["item_pass"] == 0
    some = cell("checklist-executable", 0).cell_id
    assert s["cells"][some]["item_pass"] == 11 and s["cells"][some]["coarse_overclaim_vs_judge"] is True
    jd = run / "cells" / some / "attempt-1" / "judge"
    assert sorted(p.name for p in jd.iterdir()) == ["judged_r0.json", "judged_r1.json", "judged_r2.json",
                                                   "panel.json"]
    assert json.loads((run / "eval4_judge_summary.json").read_text())["model"] == "mock-judge"
    # re-entrant: nothing is re-judged; --force re-judges
    J.judge_run(run, mj, model="mock-judge", passes=3, materials=FAKE_MATERIALS)
    assert len(calls) == judged_cells * 36
    J.judge_run(run, mj, model="mock-judge", passes=3, materials=FAKE_MATERIALS, force=True)
    assert len(calls) == judged_cells * 72
    # a different judge model is a different panel: everything is judged again
    J.judge_run(run, mj, model="other-judge", passes=1, materials=FAKE_MATERIALS)
    assert len(calls) == judged_cells * 72 + judged_cells * 12


def test_judge_run_concurrency_same_result(tmp_path):
    run = make_run(tmp_path, reps=1)
    def fn(s, u, sc, m):
        return {"passed": hashlib.sha256(u.encode()).digest()[0] % 2 == 0, "reasoning": "mock"}
    mj = MockJudge(fn)
    a = J.judge_run(run, mj, model="m", passes=2, materials=FAKE_MATERIALS, concurrency=4, force=True)
    b = J.judge_run(run, mj, model="m", passes=2, materials=FAKE_MATERIALS, concurrency=1, force=True)
    assert a["cells"] == b["cells"]


def test_judge_run_refuses_without_recalc(tmp_path, monkeypatch):
    import sys

    run = make_run(tmp_path, reps=1)
    some = run / "cells" / cell("checklist-prose", 0).cell_id / "attempt-1" / "deliverables" / "market_model.xlsx"
    stub.build_workbook(some, set(), cached=False)
    monkeypatch.setitem(sys.modules, "formulas", None)
    mj = MockJudge(lambda *a: {"passed": True, "reasoning": "x"})
    with pytest.raises(J.RecalcUnavailable):
        J.judge_run(run, mj, model="m", passes=1, materials=FAKE_MATERIALS)
    assert not (run / "eval4_judge_summary.json").exists()


def test_eval4_judge_cli_registered():
    import dbwm.eval4 as pkg

    assert set(pkg.EXTRA_COMMANDS) == {"fetch", "eval4-judge"}
