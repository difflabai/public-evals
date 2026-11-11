"""Small, deterministic statistics used by every eval's report.

Stdlib only. Every resampling routine draws from its OWN ``random.Random``
instance and iterates clusters and strata in sorted order, so results depend
only on (data, B, seed): not on call order, not on PYTHONHASHSEED, not on the
interpreter's set-iteration order.

A cluster resample in which an arm comes up empty is dropped and counted, never
dropped silently: ``cluster_bootstrap_diff`` returns the count.

Eval modules that must keep a paper routine bit-for-bit (eval 6's numpy-RNG
clustered bootstrap) keep a private copy of it in their own package. These
functions are the release statistics.

Percentile convention (shared by all bootstrap helpers, taken from the
paper's eval-1 and eval-2 reports): sort the B statistics and take
``s[int((alpha/2)*m)]`` and ``s[min(m-1, int((1-alpha/2)*m))]`` where ``m`` is
the number of retained statistics. No interpolation.
"""

from __future__ import annotations

import math
import random
from fractions import Fraction
from typing import Any, Hashable, Iterable, Mapping, Sequence

__all__ = [
    "wilson",
    "newcombe_ci",
    "fisher_exact_two_sided",
    "fisher_exact_one_sided",
    "exact_stratified_one_sided",
    "percentile_bootstrap_mean",
    "percentile_bootstrap_diff",
    "cluster_bootstrap_diff",
    "cluster_bootstrap_mean",
    "kendall_tau_b",
    "ci_excludes_zero",
]


