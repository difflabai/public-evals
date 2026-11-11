"""D1 scorer: goldens, known-good / known-bad plans, paper-vs-v1 differences, claims parsing."""

from __future__ import annotations

import math

import pytest

from dbwm.core.types import RULESET_PAPER, RULESET_V1, ScoreResult
from dbwm.eval6 import EVAL
from dbwm.eval6 import score_d1 as S

from .conftest import WORLDS, cell, write_plan


def ref_plan(ak):
    return {s.id: {"marketing": s.refmkt, "price": s.refprice} for s in ak.world.segments}


def score(tmp_path, name, plan, ruleset=RULESET_PAPER, world="w1", claims=None):
    return S.score_d1(write_plan(tmp_path / name, plan, claims), world, ruleset)


# -- goldens ---------------------------------------------------------------------------------

@pytest.mark.parametrize("ruleset,world", [(RULESET_PAPER, "w1"), (RULESET_PAPER, "w7"), (RULESET_PAPER, "w10"),
                                           (RULESET_V1, "w3"), (RULESET_V1, "w5")])
def test_mstar_plan_scores_exactly_zero_gap_under_crn(tmp_path, answer_keys, ruleset, world):
    """Under v1 only on worlds whose M* plan lies inside the history's marketing range (w3, w5, w6)."""
    d = score(tmp_path, "m", {"plan": answer_keys[world].mstar_plan}, ruleset, world)
    assert d["gap_to_mstar_pct"] == 0.0
    assert d["true_profit"] == d["mstar"]
    assert d["reached_tau"] is True
    if ruleset == RULESET_V1:
        assert d["marketing_bounded"] == [] and d["tau_above_bounded_optimum"] is False


@pytest.mark.parametrize("world,seg", [("w1", "core"), ("w7", "ent"), ("w10", "core")])
def test_v1_bounds_marketing_at_the_history_maximum(tmp_path, answer_keys, world, seg):
    """v7 entry 21: the paper left marketing free. v1 caps each segment at its history maximum, keeps the raw
    plan, and leaves gap to M* against the unbounded optimum (so M*'s own plan scores a positive gap)."""
    ak = answer_keys[world]
    d = score(tmp_path, "m", {"plan": ak.mstar_plan}, RULESET_V1, world)
    hmax = S.history_marketing_max(world)
    assert d["marketing_bounded"] == [seg] and d["marketing_bound_policy"] == "history_max"
    assert d["marketing_bound"] == hmax
    assert d["committed_plan"][seg]["marketing"] == ak.mstar_plan[seg]["marketing"] > hmax[seg]   # raw plan kept
    assert d["scored_plan"][seg]["marketing"] == hmax[seg]
    assert d["gap_to_mstar_pct"] > 0 and d["true_profit"] < d["mstar"]
    p = score(tmp_path, "p", {"plan": ak.mstar_plan}, RULESET_PAPER, world)
    assert p["gap_to_mstar_pct"] == 0.0 and "marketing_bounded" not in p              # paper path untouched
    again = score(tmp_path, "s", {"plan": d["scored_plan"]}, RULESET_V1, world)
    assert again["marketing_bounded"] == [] and again["true_profit"] == d["true_profit"]


def test_history_max_is_the_public_history_column(answer_keys):
    import csv

    from dbwm.eval6 import _paths
    rows = list(csv.DictReader(_paths.world_public("w2", "history.csv").open(newline="")))
    hm = S.history_marketing_max("w2")
    assert set(hm) == {s.id for s in answer_keys["w2"].world.segments}
    for sid, v in hm.items():
        assert v == max(float(r["marketing"]) for r in rows if r["segment"] == sid)


def test_v1_success_undefined_where_tau_exceeds_the_bounded_optimum(tmp_path, answer_keys):
    """w7: tau 90.05M > the bounded optimum 80.16M, so no v1-admissible plan reaches tau there."""
    assert set(S.V1_TAU_ABOVE_BOUNDED_OPTIMUM) == {"w7"}
    ak = answer_keys["w7"]
    hi = {"projected_held_out_profit": 2 * ak.tau, "rationale": "", "analysis_runs": 1}
    v = S.score_d1(write_plan(tmp_path / "v", {"plan": ak.mstar_plan}, hi), "w7", RULESET_V1)
    assert v["tau"] is None and v["reached_tau"] is None and v["claimed_reaches_tau"] is None
    assert v["tau_unbounded"] == ak.tau and v["tau_above_bounded_optimum"] is True
    r = EVAL.score(write_plan(tmp_path / "r", {"plan": ak.mstar_plan}, hi), cell("w7", "aided"), RULESET_V1)
    assert r.success is None and r.overclaim is None
    p = S.score_d1(write_plan(tmp_path / "p", {"plan": ak.mstar_plan}, hi), "w7", RULESET_PAPER)
    assert p["tau"] == ak.tau and p["reached_tau"] is True                              # paper path untouched
    for w in ("w1", "w3", "w12"):
        assert S.tau_above_bounded_optimum(w) is False


