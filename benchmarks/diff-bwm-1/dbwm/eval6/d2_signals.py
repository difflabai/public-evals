"""D2: reward-signal-quality rank-off (offline signals).

SCORER SIDE: the state builder recomputes V* from the true theta. Ported from
the paper harness (``bwm_latent/d2_signals.py``); the computation is unchanged.
Changes:
package imports; the budget sum uses ``latent_engine.lsum`` (pre-3.12 float
summation, without which Python 3.12 differs in the last bit on some states);
scipy.stats.kendalltau replaced by a bit-identical stdlib
``kendall_tau_b`` (scipy is optional); ``run`` can skip writing/printing and
takes ``state_set`` ("paper" | "certified"); CLI wiring for ``dbwm``.

THE D2 QUESTION. On mid-game states of the latent world, does ranking six
scripted candidate plans by a ledger-derived score achieve a higher Kendall
tau-b against the true full-horizon value V* than other signals?

FOUR OFFLINE SIGNALS (each scores every candidate; higher = better)
  LEDGER_MECHANICAL     32-path forward sim under the TRUE theta from a private
                        nowcast (true latent + N(0, 0.03)), minus a lambda-weighted
                        reconciliation penalty (lambda = 1.0, not calibrated).
  VHAT_ONLY             the same forward sim without the penalty.
  MECHANICAL_NO_LEDGER  the same TRUE-theta forward sim from the prior-mean latent
                        (F=1, u=0), with a top-line-only penalty.
  MYOPIC_ACTUALS        first-quarter profit on one realized latent path.
Note (ledger E6-13): both ledger signals use the true structural model and a
near-exact latent state, so the ledger is close to the oracle itself. VHAT_ONLY
is the ledger at lambda = 0 ("nowcast-only (lambda = 0)" in v7 Section 8).

PROJECTION SORT (not one of the four paper signals, so the paper report shape
is unchanged). Every judge is handed the no-ledger forward sim WITHOUT its
penalty: the true engine from the prior-mean latent state. ``projection_report``
sorts that projection (PROJECTION_SORT, = MECHANICAL_NO_LEDGER at lambda = 0)
and re-runs it with the structure mis-specified (coupling zeroed, sign flipped,
elasticity halved, price and churn effects zeroed). The penalty weight stays at
lambda = 1.0 on the paper path and was never calibrated, so new-run reports
(``run(..., lambda_zero=True)``, the CLI default) print lambda = 1 and
lambda = 0 side by side.

GROUND TRUTH. V* = le.held_out_profit with the true theta from the true latent
state, 300 paths, base_seed 7. Per state: tau-b of each signal vs V* (a constant
ranking maps to 0) and top-1 regret. Aggregated with a world-clustered bootstrap
(numpy default_rng(12345), B=2000).

STATES. "paper": world seeds 1-8 x cutoffs {54, 72} (16 states, includes the
decertified seed 8 and uncertified 54-quarter histories). "certified": the 11
certified worlds at cutoff 72.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import OrderedDict
from pathlib import Path

import numpy as np

from . import latent_engine as le             # FROZEN true-dynamics oracle
from .gen import make_latent_world as mlw     # generator: draw_world, reference_plans


def kendall_tau_b(x, y) -> float:
    """Kendall tau-b, stdlib, with scipy.stats.kendalltau's exact float arithmetic.

    The paper computed tau with scipy (d2_signals.py:67, 401); scipy evaluates
    ``con_minus_dis / sqrt(tot - xtie) / sqrt(tot - ytie)`` and clips to [-1, 1].
    The integer counts here come from pairwise signs (identical to scipy's
    rank-based counts) and the final expression is evaluated in the same order
    with correctly rounded sqrt and division, so the result is bit-identical
    (tests/eval6 compare against scipy when it imports). Returns nan when either
    input is constant or contains nan, as scipy does; callers map nan as the
    paper did. Kept local (rather than dbwm.core.stats) so the paper path stays
    bit-for-bit.
    """
    n = len(x)
    if n != len(y):
        raise ValueError("kendall_tau_b: inputs differ in length")
    xs = [float(v) for v in x]
    ys = [float(v) for v in y]
    if n == 0 or any(math.isnan(v) for v in xs + ys):
        return float("nan")
    cmd = xtie = ytie = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = xs[i] - xs[j]
            dy = ys[i] - ys[j]
            sx = (dx > 0) - (dx < 0)
            sy = (dy > 0) - (dy < 0)
            if sx == 0:
                xtie += 1
            if sy == 0:
                ytie += 1
            cmd += sx * sy
    tot = n * (n - 1) // 2
    if xtie == tot or ytie == tot:
        return float("nan")
    tau = cmd / math.sqrt(tot - xtie) / math.sqrt(tot - ytie)
    return min(1.0, max(-1.0, tau))


# ---------------------------------------------------------------------------
# Module constants (calibratable; sensible frozen defaults)
# ---------------------------------------------------------------------------

LEDGER_NOISE_SD = 0.03       # sd of the recorded gaussian noise on the private nowcast
LEDGER_RECON_WINDOW = 4      # # recent history periods used for the reconciled prediction error
DEFAULT_LAMBDA = 1.0         # reconciliation-penalty weight (see calibrate_lambda)

FORWARD_SEEDS = 32           # seeds for the CHEAP forward-sim projected value V-hat
FORWARD_BASE_SEED = 4242     # forward-sim base seed (DISTINCT from V*'s — independent noise)
VSTAR_SEEDS = 300            # seeds for the ground-truth V* (true-dynamics oracle)
VSTAR_BASE_SEED = 7          # V* base seed (fixed; the engine's own default)
MYOPIC_PATH_SALT = 909091    # salt for the state's single realized latent path (myopic actuals)

BOOTSTRAP_B = 2000           # world-clustered bootstrap resamples
DEFAULT_CUTOFFS = (54, 72)   # mid-game cutoffs (history horizon at which the state is taken)
WORLD_SEED_START = 1         # first world seed for the ranked (evaluation) states
CAL_SEED_START = 5001        # first world seed for the DISJOINT lambda-calibration states

CERTIFIED_SEEDS = (1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12)   # step-0 GATE=PASS (seed 8 failed)
STATE_SETS = ("paper", "certified")

# Reported signals (JUDGE is a staged hook, not scored offline).
SIGNALS = ("LEDGER_MECHANICAL", "MECHANICAL_NO_LEDGER", "VHAT_ONLY", "MYOPIC_ACTUALS")

# The handed projection, sorted, and its structure ablations (projection_report).
PROJECTION_SORT = "PROJECTION_SORT"
PROJECTION_SORT_COUPLING_ZERO = "PROJECTION_SORT_COUPLING_ZERO"
PROJECTION_SORT_SIGN_FLIPPED = "PROJECTION_SORT_SIGN_FLIPPED"
PROJECTION_SORT_ELAST_HALF = "PROJECTION_SORT_ELAST_HALF"
PROJECTION_SORT_NO_PRICE_EFFECTS = "PROJECTION_SORT_NO_PRICE_EFFECTS"
PROJECTION_SIGNALS = (PROJECTION_SORT, PROJECTION_SORT_COUPLING_ZERO, PROJECTION_SORT_SIGN_FLIPPED,
                      PROJECTION_SORT_ELAST_HALF, PROJECTION_SORT_NO_PRICE_EFFECTS)
LAMBDA_CALIBRATION_NOTE = (
    "lambda = 1.0 was never calibrated (lambda_calibrated: false; calibrate_lambda / --calibrate fits it on "
    "disjoint states). At lambda = 0 the ledger is VHAT_ONLY and the no-ledger control is the sort of the "
    "projection every judge was handed.")


# ---------------------------------------------------------------------------
# Candidate plans — M=6 scripted heuristics per state (deterministic)
#   (model-generated candidates are a documented TODO: a live authoring arm is
#    staged; here the ranked set is fully scripted.)
# ---------------------------------------------------------------------------

CANDIDATE_NAMES = ("reference", "raise_price", "concentrate_on_hub",
                   "spread", "high_marketing", "low_price")


def _hub_index(world) -> int:
    """The coupling hub = the segment other segments' demand couples TO (the nonzero
    coupling column). Falls back to the largest segment if the world has no coupling."""
    S = world.n()
    counts = [sum(1 for s in range(S) if s != j and abs(world.coupling[s][j]) > 1e-12)
              for j in range(S)]
    if max(counts) == 0:
        return int(np.argmax([s.base_signups * s.acv for s in world.segments]))
    return int(np.argmax(counts))


def candidate_plans(world, budget) -> "OrderedDict[str, dict]":
    """Six deterministic heuristic held-out plans, each respecting the total-marketing
    budget cap. Reference marketing (budget == sum of refmkt) IS the per-segment refmkt."""
    segs = world.segments
    S = world.n()
    refmkt = np.array([s.refmkt for s in segs], float)
    ref_alloc = refmkt / refmkt.sum() * budget              # == refmkt (budget = sum refmkt)
    hub = _hub_index(world)

    plans: "OrderedDict[str, dict]" = OrderedDict()
    # do-nothing-ish: reference levers, reference price
    plans["reference"] = {s.id: {"marketing": float(ref_alloc[i]), "price": float(s.refprice)}
                          for i, s in enumerate(segs)}
    # the naive-OLS trap: history says price up => signups up, so RAISE price
    plans["raise_price"] = {s.id: {"marketing": float(ref_alloc[i]),
                                   "price": float(s.refprice * 1.3)}
                            for i, s in enumerate(segs)}
    # bet the coupling is a complement: dump the budget on the hub
    conc = np.zeros(S); conc[hub] = budget
    plans["concentrate_on_hub"] = {s.id: {"marketing": float(conc[i]),
                                          "price": float(s.refprice)}
                                   for i, s in enumerate(segs)}
    # bet the coupling cannibalizes: spread the budget evenly
    plans["spread"] = {s.id: {"marketing": float(budget / S), "price": float(s.refprice)}
                       for i, s in enumerate(segs)}
    # push spend toward the most marketing-responsive segments (by mktexp)
    mktexp = np.array([s.mktexp for s in segs], float)
    hw = mktexp / mktexp.sum() * budget
    plans["high_marketing"] = {s.id: {"marketing": float(hw[i]), "price": float(s.refprice)}
                               for i, s in enumerate(segs)}
    # lean INTO the true negative elasticity: cut price (the anti-trap move)
    plans["low_price"] = {s.id: {"marketing": float(ref_alloc[i]),
                                 "price": float(s.refprice * 0.85)}
                          for i, s in enumerate(segs)}

    for k in list(plans.keys()):                             # feasibility safety
        plans[k] = le._project_budget(world, plans[k], budget)
    assert tuple(plans.keys()) == CANDIDATE_NAMES
    return plans


# ---------------------------------------------------------------------------
# Counterfactual executor (mirrors le._held_out_once; returns PER-PERIOD profit)
# ---------------------------------------------------------------------------

def execute_path(world, plan, cust0, L):
    """Realize the held-out horizon under a SPECIFIC latent path `L` [K,S] (levers set by
    `plan`, latent continues as given). Returns (total, per_period[K], per_period_seg[K,S]).
    Bit-for-bit the same profit computation as le._held_out_once — verified <1e-9 in selftest
    — but exposes the first-period profit that MYOPIC_ACTUALS needs."""
    K, S = world.held_out, world.n()
    mkt, price = le._plan_arrays(world, plan)
    prev = np.asarray(cust0, float).copy()
    per_period = np.zeros(K)
    per_period_seg = np.zeros((K, S))
    for t in range(K):
        cust = np.empty(S)
        for s in range(S):
            seg = world.segments[s]
            su = le._signups(world, s, t, L[t, s], mkt[t], price[t])
            ch = le._churn(world, s, price[t, s])
            cust[s] = prev[s] * (1 - ch) + su
            rev = cust[s] * seg.acv * price[t, s]
            p = rev * seg.gm - mkt[t, s] - seg.fixed
            per_period_seg[t, s] = p
            per_period[t] += p
        prev = cust
    return float(per_period.sum()), per_period, per_period_seg


def _held_out_profit_mine(world, plan, latent_state, cust0, n_seeds, base_seed):
    """Independent re-implementation of le.held_out_profit's mean via `execute_path`, using
    the identical per-seed rng schedule. Used by the oracle-equivalence selftest."""
    tot = 0.0
    for i in range(n_seeds):
        rng = np.random.default_rng(base_seed * 1_000_003 + i)
        L, _ = le._latent_path(world, rng, world.held_out, state0=latent_state)
        t, _, _ = execute_path(world, plan, cust0, L)
        tot += t
    return tot / n_seeds


# ---------------------------------------------------------------------------
# Private-nowcast ledger + top-line-only crude latent estimate
# ---------------------------------------------------------------------------

def make_ledger(world, obs, cutoff, sd=LEDGER_NOISE_SD, window=LEDGER_RECON_WINDOW):
    """The past controller's ledger recorded AT the state:
      - `nowcast_state`: a PRIVATE nowcast of the latent demand state (F, u) = the true
        latent state + small recorded gaussian noise (sd). NOT recoverable from top-line.
      - `recon_rms_ledger`: RMS of the recent one-step reconciled prediction error, where the
        controller predicted signups from its (noisy) private nowcast of the latent path.
      - `recon_rms_crude`: the SAME recent reconciliation, but predicting from the observable
        baseline (L=1) — the error a no-ledger author reconstructs from actuals alone.
    Seeded deterministically by (world.seed, cutoff)."""
    F_true, u_true = obs["latent_state"]
    u_true = np.asarray(u_true, float)
    S = world.n()
    rng = np.random.default_rng(world.seed * 100000 + cutoff * 31 + 7)
    nowcast_state = (float(F_true + rng.normal(0.0, sd)),
                     (u_true + rng.normal(0.0, sd, S)).tolist())

    L, mkt, price, signups = obs["latent"], obs["marketing"], obs["price"], obs["signups"]
    C = L.shape[0]
    led, crude = [], []
    for t in range(max(0, C - window), C):
        for s in range(S):
            L_nc = float(L[t, s] + rng.normal(0.0, sd))          # controller's nowcast of L[t,s]
            pred_led = le._signups(world, s, t, L_nc, mkt[t], price[t])
            pred_crude = le._signups(world, s, t, 1.0, mkt[t], price[t])  # top-line baseline
            real = float(signups[t, s])
            if real > 1e-9:
                if pred_led > 1e-9:
                    led.append(math.log(real / pred_led))
                if pred_crude > 1e-9:
                    crude.append(math.log(real / pred_crude))

    def _rms(xs):
        return float(np.sqrt(np.mean(np.array(xs) ** 2))) if xs else 0.0
    return {"nowcast_state": nowcast_state,
            "recon_rms_ledger": _rms(led), "recon_rms_crude": _rms(crude),
            "n_recon": len(led)}


def crude_latent(world, obs, mode="baseline"):
    """Latent-state estimate from the OBSERVABLE top-line ONLY (no private nowcast).
      "baseline": the prior mean, (F=1, u=0) — L≈1 everywhere. The strict isolation: the
                  confounded top-line does not let you recover (F,u), so the honest no-ledger
                  fallback is the prior mean.
      "toplevel": a crude per-segment level from recent mean signups vs base_signups (biased,
                  because the funnel exponents are unknown/confounded)."""
    S = world.n()
    if mode == "baseline":
        return (1.0, [0.0] * S)
    signups = obs["signups"]; C = signups.shape[0]
    u = []
    for s, seg in enumerate(world.segments):
        recent = float(signups[max(0, C - LEDGER_RECON_WINDOW):C, s].mean())
        crude_L = min(3.0, max(0.2, recent / max(seg.base_signups, 1e-9)))
        u.append(crude_L - 1.0)
    return (1.0, u)


# ---------------------------------------------------------------------------
# State construction — mid-game states of the stochastic latent world
# ---------------------------------------------------------------------------

def build_state(world_seed, cutoff, vstar_seeds=VSTAR_SEEDS, crude_mode="baseline"):
    """One mid-game state = a world drawn at seed `world_seed`, simulated to a mid-game
    `cutoff` history horizon. Captures the (unobserved) latent_state and customers AT the
    cutoff; the held-out K periods are the decision horizon. Same-seed states at different
    cutoffs share theta + the latent-path prefix, so they cluster on their world."""
    world = mlw.draw_world(world_seed, horizon=cutoff)       # theta fixed by seed; horizon=cutoff
    obs = le.simulate_history(world)
    latent_state = (float(obs["latent_state"][0]), [float(x) for x in obs["latent_state"][1]])
    cust0 = [float(x) for x in obs["cust_final"]]
    budget = float(le.lsum(s.refmkt for s in world.segments))
    refmkt = np.array([s.refmkt for s in world.segments], float)
    ref_alloc = refmkt / refmkt.sum() * budget

    return {
        "world_seed": world_seed, "cutoff": cutoff,
        "world": world, "latent_state": latent_state, "cust0": cust0,
        "budget": budget, "ref_alloc": ref_alloc,
        "candidates": candidate_plans(world, budget),
        "ledger": make_ledger(world, obs, cutoff),
        "crude_state": crude_latent(world, obs, crude_mode),
        "path_seed": world_seed * 10007 + cutoff * 13 + MYOPIC_PATH_SALT,
        "vstar_seeds": vstar_seeds,
    }


def enumerate_states(n_states, cutoffs, seed_start, vstar_seeds=VSTAR_SEEDS,
                     crude_mode="baseline"):
    """First `n_states` (world_seed, cutoff) pairs, WORLD-MAJOR (each world contributes all
    cutoffs before advancing) so clusters have >1 state and the clustered bootstrap bites."""
    pairs = []
    seed = seed_start
    while len(pairs) < n_states:
        for c in cutoffs:
            pairs.append((seed, c))
            if len(pairs) >= n_states:
                break
        seed += 1
    return [build_state(s, c, vstar_seeds, crude_mode) for s, c in pairs]


# ---------------------------------------------------------------------------
# Ground truth V* + the four ranking signals
# ---------------------------------------------------------------------------

def vstar_values(state):
    """TRUE expected full-held-out-horizon profit under the state's realized latent
    continuation (from the TRUE latent state). The rank-off ground truth."""
    world = state["world"]
    return {n: le.held_out_profit(world, p, state["latent_state"], state["cust0"],
                                  n_seeds=state["vstar_seeds"], base_seed=VSTAR_BASE_SEED)[0]
            for n, p in state["candidates"].items()}


def _forward_value(state, plan, start_latent):
    """Cheap forward-sim projected value V-hat: expected held-out profit under `plan` from a
    GIVEN starting latent state, few seeds (base seed distinct from V*)."""
    v, _, _ = le.held_out_profit(state["world"], plan, start_latent, state["cust0"],
                                 n_seeds=FORWARD_SEEDS, base_seed=FORWARD_BASE_SEED)
    return v


def _deviation_weight(world, plan, ref_alloc, budget):
    """How far a candidate strays from the reference operating point, in [0, ~]. The private
    nowcast (and the reconciled error) is most trustworthy near the validated regime, so the
    reconciliation penalty is WEIGHTED by this deviation (spec: 'weighted reconciled
    prediction-error'). Reference plan -> 0. Bounded marketing term avoids log(0)."""
    S = world.n()
    price_dev = 0.0
    mkt_l1 = 0.0
    for i, seg in enumerate(world.segments):
        p = plan[seg.id]
        price_dev += abs(math.log(max(float(p["price"]), 1e-9) / seg.refprice))
        mkt_l1 += abs(float(p["marketing"]) - ref_alloc[i])
    price_dev /= S
    mkt_dev = 0.5 * mkt_l1 / max(budget, 1e-9)               # total-variation distance in [0,1]
    return 0.5 * price_dev + 0.5 * mkt_dev


def signal_myopic_actuals(state):
    """Realized ONE-step profit of each candidate under the state's SINGLE realized latent
    path (all candidates share the identical path — only levers differ). Grounded in actuals
    but blind to the horizon: rewards the short-term price trap."""
    world = state["world"]
    rng = np.random.default_rng(state["path_seed"])
    L_real, _ = le._latent_path(world, rng, world.held_out, state0=state["latent_state"])
    scores = {}
    for n, p in state["candidates"].items():
        _, per_period, _ = execute_path(world, p, state["cust0"], L_real)
        scores[n] = float(per_period[0])
    return scores


def judge_signal(state, candidates):
    """AGENTIC-JUDGE arm — a LIVE Fable-5 session with information parity and the pre-parsed
    per-candidate V-hat + delta table. STAGED: needs sessions; no model is called offline."""
    raise NotImplementedError(
        "live Fable-5 agentic-judge arm — staged, needs sessions")


def signal_scores(state, lam):
    """All four offline signals' per-candidate scores (higher = better) plus the V-hat maps.
    LEDGER/VHAT share one forward sim from the private nowcast; NO_LEDGER uses the top-line
    baseline. The reconciliation penalty is scaled by the mean |V-hat| so lambda is O(1)."""
    world, budget, ref_alloc = state["world"], state["budget"], state["ref_alloc"]
    order = list(state["candidates"].keys())
    nc = state["ledger"]["nowcast_state"]
    crude = state["crude_state"]
    rms_led = state["ledger"]["recon_rms_ledger"]
    rms_crude = state["ledger"]["recon_rms_crude"]

    vhat_led = {n: _forward_value(state, p, nc) for n, p in state["candidates"].items()}
    vhat_crude = {n: _forward_value(state, p, crude) for n, p in state["candidates"].items()}
    scale_led = float(np.mean([abs(v) for v in vhat_led.values()])) or 1.0
    scale_crude = float(np.mean([abs(v) for v in vhat_crude.values()])) or 1.0
    dev = {n: _deviation_weight(world, p, ref_alloc, budget)
           for n, p in state["candidates"].items()}

    sig = {
        "LEDGER_MECHANICAL": {n: vhat_led[n] - lam * rms_led * dev[n] * scale_led
                              for n in order},
        "VHAT_ONLY": {n: vhat_led[n] for n in order},
        "MECHANICAL_NO_LEDGER": {n: vhat_crude[n] - lam * rms_crude * dev[n] * scale_crude
                                 for n in order},
        "MYOPIC_ACTUALS": signal_myopic_actuals(state),
    }
    return sig, {"vhat_ledger": vhat_led, "vhat_crude": vhat_crude, "deviation": dev}


# ---------------------------------------------------------------------------
# Per-state scoring: Kendall-tau vs V* + top-1 regret
# ---------------------------------------------------------------------------

def _tau(sig_scores, vstar, order):
    x = [sig_scores[n] for n in order]
    y = [vstar[n] for n in order]
    tau = kendall_tau_b(x, y)
    return 0.0 if (tau is None or np.isnan(tau)) else float(tau)   # constant ranking -> no info


def _top1_regret(sig_scores, vstar, order):
    pick = max(order, key=lambda n: sig_scores[n])
    best = max(vstar.values())
    return float(best - vstar[pick]), pick


def score_state(state, lam):
    sig, extra = signal_scores(state, lam)
    vstar = vstar_values(state)
    order = list(state["candidates"].keys())
    best_val = max(vstar.values())
    vstar_spread = best_val - min(vstar.values())
    out = {"world_seed": state["world_seed"], "cutoff": state["cutoff"],
           "vstar": vstar, "vstar_best_candidate": max(order, key=lambda n: vstar[n]),
           "vstar_spread": float(vstar_spread), "signals": {}}
    for name in SIGNALS:
        tau = _tau(sig[name], vstar, order)
        regret, pick = _top1_regret(sig[name], vstar, order)
        out["signals"][name] = {"tau": tau, "top1_pick": pick, "top1_regret": regret,
                                "scores": sig[name]}
    out["diagnostics"] = {"recon_rms_ledger": state["ledger"]["recon_rms_ledger"],
                          "recon_rms_crude": state["ledger"]["recon_rms_crude"],
                          "deviation": extra["deviation"]}
    return out


# ---------------------------------------------------------------------------
# The handed projection, sorted, and its structure ablations
# ---------------------------------------------------------------------------

def projection_world(world, variant=PROJECTION_SORT):
    """The engine world the projection is computed on: the true world, or a copy with the
    structure mis-specified (coupling zeroed, sign flipped, elasticity halved, or price and churn effects
    zeroed)."""
    import copy
    if variant == PROJECTION_SORT:
        return world
    w = copy.deepcopy(world)
    if variant == PROJECTION_SORT_COUPLING_ZERO:
        w.coupling = [[0.0] * w.n() for _ in range(w.n())]
    elif variant == PROJECTION_SORT_SIGN_FLIPPED:
        w.coupling = [[-x for x in row] for row in w.coupling]
    elif variant == PROJECTION_SORT_ELAST_HALF:
        for s in w.segments:
            s.elast = s.elast * 0.5
    elif variant == PROJECTION_SORT_NO_PRICE_EFFECTS:
        for s in w.segments:
            s.elast = 0.0
            s.churn_price_exp = 0.0
    else:
        raise ValueError(f"unknown projection variant {variant!r}; choose from {PROJECTION_SIGNALS}")
    return w


def projection_scores(state, variant=PROJECTION_SORT):
    """Per-candidate projection from the prior-mean latent state (the crude state every judge's
    prompt carried), FORWARD_SEEDS paths at FORWARD_BASE_SEED, on ``projection_world(variant)``.
    For PROJECTION_SORT this is exactly signal_scores' ``vhat_crude`` (the unpenalised no-ledger sim)."""
    w = projection_world(state["world"], variant)
    return {n: le.held_out_profit(w, p, state["crude_state"], state["cust0"], n_seeds=FORWARD_SEEDS,
                                  base_seed=FORWARD_BASE_SEED)[0]
            for n, p in state["candidates"].items()}


def projection_report(states, vstars=None, bootstrap=BOOTSTRAP_B, variants=PROJECTION_SIGNALS) -> dict:
    """Kendall tau vs V* of the sorted handed projection and its ablations, per state and
    world-clustered (B = bootstrap, seed 12345, as in paper v7). ``vstars`` (one V* map per
    state, e.g. the offline report's per_state[i]["vstar"]) avoids recomputing V*."""
    per_state, taus = [], {v: [] for v in variants}
    for i, st in enumerate(states):
        vs = vstars[i] if vstars is not None else vstar_values(st)
        order = list(st["candidates"].keys())
        best = max(order, key=lambda n: vs[n])
        row = {"world_seed": st["world_seed"], "cutoff": st["cutoff"], "vstar_best_candidate": best,
               "signals": {}}
        for v in variants:
            sc = projection_scores(st, v)
            tau = _tau(sc, vs, order)
            regret, pick = _top1_regret(sc, vs, order)
            taus[v].append(tau)
            row["signals"][v] = {"tau": tau, "top1_pick": pick, "top1_correct": pick == best,
                                 "top1_regret": regret}
        per_state.append(row)
    world_ids = [r["world_seed"] for r in per_state]
    agg = {}
    for v in variants:
        m, lo, hi = clustered_bootstrap(taus[v], world_ids, bootstrap)
        agg[v] = {"mean_tau": m, "tau_ci95": [lo, hi],
                  "top1_correct": sum(1 for r in per_state if r["signals"][v]["top1_correct"]),
                  "n_states": len(per_state)}
    return {"design": "D2 projection sort: the no-ledger forward sim every judge was handed, unpenalised "
                      "(lambda = 0), and with the structure mis-specified",
            "config": {"forward_seeds": FORWARD_SEEDS, "forward_base_seed": FORWARD_BASE_SEED,
                       "bootstrap_B": bootstrap, "bootstrap_seed": 12345, "start_latent": "prior mean (F=1, u=0)"},
            "aggregate": agg, "per_state": per_state}


def projection_taus(proj_report, variant=PROJECTION_SORT) -> dict:
    """{(world_seed, cutoff): tau} of one projection variant (for d2_judge.aggregate_per_state)."""
    return {(r["world_seed"], r["cutoff"]): r["signals"][variant]["tau"] for r in proj_report["per_state"]}


# ---------------------------------------------------------------------------
# World-clustered bootstrap
# ---------------------------------------------------------------------------

def clustered_bootstrap(values, world_ids, B=BOOTSTRAP_B, seed=12345):
    """Mean of `values` with a 95% CI from resampling WORLDS (clusters) with replacement."""
    values = np.asarray(values, float)
    worlds = sorted(set(world_ids))
    idx_by_world = {w: [i for i, wid in enumerate(world_ids) if wid == w] for w in worlds}
    rng = np.random.default_rng(seed)
    means = np.empty(B)
    for b in range(B):
        picks = rng.integers(0, len(worlds), size=len(worlds))
        sel = []
        for p in picks:
            sel.extend(idx_by_world[worlds[p]])
        means[b] = values[sel].mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(values.mean()), float(lo), float(hi)


# ---------------------------------------------------------------------------
# lambda calibration (on DISJOINT worlds) — a sensible default is fine
# ---------------------------------------------------------------------------

def calibrate_lambda(cal_states, grid=(0.0, 0.25, 0.5, 1.0, 2.0, 4.0)):
    """Pick lambda maximizing LEDGER_MECHANICAL mean tau over DISJOINT calibration states
    (held-out from the evaluation states). Returns (best_lambda, {lambda: mean_tau})."""
    curve = {}
    for lam in grid:
        taus = []
        for st in cal_states:
            sig, _ = signal_scores(st, lam)
            vs = vstar_values(st)
            taus.append(_tau(sig["LEDGER_MECHANICAL"], vs, list(st["candidates"].keys())))
        curve[lam] = float(np.mean(taus))
    best = max(grid, key=lambda l: curve[l])
    return best, curve


def calibration_curve(cal_states, grid=(0.0, 0.25, 0.5, 1.0, 2.0, 4.0), vstars=None):
    """``calibrate_lambda`` computed once per state: the forward sims do not depend on lambda, so
    each state's LEDGER_MECHANICAL scores are re-weighted per lambda with signal_scores' arithmetic
    (vhat - lambda * rms * deviation * mean|vhat|). Same (best, curve) as calibrate_lambda, at a
    fraction of the cost."""
    taus = {lam: [] for lam in grid}
    for i, st in enumerate(cal_states):
        vs = vstars[i] if vstars is not None else vstar_values(st)
        order = list(st["candidates"].keys())
        nc = st["ledger"]["nowcast_state"]
        rms = st["ledger"]["recon_rms_ledger"]
        vhat = {n: _forward_value(st, p, nc) for n, p in st["candidates"].items()}
        scale = float(np.mean([abs(v) for v in vhat.values()])) or 1.0
        dev = {n: _deviation_weight(st["world"], p, st["ref_alloc"], st["budget"])
               for n, p in st["candidates"].items()}
        for lam in grid:
            taus[lam].append(_tau({n: vhat[n] - lam * rms * dev[n] * scale for n in order}, vs, order))
    curve = {lam: float(np.mean(taus[lam])) for lam in grid}
    best = max(grid, key=lambda l: curve[l])
    return best, curve


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def states_for(state_set="paper", n_states=16, cutoffs=DEFAULT_CUTOFFS,
               vstar_seeds=VSTAR_SEEDS, crude_mode="baseline"):
    """The D2 state set. "paper": the paper's 16 states, world seeds 1-8 x cutoffs
    {54, 72}, world-major. It includes seed 8, which FAILED step-0 certification,
    and 54-quarter histories that were never certified (ledger E6-13 / R14).
    "certified": the 11 certified D1 worlds at their certified 72-quarter horizon."""
    if state_set == "paper":
        return enumerate_states(n_states, cutoffs, WORLD_SEED_START, vstar_seeds, crude_mode)
    if state_set == "certified":
        return [build_state(s, 72, vstar_seeds, crude_mode) for s in CERTIFIED_SEEDS]
    raise ValueError(f"unknown state set {state_set!r}; choose from {STATE_SETS}")


def run(n_states, out_path, cutoffs=DEFAULT_CUTOFFS, vstar_seeds=VSTAR_SEEDS,
        lam=DEFAULT_LAMBDA, crude_mode="baseline", bootstrap=BOOTSTRAP_B,
        calibrate=False, n_cal=6, state_set="paper", verbose=True, states=None, lambda_zero=False):
    """The offline rank-off. With the defaults the report is the paper's file byte-for-byte.
    ``states`` reuses prebuilt states (same as states_for(...)); ``lambda_zero`` (new-run reports,
    the CLI default) adds a "lambda_zero" block: the ledger and no-ledger signals with the penalty
    off (VHAT_ONLY and the projection sort) beside the lambda actually used, plus the calibration note."""
    if states is None:
        states = states_for(state_set, n_states, cutoffs, vstar_seeds, crude_mode)

    cal_curve = None
    if calibrate:
        cal_states = enumerate_states(n_cal, cutoffs, CAL_SEED_START, vstar_seeds, crude_mode)
        lam, cal_curve = calibrate_lambda(cal_states)

    per_state = [score_state(st, lam) for st in states]
    world_ids = [r["world_seed"] for r in per_state]

    # aggregate per signal
    agg = {}
    tau_by_signal = {}
    for name in SIGNALS:
        taus = [r["signals"][name]["tau"] for r in per_state]
        regrets = [r["signals"][name]["top1_regret"] for r in per_state]
        tau_by_signal[name] = taus
        m_tau, lo_tau, hi_tau = clustered_bootstrap(taus, world_ids, bootstrap)
        m_reg, lo_reg, hi_reg = clustered_bootstrap(regrets, world_ids, bootstrap)
        agg[name] = {"mean_tau": m_tau, "tau_ci95": [lo_tau, hi_tau],
                     "mean_top1_regret": m_reg, "top1_regret_ci95": [lo_reg, hi_reg]}

    # paired contrasts (clustered CI on the per-state tau DIFFERENCE) for the decision rule
    contrasts = {}
    for a, b in (("LEDGER_MECHANICAL", "MYOPIC_ACTUALS"),
                 ("LEDGER_MECHANICAL", "MECHANICAL_NO_LEDGER"),
                 ("LEDGER_MECHANICAL", "VHAT_ONLY")):
        diff = [ta - tb for ta, tb in zip(tau_by_signal[a], tau_by_signal[b])]
        m, lo, hi = clustered_bootstrap(diff, world_ids, bootstrap)
        contrasts[f"{a}_minus_{b}"] = {"mean_tau_diff": m, "ci95": [lo, hi],
                                       "excludes_0": bool(lo > 0 or hi < 0)}

    closest = max(SIGNALS, key=lambda s: agg[s]["mean_tau"])
    report = {
        "design": "D2 reward-signal-quality rank-off (offline)",
        "config": {"n_states": len(states), "worlds": sorted(set(world_ids)),
                   "cutoffs": list(cutoffs), "candidates": list(CANDIDATE_NAMES),
                   "vstar_seeds": vstar_seeds, "forward_seeds": FORWARD_SEEDS,
                   "lambda": lam, "lambda_calibrated": bool(calibrate),
                   "lambda_curve": cal_curve, "crude_mode": crude_mode,
                   "ledger_noise_sd": LEDGER_NOISE_SD, "bootstrap_B": bootstrap,
                   **({} if state_set == "paper" else {"state_set": state_set})},
        "aggregate": agg,
        "contrasts": contrasts,
        "closest_to_vstar": closest,
        "agentic_judge": "STAGED — judge_signal raises NotImplementedError (needs live sessions)",
        "per_state": per_state,
    }
    if lambda_zero:
        proj = projection_report(states, [r["vstar"] for r in per_state], bootstrap, (PROJECTION_SORT,))
        report["lambda_zero"] = {
            "lambda_used": lam,
            "LEDGER_MECHANICAL": {"lambda_used": agg["LEDGER_MECHANICAL"], "lambda_0": agg["VHAT_ONLY"]},
            "MECHANICAL_NO_LEDGER": {"lambda_used": agg["MECHANICAL_NO_LEDGER"],
                                     "lambda_0": proj["aggregate"][PROJECTION_SORT]},
            "note": LAMBDA_CALIBRATION_NOTE,
            "per_state_projection_sort_tau": [r["signals"][PROJECTION_SORT]["tau"] for r in proj["per_state"]],
        }
    if out_path:
        Path(out_path).write_text(report_json(report))
    if verbose:
        _print_summary(report)
    return report


def report_json(report) -> str:
    """The report file text, exactly as the paper wrote it."""
    return json.dumps(report, indent=2, default=float) + "\n"


def _print_summary(report):
    agg = report["aggregate"]
    cfg = report["config"]
    print(f"\nD2 reward-signal rank-off  (offline)  "
          f"n_states={cfg['n_states']}  worlds={len(cfg['worlds'])}  "
          f"cutoffs={cfg['cutoffs']}  lambda={cfg['lambda']}")
    print(f"  candidates (M={len(cfg['candidates'])}): {', '.join(cfg['candidates'])}")
    print(f"  V* seeds={cfg['vstar_seeds']}  forward seeds={cfg['forward_seeds']}  "
          f"crude={cfg['crude_mode']}")
    print("\n  signal                 mean_tau   tau 95% CI            mean_top1_regret")
    print("  " + "-" * 74)
    for name in SIGNALS:
        a = agg[name]
        star = "  <== closest to V*" if name == report["closest_to_vstar"] else ""
        print(f"  {name:<22}{a['mean_tau']:+.3f}    "
              f"[{a['tau_ci95'][0]:+.3f}, {a['tau_ci95'][1]:+.3f}]   "
              f"{a['mean_top1_regret']:>14,.0f}{star}")
    print("\n  paired tau contrasts (world-clustered 95% CI on the difference):")
    for k, v in report["contrasts"].items():
        flag = "  *CI excludes 0*" if v["excludes_0"] else ""
        print(f"    {k:<48}{v['mean_tau_diff']:+.3f}  "
              f"[{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]{flag}")
    lz = report.get("lambda_zero")
    if lz:
        print(f"\n  penalty weight side by side (lambda used = {lz['lambda_used']} vs lambda = 0):")
        for name, label0 in (("LEDGER_MECHANICAL", "VHAT_ONLY"), ("MECHANICAL_NO_LEDGER", "PROJECTION_SORT")):
            a, b = lz[name]["lambda_used"], lz[name]["lambda_0"]
            print(f"    {name:<22}{a['mean_tau']:+.3f} [{a['tau_ci95'][0]:+.3f}, {a['tau_ci95'][1]:+.3f}]   "
                  f"lambda=0 ({label0}) {b['mean_tau']:+.3f} [{b['tau_ci95'][0]:+.3f}, {b['tau_ci95'][1]:+.3f}]")
        print(f"    {lz['note']}")
    print(f"\n  agentic-judge arm: {report['agentic_judge']}")


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def selftest():
    problems = []
    st = build_state(WORLD_SEED_START, DEFAULT_CUTOFFS[-1], vstar_seeds=120)
    world = st["world"]

    # 1) candidate set: 6 distinct, budget-feasible plans
    cands = st["candidates"]
    if tuple(cands.keys()) != CANDIDATE_NAMES:
        problems.append(f"candidate names {tuple(cands.keys())} != {CANDIDATE_NAMES}")
    else:
        over = [n for n, p in cands.items()
                if mlw.total_marketing(world, p) > st["budget"] * 1.0001]
        if over:
            problems.append(f"candidates over budget: {over}")
        else:
            print(f"  [ok] {len(cands)} distinct budget-feasible candidates")

    # 2) ORACLE-EQUIVALENCE: my executor's held-out-profit mean matches le.held_out_profit
    plan = cands["reference"]
    mine = _held_out_profit_mine(world, plan, st["latent_state"], st["cust0"],
                                 n_seeds=64, base_seed=7)
    ref = le.held_out_profit(world, plan, st["latent_state"], st["cust0"],
                             n_seeds=64, base_seed=7)[0]
    if abs(mine - ref) > 1e-9:
        problems.append(f"executor != le.held_out_profit: |{mine}-{ref}|={abs(mine-ref):.3e}")
    else:
        print(f"  [ok] executor matches le.held_out_profit to <1e-9 (|d|={abs(mine-ref):.2e})")

    # 2b) single-path oracle-equivalence vs le._held_out_once with matched rng
    rng1 = np.random.default_rng(321)
    L = le._latent_path(world, rng1, world.held_out, state0=st["latent_state"])[0]
    tot_mine, _, _ = execute_path(world, plan, st["cust0"], L)
    rng2 = np.random.default_rng(321)
    tot_le, _ = le._held_out_once(world, plan, st["latent_state"], st["cust0"], rng2)
    # Relative, not absolute: the totals are ~1e7, and two summation orders differ in the
    # last bits on Linux/glibc (2.98e-08 absolute = 3e-15 relative in the 2026-10-03
    # fresh-clone check) while being bit-identical on macOS (diff-bwm-1 change).
    if abs(tot_mine - tot_le) > 1e-12 * max(1.0, abs(tot_le)):
        problems.append(f"execute_path != le._held_out_once: {abs(tot_mine-tot_le):.3e}")
    else:
        print(f"  [ok] execute_path matches le._held_out_once on a fixed path (|d|={abs(tot_mine-tot_le):.2e})")

    # 3) V* determinism given base_seed
    v1 = le.held_out_profit(world, plan, st["latent_state"], st["cust0"], 100, VSTAR_BASE_SEED)[0]
    v2 = le.held_out_profit(world, plan, st["latent_state"], st["cust0"], 100, VSTAR_BASE_SEED)[0]
    if v1 != v2:
        problems.append("le.held_out_profit not deterministic given base_seed")
    else:
        print("  [ok] V* deterministic given base_seed")

    # 4) JUDGE hook raises NotImplementedError (no model called)
    try:
        judge_signal(st, cands)
        problems.append("judge_signal did NOT raise NotImplementedError")
    except NotImplementedError:
        print("  [ok] judge_signal raises NotImplementedError (staged live arm)")

    # 5) private nowcast carries info top-line lacks: ledger recon error < crude recon error
    if st["ledger"]["recon_rms_ledger"] >= st["ledger"]["recon_rms_crude"]:
        print(f"  [warn] ledger recon rms ({st['ledger']['recon_rms_ledger']:.3f}) not below "
              f"crude ({st['ledger']['recon_rms_crude']:.3f}) on this state (state-specific; "
              f"not fatal)")
    else:
        print(f"  [ok] ledger recon rms {st['ledger']['recon_rms_ledger']:.3f} < crude "
              f"{st['ledger']['recon_rms_crude']:.3f} (private nowcast tracks better)")

    # 6) tiny 3-state end-to-end: valid taus + a real V* spread on every state
    states = enumerate_states(3, DEFAULT_CUTOFFS, WORLD_SEED_START, vstar_seeds=120)
    for st3 in states:
        r = score_state(st3, DEFAULT_LAMBDA)
        if r["vstar_spread"] <= 0:
            problems.append(f"degenerate V* (no spread) at seed={st3['world_seed']} "
                            f"cutoff={st3['cutoff']}")
        for name in SIGNALS:
            tau = r["signals"][name]["tau"]
            if not (-1.0 - 1e-9 <= tau <= 1.0 + 1e-9):
                problems.append(f"tau out of range for {name}: {tau}")
    if not any("tau out of range" in p or "degenerate V*" in p for p in problems):
        print("  [ok] 3-state end-to-end: all taus in [-1,1], every state has V* spread")

    if problems:
        print("\nSELFTEST FAILED:")
        for p in problems:
            print("  -", p)
        raise SystemExit(1)
    print("selftest OK — D2 offline pipeline is deterministic and oracle-equivalent.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def configure_parser(ap):
    sub = ap.add_subparsers(dest="d2cmd", required=True)
    sub.add_parser("selftest")
    r = sub.add_parser("run")
    r.add_argument("--n-states", type=int, default=16)
    r.add_argument("--out", required=True)
    r.add_argument("--cutoffs", default=",".join(str(c) for c in DEFAULT_CUTOFFS),
                   help="comma-separated mid-game history cutoffs")
    r.add_argument("--vstar-seeds", type=int, default=VSTAR_SEEDS)
    r.add_argument("--lambda", dest="lam", type=float, default=DEFAULT_LAMBDA)
    r.add_argument("--calibrate", action="store_true",
                   help="calibrate lambda on DISJOINT worlds (overrides --lambda)")
    r.add_argument("--crude-mode", default="baseline", choices=["baseline", "toplevel"])
    r.add_argument("--bootstrap", type=int, default=BOOTSTRAP_B)
    r.add_argument("--state-set", default="paper", choices=STATE_SETS,
                   help="paper = the paper's 16 states (incl. decertified seed 8); "
                        "certified = the 11 certified worlds at cutoff 72")
    r.add_argument("--paper-format", action="store_true",
                   help="write the paper's report shape only (no lambda = 0 side-by-side block)")
    return ap


def run_cli(args) -> int:
    if args.d2cmd == "selftest":
        selftest()
        return 0
    cutoffs = tuple(int(x) for x in args.cutoffs.split(",") if x.strip())
    run(args.n_states, args.out, cutoffs=cutoffs, vstar_seeds=args.vstar_seeds,
        lam=args.lam, crude_mode=args.crude_mode, bootstrap=args.bootstrap,
        calibrate=args.calibrate, state_set=args.state_set,
        lambda_zero=not getattr(args, "paper_format", False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    return run_cli(configure_parser(ap).parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
