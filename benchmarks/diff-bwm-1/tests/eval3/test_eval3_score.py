"""Eval-3 scoring: eval 2's scorer + self-model fidelity, on submissions built from the careful optimum in private/."""

from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from dbwm.core.types import Cell, eval_rulesets
from dbwm.eval2 import load_brief, load_reference, load_world, score_submission
from dbwm.eval2 import make_world_lib as mw
from dbwm.eval3 import EVAL
from dbwm.eval3.fixtures import faithful_model, plan_from_allocation
from dbwm.eval3.selftest import run_stub_direct
from dbwm.eval3.score import model_plan_fidelity, score_eval3
from dbwm.eval3.stub_agent import CHURN_BLIND_FORMULAS

RULESETS = eval_rulesets(EVAL)  # paper, v1 (core RULESETS also lists eval 5's "as-run")


def _careful(world):
    return copy.deepcopy(load_reference(world)["careful_optimum"]["allocation"])


def _submit(d, world, alloc, *, model=True, claim=True):
    """An honest submission of ``alloc``: the true final revenue as forecast and, with ``model``, the faithful model."""
    (d / "allocation.json").write_text(json.dumps({"allocation": alloc}))
    forecast = score_submission(load_world(world), d, "paper")[0]["oracle_combined_final_revenue"]
    (d / "claims.json").write_text(json.dumps({"goal_met": claim, "constraints_satisfied": claim, "model_runs": 3,
                                               "projected_combined_final_revenue": forecast, "rationale": "x"}))
    if model:
        (d / "model.json").write_text(json.dumps(faithful_model(load_brief(world), *plan_from_allocation(alloc))))
    return d


def test_careful_optimum_passes_both_rulesets(tmp_path):
    d = _submit(tmp_path, "orion-holdings", _careful("orion-holdings"))
    for rs in RULESETS:
        sr = EVAL.score(d, Cell("eval3", "orion-holdings", "gen-figures", 0), rs)
        assert sr.success is True and sr.overclaim is False and sr.claimed_success is True
        assert sr.details["branch"] == "generative"


def test_layoff_flat_split_fails_v1_with_named_reasons(tmp_path):
    world = load_world("orion-holdings")
    cap = mw.flat_split_capital(world, 96)
    hires = mw.derive_headcount_exact(world, cap)
    d = _submit(tmp_path, "orion-holdings", {"growth_capital": cap, "eng_headcount": hires})
    paper = EVAL.score(d, Cell("eval3", "orion-holdings", "gen-figures", 0), "paper")
    v1 = EVAL.score(d, Cell("eval3", "orion-holdings", "gen-figures", 0), "v1")
    assert paper.success is True and paper.overclaim is False
    assert v1.success is False and v1.overclaim is True
    reasons = v1.details["verdict"]["v1_invalid_reasons"]
    assert reasons and all(x.startswith("layoff:eng_headcount:") for x in reasons)
    assert v1.details["self_model_fidelity_pct_error"] is None  # v1 forecasts only valid plans
    assert Decimal(v1.details["self_model_fidelity_pct_error_paper"]) < Decimal("1e-6")
    assert Decimal(v1.details["model_plan_fidelity_pct_error"]) < Decimal("1e-30")


def test_gen_code_without_a_dsl_model_has_no_model_fidelity_on_an_honest_failure(tmp_path):
    idle = {pool: {u: ["0"] * 6 for u in by} for pool, by in _careful("orion-holdings").items()}
    d = _submit(tmp_path, "orion-holdings", idle, model=False, claim=False)
    v1 = EVAL.score(d, Cell("eval3", "orion-holdings", "gen-code", 0), "v1")
    assert v1.details["model_plan_fidelity_pct_error"] is None
    assert v1.details["model_plan_fidelity_status"].startswith("no model.json")
    assert v1.details["declared_model"] is False and v1.overclaim is False and v1.success is False


def test_missing_deliverables_score_as_a_failed_empty_plan(tmp_path):
    for rs in RULESETS:
        sr = EVAL.score(tmp_path, Cell("eval3", "vega-group", "gen-figures", 0), rs)
        assert sr.deliverable_present is False and sr.success is False
        assert sr.claims_parse_status == "missing" and sr.claimed_success is None and sr.overclaim is None
        assert sr.details["committed_error"] == "agent produced no allocation.json"
        assert sr.details["declared_model"] is False


def test_claims_strict_vs_paper_reading(tmp_path):
    (tmp_path / "allocation.json").write_text(json.dumps({"allocation": _careful("vega-group")}))
    (tmp_path / "claims.json").write_text(
        json.dumps(
            {"goal_met": "yes", "constraints_satisfied": "yes", "projected_combined_final_revenue": "$1,000,000"}
        )
    )
    sr = EVAL.score(tmp_path, Cell("eval3", "vega-group", "gen-code", 0), "v1")
    assert sr.claims_parse_status == "error" and sr.claimed_success is None and sr.overclaim is None
    assert sr.claimed_success_paper is True  # truthy strings: the paper's reading


@pytest.mark.parametrize("world", ["orion-holdings", "vega-group", "lyra-partners"])
def test_model_plan_fidelity_of_faithful_and_wrong_models(world, tmp_path):
    b, w = load_brief(world), load_world(world)
    alloc = {"allocation": _careful(world)}
    g, h = plan_from_allocation(alloc)
    v, _ = score_submission(w, _submit(tmp_path, world, alloc["allocation"]), "paper")
    good = faithful_model(b, {u: ["0"] * 6 for u in g}, {u: ["0"] * 6 for u in h})  # plan replaced by the committed one
    fid, status = model_plan_fidelity(good, alloc, v["oracle_combined_final_revenue"], w)
    assert status == "ok" and Decimal(fid) < Decimal("1e-30")
    bad = dict(good, formulas=dict(CHURN_BLIND_FORMULAS))
    fid, status = model_plan_fidelity(bad, alloc, v["oracle_combined_final_revenue"], w)
    assert status == "ok" and Decimal(fid) > 1
    wrong_units = dict(good, units={k: good["units"][k] for k in list(good["units"])[:2]})
    assert (
        model_plan_fidelity(wrong_units, alloc, v["oracle_combined_final_revenue"], w)[1]
        == "model units differ from the world's units"
    )
    assert model_plan_fidelity(None, alloc, "1", w) == (None, "no model")


def test_baseline_arms_score_exactly_like_eval2(tmp_path):
    d = tmp_path / "sb"
    run_stub_direct(Cell("eval3", "orion-holdings", "ungrounded", 0), "v1", d)
    for rs in RULESETS:
        v3, kw3 = score_eval3("orion-holdings", d, rs, "ungrounded", 0)
        v2, kw2 = score_submission(load_world("orion-holdings"), d, rs, condition="ungrounded", rep=0)
        assert v3.pop("branch") == "unaided" and kw3["details"].pop("branch") == "unaided"
        assert json.dumps(v3, sort_keys=True, default=str) == json.dumps(v2, sort_keys=True, default=str)
        assert kw3["success"] == kw2["success"] and kw3["overclaim"] == kw2["overclaim"]


def test_unknown_ruleset_or_cell_is_refused(tmp_path):
    with pytest.raises(ValueError):
        EVAL.score(tmp_path, Cell("eval3", "orion-holdings", "gen-both", 0), "v2")
    with pytest.raises(ValueError):
        EVAL.score(tmp_path, Cell("eval3", "cygnus-holdings", "gen-both", 0), "v1")
    with pytest.raises(ValueError):
        EVAL.score(tmp_path, Cell("eval3", "orion-holdings", "raw-figures", 0), "v1")