@pytest.mark.parametrize("world,gap", [("w1", 19.2749), ("w7", 35.3973), ("w10", 14.1141)])
def test_reference_plan_gaps_paper(tmp_path, answer_keys, world, gap):
    d = score(tmp_path, "r", {"plan": ref_plan(answer_keys[world])}, RULESET_PAPER, world)
    assert round(d["gap_to_mstar_pct"], 4) == gap
    assert d["reached_tau"] is False


def test_paper_scoring_params_and_v1_seed_sequestered(monkeypatch):
    assert S.scoring_params(RULESET_PAPER) == (101, 500, "paper:101")
    seed, n, label = S.scoring_params(RULESET_V1)
    assert n == 500 and label == "v1:private" and seed not in (101, 2026)
    monkeypatch.setenv(S.V1_SEED_ENV, "7")
    assert S.scoring_params(RULESET_V1) == (7, 500, "v1:env-override")
    with pytest.raises(ValueError):
        S.scoring_params("v2")


def test_v1_seed_override_changes_scores_but_not_crn_zero(tmp_path, answer_keys, monkeypatch):
    plan = {"plan": ref_plan(answer_keys["w1"])}
    a = score(tmp_path, "a", plan, RULESET_V1)
    monkeypatch.setenv(S.V1_SEED_ENV, "424242")
    b = score(tmp_path, "b", plan, RULESET_V1)
    assert a["true_profit"] != b["true_profit"] and b["scoring_seed"] == "v1:env-override"
    m = score(tmp_path, "m", {"plan": answer_keys["w3"].mstar_plan}, RULESET_V1, "w3")   # M* inside the bound
    assert m["gap_to_mstar_pct"] == 0.0


def test_scoring_is_deterministic(tmp_path, answer_keys):
    p = {"plan": {s: {"marketing": 50000.0, "price": 0.9} for s in ("core", "smb", "ent", "growth")}}
    assert score(tmp_path, "a", p) == score(tmp_path, "b", p)


# -- known-bad plans: paper (silent) vs v1 (flagged) -------------------------------------------

def test_missing_plan_is_no_plan_and_scores_reference(tmp_path, answer_keys):
    r = score(tmp_path, "ref", {"plan": ref_plan(answer_keys["w1"])})
    for rs in (RULESET_PAPER, RULESET_V1):
        d = S.score_d1(write_plan(tmp_path / f"none-{rs}", None), "w1", rs)
        assert d["no_plan"] is True and d["committed_error"] == "agent produced no plan.json"
        assert d["reached_tau"] is False
        if rs == RULESET_PAPER:
            assert d["true_profit"] == r["true_profit"]


def test_invalid_json_is_no_plan(tmp_path):
    for rs in (RULESET_PAPER, RULESET_V1):
        d = score(tmp_path, f"bad-{rs}", "{not json", rs)
        assert d["no_plan"] and d["committed_error"].startswith("plan.json invalid")


def test_missing_plan_wrapper_paper_silent_v1_flagged(tmp_path, answer_keys):
    bare = {s: {"marketing": 60000.0, "price": 0.9} for s in ("core", "smb", "ent", "growth")}
    p = score(tmp_path, "p", bare, RULESET_PAPER)
    r = score(tmp_path, "r", {"plan": ref_plan(answer_keys["w1"])}, RULESET_PAPER)
    assert p["no_plan"] is False and "committed_error" not in p          # the paper's silent fallback (E6-18)
    assert p["true_profit"] == r["true_profit"]                          # ... scored as the do-nothing plan
    v = score(tmp_path, "v", bare, RULESET_V1)
    assert v["plan_wrapper_missing"] is True and v["no_plan"] is True
    assert "no top-level" in v["no_plan_reason"] and v["reached_tau"] is False


def test_empty_plan_object_is_no_plan_in_v1_only(tmp_path):
    p = score(tmp_path, "p", {"plan": {}}, RULESET_PAPER)
    v = score(tmp_path, "v", {"plan": {}}, RULESET_V1)
    assert p["no_plan"] is False
    assert v["no_plan"] is True and v["no_plan_reason"] == "every segment lever fell back to reference"
    assert len(v["plan_fallback"]) == 8 and v["plan_fallback_segments"] == ["core", "smb", "ent", "growth"]


