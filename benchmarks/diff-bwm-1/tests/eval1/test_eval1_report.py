"""Eval-1 run report: paper aggregation over dbwm verdicts, both denominators, neutral captions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dbwm.core.types import RULESET_V1, Cell, Outcome
from dbwm.eval1.arms import ARM_ORDER
from dbwm.eval1.report import build_eval1_report

INSTANCES = ("sprout-crm__hard", "voltcore-devices__hostile")


@pytest.fixture
def run_dir(ev, oracle_for, tmp_path):
    """A synthetic run: stub deliverables scored and written as dbwm.verdict/1 files.

    One designed cell is left without any attempt (missing) and one is an infra
    failure (unscored), so the designed denominator differs from the scored one.
    """
    verdict_mod = pytest.importorskip("dbwm.core.verdict")
    rd = tmp_path / "run"
    designed = [Cell("eval1", i, a, 0) for i in INSTANCES for a in ARM_ORDER]
    missing = designed[0]  # sprout-crm__hard__ungrounded__r0
    infra = designed[1]  # sprout-crm__hard__grounded-readonly__r0
    for cell in designed:
        if cell == missing:
            continue
        adir = rd / "cells" / cell.cell_id / "attempt-1"
        sb = tmp_path / "sb" / cell.cell_id
        sb.mkdir(parents=True)
        (sb / "TASK.md").write_text(ev.prompt(cell), encoding="utf-8")
        log: list = []
        if cell == infra:
            v = verdict_mod.build_verdict(cell=cell, attempt=1, outcome=Outcome.INFRA_API, score=None,
                                          ruleset=RULESET_V1, regime_hash="a" * 64, engine_hash="b" * 64)
        else:
            ev.stub_agent(cell, sb, oracle_for(ev, cell, sb, log))
            score = ev.score(sb, cell, RULESET_V1)
            v = verdict_mod.build_verdict(cell=cell, attempt=1, outcome=Outcome.COMPLETED, score=score,
                                          regime_hash="a" * 64, engine_hash="b" * 64,
                                          oracle_calls=len(log), oracle_override_attempts=0)
        adir.mkdir(parents=True)
        (adir / "verdict.json").write_text(json.dumps(v), encoding="utf-8")
    manifest = {"run_id": "synthetic", "adapter": "stub", "isolation": "host", "conforming": True,
                "regime": {"reps": 1, "model": "stub"},
                "cells": [{"instance": c.instance, "arm": c.arm, "rep": c.rep, "cell_id": c.cell_id}
                          for c in designed]}
    (rd / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return rd


def test_report_both_denominators_and_html(run_dir):
    rep = build_eval1_report(run_dir)
    assert rep["load_errors"] == []
    sec = rep["rulesets"]["v1"]
    den = sec["designed_denominators"]
    assert set(den) == set(ARM_ORDER)
    assert den["ungrounded"]["designed"] == 2 and den["ungrounded"]["scored"] == 1
    assert den["ungrounded"]["unscored_or_missing"] == 1
    assert den["grounded-readonly"]["scored"] == 1 and den["grounded-readonly"]["unscored_or_missing"] == 1
    for a in den.values():
        if a["scored"]:
            assert a["valid_rate_designed"] <= a["valid_rate_scored"]
    assert sec["decision_rule_verdict"] in ("PROVED", "PARTIAL", "DISPROVED")
    assert sec["paper_summary"]["n"] == 10
    html = (run_dir / sec["html"]).read_text(encoding="utf-8")
    assert "STUB PLANNER" in html  # data-source banner from the manifest's adapter
    assert "Violations fall monotonically" not in html and "prose-only planning collapses" not in html
    reasons = {e["cell_id"]: e["reasons"] for e in sec["excluded_or_missing"]}
    assert reasons["sprout-crm__hard__ungrounded__r0"] == ["missing:no attempt"]
    assert reasons["sprout-crm__hard__grounded-readonly__r0"][0].startswith("unscored:")


def test_report_mechanism_from_oracle_log(run_dir):
    sec = build_eval1_report(run_dir, write_html=False)["rulesets"]["v1"]
    by = sec["paper_summary"]["by_condition"]
    assert by["ungrounded"]["queried_model_rate"] == 0
    assert by["grounded-iterate"]["queried_model_rate"] == 1


def test_report_empty_run(tmp_path):
    assert build_eval1_report(tmp_path)["rulesets"] == {}
