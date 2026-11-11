"""EVAL.report over a synthetic run directory built with the core verdict builder."""

from __future__ import annotations

import json

from dbwm.core import verdict as core_verdict
from dbwm.core.types import Outcome
from dbwm.eval5 import EVAL
from dbwm.eval5 import dataset as D
from dbwm.eval5 import stubs
from dbwm.eval5.arms import ARM_ORDER

from .conftest import cell, direct_caller


def _run_dir(tmp_path):
    run = tmp_path / "run"
    cells = []
    for sid in D.SCENARIOS[:2]:
        for arm in ARM_ORDER:
            c = cell(sid, arm)
            cells.append({"instance": sid, "arm": arm, "rep": 0, "cell_id": c.cell_id})
            sb = tmp_path / "sb" / c.cell_id
            sb.mkdir(parents=True)
            EVAL.stub_agent(c, sb, direct_caller(sid, arm, sb))
            for rs in ("paper", "as-run", "v1"):
                adir = run / "cells" / c.cell_id / "attempt-1"
                adir.mkdir(parents=True, exist_ok=True)
                v = core_verdict.build_verdict(cell=c, attempt=1, outcome=Outcome.COMPLETED,
                                               score=EVAL.score(sb, c, rs), regime_hash="0" * 64,
                                               engine_hash="1" * 64, model_requested="stub")
                name = "verdict.json" if rs == "v1" else f"verdict.{rs}.json"
                core_verdict.write_verdict(adir / name, v)
    (run / "manifest.json").write_text(json.dumps({"run_id": "t", "cells": cells}))
    return run


def test_report_on_synthetic_run(tmp_path):
    run = _run_dir(tmp_path)
    rep = EVAL.report(run)
    secs = {s["ruleset"]: s for s in rep["eval5"]}
    assert set(secs) == {"paper", "as-run", "v1"}
    v1 = secs["v1"]["arms"]
    assert v1["baseline"]["all_items"]["k"] == 0 and v1["checklist-executable"]["all_items"]["k"] == 2
    assert v1["baseline"]["p_believes_given_fail"] == {"k": 2, "n": 2, "rate": 1.0,
                                                       "ci95": v1["baseline"]["p_believes_given_fail"]["ci95"]}
    labels = {(c["label"], c["denominator"]) for c in secs["v1"]["contrasts"]}
    assert len(labels) == 6
    assert (run / "eval5_report.md").read_text().startswith("# Eval 5")
    assert "answer-disclosure contrast" in (run / "eval5_report.md").read_text()
