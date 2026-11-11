"""dbwm.core.stats exact tests and Newcombe interval, pinned to goldens at full precision.

The tables are synthetic. Goldens come from implementations independent of dbwm.core.stats:
scipy.stats.fisher_exact (two-sided, and "greater" for one-sided), a closed-form Wilson interval
(z = 1.96) inside Newcombe's hybrid score, and the stratified test by brute-force enumeration of the
joint per-stratum support in exact fractions.
"""

from __future__ import annotations

import math
import random

import pytest

from dbwm.core import stats as S

# (k1, n1, k2, n2) -> two-sided p.
FISHER_GOLDEN = {
    (22, 31, 0, 25): 9.59334147174362e-09,
    (21, 26, 0, 34): 8.238497385141896e-12,
    (8, 27, 0, 37): 0.0005015797683589846,
    (6, 23, 0, 26): 0.007218844984802435,
    (3, 22, 0, 28): 0.07857142857142858,
    (5, 13, 0, 18): 0.007574553736956407,
    (11, 37, 13, 37): 0.8042334251262886,
    (2, 31, 1, 28): 1.0,
    (0, 25, 0, 31): 1.0,
    (13, 31, 13, 31): 1.0,
    (1, 28, 19, 33): 4.91608125028447e-06,
    (12, 67, 0, 30): 0.015765671958369535,
    (5, 61, 6, 53): 0.7523707704976431,
    (31, 35, 0, 18): 1.1320451209509417e-10,
    (17, 29, 4, 27): 0.0009190157223075729,
    (3, 46, 13, 37): 0.001562090392876124,
    (49, 61, 2, 34): 6.100854655064153e-13,
    (88, 131, 1, 37): 1.6566668786603847e-13,
    (34, 116, 22, 59): 0.30698587686310197,
    (20, 43, 7, 32): 0.031853272751096326,
    (14, 76, 21, 26): 1.8647612309361e-08,
    (25, 72, 4, 38): 0.006329973721874472,
    (81, 123, 20, 46): 0.013098175049406182,
    (80, 111, 15, 60): 3.3711708089797713e-09,
}

NEWCOMBE_GOLDEN = {
    (22, 31, 0, 25): (0.7096774193548387, 0.489273069429813, 0.8390437928156712),
    (21, 26, 0, 34): (0.8076923076923077, 0.5953889383890378, 0.9149306422821173),
    (6, 23, 0, 26): (0.2608695652173913, 0.0740506058427936, 0.4647039054375056),
    (12, 67, 0, 30): (0.1791044776119403, 0.043832101211703556, 0.28747713612629794),
    (5, 13, 0, 18): (0.38461538461538464, 0.11258541039487313, 0.6447751902773146),
    (2, 31, 1, 28): (0.02880184331797235, -0.12010301072189607, 0.17446960876125084),
    (1, 28, 19, 33): (-0.5400432900432901, -0.6947478980778228, -0.32068874440853834),
    (11, 37, 13, 37): (-0.05405405405405406, -0.2563497926032787, 0.15448142487203648),
    (13, 31, 13, 31): (0.0, -0.23240393834152168, 0.23240393834152168),
    (88, 131, 1, 37): (0.6447286981638127, 0.5051715875854155, 0.7224933987106562),
}

STRATIFIED_GOLDEN = [
    ([(6, 23, 0, 26), (4, 17, 0, 25)], 0.00015349639117153387),
    ([(2, 31, 0, 28), (3, 22, 0, 30)], 0.01893788131626341),
    ([(21, 26, 0, 34), (8, 27, 0, 37), (19, 33, 0, 31)], 3.8802554485304496e-22),
    ([(5, 13, 9, 47), (6, 13, 11, 47)], 0.0310009787709793),
]


@pytest.mark.parametrize("args", sorted(FISHER_GOLDEN))
def test_fisher_two_sided_matches_golden(args):
    assert S.fisher_exact_two_sided(*args) == pytest.approx(FISHER_GOLDEN[args], rel=1e-12, abs=1e-15)


