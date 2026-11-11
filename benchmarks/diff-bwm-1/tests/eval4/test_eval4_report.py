"""EVAL.report(run_dir) for a new run: judged primary (ITT, designed-missing
denominators), contrasts, judge-referenced calibration, and the markdown."""

from __future__ import annotations

import json
import shutil

from ._support import FAKE_MATERIALS, cell
from .test_eval4_judge import make_run

from dbwm.core.judge import MockJudge
from dbwm.eval4 import EVAL, judge as J


def test_report_before_and_after_judging(tmp_path):
    run = make_run(tmp_path, reps=2, no_workbook_cell=cell("checklist-prose", 1).cell_id)
    r0 = EVAL.report(run)
    assert isinstance(r0["primary_judged_item_pass"], str) and "eval4-judge" in r0["primary_judged_item_pass"]
    assert r0["per_arm"]["checklist-executable"]["checker_all12"] == 2
    assert r0["per_arm"]["checklist-executable"]["qa_check_calls"]["mean"] == 2
    mj = MockJudge(lambda s, u, sc, m: {"passed": "W1-T2-A-07" not in u, "reasoning": "mock"})
    J.judge_run(run, mj, model="mock-judge", passes=3, materials=FAKE_MATERIALS)
    r = EVAL.report(run)
    pj = r["primary_judged_item_pass"]
    assert pj["checklist-executable"]["scored_itt"]["k"] == 22 and pj["checklist-executable"]["scored_itt"]["n"] == 24
    # the no-workbook prose cell fails every item (ITT)
    assert pj["checklist-prose"]["scored_itt"]["k"] == 11 and pj["checklist-prose"]["scored_itt"]["n"] == 24
    c = r["contrasts"]["checklist-executable - checklist-prose"]
    assert c["hypothesis"] == "H1" and abs(c["point"] - (22 / 24 - 11 / 24)) < 1e-12
    assert r["per_arm"]["checklist-executable"]["coarse_overclaim_vs_judge"] == [2, 2]
    assert "| checklist-executable | 22/24 = 92%" in r["markdown"]
    assert json.dumps(r)  # serializable


def test_designed_missing_cells_fail_in_second_denominator(tmp_path):
    run = make_run(tmp_path, reps=2)
    mj = MockJudge(lambda s, u, sc, m: {"passed": True, "reasoning": "mock"})
    J.judge_run(run, mj, model="mock-judge", passes=1, materials=FAKE_MATERIALS)
    shutil.rmtree(run / "cells" / cell("baseline", 1).cell_id)  # a designed cell never ran
    r = EVAL.report(run)
    b = r["primary_judged_item_pass"]["baseline"]
    assert b["scored_itt"]["k"] == 12 and b["scored_itt"]["n"] == 12
    assert b["designed_missing_fail"]["k"] == 12 and b["designed_missing_fail"]["n"] == 24
    assert r["per_arm"]["baseline"]["missing_or_unscored"] == 1
