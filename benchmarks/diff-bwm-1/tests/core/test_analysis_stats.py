"""dbwm.core.stats: Wilson, bootstraps (deterministic, hash-seed independent), Kendall tau-b."""

from __future__ import annotations

import json
import math
import random
import sys

import pytest

from analysis_fixtures.helpers import run_in_subprocess
from dbwm.core import stats as S

# Golden values computed once from the published routines, run read-only from the
# frozen harness snapshot: confirm_report.wilson (eval 2) and bwm_theory/report.py
# bootstrap_ci_rate / bootstrap_ci_difference (eval 1) with b=2000 unless noted.
WILSON_GOLDEN = {
    (0, 10): (0.0, 0.0, 0.2775401687666166),
    (3, 10): (0.3, 0.10778928748621183, 0.6032267800204347),
    (10, 10): (1.0, 0.7224598312333834, 1.0),
    (17, 40): (0.425, 0.28509168524815875, 0.5780519956394001),
    (1, 1): (1.0, 0.20654329147389294, 1.0),
}
A = [True, False, True, True, False, True, None, False, True, True]
B = [False, False, True, False, False, None, False, True]


@pytest.mark.parametrize("kn", sorted(WILSON_GOLDEN))
def test_wilson_matches_published(kn):
    assert S.wilson(*kn) == WILSON_GOLDEN[kn]


def test_wilson_edges():
    assert S.wilson(0, 0) == (None, None, None)
    for bad in [(-1, 3), (4, 3), (1, -1)]:
        with pytest.raises(ValueError):
            S.wilson(*bad)
    for bad in [(1.0, 3), (True, 3), (1, "3")]:
        with pytest.raises(TypeError):
            S.wilson(*bad)


def test_percentile_bootstraps_reproduce_eval1_draw_sequence():
    # The eval-1 report threads ONE random.Random(12345) through successive calls.
    rng = random.Random(12345)
    assert S.percentile_bootstrap_mean(A, 2000, rng=rng) == (0.3333333333333333, 0.8888888888888888)
    assert S.percentile_bootstrap_diff(A, B, 2000, rng=rng) == (
        0.38095238095238093, -0.12698412698412698, 0.7777777777777778)
    rng = random.Random(7)
    assert S.percentile_bootstrap_diff([0.5, 1.25, 3.0, 2.0], [1.0, 0.0, 0.25], 500, rng=rng) == (
        1.2708333333333333, 0.20833333333333337, 2.1458333333333335)


def test_percentile_bootstrap_seed_and_empty():
    assert S.percentile_bootstrap_mean(A, 200, 1) == S.percentile_bootstrap_mean(A, 200, 1)
    assert S.percentile_bootstrap_mean([], 200, 1) == (None, None)
    assert S.percentile_bootstrap_diff([], B, 200, 1) == (None, None, None)
    with pytest.raises(ValueError):
        S.percentile_bootstrap_mean(A, 200)  # a seed is required
    with pytest.raises(TypeError):
        S.percentile_bootstrap_mean(["x"], 200, 1)
    with pytest.raises(TypeError):
        S.percentile_bootstrap_mean(A, 200, 1.5)


CLUSTER_A = {"w1": [True, True, False], "w2": [True, False], "w3": [False, False, True, True]}
CLUSTER_B = {"w1": [False, False, False], "w2": [True, False], "w3": [False], "w4": [False, True]}


def test_cluster_bootstrap_point_and_determinism():
    p, lo, hi, dropped = S.cluster_bootstrap_diff(CLUSTER_A, CLUSTER_B, 2000, 20260625)
    assert p == pytest.approx(5 / 9 - 2 / 8)
    assert lo is not None and hi is not None and lo <= p <= hi
    assert (p, lo, hi, dropped) == S.cluster_bootstrap_diff(CLUSTER_A, CLUSTER_B, 2000, 20260625)
    # Insertion order of clusters and strata must not matter.
    rev_a = dict(reversed(list(CLUSTER_A.items())))
    rev_b = dict(reversed(list(CLUSTER_B.items())))
    assert S.cluster_bootstrap_diff(rev_a, rev_b, 2000, 20260625) == (p, lo, hi, dropped)
    # A different seed gives (almost surely) different endpoints.
    assert S.cluster_bootstrap_diff(CLUSTER_A, CLUSTER_B, 2000, 1)[1:3] != (lo, hi)