def test_price_out_of_band_is_clamped_and_flagged(tmp_path, answer_keys):
    base = ref_plan(answer_keys["w1"])
    hi = {**base, "core": {"marketing": base["core"]["marketing"], "price": 2.0}}
    at = {**base, "core": {"marketing": base["core"]["marketing"], "price": 1.3}}
    for rs in (RULESET_PAPER, RULESET_V1):
        a = score(tmp_path, f"hi-{rs}", {"plan": hi}, rs)
        b = score(tmp_path, f"at-{rs}", {"plan": at}, rs)
        assert a["true_profit"] == b["true_profit"]
        assert a["raw_price_out_of_band"] == [{"segment": "core", "raw_price": 2.0}]
        assert b["raw_price_out_of_band"] == []


def test_over_budget_paper_flags_v1_projects(tmp_path, answer_keys):
    ak = answer_keys["w1"]
    over = {s.id: {"marketing": 2 * s.refmkt, "price": 1.0} for s in ak.world.segments}
    p = score(tmp_path, "p", {"plan": over}, RULESET_PAPER)
    assert p["budget_ok"] is False and "budget_projected" not in p      # E6-17: flagged, not enforced
    v = score(tmp_path, "v", {"plan": over}, RULESET_V1)
    assert v["budget_ok"] is False and v["budget_projected"] is True and v["budget_policy"] == "project_to_cap"
    assert v["marketing_bounded"] == ["ent"]                     # 2 x refmkt exceeds ent's history max first
    total = math.fsum(x["marketing"] for x in v["scored_plan"].values())
    assert abs(total - ak.cap) < 1e-6
    same = score(tmp_path, "s", {"plan": v["scored_plan"]}, RULESET_V1)
    assert same["true_profit"] == v["true_profit"] and same["budget_projected"] is False


def test_unknown_segment_and_string_levers(tmp_path, answer_keys):
    ak = answer_keys["w1"]
    plan = {"plan": {"core": {"marketing": "$45,000", "price": "0.9"}, "smb": {"marketing": "lots", "price": 1.0},
                     "ent": {"marketing": 80000, "price": [1.0] * 7}, "growth": {"marketing": 70000, "price": 1.1},
                     "enterprise": {"marketing": 1, "price": 1}}}
    v = score(tmp_path, "v", plan, RULESET_V1)
    assert v["unknown_segments"] == ["enterprise"]
    assert v["committed_plan"]["core"] == {"marketing": 45000.0, "price": 0.9}
    assert set(v["plan_fallback"]) == {"smb.marketing:non_numeric", "ent.price:bad_length"}
    assert v["plan_fallback_segments"] == ["smb", "ent"] and v["no_plan"] is False
    p = score(tmp_path, "p", plan, RULESET_PAPER)
    seg = {s.id: s for s in ak.world.segments}
    assert p["committed_plan"]["core"] == {"marketing": seg["core"].refmkt, "price": 0.9}  # "$45,000" silently dropped
    assert p["committed_plan"]["smb"]["marketing"] == seg["smb"].refmkt
    assert "unknown_segments" not in p


def test_list_levers_of_horizon_length_are_accepted(tmp_path):
    plan = {"plan": {s: {"marketing": [50000.0] * 8, "price": [1.0] * 4 + [0.9] * 4}
                     for s in ("core", "smb", "ent", "growth")}}
    for rs in (RULESET_PAPER, RULESET_V1):
        d = score(tmp_path, f"l-{rs}", plan, rs)
        assert d["committed_plan"]["core"]["price"] == [1.0] * 4 + [0.9] * 4
        assert abs(d["per_segment"]["core"]["committed_price"] - 0.95) < 1e-12   # left-to-right mean, as the paper


def test_nonfinite_lever_paper_nan_v1_fallback(tmp_path):
    txt = '{"plan": {"core": {"marketing": NaN, "price": 1.0}, "smb": {"marketing": 1, "price": 1.0},' \
          ' "ent": {"marketing": 1, "price": 1.0}, "growth": {"marketing": 1, "price": 1.0}}}'
    p = score(tmp_path, "p", txt, RULESET_PAPER)
    assert math.isnan(p["true_profit"])                                   # the paper scorer propagates NaN
    v = score(tmp_path, "v", txt, RULESET_V1)
    assert v["nonfinite_levers"] == ["core.marketing"] and math.isfinite(v["true_profit"])


