"""Contrast: the uplift formula, both no-plan policies, the seeded bootstrap, summation, dropped worlds,
moat buckets and the CLI on a run."""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

from dbwm.eval6 import contrast as C
from dbwm.eval6 import d2_judge as J

from .conftest import PKG_ROOT


def test_uplift_formula_and_policies():
    trials = [
        {"world": "w1", "arm": "aided", "true_profit": 110.0, "no_plan": False},
        {"world": "w1", "arm": "unaided", "true_profit": 100.0, "no_plan": False},
        {"world": "w2", "arm": "aided", "true_profit": 90.0, "no_plan": True},
        {"world": "w2", "arm": "unaided", "true_profit": 100.0, "no_plan": False},
        {"world": "w3", "arm": "aided", "true_profit": 120.0, "no_plan": False},
        {"world": "w3", "arm": "unaided", "true_profit": None, "no_plan": False, "excluded": True,
         "exclusion_reason": "audit_flagged"},
    ]
    ref_ = C.uplift_from_trials(trials, "reference")
    assert ref_["per_world"] == {"w1": pytest.approx(10.0), "w2": pytest.approx(-10.0)}
    assert ref_["dropped_worlds"] == ["w3"] and ref_["dropped_trials"][0]["reason"] == "audit_flagged"
    ex = C.uplift_from_trials(trials, "exclude")
    assert list(ex["per_world"]) == ["w1"] and set(ex["dropped_worlds"]) == {"w2", "w3"}
    with pytest.raises(ValueError):
        C.uplift_from_trials(trials, "impute")


def test_world_order_is_numeric():
    assert sorted(["w10", "w2", "w1", "p3a"], key=C.world_key) == ["w1", "w2", "w10", "p3a"]


def test_bootstrap_is_seeded_and_deterministic():
    pw = {"w1": 1.0, "w2": 5.0, "w3": 9.0, "w4": -2.0}
    assert C.cluster_bootstrap_ci(pw, 500, 3) == C.cluster_bootstrap_ci(dict(reversed(list(pw.items()))), 500, 3)
    assert C.cluster_bootstrap_ci(pw, 500, 3) != C.cluster_bootstrap_ci(pw, 500, 4)


def test_summarize_drops_worlds_and_moat_buckets(tmp_path):
    pw = {"w1": 4.0, "w2": 8.0, "w3": 12.0, "w4": 20.0, "w7": 100.0}
    s = C.summarize(pw, B=200, exclude_worlds=["w7", "w99"])
    assert s["n_worlds"] == 4 and s["uplift_mean"] == 11.0 and s["uplift_median"] == 10.0
    assert list(s["per_world"]) == ["w1", "w2", "w3", "w4"] and s["excluded_worlds"] == ["w7"]
    assert s["ci95"] == C.cluster_bootstrap_ci({w: pw[w] for w in ("w1", "w2", "w3", "w4")}, 200, C.DEFAULT_SEED)
    assert C.summarize(pw, exclude_worlds=list(pw)) == {"n_worlds": 0, "per_world": {}}
    scan = [{"world_id": "w1", "GATE_PASS": True, "gamma_moat": {"true_profit_gap_pct": 3.5}},
            {"world_id": "w2", "GATE_PASS": True, "gamma_moat": {"true_profit_gap_pct": 9.99}},
            {"world_id": "w3", "GATE_PASS": True, "gamma_moat": {"true_profit_gap_pct": 10.0}},
            {"world_id": "w4", "GATE_PASS": True, "gamma_moat": {"true_profit_gap_pct": 12.0}},
            {"world_id": "w7", "GATE_PASS": False, "gamma_moat": {"true_profit_gap_pct": 30.0}}]
    (tmp_path / "certify_scan.json").write_text(json.dumps(scan))
    moats = C.moats_from_certify_scan(tmp_path / "certify_scan.json")
    assert moats == {"w1": 3.5, "w2": 9.99, "w3": 10.0, "w4": 12.0}       # a world failing the gate has no moat
    b = C.moat_buckets(pw, moats)
    assert b["thresholds"] == {"low_lt": 10.0, "high_ge": 12.0}
    assert b["low"] == {"n": 2, "worlds": ["w1", "w2"], "mean_uplift": 6.0}
    assert b["mid"] == {"n": 1, "worlds": ["w3"], "mean_uplift": 12.0}
    assert b["high"] == {"n": 1, "worlds": ["w4"], "mean_uplift": 20.0}   # w7: in no bucket
    assert C.moat_buckets(pw, {"w1": 11.0})["low"] == {"n": 0, "worlds": [], "mean_uplift": None}


