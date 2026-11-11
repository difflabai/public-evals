"""D3 scorer: references, agreement, consensus-wrong, the gate, and world-clustered intervals.

Ported from the pilot's ``d3_score.py`` (harness aaa04e5, ``scripts/bwm_latent/d3_score.py``;
the file itself is not shipped). Two reference sets, one per ruleset:

``paper`` (``reference_values_paper``): the pilot's references, defects kept (v7
Appendix E.7). Exponents come from theta.json and
profit from ``latent_engine.held_out_profit`` (800 paths, base seed 9001), both right;
revenue and probe end-customers come from the pilot's own re-walk
(``_revenue_and_customers_paper``, ``d3_score.py:104-138`` verbatim), which departs from
the engine four ways (seeds 9001 + k, a market factor decaying to 0, latent deviations
read as demand levels with a 0.05 floor, a power-law coupling); the spillover truth is
``1.1**g - 1``.

``v1`` (``reference_values_v1``): the engine's own held-out loop on the engine's seed
schedule (base seed 9001 x 1,000,003 + i), revenue, end-customers and profit from one
walk, and the spillover the engine implies for a +10% partner-marketing rise,
``exp(0.1 g) - 1``. The loop's profit is asserted equal to ``held_out_profit`` (the
equality check v7 E.7 says would have caught the defect).

Grading (``d3_score.py:54-62, 180-208``, unchanged): per world x figure over the
reported runs, agree when max - min <= 0.02 (exponents), <= 1.0 point (spillover), or <=
1% of |median| (forecasts); wrong when |median - truth| > 0.10, 5 points or 5% of |truth|;
"at null" when every spillover answer is 0 (reported separately, never pooled). The gate
(KILL-1) fires at agree >= 70% of cells; a world is retained when it agrees on <= 2 of
6 figures. Intervals: the unclustered Wilson the pilot printed and the world-clustered
bootstrap (``random.Random(20261006)``, B = 20000, [xs[int(.025B)], xs[int(.975B) - 1]],
a fresh generator per readout: see ``world_clustered_ci``). A world with fewer than 5
reports is labelled agree-k, k = its report count (v7 E.6).
"""

from __future__ import annotations

import itertools
import json
import math
import random
import threading
from pathlib import Path
from typing import Any

from .. import _paths
from .brief import FIGURE_NAMES, detect_probe_pair, pinned_plan

D3_GRADE_BASE_SEED = 9001
GRADE_N_SEEDS = 800
EQUALITY_CHECK_SEEDS = 100      # reference_values_v1's walk-vs-engine profit check (first 100 paths)
KILL1_THRESHOLD = 0.7
RETAIN_MAX_AGREE = 2
BOOT_SEED = 20261006
BOOT_B = 20000
RULESET_PAPER, RULESET_V1 = "paper", "v1"
SPILLOVER = "cross_segment_spillover_pct"
FORECASTS = ("pinned_plan_total_profit_usd", "pinned_plan_total_revenue_usd",
             "pinned_plan_end_customers_probe_segment")
STRUCTURAL = ("price_elasticity_probe_segment", "marketing_exponent_probe_segment")

FIGURES = [
    # name, kind, agree tolerance, wrong tolerance
    ("price_elasticity_probe_segment",          "abs", 0.02, 0.10),
    ("marketing_exponent_probe_segment",        "abs", 0.02, 0.10),
    ("cross_segment_spillover_pct",             "abs", 1.0,  5.0),
    ("pinned_plan_total_profit_usd",            "rel", 0.01, 0.05),
    ("pinned_plan_total_revenue_usd",           "rel", 0.01, 0.05),
    ("pinned_plan_end_customers_probe_segment", "rel", 0.01, 0.05),
]
assert [f[0] for f in FIGURES] == FIGURE_NAMES

_LOCK = threading.Lock()
_REF_CACHE: dict = {}


def theta(world_id: str) -> dict:
    return json.loads(_paths.answer_key(world_id, "theta.json").read_text())


def _answer_key(world_id: str):
    from .. import latent_engine as le
    th = theta(world_id)
    ms = json.loads(_paths.answer_key(world_id, "mstar.json").read_text())
    ls = ms["latent_state"]
    return th, le.world_from_dict(th), (float(ls[0]), [float(x) for x in ls[1]]), \
        [float(x) for x in ms["cust_final"]]