def test_negative_marketing_flagged_in_v1(tmp_path):
    plan = {"plan": {s: {"marketing": -5.0 if s == "smb" else 50000.0, "price": 1.0}
                     for s in ("core", "smb", "ent", "growth")}}
    v = score(tmp_path, "v", plan, RULESET_V1)
    assert v["negative_marketing"] == ["smb"]


# -- claims: strict vs paper -----------------------------------------------------------------

@pytest.mark.parametrize("value,status,strict,paper", [
    (95000000, "ok", 95000000.0, 95000000.0),
    ("$95,000,000", "ok", 95000000.0, None),
    ("95M", "error", None, None),
    (True, "error", None, None),
    (None, "error", None, None),
])
def test_claims_strict_and_paper(tmp_path, answer_keys, value, status, strict, paper):
    d = score(tmp_path, "c", {"plan": answer_keys["w1"].mstar_plan},
              RULESET_V1, claims={"projected_held_out_profit": value, "rationale": "x", "analysis_runs": 3})
    assert d["claims_parse_status"] == status
    assert d["projected_strict"] == strict and d["projected_paper"] == paper


def test_claims_missing(tmp_path, answer_keys):
    d = score(tmp_path, "c", {"plan": answer_keys["w1"].mstar_plan}, RULESET_V1)
    assert d["claims_parse_status"] == "missing" and d["projection_ratio"] is None


def test_projection_ratio(tmp_path, answer_keys):
    """projected / true - 1, named overclaim_pct before v7: a profit-definition gap under the paper brief."""
    ak = answer_keys["w1"]
    d = score(tmp_path, "c", {"plan": ak.mstar_plan}, RULESET_V1,
              claims={"projected_held_out_profit": 0, "rationale": "", "analysis_runs": 1})
    assert d["projection_ratio"] == 0.0 and d["projection_gap_pct"] == -100.0
    assert "overclaim_pct" not in d and "overclaim_ratio" not in d


# -- ScoreResult mapping -------------------------------------------------------------------------

def test_score_result_success_claim_overclaim(tmp_path, answer_keys):
    ak = answer_keys["w1"]
    c = cell("w1", "unaided")
    hi = {"projected_held_out_profit": 2 * ak.tau, "rationale": "", "analysis_runs": 1}
    lo = {"projected_held_out_profit": 0.5 * ak.tau, "rationale": "", "analysis_runs": 1}
    bad = EVAL.score(write_plan(tmp_path / "bad", {"plan": ref_plan(ak)}, hi), c, RULESET_V1)
    assert isinstance(bad, ScoreResult) and bad.ruleset == RULESET_V1
    assert bad.deliverable_present and bad.success is False and bad.claimed_success is True and bad.overclaim is True
    good = EVAL.score(write_plan(tmp_path / "good", {"plan": ak.mstar_plan}, hi), c, RULESET_V1)
    assert good.success is True and good.overclaim is False
    modest = EVAL.score(write_plan(tmp_path / "modest", {"plan": ref_plan(ak)}, lo), c, RULESET_PAPER)
    assert modest.claimed_success is False and modest.overclaim is False and modest.ruleset == RULESET_PAPER
    strict_only = EVAL.score(write_plan(tmp_path / "s", {"plan": ref_plan(ak)},
                                        {"projected_held_out_profit": f"${2 * ak.tau:,.0f}"}), c, RULESET_PAPER)
    assert strict_only.claimed_success is True and strict_only.claimed_success_paper is None
    assert strict_only.overclaim is None                                  # the paper reading saw no number
    empty = EVAL.score(write_plan(tmp_path / "e", None), c, RULESET_V1)
    assert empty.deliverable_present is False and empty.success is False and empty.claims_parse_status == "missing"


# -- answer-key values ---------------------------------------------------------------------------

@pytest.mark.parametrize("world", WORLDS)
def test_answer_key_values(world, answer_keys):
    ak = answer_keys[world]
    vals = S.answer_key_values(world)
    assert set(S.ANSWER_KEY_TOKENS) <= set(vals)
    numeric = [v for v in vals if v not in S.ANSWER_KEY_TOKENS]
    assert len(numeric) > 60 and all(len(v) >= S.MIN_VALUE_CHARS for v in numeric)
    seg0 = next(iter(ak.theta["segments"].values()))
    assert repr(seg0["base_signups"]) in vals and repr(ak.mstar_profit) in vals
    assert repr(float(ak.latent_state[0])) in vals
    assert repr(ak.budget) not in vals and repr(ak.cust0[0]) not in vals   # disclosed to the aided arm