def test_neumaier_matches_python312_sum():
    xs = [0.1] * 10 + [1e16, 1.0, -1e16]
    assert J.neumaier_sum(xs) == 2.0 and J.lsum(xs) != 2.0
    if sys.version_info >= (3, 12):
        assert sum(xs) == J.neumaier_sum(xs)


def _write_run(run: Path, profits: dict) -> Path:
    """dbwm.verdict/1-shaped files, one scored attempt per (world, arm) cell."""
    for (world, arm), profit in profits.items():
        cid = f"{world}__{arm}__r0"
        d = run / "cells" / cid / "attempt-1"
        d.mkdir(parents=True)
        (d / "verdict.json").write_text(json.dumps({
            "eval": "eval6", "attempt": 1, "scored": True, "outcome": "completed", "ruleset": "v1",
            "cell": {"cell_id": cid, "instance": world, "arm": arm, "rep": 0},
            "details": {"true_profit": profit, "no_plan": False}, "audit": {"flagged": False}}))
    return run


def test_contrast_cli_on_a_run(tmp_path):
    run = _write_run(tmp_path / "run", {("w1", "aided"): 110.0, ("w1", "unaided"): 100.0,
                                        ("w2", "aided"): 90.0, ("w2", "unaided"): 100.0,
                                        ("w7", "aided"): 150.0, ("w7", "unaided"): 100.0})
    out = tmp_path / "c.json"
    r = subprocess.run([sys.executable, "-m", "dbwm", "eval6-d1", "contrast", "--run", str(run),
                        "--bootstrap", "200", "--drop", "w7", "--out", str(out)],
                       cwd=PKG_ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-1500:]
    d = json.loads(out.read_text())
    assert d["reference"]["per_world"] == {"w1": pytest.approx(10.0), "w2": pytest.approx(-10.0),
                                           "w7": pytest.approx(50.0)}
    assert d["reference_drop_w7"]["excluded_worlds"] == ["w7"] and "exclude_drop_w7" in d
    assert d["reference_drop_w7"]["uplift_mean"] == pytest.approx(0.0)
    assert statistics.mean(d["exclude"]["per_world"].values()) == d["exclude"]["uplift_mean"]


CONTRAST_SCRIPT = r"""
import json, sys
from pathlib import Path
from dbwm.eval6 import contrast as C
print(json.dumps(C.contrast_both_policies(C.trials_from_run(Path(sys.argv[1])), B=500)))
"""


def test_contrast_independent_of_hash_seed(tmp_path):
    worlds = ("w1", "w2", "w3", "w4", "w5", "w6", "w10", "w12")
    profits = {}
    for i, w in enumerate(worlds):
        profits[(w, "aided")] = 100.0 + 9 * i + 4 * (i % 3)
        profits[(w, "unaided")] = 100.0 + 5 * i
    run = _write_run(tmp_path / "run", profits)
    outs = set()
    for seed in ("0", "5", "7"):
        r = subprocess.run([sys.executable, "-c", CONTRAST_SCRIPT, str(run)], cwd=PKG_ROOT,
                           env=dict(os.environ, PYTHONHASHSEED=seed), capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-1500:]
        outs.add(r.stdout)
    assert len(outs) == 1
    assert json.loads(outs.pop())["reference"]["n_worlds"] == len(worlds)
