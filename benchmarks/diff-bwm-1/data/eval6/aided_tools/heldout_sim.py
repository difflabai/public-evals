"""Held-out horizon simulator for a fitted subscription-business model.

Forward-simulates a plan over the decision horizon from a given starting latent
demand state and customer count, under the parameters in a fitted model file
(fitted_model.json, key "fitted_world"). Latent demand per segment is
    L_s[t] = clip(1 + factor_load_s * (F[t] - 1) + u_s[t], 0.2, 3.0)
with a shared AR(1) factor F (factor_rho, factor_sigma) and idiosyncratic AR(1)
terms u_s (rho, sigma). Each quarter:
    signups_s = base_signups * L_s * (mkt_s/refmkt)^mktexp * (price_s/refprice)^elast
                * exp(sum_j coupling[s][j] * (mkt_j/refmkt_j - 1))
    churn_s   = clip(churn0 * (price_s/refprice)^churn_price_exp, 0.005, 0.5)
    customers = customers_prev * (1 - churn_s) + signups_s
    profit_s  = customers * acv * price_s * gm - mkt_s - fixed
Price is clamped to [0.7, 1.3] x refprice and marketing is floored at 0.

Used by model_sim.py; importable from your own scripts (``import heldout_sim``).
Requires numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Fields the held-out dynamics use. Any other keys in a model file are ignored.
SEG_KEYS = ("base_signups", "acv", "mktexp", "elast", "churn0",
            "churn_price_exp", "rho", "sigma", "factor_load", "refmkt", "refprice",
            "gm", "fixed")

PRICE_LO, PRICE_HI = 0.7, 1.3


@dataclass
class Segment:
    id: str
    base_signups: float
    acv: float
    mktexp: float
    elast: float
    churn0: float
    churn_price_exp: float
    rho: float
    sigma: float
    factor_load: float
    refmkt: float
    refprice: float
    gm: float
    fixed: float


@dataclass
class Model:
    id: str
    segments: list
    coupling: list            # coupling[s][j]: marketing in j shifts demand in s
    held_out: int = 8
    factor_rho: float = 0.7
    factor_sigma: float = 0.16

    def n(self):
        return len(self.segments)


def model_from_dict(d):
    """Build a Model from a fitted_model.json "fitted_world" dict."""
    segs = []
    for sid, sv in d["segments"].items():
        kw = {k: float(sv[k]) for k in SEG_KEYS if k != "refprice"}
        kw["refprice"] = float(sv.get("refprice", 1.0))
        segs.append(Segment(id=sid, **kw))
    order = d.get("segment_order") or list(d["segments"].keys())
    segs = sorted(segs, key=lambda s: order.index(s.id))
    return Model(id=d["id"], segments=segs, coupling=d["coupling"],
                 held_out=int(d["held_out"]),
                 factor_rho=float(d.get("factor_rho", 0.7)),
                 factor_sigma=float(d.get("factor_sigma", 0.16)))


def _latent_path(model, rng, T, state0):
    """Latent demand continuing from state0 = (F_prev, u_prev[S]). Returns (L[T,S], (F, u))."""
    S = model.n()
    L = np.empty((T, S))
    F_prev, u_prev = float(state0[0]), np.asarray(state0[1], float).copy()
    for t in range(T):
        F = 1.0 + model.factor_rho * (F_prev - 1.0) + rng.normal(0.0, model.factor_sigma)
        u = np.empty(S)
        for s in range(S):
            seg = model.segments[s]
            u[s] = seg.rho * u_prev[s] + rng.normal(0.0, seg.sigma)
            val = 1.0 + seg.factor_load * (F - 1.0) + u[s]
            L[t, s] = min(3.0, max(0.2, val))
        F_prev, u_prev = F, u
    return L, (F_prev, u_prev)


def _signups(model, s, t, L_ts, mkt_row, price_row):
    seg = model.segments[s]
    mkt_term = (mkt_row[s] / seg.refmkt) ** seg.mktexp
    price_term = (price_row[s] / seg.refprice) ** seg.elast
    coup = 0.0
    for j in range(model.n()):
        if j == s:
            continue
        segj = model.segments[j]
        coup += model.coupling[s][j] * (mkt_row[j] / segj.refmkt - 1.0)
    coup_term = math.exp(coup)
    su = seg.base_signups * L_ts * mkt_term * price_term * coup_term
    return max(0.0, su)


def _churn(model, s, price_s):
    seg = model.segments[s]
    c = seg.churn0 * (price_s / seg.refprice) ** seg.churn_price_exp
    return min(0.5, max(0.005, c))


def _as_series(v, K, fallback):
    if isinstance(v, (list, tuple)):
        if len(v) != K:
            raise ValueError(f"plan series length {len(v)} != held_out {K}")
        return np.array([float(x) for x in v], float)
    if v is None:
        v = fallback
    return np.full(K, float(v))


def _plan_arrays(model, plan):
    """plan = {seg_id: {"marketing": x, "price": p}} (scalars, or length-K lists).
    Missing segments/levers fall back to reference. Returns (mkt[K,S], price[K,S])."""
    K, S = model.held_out, model.n()
    mkt = np.empty((K, S)); price = np.empty((K, S))
    for s, seg in enumerate(model.segments):
        p = (plan or {}).get(seg.id, {})
        m = p.get("marketing", seg.refmkt)
        pr = p.get("price", seg.refprice)
        mkt[:, s] = np.maximum(0.0, _as_series(m, K, seg.refmkt))
        price[:, s] = np.clip(_as_series(pr, K, seg.refprice),
                              seg.refprice * PRICE_LO, seg.refprice * PRICE_HI)
    return mkt, price


def _held_out_once(model, plan, latent_state, cust0, rng):
    K, S = model.held_out, model.n()
    mkt, price = _plan_arrays(model, plan)
    L, _ = _latent_path(model, rng, K, state0=latent_state)
    prev_cust = np.asarray(cust0, float).copy()
    total = np.zeros(S)
    for t in range(K):
        cust = np.empty(S)
        for s in range(S):
            seg = model.segments[s]
            su = _signups(model, s, t, L[t, s], mkt[t], price[t])
            ch = _churn(model, s, price[t, s])
            cust[s] = prev_cust[s] * (1 - ch) + su
            rev = cust[s] * seg.acv * price[t, s]
            total[s] += rev * seg.gm - mkt[t, s] - seg.fixed
        prev_cust = cust
    return total.sum(), total


def held_out_profit(model, plan, latent_state, cust0, n_seeds=400, base_seed=7):
    """Monte-Carlo expected held-out profit of `plan` under `model`.
    Returns (mean_total, stderr, per_segment_mean)."""
    K, S = model.held_out, model.n()
    totals = np.empty(n_seeds)
    perseg = np.zeros((n_seeds, S))
    for i in range(n_seeds):
        rng = np.random.default_rng(base_seed * 1_000_003 + i)
        tot, seg = _held_out_once(model, plan, latent_state, cust0, rng)
        totals[i] = tot; perseg[i] = seg
    return float(totals.mean()), float(totals.std(ddof=1) / math.sqrt(n_seeds)), perseg.mean(0)


def project_budget(model, plan, budget):
    """Scale marketing down proportionally if a constant plan exceeds the total budget."""
    tot = 0.0
    for s in model.segments:
        tot += float(plan[s.id]["marketing"])
    if tot <= budget or tot <= 0:
        return plan
    f = budget / tot
    return {s.id: {"marketing": float(plan[s.id]["marketing"]) * f,
                   "price": float(plan[s.id]["price"])} for s in model.segments}


def _default_grid():
    price_mults = [round(PRICE_LO + 0.05 * i, 2)                      # 0.70..1.30
                   for i in range(int((PRICE_HI - PRICE_LO) / 0.05) + 1)]
    mkt_mults = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
    return price_mults, mkt_mults


def optimize_plan(model, latent_state, cust0, n_seeds=200, base_seed=11, budget=None):
    """Per-segment coordinate search (price x marketing grid, 3 sweeps) followed by a
    local polish, maximizing held_out_profit under `model`. If `budget` is set, each
    candidate is projected onto the total-marketing cap first. Returns
    (plan, mean_profit, stderr); the final estimate uses max(n_seeds, 400) paths
    at base_seed + 1."""
    price_mults, mkt_mults = _default_grid()
    plan = {seg.id: {"marketing": seg.refmkt, "price": seg.refprice}
            for seg in model.segments}
    for _ in range(3):
        for s, seg in enumerate(model.segments):
            best, best_val = None, -1e18
            for pm in price_mults:
                for mm in mkt_mults:
                    trial = {k: dict(v) for k, v in plan.items()}
                    trial[seg.id] = {"marketing": mm * seg.refmkt,
                                     "price": pm * seg.refprice}
                    if budget is not None:
                        trial = project_budget(model, trial, budget)
                    val, _, _ = held_out_profit(model, trial, latent_state, cust0,
                                                n_seeds=n_seeds, base_seed=base_seed)
                    if val > best_val:
                        best_val, best = val, dict(trial[seg.id])
            plan[seg.id] = best
    if budget is not None:
        plan = project_budget(model, plan, budget)

    def _val(pl):
        return held_out_profit(model, pl, latent_state, cust0, n_seeds=n_seeds,
                               base_seed=base_seed)[0]
    cur = _val(plan)
    for _ in range(3):
        improved = False
        for seg in model.segments:
            base_p = float(plan[seg.id]["price"]); base_m = float(plan[seg.id]["marketing"])
            for dp in (-0.04, -0.02, 0.02, 0.04):
                for dm in (-0.15, 0.0, 0.15):
                    trial = {k: dict(v) for k, v in plan.items()}
                    trial[seg.id] = {"price": max(0.5, base_p + dp),
                                     "marketing": max(0.0, base_m + dm * seg.refmkt)}
                    if budget is not None:
                        trial = project_budget(model, trial, budget)
                    v = _val(trial)
                    if v > cur + 1e-6:
                        cur, plan, improved = v, trial, True
                        base_p = float(plan[seg.id]["price"]); base_m = float(plan[seg.id]["marketing"])
        if not improved:
            break
    mean, se, _ = held_out_profit(model, plan, latent_state, cust0,
                                  n_seeds=max(n_seeds, 400), base_seed=base_seed + 1)
    return plan, mean, se


# Names matching the simulation API of the paper-era aided sandbox, so scripts
# written against it (world_from_dict / held_out_profit / optimize_plan) still run.
world_from_dict = model_from_dict
LatentWorld = Model
_project_budget = project_budget