# ---------------------------------------------------------------------------
# Binomial interval
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None, float | None]:
    """Wilson score interval: ``(p, lo, hi)``; ``(None, None, None)`` when n == 0.

    Same formula as the published eval-2 report (confirm_report.py:43-50).
    """
    if isinstance(k, bool) or isinstance(n, bool) or not isinstance(k, int) or not isinstance(n, int):
        raise TypeError(f"wilson needs integer counts, got k={k!r} n={n!r}")
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"wilson needs 0 <= k <= n, got k={k} n={n}")
    if n == 0:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def newcombe_ci(k1: int, n1: int, k2: int, n2: int,
                z: float = 1.96) -> tuple[float | None, float | None, float | None]:
    """Newcombe's hybrid score interval for p1 - p2 (two independent proportions): ``(d, lo, hi)``.

    Built from the two Wilson intervals above (method 10 of Newcombe 1998):
    ``lo = d - sqrt((p1-l1)^2 + (u2-p2)^2)``, ``hi = d + sqrt((u1-p1)^2 + (p2-l2)^2)``.
    The formula paper v7 uses.
    ``(None, None, None)`` when either n is 0.
    """
    _check_two_arms(k1, n1, k2, n2, "newcombe_ci")
    if n1 == 0 or n2 == 0:
        return (None, None, None)
    p1, l1, u1 = wilson(k1, n1, z)
    p2, l2, u2 = wilson(k2, n2, z)
    d = p1 - p2
    return (d, d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


# ---------------------------------------------------------------------------
# Exact tests on 2x2 tables (hypergeometric, exact integer arithmetic)
# ---------------------------------------------------------------------------

def _check_two_arms(k1: int, n1: int, k2: int, n2: int, who: str) -> None:
    for name, v in (("k1", k1), ("n1", n1), ("k2", k2), ("n2", n2)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise TypeError(f"{who} needs integer counts, got {name}={v!r}")
    if not (0 <= k1 <= n1 and 0 <= k2 <= n2):
        raise ValueError(f"{who} needs 0 <= k <= n in both arms, got {k1}/{n1} and {k2}/{n2}")


def _hypergeometric(k1: int, n1: int, k2: int, n2: int) -> tuple[int, dict[int, int]]:
    """(C(n1+n2, K), {x: C(n1, x) * C(n2, K - x)}) over the support of arm 1's successes, K = k1 + k2."""
    big_k = k1 + k2
    lo, hi = max(0, big_k - n2), min(n1, big_k)
    return math.comb(n1 + n2, big_k), {x: math.comb(n1, x) * math.comb(n2, big_k - x) for x in range(lo, hi + 1)}


def fisher_exact_two_sided(k1: int, n1: int, k2: int, n2: int, *, rel_tol: float = 1e-9) -> float:
    """Two-sided Fisher exact p for k1/n1 vs k2/n2 (margins fixed).

    Sums the probabilities of every table no more likely than the observed one,
    with the relative tolerance paper v7 uses (``p <= p_obs * (1 + 1e-9)``;
    scipy and R use 1e-7). Weights are exact integers, so ties are exact and only a
    genuine near-tie depends on ``rel_tol``. An empty arm (n = 0) gives 1.0.
    """
    _check_two_arms(k1, n1, k2, n2, "fisher_exact_two_sided")
    total, w = _hypergeometric(k1, n1, k2, n2)
    cut = w[k1] * (1 + Fraction(rel_tol))
    return float(Fraction(sum(v for v in w.values() if v <= cut), total))


def fisher_exact_one_sided(k1: int, n1: int, k2: int, n2: int) -> float:
    """One-sided Fisher exact p that arm 1's rate exceeds arm 2's: P(X >= k1) under the hypergeometric null."""
    _check_two_arms(k1, n1, k2, n2, "fisher_exact_one_sided")
    total, w = _hypergeometric(k1, n1, k2, n2)
    return float(Fraction(sum(v for x, v in w.items() if x >= k1), total))


def exact_stratified_one_sided(strata: Iterable[tuple[int, int, int, int]]) -> float:
    """Exact conditional test of a common odds ratio of 1 against arm 1 > arm 2 across 2x2 strata.

    ``strata`` = [(k1, n1, k2, n2), ...] (e.g. one per world). The statistic is
    arm 1's total successes; its null distribution is the convolution of the
    per-stratum hypergeometrics (margins fixed within each stratum). Returns
    P(T >= T_obs), exactly (paper v7 Appendix C).
    """
    dist: dict[int, Fraction] = {0: Fraction(1)}
    t_obs = 0
    for s in strata:
        k1, n1, k2, n2 = s
        _check_two_arms(k1, n1, k2, n2, "exact_stratified_one_sided")
        t_obs += k1
        total, w = _hypergeometric(k1, n1, k2, n2)
        nd: dict[int, Fraction] = {}
        for t, pt in dist.items():
            for x, wx in w.items():
                nd[t + x] = nd.get(t + x, Fraction(0)) + pt * Fraction(wx, total)
        dist = nd
    return float(sum((p for t, p in dist.items() if t >= t_obs), Fraction(0)))


# ---------------------------------------------------------------------------
# Plain percentile bootstrap (eval-1 report semantics)
# ---------------------------------------------------------------------------

def _clean(values: Iterable[Any]) -> list[float]:
    out = []
    for v in values:
        if v is None:
            continue
        if isinstance(v, bool):
            out.append(1.0 if v else 0.0)
        elif isinstance(v, (int, float)):
            out.append(float(v))
        else:
            raise TypeError(f"bootstrap values must be bool/int/float/None, got {type(v).__name__}")
    return out


def _rng(seed: int | None, rng: random.Random | None) -> random.Random:
    if rng is not None:
        return rng
    if seed is None:
        raise ValueError("a seed is required (or pass rng=random.Random(seed))")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError(f"seed must be an int, got {seed!r}")
    return random.Random(seed)


def _percentiles(stats: list[float], alpha: float) -> tuple[float | None, float | None]:
    if not stats:
        return (None, None)
    stats.sort()
    m = len(stats)
    lo = stats[int((alpha / 2) * m)]
    hi = stats[min(m - 1, int((1 - alpha / 2) * m))]
    return (lo, hi)


def percentile_bootstrap_mean(
    values: Sequence[Any],
    B: int = 2000,
    seed: int | None = None,
    *,
    alpha: float = 0.05,
    rng: random.Random | None = None,
) -> tuple[float | None, float | None]:
    """Percentile CI ``(lo, hi)`` for the mean of a 0/1 or numeric list (None values dropped).

    Draw sequence identical to the published eval-1 ``bootstrap_ci_rate``
    (bwm_theory/report.py:270-283). Passing a shared ``rng`` reproduces that
    report's habit of threading one ``random.Random(12345)`` through several
    calls in a fixed order; passing ``seed`` gives an independent stream.
    """
    if B <= 0:
        raise ValueError("B must be positive")
    vals = _clean(values)
    n = len(vals)
    if n == 0:
        return (None, None)
    r = _rng(seed, rng)
    means = []
    for _ in range(B):
        sample = [vals[r.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int((alpha / 2) * B)]
    hi = means[min(B - 1, int((1 - alpha / 2) * B))]
    return (lo, hi)


def percentile_bootstrap_diff(
    a: Sequence[Any],
    b: Sequence[Any],
    B: int = 2000,
    seed: int | None = None,
    *,
    alpha: float = 0.05,
    rng: random.Random | None = None,
) -> tuple[float | None, float | None, float | None]:
    """``(point, lo, hi)`` for mean(a) - mean(b), resampling each group independently.

    Draw sequence identical to the published eval-1 ``bootstrap_ci_difference``
    (bwm_theory/report.py:286-305). ``(None, None, None)`` if either group is empty.
    """
    if B <= 0:
        raise ValueError("B must be positive")
    xa, xb = _clean(a), _clean(b)
    na, nb = len(xa), len(xb)
    if na == 0 or nb == 0:
        return (None, None, None)
    r = _rng(seed, rng)
    point = (sum(xa) / na) - (sum(xb) / nb)
    diffs = []
    for _ in range(B):
        sa = sum(xa[r.randrange(na)] for _ in range(na)) / na
        sb = sum(xb[r.randrange(nb)] for _ in range(nb)) / nb
        diffs.append(sa - sb)
    diffs.sort()
    lo = diffs[int((alpha / 2) * B)]
    hi = diffs[min(B - 1, int((1 - alpha / 2) * B))]
    return (point, lo, hi)


# ---------------------------------------------------------------------------
# Cluster (world) bootstrap
# ---------------------------------------------------------------------------

def _sorted_keys(keys: Iterable[Hashable]) -> list[Hashable]:
    keys = list(keys)
    try:
        return sorted(keys)
    except TypeError:  # mixed key types: order by type name, then repr
        return sorted(keys, key=lambda k: (type(k).__name__, repr(k)))


def _strata(v: Any) -> list[list[float]]:
    """A cluster's trials: a flat sequence (one stratum) or a mapping stratum -> sequence."""
    if v is None:
        return []
    if isinstance(v, Mapping):
        return [_clean(v[k]) for k in _sorted_keys(v.keys())]
    if isinstance(v, (str, bytes)):
        raise TypeError("cluster values must be a sequence of trial values, not a string")
    return [_clean(v)]


def cluster_bootstrap_diff(
    a: Mapping[Hashable, Any],
    b: Mapping[Hashable, Any],
    B: int = 2000,
    seed: int | None = None,
    *,
    alpha: float = 0.05,
    within: bool = True,
) -> tuple[float | None, float | None, float | None, int]:
    """World-clustered bootstrap of pooled mean(a) - pooled mean(b).

    ``a`` and ``b`` map a cluster id (world, scenario, ...) to that cluster's
    trial values for the arm (bool/0-1/float; None dropped). A cluster value may
    instead be a mapping ``stratum -> values`` (e.g. condition -> values when
    an "arm" pools several conditions); strata are then resampled separately,
    as the published eval-2 report resampled each condition within a world.

    Each replicate draws len(clusters) clusters with replacement from the
    sorted union of cluster ids, then (``within=True``, the eval-2 two-stage
    scheme) resamples each drawn cluster's trials with replacement per
    stratum, and recomputes the pooled difference. A replicate in which either
    arm has no trials is dropped and counted.

    Returns ``(point, lo, hi, dropped)``. ``point`` is the pooled difference
    on the observed data; ``(None, None, None, 0)`` if either arm is empty;
    ``lo``/``hi`` are None if every replicate was dropped.
    """
    if B <= 0:
        raise ValueError("B must be positive")
    r = _rng(seed, None)
    clusters = _sorted_keys(set(a) | set(b))
    sa_ = {c: _strata(a.get(c)) for c in clusters}
    sb_ = {c: _strata(b.get(c)) for c in clusters}

    def pooled(strata_by_cluster: Mapping[Hashable, list[list[float]]]) -> tuple[float, int]:
        s, n = 0.0, 0
        for c in clusters:
            for pool in strata_by_cluster[c]:
                s += sum(pool)
                n += len(pool)
        return s, n

    ta, na = pooled(sa_)
    tb, nb = pooled(sb_)
    if na == 0 or nb == 0:
        return (None, None, None, 0)
    point = ta / na - tb / nb

    nc = len(clusters)
    diffs: list[float] = []
    dropped = 0
    for _ in range(B):
        sum_a = sum_b = 0.0
        n_a = n_b = 0
        for _k in range(nc):
            c = clusters[r.randrange(nc)]
            for pool in sa_[c]:
                m = len(pool)
                if within:
                    for _j in range(m):
                        sum_a += pool[r.randrange(m)]
                else:
                    sum_a += sum(pool)
                n_a += m
            for pool in sb_[c]:
                m = len(pool)
                if within:
                    for _j in range(m):
                        sum_b += pool[r.randrange(m)]
                else:
                    sum_b += sum(pool)
                n_b += m
        if n_a == 0 or n_b == 0:
            dropped += 1
            continue
        diffs.append(sum_a / n_a - sum_b / n_b)
    lo, hi = _percentiles(diffs, alpha)
    return (point, lo, hi, dropped)


def cluster_bootstrap_mean(
    values: Mapping[Hashable, Any],
    B: int = 2000,
    seed: int | None = None,
    *,
    alpha: float = 0.05,
    within: bool = False,
) -> tuple[float | None, float | None, float | None]:
    """``(mean, lo, hi)`` of the pooled mean with clusters resampled with replacement.

    Default ``within=False`` resamples whole clusters only (the shape of eval
    6's D2 world-clustered bootstrap, d2_signals.py:435-449, though that code
    used numpy's RNG and linear-interpolated percentiles; reproduce it from a
    private copy when bit-for-bit numbers are needed).
    """
    if B <= 0:
        raise ValueError("B must be positive")
    r = _rng(seed, None)
    clusters = _sorted_keys(values.keys())
    strata = {c: _strata(values[c]) for c in clusters}
    total = sum(sum(p) for c in clusters for p in strata[c])
    n = sum(len(p) for c in clusters for p in strata[c])
    if n == 0:
        return (None, None, None)
    nc = len(clusters)
    means: list[float] = []
    for _ in range(B):
        s, m_n = 0.0, 0
        for _k in range(nc):
            c = clusters[r.randrange(nc)]
            for pool in strata[c]:
                m = len(pool)
                if within:
                    for _j in range(m):
                        s += pool[r.randrange(m)]
                else:
                    s += sum(pool)
                m_n += m
        if m_n:
            means.append(s / m_n)
    lo, hi = _percentiles(means, alpha)
    return (total / n, lo, hi)


def ci_excludes_zero(lo: float | None, hi: float | None) -> bool:
    if lo is None or hi is None:
        return False
    return lo > 0 or hi < 0


# ---------------------------------------------------------------------------
# Rank correlation
# ---------------------------------------------------------------------------

def kendall_tau_b(x: Sequence[float], y: Sequence[float]) -> float:
    """Kendall's tau-b with tie correction; the statistic scipy.stats.kendalltau returns by default.

    O(n^2) exact pair counting (inputs here are short candidate lists). Returns
    NaN exactly where scipy does: fewer than two observations, a NaN in either
    input, or a constant input (every pair tied). The published D2 code mapped
    that NaN to 0.0 (d2_signals.py:399-402); callers that need the paper's
    convention do the same explicitly.
    """
    xs, ys = list(x), list(y)
    if len(xs) != len(ys):
        raise ValueError(f"x and y must have the same length ({len(xs)} != {len(ys)})")
    n = len(xs)
    if n < 2:
        return float("nan")
    for v in xs + ys:
        if isinstance(v, float) and math.isnan(v):
            return float("nan")
    tot = n * (n - 1) // 2
    con_minus_dis = 0
    xtie = ytie = 0
    for i in range(n - 1):
        xi, yi = xs[i], ys[i]
        for j in range(i + 1, n):
            dx = (xi > xs[j]) - (xi < xs[j])
            dy = (yi > ys[j]) - (yi < ys[j])
            if dx == 0:
                xtie += 1
            if dy == 0:
                ytie += 1
            con_minus_dis += dx * dy
    if xtie == tot or ytie == tot:
        return float("nan")
    tau = con_minus_dis / math.sqrt(tot - xtie) / math.sqrt(tot - ytie)
    return min(1.0, max(-1.0, tau))