def _probe_gamma(th, probe):
    segs = list(th["segment_order"])
    i = segs.index(probe)
    C = th["coupling"]
    partner_j = max(range(len(segs)), key=lambda j: abs(C[i][j]) if j != i else -1)
    return i, C[i][partner_j]


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

def reference_values_paper(world_id: str, probe: str) -> dict:
    """The pilot's reference_values (d3_score.py:70-101), defects included (v7 E.7)."""
    from .. import latent_engine as le
    th, world, latent_state, cust0 = _answer_key(world_id)
    i = list(th["segment_order"]).index(probe)
    plan = pinned_plan(th)
    prof, _se = le.held_out_profit(world, plan, latent_state, cust0,
                                   n_seeds=GRADE_N_SEEDS, base_seed=D3_GRADE_BASE_SEED)[:2]
    rev, cust_end = _revenue_and_customers_paper(world, plan, latent_state, cust0, i)
    _, gamma = _probe_gamma(th, probe)
    spill = (1.10 ** gamma - 1.0) * 100.0
    return {
        "price_elasticity_probe_segment": th["segments"][probe]["elast"],
        "marketing_exponent_probe_segment": th["segments"][probe]["mktexp"],
        "cross_segment_spillover_pct": spill,
        "pinned_plan_total_profit_usd": prof,
        "pinned_plan_total_revenue_usd": rev,
        "pinned_plan_end_customers_probe_segment": cust_end,
    }


def _revenue_and_customers_paper(world, plan, latent_state, cust0, probe_idx):
    """d3_score._revenue_and_customers, VERBATIM (the v7 E.7 defect: not the engine's walk), except that the two
    means sum left to right (``lsum``), as the pilot's Python did."""
    import numpy as np
    K, S = world.held_out, world.n()
    revs, cends = [], []
    for k in range(GRADE_N_SEEDS):
        rng = np.random.default_rng(D3_GRADE_BASE_SEED + k)
        f, Lv = latent_state[0], list(latent_state[1])
        cust = list(cust0)
        total_rev = 0.0
        for t in range(K):
            f = world.factor_rho * f + rng.normal(0.0, world.factor_sigma)
            for s_i, seg in enumerate(world.segments):
                Lv[s_i] = max(0.05, seg.rho * Lv[s_i]
                              + seg.factor_load * f + rng.normal(0.0, seg.sigma))
            for s_i, seg in enumerate(world.segments):
                mkt = plan[seg.id]["marketing"]
                price = plan[seg.id]["price"]
                coup = 1.0
                for j, other in enumerate(world.segments):
                    g = world.coupling[s_i][j]
                    if g:
                        coup *= (plan[other.id]["marketing"] / other.refmkt) ** g
                sign = (seg.base_signups * Lv[s_i]
                        * (mkt / seg.refmkt) ** seg.mktexp
                        * (price / seg.refprice) ** seg.elast * coup)
                ch = min(0.5, max(0.005, seg.churn0 * price ** seg.churn_price_exp))
                cust[s_i] = cust[s_i] * (1.0 - ch) + sign
                total_rev += cust[s_i] * seg.acv * price
        revs.append(total_rev)
        cends.append(cust[probe_idx])
    # left-to-right sums: the pilot ran on a Python whose ``sum`` was not compensated (3.12's moves the last bit)
    from ..latent_engine import lsum
    return float(lsum(revs) / len(revs)), float(lsum(cends) / len(cends))


