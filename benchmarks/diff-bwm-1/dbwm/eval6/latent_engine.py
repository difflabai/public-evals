"""Stochastic latent-state engine for eval 6 (D1 hindcast, D2 rank-off, step-0 gate).

SCORER SIDE. This module is the true-dynamics Monte-Carlo oracle. It is never
staged into an agent sandbox (the paper staged it into the aided sandbox,
d1_planner.py:199, which together with the world seed in fitted_model.json let
``simulate_history`` replay the true latent path bit-exactly).
The aided arm now gets ``data/eval6/aided_tools/heldout_sim.py`` instead, a
held-out-only subset with no history simulator and no seed.

Ported verbatim from the paper harness (``bwm_latent/latent_engine.py``). Two
changes: this docstring, and ``_project_budget`` sums with ``lsum`` (pre-3.12
left-to-right float summation) so the M* search reproduces on Python >= 3.12.
Every arithmetic expression and RNG call is kept in its original order so the
``paper`` ruleset scores bit-for-bit as the paper harness did.

WHY THIS ENGINE EXISTS (the epistemic-access principle)
-------------------------------------------------------
A deterministic, fully disclosed engine is saturated at frontier capability: a
disclosed spec plus deterministic dynamics lets the agent rebuild a bit-exact
oracle from the brief alone. The aided/unaided gap can only survive where the
agent cannot *know or verify* something from inside the sandbox.

A multi-segment subscription business is driven by a LATENT per-segment demand
state the agent never observes, and its history was generated under an
ENDOGENOUS policy (past levers set as a function of the latent state). That
endogeneity confounds the decision-relevant effects. Worlds drawn by
``dbwm.eval6.gen.make_latent_world`` have four segments: one hub whose marketing
spills into the three spokes' demand with a single random sign per world.

THE CONFOUND, CONCRETELY
    signups_s[t] = base_s * L_s[t] * (mkt/refmkt)^mktexp * (price/refprice)^ELAST
                   * exp(sum_j gamma_sj * (mkt_j/refmkt_j - 1))
    history policy (common_factor): every segment's levers track the shared
    market signal G = mean_s L_s, so in-sample the levers are collinear and the
    cross-segment coupling gamma is not identified.

OUTPUTS
  - `simulate_history`  : endogenous-policy confounded history (what the agent sees)
  - `held_out_profit`   : Monte-Carlo true expected profit of a plan on the
                          post-history horizon (reality as oracle; D1's metric)
  - `optimize_plan`     : the grid optimum M* against the TRUE theta (a loose
                          bound: M* is a grid optimum, so a plan can beat it)
  - `fit_negloglik`     : residual of a candidate theta on the observable history
Deterministic given seeds (numpy default_rng). Floats, not Decimals: the ground
truth is a Monte-Carlo expectation reported with its own noise band.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

# Per-segment structural parameters. `elast`, `churn_price_exp`, and the coupling
# terms are the DECISION-RELEVANT, CONFOUNDED quantities. `phi`/`psi` are the
# historical policy exponents (they define the endogeneity; the agent is not told
# them and they do not apply in the held-out horizon, where the agent sets levers).
SEG_KEYS = ("base_signups", "acv", "mktexp", "elast", "churn0",
            "churn_price_exp", "rho", "sigma", "factor_load", "refmkt", "refprice",
            "phi", "psi", "gm", "fixed", "cust0")


@dataclass
class Segment:
    id: str
    base_signups: float      # signups intercept at L=1, levers at reference
    acv: float               # reference ARPU/quarter (price is a multiplier around 1.0)
    mktexp: float            # marketing response exponent in (0,1), concave
    elast: float             # TRUE causal price elasticity of signups (< 0)
    churn0: float            # base churn/quarter at reference price
    churn_price_exp: float   # churn response to price (> 0: higher price -> more churn)
    rho: float               # idiosyncratic latent AR(1) persistence
    sigma: float             # idiosyncratic latent AR(1) innovation sd
    factor_load: float       # loading on the SHARED market factor F (couples segments'
                             #   latent demand -> confounds cross-segment coupling gamma)
    refmkt: float            # reference marketing spend/quarter
    refprice: float          # reference price multiplier (=1.0 by convention)
    phi: float               # HISTORY policy: mkt = refmkt * L^phi (endogeneity)
    psi: float               # HISTORY policy: price = refprice * L^psi (endogeneity)
    gm: float                # gross margin on revenue
    fixed: float             # fixed opex/quarter
    cust0: float             # starting customers

    def vec(self):
        return {k: getattr(self, k) for k in SEG_KEYS}


@dataclass
class LatentWorld:
    id: str
    segments: list            # list[Segment]
    coupling: list            # SxS matrix gamma[s][j]: mkt in j shifts demand in s
    horizon: int = 72         # history length T
    held_out: int = 8         # decision horizon K
    policy_noise: float = 0.05  # multiplicative lognormal noise on historical levers
    meas_noise: float = 0.02    # multiplicative lognormal measurement noise on signups
    factor_rho: float = 0.7     # shared market-factor F AR(1) persistence
    factor_sigma: float = 0.16  # shared market-factor F innovation sd
    history_policy: str = "common_factor"  # "common_factor" (levers co-move with the
                             #   shared factor F -> cross-segment levers collinear ->
                             #   coupling non-identified) or "endogenous_own" (levers
                             #   track each segment's own latent -> coupling identifiable)
    seed: int = 0             # world seed (fixes theta draw + reference latent path)

    def n(self):
        return len(self.segments)

    def to_public(self):
        """Structural FORM only, no parameter values — what STRUCTURE-ONLY arm gets."""
        return {"id": self.id, "n_segments": self.n(),
                "segment_ids": [s.id for s in self.segments],
                "horizon": self.horizon, "held_out": self.held_out,
                "form": "signups = base*L*(mkt/refmkt)^mktexp*(price/refprice)^elast*coupling; "
                        "customers rolls forward with price-sensitive churn; "
                        "revenue = customers*acv*price; latent demand L is AR(1) and UNOBSERVED."}

    def to_answer_key(self):
        return {"id": self.id, "seed": self.seed,
                "coupling": [[float(x) for x in row] for row in self.coupling],
                "segments": {s.id: s.vec() for s in self.segments},
                "horizon": self.horizon, "held_out": self.held_out}


# ---------------------------------------------------------------------------
# Core dynamics (one latent path)
# ---------------------------------------------------------------------------

def _latent_path(world: LatentWorld, rng, T, state0=None):
    """Latent demand with a SHARED market factor F plus per-segment idiosyncratic u:
        F[t] = 1 + factor_rho*(F[t-1]-1) + N(0, factor_sigma)      (one shared path)
        u_s[t] = rho_s*u_s[t-1] + N(0, sigma_s)                     (idiosyncratic)
        L_s[t] = clip(1 + factor_load_s*(F[t]-1) + u_s[t], 0.2, 3.0)
    The shared F correlates segments' latent demand, which is what makes the
    cross-segment coupling GAMMA non-identified from history (spillover is
    confounded with the common demand factor).

    `state0` = (F_prev, u_prev[S]) continues a path (held-out); None starts fresh.
    Returns (L[T,S], (F_final, u_final)).
    """
    S = world.n()
    L = np.empty((T, S))
    if state0 is None:
        F_prev = 1.0
        u_prev = np.zeros(S)
    else:
        F_prev, u_prev = float(state0[0]), np.asarray(state0[1], float).copy()
    for t in range(T):
        F = 1.0 + world.factor_rho * (F_prev - 1.0) + rng.normal(0.0, world.factor_sigma)
        u = np.empty(S)
        for s in range(S):
            seg = world.segments[s]
            u[s] = seg.rho * u_prev[s] + rng.normal(0.0, seg.sigma)
            val = 1.0 + seg.factor_load * (F - 1.0) + u[s]
            L[t, s] = min(3.0, max(0.2, val))
        F_prev, u_prev = F, u
    return L, (F_prev, u_prev)


def _signups(world, s, t, L_ts, mkt_row, price_row):
    """signups for segment s at time t given the levers of ALL segments this period."""
    seg = world.segments[s]
    mkt_term = (mkt_row[s] / seg.refmkt) ** seg.mktexp
    price_term = (price_row[s] / seg.refprice) ** seg.elast
    # cross-segment marketing spillover into demand (sign-ambiguous coupling)
    coup = 0.0
    for j in range(world.n()):
        if j == s:
            continue
        segj = world.segments[j]
        coup += world.coupling[s][j] * (mkt_row[j] / segj.refmkt - 1.0)
    coup_term = math.exp(coup)
    su = seg.base_signups * L_ts * mkt_term * price_term * coup_term
    return max(0.0, su)


def _churn(world, s, price_s):
    seg = world.segments[s]
    c = seg.churn0 * (price_s / seg.refprice) ** seg.churn_price_exp
    return min(0.5, max(0.005, c))


def simulate_history(world: LatentWorld):
    """Generate the endogenous-policy confounded history the agent observes.

    Historical levers are set BY the latent state (spend/price into demand), which
    is exactly what confounds the elasticities. Returns a dict of observable
    per-segment series plus the (sequestered) latent path.
    """
    rng = np.random.default_rng(world.seed * 100003 + 1)
    T, S = world.horizon, world.n()
    L, latent_state = _latent_path(world, rng, T)
    # shared "market signal" that the CENTRALIZED historical budget/pricing policy
    # reacts to: the cross-segment average latent demand (a proxy for the factor F).
    G = L.mean(axis=1)  # shape (T,)
    mkt = np.empty((T, S)); price = np.empty((T, S))
    signups = np.empty((T, S)); customers = np.empty((T, S))
    churn = np.empty((T, S)); revenue = np.empty((T, S)); profit = np.empty((T, S))
    prev_cust = np.array([seg.cust0 for seg in world.segments], float)
    for t in range(T):
        # historical policy sets levers. common_factor: every segment's marketing &
        # price track the SHARED market signal G (levers co-move -> collinear in-sample
        # -> the causal cross-segment coupling is not identified). endogenous_own:
        # levers track each segment's OWN latent (coupling identifiable via idio swings).
        for s in range(S):
            seg = world.segments[s]
            drv = G[t] if world.history_policy == "common_factor" else L[t, s]
            mkt[t, s] = seg.refmkt * (drv ** seg.phi) * math.exp(rng.normal(0, world.policy_noise))
            price[t, s] = seg.refprice * (drv ** seg.psi) * math.exp(rng.normal(0, world.policy_noise))
        for s in range(S):
            seg = world.segments[s]
            su = _signups(world, s, t, L[t, s], mkt[t], price[t])
            su *= math.exp(rng.normal(0, world.meas_noise))  # measurement noise
            ch = _churn(world, s, price[t, s])
            cust = prev_cust[s] * (1 - ch) + su
            rev = cust * seg.acv * price[t, s]
            signups[t, s] = su; churn[t, s] = ch; customers[t, s] = cust
            revenue[t, s] = rev
            profit[t, s] = rev * seg.gm - mkt[t, s] - seg.fixed
        prev_cust = customers[t]
    return {
        "marketing": mkt, "price": price, "signups": signups,
        "customers": customers, "churn": churn, "revenue": revenue,
        "profit": profit, "latent": L, "L_final": L[-1].copy(),
        "cust_final": customers[-1].copy(),
        "latent_state": (float(latent_state[0]), latent_state[1].tolist()),
    }


# ---------------------------------------------------------------------------
# Held-out evaluation (reality as oracle) — D1's decisive metric
# ---------------------------------------------------------------------------

# The price lever is only CALIBRATED near the reference operating point. The
# multiplicative power-law response (price/refprice)^elast is degenerate outside a
# realistic band (price -> 0 sends signups -> infinity via the negative exponent;
# price -> infinity sends ARPU -> infinity on the customer stock), so an unbounded
# price makes the decision problem ill-posed and rewards out-of-calibration
# extrapolation. Every plan's price is HARD-CLAMPED to this band before scoring, in
# the true oracle AND the agent's simulator, so the optimum is interior and no arm can
# exploit the extrapolation. (Realistic: businesses cannot arbitrarily reprice.)
PRICE_LO, PRICE_HI = 0.7, 1.3


def _plan_arrays(world, plan):
    """Accept a plan as {seg_id: {"marketing":[K], "price":[K]}} OR
    {seg_id: {"marketing": scalar, "price": scalar}} (constant over K). Returns
    (mkt[K,S], price[K,S]). Missing segments/levers fall back to reference. Price is
    clamped to the calibrated band [refprice*PRICE_LO, refprice*PRICE_HI]; marketing is
    floored at 0."""
    K, S = world.held_out, world.n()
    mkt = np.empty((K, S)); price = np.empty((K, S))
    for s, seg in enumerate(world.segments):
        p = (plan or {}).get(seg.id, {})
        m = p.get("marketing", seg.refmkt)
        pr = p.get("price", seg.refprice)
        mkt[:, s] = np.maximum(0.0, _as_series(m, K, seg.refmkt))
        price[:, s] = np.clip(_as_series(pr, K, seg.refprice),
                              seg.refprice * PRICE_LO, seg.refprice * PRICE_HI)
    return mkt, price


def _as_series(v, K, fallback):
    if isinstance(v, (list, tuple)):
        if len(v) != K:
            raise ValueError(f"plan series length {len(v)} != held_out {K}")
        return np.array([float(x) for x in v], float)
    if v is None:
        v = fallback
    return np.full(K, float(v))


def _held_out_once(world, plan, latent_state, cust0, rng):
    """One realized held-out trajectory under the agent's fixed plan. Latent demand
    CONTINUES from `latent_state`=(F_final, u_final) with fresh innovations (the plan
    sets only the levers, never the latent state)."""
    K, S = world.held_out, world.n()
    mkt, price = _plan_arrays(world, plan)
    L, _ = _latent_path(world, rng, K, state0=latent_state)
    prev_cust = np.asarray(cust0, float).copy()
    total = np.zeros(S)
    for t in range(K):
        cust = np.empty(S)
        for s in range(S):
            seg = world.segments[s]
            su = _signups(world, s, t, L[t, s], mkt[t], price[t])
            ch = _churn(world, s, price[t, s])
            cust[s] = prev_cust[s] * (1 - ch) + su
            rev = cust[s] * seg.acv * price[t, s]
            total[s] += rev * seg.gm - mkt[t, s] - seg.fixed
        prev_cust = cust
    return total.sum(), total


def held_out_profit(world: LatentWorld, plan, latent_state, cust0, n_seeds=400, base_seed=7):
    """Monte-Carlo TRUE expected held-out profit of `plan` (reality as oracle).
    Returns (mean_total, stderr, per_segment_mean)."""
    K, S = world.held_out, world.n()
    totals = np.empty(n_seeds)
    perseg = np.zeros((n_seeds, S))
    for i in range(n_seeds):
        rng = np.random.default_rng(base_seed * 1_000_003 + i)
        tot, seg = _held_out_once(world, plan, latent_state, cust0, rng)
        totals[i] = tot; perseg[i] = seg
    return float(totals.mean()), float(totals.std(ddof=1) / math.sqrt(n_seeds)), perseg.mean(0)


# ---------------------------------------------------------------------------
# The verified optimum M* against the TRUE theta
# ---------------------------------------------------------------------------

def _default_grid():
    # price multipliers around reference, and marketing as a multiple of refmkt.
    price_mults = [round(PRICE_LO + 0.05 * i, 2)                      # 0.70..1.30 (calibrated band)
                   for i in range(int((PRICE_HI - PRICE_LO) / 0.05) + 1)]
    mkt_mults = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
    return price_mults, mkt_mults


def optimize_plan(world: LatentWorld, latent_state, cust0, n_seeds=200, base_seed=11,
                  budget=None):
    """Per-segment coordinate optimum of expected held-out profit against the TRUE
    theta. Segments are near-separable (coupling is a modest demand shifter), so a
    per-segment grid then coupling-aware refinement sweeps are a faithful M*.
    If `budget` is set, projects each candidate onto the total-marketing cap before
    scoring so M* is feasible. Returns (plan, mean_profit, stderr)."""
    price_mults, mkt_mults = _default_grid()
    plan = {seg.id: {"marketing": seg.refmkt, "price": seg.refprice}
            for seg in world.segments}
    for _ in range(3):
        for s, seg in enumerate(world.segments):
            best, best_val = None, -1e18
            for pm in price_mults:
                for mm in mkt_mults:
                    trial = {k: dict(v) for k, v in plan.items()}
                    trial[seg.id] = {"marketing": mm * seg.refmkt,
                                     "price": pm * seg.refprice}
                    if budget is not None:
                        trial = _project_budget(world, trial, budget)
                    val, _, _ = held_out_profit(world, trial, latent_state, cust0,
                                                n_seeds=n_seeds, base_seed=base_seed)
                    if val > best_val:
                        best_val, best = val, dict(trial[seg.id])
            plan[seg.id] = best
    if budget is not None:
        plan = _project_budget(world, plan, budget)
    # local polish: fine price steps + marketing nudges around the grid optimum, so M* is a
    # genuine ceiling (a coarse grid can be beaten by a capable agent, yielding a spurious
    # negative gap-to-M*). Accept any improvement under a fixed evaluation seed.
    def _val(pl):
        return held_out_profit(world, pl, latent_state, cust0, n_seeds=n_seeds,
                               base_seed=base_seed)[0]
    cur = _val(plan)
    for _ in range(3):
        improved = False
        for seg in world.segments:
            base_p = float(plan[seg.id]["price"]); base_m = float(plan[seg.id]["marketing"])
            for dp in (-0.04, -0.02, 0.02, 0.04):
                for dm in (-0.15, 0.0, 0.15):
                    trial = {k: dict(v) for k, v in plan.items()}
                    trial[seg.id] = {"price": max(0.5, base_p + dp),
                                     "marketing": max(0.0, base_m + dm * seg.refmkt)}
                    if budget is not None:
                        trial = _project_budget(world, trial, budget)
                    v = _val(trial)
                    if v > cur + 1e-6:
                        cur, plan, improved = v, trial, True
                        base_p = float(plan[seg.id]["price"]); base_m = float(plan[seg.id]["marketing"])
        if not improved:
            break
    mean, se, _ = held_out_profit(world, plan, latent_state, cust0,
                                  n_seeds=max(n_seeds, 400), base_seed=base_seed + 1)
    return plan, mean, se


def lsum(xs):
    """Left-to-right float sum, as Python's built-in ``sum`` behaved before 3.12.

    Python 3.12 switched ``sum`` of floats to compensated (Neumaier) summation,
    which changes last-bit results (e.g. the w3 and w5 budgets). The paper ran on
    Python 3.9, so every paper-era float sum in eval 6 goes through this helper and
    results are identical on every supported interpreter."""
    total = 0
    for x in xs:
        total = total + x
    return total


def _project_budget(world, plan, budget):
    """Scale down marketing proportionally if the plan exceeds the total budget."""
    tot = lsum(float(plan[s.id]["marketing"]) for s in world.segments)
    if tot <= budget or tot <= 0:
        return plan
    f = budget / tot
    return {s.id: {"marketing": float(plan[s.id]["marketing"]) * f,
                   "price": float(plan[s.id]["price"])} for s in world.segments}


# ---------------------------------------------------------------------------
# History likelihood of a candidate theta (for the identifiability certificate)
# ---------------------------------------------------------------------------

def fit_negloglik(theta_world: LatentWorld, obs):
    """Gaussian neg-log-lik (up to const) of the OBSERVABLE history under a candidate
    theta, reconstructing the latent path from the candidate's own policy inverse.

    Because history levers were set by mkt=refmkt*L^phi, a candidate theta INFERS
    the latent path it would need: L_hat = (mkt/refmkt)^(1/phi). It then predicts
    signups and scores the residual against observed signups (in logs). Two thetas
    that predict the observed (signups | mkt, price) equally well are observationally
    equivalent on this history — the basis of the non-identifiability certificate.
    """
    mkt, price, signups = obs["marketing"], obs["price"], obs["signups"]
    T, S = signups.shape
    resid = []
    for s in range(S):
        seg = theta_world.segments[s]
        Lhat = np.maximum(1e-6, (mkt[:, s] / seg.refmkt) ** (1.0 / max(seg.phi, 1e-3)))
        pred = (seg.base_signups * Lhat
                * (mkt[:, s] / seg.refmkt) ** seg.mktexp
                * (price[:, s] / seg.refprice) ** seg.elast)
        # coupling omitted in the fit proxy (modest, same across candidates)
        lr = np.log(np.maximum(1e-6, signups[:, s])) - np.log(np.maximum(1e-6, pred))
        resid.append(lr)
    resid = np.concatenate(resid)
    return float(np.sum(resid ** 2)), float(np.sqrt(np.mean(resid ** 2)))


# ---------------------------------------------------------------------------
# (de)serialization
# ---------------------------------------------------------------------------

def world_from_dict(d):
    segs = [Segment(id=sid, **{k: float(v) for k, v in sv.items()})
            for sid, sv in d["segments"].items()]
    # preserve segment order
    order = d.get("segment_order") or list(d["segments"].keys())
    segs = sorted(segs, key=lambda s: order.index(s.id))
    return LatentWorld(id=d["id"], segments=segs, coupling=d["coupling"],
                       horizon=int(d["horizon"]), held_out=int(d["held_out"]),
                       policy_noise=float(d.get("policy_noise", 0.05)),
                       meas_noise=float(d.get("meas_noise", 0.02)),
                       factor_rho=float(d.get("factor_rho", 0.7)),
                       factor_sigma=float(d.get("factor_sigma", 0.16)),
                       history_policy=d.get("history_policy", "common_factor"),
                       seed=int(d.get("seed", 0)))


def world_to_dict(world: LatentWorld):
    return {"id": world.id, "segment_order": [s.id for s in world.segments],
            "segments": {s.id: s.vec() for s in world.segments},
            "coupling": [[float(x) for x in row] for row in world.coupling],
            "horizon": world.horizon, "held_out": world.held_out,
            "policy_noise": world.policy_noise, "meas_noise": world.meas_noise,
            "factor_rho": world.factor_rho, "factor_sigma": world.factor_sigma,
            "history_policy": world.history_policy,
            "seed": world.seed}


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def _demo_world(seed=0):
    rng = np.random.default_rng(seed * 7 + 3)
    segs = []
    ids = ["core", "smb", "ent", "growth"][:4]
    for i, sid in enumerate(ids):
        segs.append(Segment(
            id=sid,
            base_signups=float(rng.uniform(120, 300)),
            acv=float(rng.uniform(900, 1400)),
            mktexp=float(rng.uniform(0.35, 0.55)),
            elast=float(rng.uniform(-1.3, -0.7)),           # TRUE negative elasticity
            churn0=float(rng.uniform(0.04, 0.09)),
            churn_price_exp=float(rng.uniform(0.6, 1.2)),    # price up -> churn up
            rho=float(rng.uniform(0.5, 0.8)),
            sigma=float(rng.uniform(0.06, 0.11)),
            factor_load=float(rng.uniform(0.5, 1.1)),        # loads on the shared factor
            refmkt=float(rng.uniform(40000, 90000)),
            refprice=1.0,
            phi=float(rng.uniform(0.6, 1.0)),                # spend into demand
            psi=float(rng.uniform(0.35, 0.6)),               # price into demand (small psi -> strong trap)
            gm=float(rng.uniform(0.7, 0.82)),
            fixed=float(rng.uniform(30000, 60000)),
            cust0=float(rng.uniform(1500, 4000))))
    S = len(segs)
    coupling = [[0.0] * S for _ in range(S)]
    # a couple of sign-ambiguous cross-segment spillovers
    coupling[1][0] = float(rng.uniform(-0.18, 0.18))
    coupling[3][0] = float(rng.uniform(-0.18, 0.18))
    return LatentWorld(id=f"demo-{seed}", segments=segs, coupling=coupling, seed=seed)


def selftest():
    problems = []
    w = _demo_world(0)

    # 1) determinism: same seed -> identical history
    h1 = simulate_history(w); h2 = simulate_history(w)
    if not np.allclose(h1["signups"], h2["signups"]):
        problems.append("history not deterministic given world seed")
    else:
        print("  [ok] history deterministic given world seed")

    # 2) round-trip (de)serialization
    w2 = world_from_dict(world_to_dict(w))
    h3 = simulate_history(w2)
    if not np.allclose(h1["signups"], h3["signups"]):
        problems.append("world (de)serialization changed the dynamics")
    else:
        print("  [ok] world dict round-trips exactly")

    # 3) held-out MC estimator: stderr shrinks with more seeds (variance sanity)
    ls, c0 = h1["latent_state"], h1["cust_final"]
    ref = {s.id: {"marketing": s.refmkt, "price": s.refprice} for s in w.segments}
    _, se_lo, _ = held_out_profit(w, ref, ls, c0, n_seeds=50, base_seed=1)
    _, se_hi, _ = held_out_profit(w, ref, ls, c0, n_seeds=800, base_seed=1)
    if not (se_hi < se_lo):
        problems.append(f"MC stderr did not shrink with n ({se_hi} !< {se_lo})")
    else:
        print(f"  [ok] MC stderr shrinks with n ({se_lo:.0f} -> {se_hi:.0f})")

    # 4) THE SIGN TRAP: naive OLS of log(signups) on log(price) over the history
    #    recovers a POSITIVE slope though the TRUE elasticity is negative.
    pos_slopes = 0
    for s in range(w.n()):
        lp = np.log(h1["price"][:, s]); lsig = np.log(np.maximum(1e-6, h1["signups"][:, s]))
        slope = np.polyfit(lp, lsig, 1)[0]
        if slope > 0 and w.segments[s].elast < 0:
            pos_slopes += 1
    if pos_slopes < w.n() - 1:
        problems.append(f"sign trap weak: only {pos_slopes}/{w.n()} segments flip sign")
    else:
        print(f"  [ok] sign trap present: naive OLS gives +slope on {pos_slopes}/{w.n()} "
              f"segments though true elasticity < 0")

    # 5) M* beats the naive 'raise price' plan on TRUE held-out profit
    star_plan, star, sstar = optimize_plan(w, ls, c0, n_seeds=150, base_seed=5)
    naive = {s.id: {"marketing": s.refmkt * 1.5, "price": s.refprice * 1.3}
             for s in w.segments}   # naive: spend more AND raise price (the trap)
    nval, nse, _ = held_out_profit(w, naive, ls, c0, n_seeds=400, base_seed=5)
    if not (star > nval + 2 * (sstar + nse)):
        problems.append(f"M* ({star:.0f}) not clearly above naive-raise-price "
                        f"({nval:.0f}) — world not decision-hard")
    else:
        print(f"  [ok] M*={star:.0f} > naive-raise-price={nval:.0f} "
              f"(gap {star - nval:.0f}, well beyond MC noise)")

    if problems:
        print("\nSELFTEST FAILED:")
        for p in problems:
            print("  -", p)
        raise SystemExit(1)
    print("selftest OK — latent engine is deterministic, confounded, and decision-hard.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    args = ap.parse_args()
    if args.cmd == "selftest":
        selftest()
