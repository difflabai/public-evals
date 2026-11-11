"""Eval-1 instrument: engine vs calibration, briefs, generator, decimal context."""

from __future__ import annotations

import threading
from decimal import Decimal, getcontext, localcontext

import pytest

from dbwm.eval1 import selftest as ST
from dbwm.eval1.engine import check_constraints, evaluate, goal_multiple, load_model_and_tiers


def test_calibration_90_cells_rederived():
    res = ST.rederive_calibration()
    assert res["companies"] == 10
    assert res["constraint_cells"] == 90
    assert res["problems"] == []


def test_tier_semantics_careful_everywhere_naive_soft_only(calibration):
    """careful is fully valid on soft/hard/hostile for all 10; naive only on soft; base never."""
    for mid, entry in calibration.items():
        model, tiers_doc = load_model_and_tiers(mid)
        target = Decimal(str(tiers_doc["goal"]["target_multiple"]))
        for name, plan in (("base", {}), ("naive", entry["reference_plans"]["naive"]),
                           ("careful", entry["reference_plans"]["careful"])):
            periods = evaluate(model, plan)
            m = goal_multiple(periods, tiers_doc["goal"])
            for tier_name, tier in tiers_doc["tiers"].items():
                ok = (m is not None and m >= target) and all(
                    d["ok"] for d in check_constraints(model, periods, plan, tier))
                want = {"base": False, "naive": tier_name == "soft", "careful": True}[name]
                assert ok is want, (mid, name, tier_name)


def test_briefs_regenerate_and_sha_prefixes():
    checks = ST.check_briefs()
    assert all(c.ok for c in checks), [c.detail for c in checks]


def test_generator_regenerates_worlds_and_calibration():
    c = ST.check_generator()
    assert c.ok, c.detail


def test_engine_ignores_caller_decimal_context():
    """The paper set prec 50 at import (main thread only); the port fixes the context per call,
    so a threaded oracle server or a caller with a 28-digit context gets identical figures."""
    model, _ = load_model_and_tiers("northwind-analytics")
    ref = [str(p["revenue"]) for p in evaluate(model)]
    with localcontext() as ctx:
        ctx.prec = 12
        assert [str(p["revenue"]) for p in evaluate(model)] == ref
    out = {}

    def worker():
        getcontext().prec = 28
        out["rev"] = [str(p["revenue"]) for p in evaluate(model)]

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert out["rev"] == ref
    assert any(len(x) > 30 for x in ref)  # genuinely 50-digit figures


def test_unknown_company_and_bad_binding():
    with pytest.raises(KeyError):
        load_model_and_tiers("no-such-company")
    model, _ = load_model_and_tiers("sprout-crm")
    with pytest.raises(ValueError):
        evaluate(model, {"revenue": ["1"] * 6})
    with pytest.raises(ValueError):
        evaluate(model, {"acv": ["1"] * 5})