def reference_values_v1(world_id: str, probe: str) -> dict:
    """Engine-consistent references (v7 E.7 correction): one walk of latent_engine's own held-out
    loop gives profit, revenue and probe end-customers; spillover = exp(0.1 g) - 1."""
    import numpy as np

    from .. import latent_engine as le
    th, world, latent_state, cust0 = _answer_key(world_id)
    i, gamma = _probe_gamma(th, probe)
    plan = pinned_plan(th)
    mkt, price = le._plan_arrays(world, plan)
    K, S = world.held_out, world.n()
    P, Rv, C = [], [], []
    for k in range(GRADE_N_SEEDS):
        rng = np.random.default_rng(D3_GRADE_BASE_SEED * 1_000_003 + k)
        L, _ = le._latent_path(world, rng, K, state0=latent_state)
        prev = np.asarray(cust0, float).copy()
        rv = pf = 0.0
        for t in range(K):
            cu = np.empty(S)
            for s in range(S):
                seg = world.segments[s]
                su = le._signups(world, s, t, L[t, s], mkt[t], price[t])
                ch = le._churn(world, s, price[t, s])
                cu[s] = prev[s] * (1 - ch) + su
                r = cu[s] * seg.acv * price[t, s]
                rv += r
                pf += r * seg.gm - mkt[t, s] - seg.fixed
            prev = cu
        P.append(pf)
        Rv.append(rv)
        C.append(prev[i])
    profit = float(np.mean(P))
    # The E.7 equality check: the walk's profit on its first paths must equal the engine's own
    # held_out_profit on the same paths (a subset keeps it cheap; the same paths, so any walk that
    # departs from the engine fails it).
    m = EQUALITY_CHECK_SEEDS
    engine = le.held_out_profit(world, plan, latent_state, cust0, n_seeds=m, base_seed=D3_GRADE_BASE_SEED)[0]
    walk = float(np.mean(P[:m]))
    if abs(walk - engine) > 1e-9 * max(1.0, abs(engine)):
        raise AssertionError(f"D3 v1 references for {world_id}: the walk's profit {walk!r} differs from "
                             f"latent_engine.held_out_profit {engine!r} on the first {m} paths (the E.7 check)")
    return {
        "price_elasticity_probe_segment": th["segments"][probe]["elast"],
        "marketing_exponent_probe_segment": th["segments"][probe]["mktexp"],
        "cross_segment_spillover_pct": (math.exp(0.1 * gamma) - 1.0) * 100.0,
        "pinned_plan_total_profit_usd": profit,
        "pinned_plan_total_revenue_usd": float(np.mean(Rv)),
        "pinned_plan_end_customers_probe_segment": float(np.mean(C)),
    }


def references(world_id: str, probe: str | None = None, ruleset: str = RULESET_V1) -> dict:
    """Cached references for one world (probe defaults to detect_probe_pair)."""
    if ruleset not in (RULESET_PAPER, RULESET_V1):
        raise ValueError(f"unknown D3 ruleset {ruleset!r} (paper, v1)")
    probe = probe or detect_probe_pair(theta(world_id))[0]
    key = (world_id, probe, ruleset, str(_paths.private()))
    with _LOCK:
        if key in _REF_CACHE:
            return dict(_REF_CACHE[key])
    fn = reference_values_paper if ruleset == RULESET_PAPER else reference_values_v1
    ref = fn(world_id, probe)
    with _LOCK:
        _REF_CACHE[key] = dict(ref)
    return ref


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def figure_values(runs, name) -> list[float]:
    vals = [r["figures"].get(name) for r in runs]
    return [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]