def test_fisher_one_sided_matches_golden():
    assert S.fisher_exact_one_sided(8, 27, 0, 37) == pytest.approx(0.0005015797683589847, rel=1e-12)
    assert S.fisher_exact_one_sided(5, 13, 0, 18) == pytest.approx(0.007574553736956407, rel=1e-12)
    assert S.fisher_exact_one_sided(2, 31, 1, 28) == pytest.approx(0.5387738780030147, rel=1e-12)
    assert S.fisher_exact_one_sided(1, 28, 19, 33) == pytest.approx(0.9999999080970163, rel=1e-12)


@pytest.mark.parametrize("args", sorted(NEWCOMBE_GOLDEN))
def test_newcombe_matches_golden(args):
    got = S.newcombe_ci(*args)
    for g, w in zip(got, NEWCOMBE_GOLDEN[args]):
        assert g == pytest.approx(w, rel=1e-12, abs=1e-15)


@pytest.mark.parametrize("strata,want", STRATIFIED_GOLDEN)
def test_stratified_exact_matches_golden(strata, want):
    assert S.exact_stratified_one_sided(strata) == pytest.approx(want, rel=1e-12)


def test_exact_tests_properties():
    rng = random.Random(7)
    for _ in range(300):
        n1, n2 = rng.randint(0, 25), rng.randint(0, 25)
        k1, k2 = rng.randint(0, n1), rng.randint(0, n2)
        p = S.fisher_exact_two_sided(k1, n1, k2, n2)
        assert 0.0 < p <= 1.0
        # symmetric in the arms and in success/failure labels
        assert p == S.fisher_exact_two_sided(k2, n2, k1, n1)
        assert p == S.fisher_exact_two_sided(n1 - k1, n1, n2 - k2, n2)
        # one-sided tails: P(X >= k1) + P(X <= k1) = 1 + P(X = k1)
        up = S.fisher_exact_one_sided(k1, n1, k2, n2)
        down = S.fisher_exact_one_sided(n1 - k1, n1, n2 - k2, n2)
        assert up + down >= 1.0 - 1e-12
        # a single stratum reduces to the one-sided Fisher test
        assert S.exact_stratified_one_sided([(k1, n1, k2, n2)]) == pytest.approx(up, rel=1e-12)
        d, lo, hi = S.newcombe_ci(k1, n1, k2, n2) if n1 and n2 else (0.0, -1.0, 1.0)
        assert -1.0 <= lo <= d <= hi <= 1.0


def test_exact_tests_edges_and_types():
    assert S.fisher_exact_two_sided(0, 0, 0, 0) == 1.0
    assert S.fisher_exact_two_sided(3, 5, 0, 0) == 1.0
    assert S.newcombe_ci(1, 2, 0, 0) == (None, None, None)
    assert S.exact_stratified_one_sided([]) == 1.0
    for fn in (S.fisher_exact_two_sided, S.fisher_exact_one_sided, S.newcombe_ci):
        with pytest.raises(ValueError):
            fn(5, 4, 0, 1)
        with pytest.raises(ValueError):
            fn(-1, 4, 0, 1)
        with pytest.raises(TypeError):
            fn(1.0, 4, 0, 1)
        with pytest.raises(TypeError):
            fn(True, 4, 0, 1)


def test_fisher_matches_scipy_when_available():
    try:
        from scipy.stats import fisher_exact
    except Exception as e:  # noqa: BLE001 - scipy missing or unloadable on this interpreter
        pytest.skip(f"scipy.stats not importable here: {type(e).__name__}")
    rng = random.Random(11)
    for _ in range(200):
        n1, n2 = rng.randint(1, 30), rng.randint(1, 30)
        k1, k2 = rng.randint(0, n1), rng.randint(0, n2)
        want = fisher_exact([[k1, n1 - k1], [k2, n2 - k2]], alternative="two-sided")[1]
        assert math.isclose(S.fisher_exact_two_sided(k1, n1, k2, n2), want, rel_tol=1e-9, abs_tol=1e-14)
        want1 = fisher_exact([[k1, n1 - k1], [k2, n2 - k2]], alternative="greater")[1]
        assert math.isclose(S.fisher_exact_one_sided(k1, n1, k2, n2), want1, rel_tol=1e-9, abs_tol=1e-14)