def test_cluster_bootstrap_drops_and_counts_empty_replicates():
    a = {"w1": [True]}
    b = {"w2": [False]}
    p, lo, hi, dropped = S.cluster_bootstrap_diff(a, b, 400, 3)
    assert p == 1.0
    assert dropped > 0  # replicates drawing only w1 (or only w2) have an empty arm
    assert lo == hi == 1.0


def test_cluster_bootstrap_strata_and_whole_cluster_mode():
    a = {"w1": {"c2": [True, False], "c1": [True]}, "w2": {"c1": [False], "c2": [True]}}
    b = {"w1": [False, False], "w2": [True]}
    r1 = S.cluster_bootstrap_diff(a, b, 500, 5)
    assert r1[0] == pytest.approx(3 / 5 - 1 / 3)
    r2 = S.cluster_bootstrap_diff(a, b, 500, 5, within=False)
    assert r2[0] == r1[0] and r2 != r1
    assert S.cluster_bootstrap_diff({}, b, 100, 1) == (None, None, None, 0)
    with pytest.raises(ValueError):
        S.cluster_bootstrap_diff(a, b, 0, 1)
    with pytest.raises(TypeError):
        S.cluster_bootstrap_diff({"w1": "TTF"}, b, 10, 1)


def test_cluster_bootstrap_mean():
    m, lo, hi = S.cluster_bootstrap_mean({"w1": [1.0, 0.0], "w2": [1.0], 3: [0.0]}, 500, 9)
    assert m == pytest.approx(0.5) and lo <= m <= hi
    assert S.cluster_bootstrap_mean({}, 10, 1) == (None, None, None)


def test_ci_excludes_zero():
    assert S.ci_excludes_zero(0.01, 0.2) and S.ci_excludes_zero(-0.3, -0.1)
    assert not S.ci_excludes_zero(-0.1, 0.1) and not S.ci_excludes_zero(None, 0.2)


@pytest.mark.parametrize("seed", list(range(8)))
def test_hash_seed_independence(seed):
    """The clustered bootstrap must not move with PYTHONHASHSEED."""
    code = (
        "import json; from dbwm.core import stats as S\n"
        "a={'w%d'%i:[bool((i*7+j)%3) for j in range(4)] for i in range(9)}\n"
        "b={'w%d'%i:{'c1':[bool((i+j)%2) for j in range(3)],'c2':[bool(j%2) for j in range(2)]} for i in range(9)}\n"
        "a['x']=[True]; b[('t',1)]=[False]\n"
        "print(json.dumps([S.cluster_bootstrap_diff({k:v for k,v in a.items() if k!='x'},"
        "{k:v for k,v in b.items() if not isinstance(k,tuple)},300,11),"
        "S.cluster_bootstrap_mean({str(k):v for k,v in a.items()},300,4)]))\n")
    out = run_in_subprocess(code, {"PYTHONHASHSEED": str(seed)}, sys.executable)
    ref = run_in_subprocess(code, {"PYTHONHASHSEED": "0"}, sys.executable)
    assert json.loads(out) == json.loads(ref)


def test_kendall_tau_b_simple_cases():
    assert S.kendall_tau_b([1, 2, 3], [1, 2, 3]) == 1.0
    assert S.kendall_tau_b([1, 2, 3], [3, 2, 1]) == -1.0
    assert math.isnan(S.kendall_tau_b([1], [1]))
    assert math.isnan(S.kendall_tau_b([1, 1, 1], [1, 2, 3]))
    assert math.isnan(S.kendall_tau_b([1, float("nan")], [1, 2]))
    with pytest.raises(ValueError):
        S.kendall_tau_b([1, 2], [1])


def test_kendall_tau_b_matches_scipy():
    try:
        import scipy.stats as scipy_stats
    except ImportError as e:  # absent, or present but unloadable on this platform
        pytest.skip(f"scipy unavailable ({type(e).__name__}): exact comparison skipped")
    rng = random.Random(42)
    for trial in range(200):
        n = rng.randint(2, 25)
        ties = rng.choice([0, 3, 6])
        x = [rng.randint(0, ties) if ties else rng.random() for _ in range(n)]
        y = [rng.randint(0, ties) if ties else rng.random() for _ in range(n)]
        want = scipy_stats.kendalltau(x, y).statistic if hasattr(scipy_stats.kendalltau(x, y), "statistic") \
            else scipy_stats.kendalltau(x, y)[0]
        got = S.kendall_tau_b(x, y)
        if math.isnan(want):
            assert math.isnan(got), (x, y)
        else:
            assert abs(got - want) <= 1e-12, (trial, x, y, got, want)
