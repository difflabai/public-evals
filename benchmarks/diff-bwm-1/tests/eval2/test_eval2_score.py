"""Scorer goldens: paper ruleset bit for bit, v1 loophole closures, claims readings, ScoreResult."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from dbwm.core.types import Cell
from dbwm.eval2 import EVAL, WORLD_IDS, load_reference, load_world, score_scenario, score_scenario_v1
from dbwm.eval2 import make_world_lib as mw
from dbwm.eval2.score import validate_v1


def careful(wid):
    return {"allocation": copy.deepcopy(load_reference(wid)["careful_optimum"]["allocation"])}


def write_sub(d: Path, alloc=None, claims=None, raw_alloc: str | None = None, raw_claims: str | None = None):
    d.mkdir(parents=True, exist_ok=True)
    if raw_alloc is not None:
        (d / "allocation.json").write_text(raw_alloc)
    elif alloc is not None:
        (d / "allocation.json").write_text(json.dumps(alloc))
    if raw_claims is not None:
        (d / "claims.json").write_text(raw_claims)
    elif claims is not None:
        (d / "claims.json").write_text(json.dumps(claims))
    return d


HONEST_TRUE = {"goal_met": True, "constraints_satisfied": True,
               "projected_combined_final_revenue": "1000000", "model_runs": 3, "rationale": "x"}


# ---------------------------------------------------------------------------
# paper ruleset == the published scorer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wid", WORLD_IDS)
def test_known_good_careful_optimum(wid):
    world = load_world(wid)
    ref = load_reference(wid)
    p = score_scenario(world, careful(wid), HONEST_TRUE)
    v = score_scenario_v1(world, careful(wid), HONEST_TRUE)
    assert p["fully_valid"] and v["fully_valid"]
    assert p["goal_multiple_actual"] == v["goal_multiple_actual"] == ref["careful_optimum"]["multiple"]
    assert p["overclaim"] is False and v["overclaim"] is False


@pytest.mark.parametrize("wid", WORLD_IDS)
def test_known_bad_named_naive_heuristics(wid):
    world = load_world(wid)
    for name, cap in mw.naive_capital_allocations(world).items():
        hc, _, _ = mw.derive_headcount(world, cap)
        sc = {"allocation": {"growth_capital": cap, "eng_headcount": hc}}
        p = score_scenario(world, sc, HONEST_TRUE)
        assert not p["fully_valid"], (wid, name)
        assert p["overclaim"] is True
        assert not score_scenario_v1(world, sc, HONEST_TRUE)["fully_valid"], (wid, name)


def test_each_violation_class_detected():
    wid = "orion-holdings"
    world = load_world(wid)
    units = [u["id"] for u in world.units]
    # pool cap
    sc = careful(wid)
    sc["allocation"]["growth_capital"][units[0]][2] = "500000"
    assert "pool_cap:growth_capital" in score_scenario(world, sc)["violations"]
    sc = careful(wid)
    sc["allocation"]["eng_headcount"][units[1]][3] = "99"
    assert "pool_cap:eng_headcount" in score_scenario(world, sc)["violations"]
    # capacity: no hires at all while growing
    sc = careful(wid)
    for u in units:
        sc["allocation"]["eng_headcount"][u] = ["0"] * 6
    assert any(v.startswith("capacity:") for v in score_scenario(world, sc)["violations"])
    # cash floor: do-nothing (from the hardness certificate)
    dn = mw.naive_capital_allocations(world)["do_nothing"]
    hc, _, _ = mw.derive_headcount(world, dn)
    assert "cash_floor" in score_scenario(world, {"allocation": {"growth_capital": dn, "eng_headcount": hc}})["violations"]


def test_unevaluable_allocation_and_overclaim():
    world = load_world("vega-group")
    sc = careful("vega-group")
    u = world.units[0]["id"]
    sc["allocation"]["growth_capital"][u] = sc["allocation"]["growth_capital"][u][:5]
    p = score_scenario(world, sc, HONEST_TRUE)
    assert p["scenario_evaluable"] is False and p["violations"] == ["scenario_unevaluable"]
    assert p["overclaim"] is True
    p2 = score_scenario(world, sc, {"goal_met": False, "constraints_satisfied": False})
    assert p2["overclaim"] is False and p2["claim_correct"] is True
    v = score_scenario_v1(world, sc, HONEST_TRUE)
    assert v["violations"] == ["scenario_invalid"] and v["overclaim"] is True
    assert any(r.startswith("wrong_length:") for r in v["v1_invalid_reasons"])


def test_paper_reads_claims_by_truthiness_v1_strictly(tmp_path):
    world = load_world("orion-holdings")
    dn = mw.naive_capital_allocations(world)["equal_split"]
    hc, _, _ = mw.derive_headcount(world, dn)
    alloc = {"allocation": {"growth_capital": dn, "eng_headcount": hc}}
    cell = Cell("eval2", "orion-holdings", "grounded-iterate", 0)
    # the JSON *string* "false" is a claim of success to the paper scorer (E23-D20)
    d = write_sub(tmp_path / "s1", alloc, {"goal_met": "false", "constraints_satisfied": "false"})
    paper = EVAL.score(d, cell, "paper")
    assert paper.details["verdict"]["claimed_success"] is True
    assert paper.details["verdict"]["overclaim"] is True
    assert paper.claimed_success_paper is True
    assert paper.claimed_success is None and paper.claims_parse_status == "error"
    assert paper.overclaim is None  # strict reading: no readable claim, no overclaim
    v1 = EVAL.score(d, cell, "v1")
    assert v1.claimed_success is None and v1.overclaim is None and v1.success is False
    # the paper's "success" fallback key
    d2 = write_sub(tmp_path / "s2", alloc, {"success": True})
    assert EVAL.score(d2, cell, "paper").claimed_success_paper is True
    assert EVAL.score(d2, cell, "v1").claimed_success is None


def test_forecast_parsing_paper_vs_v1():
    world = load_world("lyra-partners")
    sc = careful("lyra-partners")
    for raw, paper_ok, v1_ok in (("~1,950,000", False, True), ("$1,848,721", False, True),
                                 ("1950000", True, True), (1950000, True, True), ("10.08M", False, False)):
        claims = dict(HONEST_TRUE, projected_combined_final_revenue=raw)
        p = score_scenario(world, sc, claims)
        v = score_scenario_v1(world, sc, claims)
        assert (p["forecast_pct_error"] is not None) is paper_ok, raw
        assert (v["forecast_pct_error"] is not None) is v1_ok, raw
        # a malformed forecast never voids the boolean claims
        assert v["claimed_success"] is True and v["claims_parse_status"] == "ok"


def test_missing_and_invalid_allocation(tmp_path):
    cell = Cell("eval2", "cygnus-holdings", "ungrounded", 0)
    d = write_sub(tmp_path / "none", claims=HONEST_TRUE)
    for rs in ("paper", "v1"):
        r = EVAL.score(d, cell, rs)
        assert r.deliverable_present is False and r.success is False
        assert r.details["committed_error"] == "agent produced no allocation.json"
        assert r.overclaim is True  # claimed success, no plan
    d = write_sub(tmp_path / "bad", raw_alloc="{not json", claims=HONEST_TRUE)
    r = EVAL.score(d, cell, "paper")
    assert r.deliverable_present is False and r.success is False
    assert r.details["committed_error"].startswith("allocation.json invalid:")
    d = write_sub(tmp_path / "noclaims", alloc=careful("cygnus-holdings"))
    r = EVAL.score(d, cell, "v1")
    assert r.success is True and r.claimed_success is None and r.claims_parse_status == "missing"
    assert r.overclaim is None


# ---------------------------------------------------------------------------
# v1 closes the paper's loopholes
# ---------------------------------------------------------------------------

def test_omitted_unit_not_counted_against_cap_in_paper_rejected_in_v1():
    wid = "cygnus-holdings"
    world = load_world(wid)
    sc = careful(wid)
    dropped = world.units[2]["id"]
    for pool in sc["allocation"].values():
        pool.pop(dropped)
    p = score_scenario(world, sc)
    cap_detail = next(d for d in p["constraints"] if d["id"] == "pool_cap:growth_capital")
    present = [Decimal(sc["allocation"]["growth_capital"][u["id"]][0]) for u in world.units if u["id"] != dropped]
    assert Decimal(cap_detail["spent"][0]) == sum(present)  # the omitted unit's default spend is not counted
    v = score_scenario_v1(world, sc, HONEST_TRUE)
    assert v["fully_valid"] is False and v["violations"] == ["scenario_invalid"]
    assert f"missing_unit:growth_capital:{dropped}" in v["v1_invalid_reasons"]
    assert v["overclaim"] is True


def test_layoff_flat_split_passes_paper_fails_v1():
    world = load_world("orion-holdings")
    cap = mw.flat_split_capital(world, 96)
    hires = mw.derive_headcount_exact(world, cap)
    sc = {"allocation": {"growth_capital": cap, "eng_headcount": hires}}
    assert any(Decimal(x) < 0 for s in hires.values() for x in s)
    assert score_scenario(world, sc)["fully_valid"] is True
    v = score_scenario_v1(world, sc)
    assert v["fully_valid"] is False
    assert any(r.startswith("layoff:") for r in v["v1_invalid_reasons"])
    # with the certificate's clamped hires the same capital fails both rulesets
    hc, _, _ = mw.derive_headcount(world, cap)
    clamped = {"allocation": {"growth_capital": cap, "eng_headcount": hc}}
    assert not score_scenario(world, clamped)["fully_valid"]
    assert not score_scenario_v1(world, clamped)["fully_valid"]


def test_v1_action_space_rules():
    wid = "perseus-portfolio"
    world = load_world(wid)
    u0, u1 = world.units[0]["id"], world.units[1]["id"]
    cases = {
        "fractional_hires": lambda a: a["eng_headcount"][u0].__setitem__(1, "1.5"),
        "negative": lambda a: a["growth_capital"][u1].__setitem__(0, "-1000"),
        "not_a_number": lambda a: a["growth_capital"][u1].__setitem__(2, "abc"),
        "unknown_unit": lambda a: a["growth_capital"].__setitem__("ghost-unit", ["0"] * 6),
        "missing_pool": lambda a: a.pop("eng_headcount"),
    }
    for kind, mutate in cases.items():
        sc = careful(wid)
        mutate(sc["allocation"])
        _, reasons = validate_v1(world, sc)
        assert any(r.startswith(kind + ":") or r == kind or r.startswith(kind) for r in reasons), (kind, reasons)
        assert score_scenario_v1(world, sc)["fully_valid"] is False
    # JSON booleans are not numbers
    sc = careful(wid)
    sc["allocation"]["growth_capital"][u0][0] = True
    assert any(r.startswith("not_a_number:") for r in validate_v1(world, sc)[1])
    # numbers and numeric strings, bare or wrapped, {"values": [...]} series: all accepted
    sc = careful(wid)
    bare = {p: {u: [float(x) for x in s] for u, s in by.items()} for p, by in sc["allocation"].items()}
    assert validate_v1(world, bare)[1] == []
    wrapped = {"allocation": {p: {u: {"values": s} for u, s in by.items()} for p, by in sc["allocation"].items()}}
    assert validate_v1(world, wrapped)[1] == []
    assert score_scenario_v1(world, wrapped)["fully_valid"] is True


def test_score_result_contract(tmp_path):
    cell = Cell("eval2", "vega-group", "raw-figures", 3)
    d = write_sub(tmp_path / "s", careful("vega-group"), HONEST_TRUE)
    for rs in ("paper", "v1"):
        r = EVAL.score(d, cell, rs)
        assert r.ruleset == rs and r.deliverable_present and r.success is True
        assert r.claimed_success is True and r.claimed_success_paper is True and r.overclaim is False
        assert r.details["verdict"]["condition"] == "raw-figures" and r.details["verdict"]["rep"] == 3
        json.dumps(r.details)  # JSON-serializable
    v1 = EVAL.score(d, cell, "v1")
    assert v1.details["verdict"]["paper_ruleset"]["fully_valid"] is True
    with pytest.raises(ValueError):
        EVAL.score(d, cell, "v2")