def grade_values(vals, truth, kind, tol_a, tol_w, name, mult: float = 1.0) -> dict | None:
    """One cell (d3_score.py:180-208): None when fewer than 2 values were reported."""
    if len(vals) < 2:
        return None
    tol_a = tol_a * mult
    lo, hi = min(vals), max(vals)
    med = sorted(vals)[len(vals) // 2]
    t = truth
    if kind == "abs":
        agree = (hi - lo) <= tol_a
        wrong = abs(med - t) > tol_w
        spread = hi - lo
        dev = abs(med - t)
    else:
        agree = (hi - lo) <= tol_a * abs(med) if med else False
        wrong = abs(med - t) > tol_w * abs(t) if t else False
        spread = (hi - lo) / abs(med) if med else float("inf")
        dev = abs(med - t) / abs(t) if t else float("inf")
    at_null = (name == SPILLOVER and all(abs(v) < 1e-9 for v in vals))
    return {"n": len(vals), "agree5": agree, "wrong": wrong, "at_null": at_null, "spread": spread,
            "median": med, "truth": t, "dev": dev, "lo": lo, "hi": hi}


def _within(vals, kind, tol) -> bool:
    lo, hi = min(vals), max(vals)
    med = sorted(vals)[len(vals) // 2]
    return (hi - lo) <= tol if kind == "abs" else ((hi - lo) <= tol * abs(med) if med else False)


def max_agree_subset(vals, kind, tol) -> int:
    """Largest k such that some k-subset of the runs lies within tolerance (paper v7's
    ">= 4 of 5" rule; median of the subset for the relative figures)."""
    for k in range(len(vals), 0, -1):
        for c in itertools.combinations(vals, k):
            if _within(list(c), kind, tol):
                return k
    return 1


def grade_world(runs, ref, mult: float = 1.0, kmin: int | None = None) -> dict:
    out = {}
    for name, kind, tol_a, tol_w in FIGURES:
        vals = figure_values(runs, name)
        g = grade_values(vals, ref[name], kind, tol_a, tol_w, name, mult)
        if g is not None and kmin is not None:
            g["agree5"] = max_agree_subset(vals, kind, tol_a * mult) >= kmin
        out[name] = g
    return out


def gate(by_world: dict[str, list[dict]], refs: dict[str, dict], mult: float = 1.0,
         kmin: int | None = None, ci: bool = True) -> dict:
    """Agreement, consensus-wrong (real / at null), retention and per-world labels for one readout.
    ``by_world``: world -> its runs' submissions ({"figures": {...}}); ``refs``: world -> references."""
    worlds = sorted(by_world, key=lambda x: int(x[1:]))
    per_world, agree = {}, 0
    cells = real = null = 0
    real_cells, null_cells = [], []
    for w in worlds:
        g = grade_world(by_world[w], refs[w], mult, kmin)
        n_reports = max((c["n"] for c in g.values() if c is not None), default=0)
        k = sum(1 for c in g.values() if c is not None and c["agree5"])
        per_world[w] = {"n_runs": len(by_world[w]), "n_reports": n_reports,
                        "label": f"agree-{n_reports}", "agree": k, "figures": g}
        for name, c in g.items():
            if c is None:
                continue
            cells += 1
            agree += 1 if c["agree5"] else 0
            if c["agree5"] and c["wrong"]:
                if c["at_null"]:
                    null += 1
                    null_cells.append(f"{w}:{name}")
                else:
                    real += 1
                    real_cells.append(f"{w}:{name}")
    p, lo, hi = wilson(agree, cells)
    retained = [w for w in worlds if per_world[w]["agree"] <= RETAIN_MAX_AGREE]
    full = all(per_world[w]["n_reports"] >= 5 for w in worlds)
    return {"n_worlds": len(worlds), "n_sessions": sum(len(v) for v in by_world.values()),
            "cells": cells, "agree": agree, "rate": p, "wilson_ci95": [lo, hi],
            "world_clustered_ci95": (world_clustered_ci({w: per_world[w]["agree"] for w in worlds}, 6)
                                     if ci else None),
            "kill1_threshold": KILL1_THRESHOLD, "kill1_fires": bool(cells and agree / cells >= KILL1_THRESHOLD),
            "label": "agree-5" if full else "agree-k (k = reports per world; fewer than 5 in "
                     + ", ".join(w for w in worlds if per_world[w]["n_reports"] < 5) + ")",
            "consensus_wrong_real": real, "consensus_wrong_at_null": null,
            "consensus_wrong_real_cells": real_cells, "consensus_wrong_at_null_cells": null_cells,
            "retained": retained, "per_world": per_world}


def by_figure(gate_out: dict, figures=FIGURE_NAMES) -> dict:
    return {name: sum(1 for pw in gate_out["per_world"].values()
                      if pw["figures"].get(name) and pw["figures"][name]["agree5"]) for name in figures}


def cut(gate_out: dict, figures, seed: int = BOOT_SEED) -> dict:
    """Agreement over a subset of figures with its world-clustered interval."""
    per = {w: sum(1 for n in figures if pw["figures"].get(n) and pw["figures"][n]["agree5"])
           for w, pw in gate_out["per_world"].items()}
    n = len(figures) * len(per)
    k = sum(per.values())
    return {"agree": k, "cells": n, "rate": k / n if n else None,
            "world_clustered_ci95": world_clustered_ci(per, len(figures), seed=seed)}


# ---------------------------------------------------------------------------
# Intervals
# ---------------------------------------------------------------------------

def wilson(k, n, z=1.96):
    """The pilot's unclustered Wilson interval (the world-clustered one is world_clustered_ci)."""
    if n == 0:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def world_clustered_ci(per_world_agree: dict[str, int], n_figures: int, B: int = BOOT_B,
                       seed: int = BOOT_SEED) -> list[float]:
    """World-clustered bootstrap of the agreement rate: resample the worlds with replacement
    (random.Random(seed), B draws), rate = agreeing cells / (n_figures x worlds), interval
    [xs[int(.025B)], xs[int(.975B) - 1]] (as in paper v7). A FRESH generator per
    readout, so a readout's interval does not depend on which readouts were computed before it."""
    W = sorted(per_world_agree, key=lambda x: int(x[1:]))
    if not W or not n_figures:
        return [None, None]
    rng = random.Random(seed)
    xs = []
    for _ in range(B):
        ws = [rng.choice(W) for _ in W]
        xs.append(sum(per_world_agree[w] for w in ws) / (n_figures * len(ws)))
    xs.sort()
    return [xs[int(.025 * B)], xs[int(.975 * B) - 1]]


# ---------------------------------------------------------------------------
# One attempt of the runnable arm
# ---------------------------------------------------------------------------

def score_attempt(deliverables_dir: Path, world_id: str, ruleset: str = RULESET_V1) -> dict:
    """Per-attempt details: the six figures read as strict numbers (missing -> missing_figures),
    each one's deviation from the references, per-figure confidence and the first action as
    reported, and the session's own plan scored with the eval-6 D1 scorer (same ruleset)."""
    from dbwm.core.claims import parse_claims_detailed

    from ..score_d1 import score_d1
    deliverables_dir = Path(deliverables_dir)
    th = theta(world_id)
    probe, partner = detect_probe_pair(th)
    ref = references(world_id, probe, ruleset)
    cpath = deliverables_dir / "claims.json"
    cp = parse_claims_detailed(cpath, {n: "number" for n in FIGURE_NAMES}, required=FIGURE_NAMES)
    raw = cp.raw if isinstance(cp.raw, dict) else {}
    figures, missing, per_fig = {}, [], {}
    for name, kind, _tol_a, tol_w in FIGURES:
        d = cp.strict.get(name)
        v = float(d) if d is not None and math.isfinite(float(d)) else None
        figures[name] = v
        if v is None:
            missing.append(name)
            continue
        t = ref[name]
        dev = abs(v - t) if kind == "abs" else (abs(v - t) / abs(t) if t else float("inf"))
        per_fig[name] = {"value": v, "reference": t, "deviation": dev, "deviation_kind": kind,
                         "wrong": dev > tol_w}
    conf = raw.get("confidence") if isinstance(raw.get("confidence"), dict) else None
    plan = score_d1(deliverables_dir, world_id, ruleset)
    prices = [s.get("committed_price") for s in plan["per_segment"].values()]
    return {
        "world_id": world_id, "ruleset": ruleset, "probe_segment": probe, "partner_segment": partner,
        "figures": figures, "missing_figures": missing, "complete_report": not missing,
        "per_figure": per_fig, "references": ref,
        "reference_kind": "engine-consistent (v7 E.7)" if ruleset == RULESET_V1 else "pilot (defective, v7 E.7)",
        "confidence": conf, "first_action": raw.get("first_action") if isinstance(raw.get("first_action"), str)
        else None,
        "claims_parse_status": cp.status, "claims_errors": list(cp.errors),
        "plan": {"no_plan": plan["no_plan"], "no_plan_reason": plan["no_plan_reason"],
                 "true_profit": plan["true_profit"], "gap_to_mstar_pct": plan["gap_to_mstar_pct"],
                 "segments_price_raised": sum(1 for p in prices if p is not None and p > 1.0 + 1e-9),
                 "segments_price_at_cap": sum(1 for p in prices if p is not None and p >= 1.3 - 1e-9),
                 "marketing_bounded": plan.get("marketing_bounded", []),
                 "budget_projected": plan.get("budget_projected", False)},
    }


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=float))
