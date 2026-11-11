"""Step-0 gate: certify that a latent world is verified-hard AND carries a
non-recoverable, prior-free information moat.

SCORER SIDE (computes M* and reads the true coupling). Ported from the paper
harness (``bwm_latent/certify_world.py``); ``certify`` and its helpers are
unchanged except the budget sum (``latent_engine.lsum``, pre-3.12 float
summation); the CLI gained ``--seed-salt``. ``private/eval6/certify_scan.json``
(the shipped 12-seed scan, n_seeds=400) is the normative v1 certificate.

What the gate actually tests (docs/evals/eval6.md, "Step-0 certification"):

1. VERIFIED-HARD. M* (grid optimum vs the true theta, base_seed 101) beats the
   best of four naive heuristics (reference, raise_price, concentrate, spread)
   by more than 4 standard errors and by more than 3%. ``tau`` is the midpoint
   between the best naive plan and M*.

2. COUPLING SIGN NOT RECOVERABLE (majority rule). For each of the 3 spokes,
   regress log(signups_s) on [1, log mkt_s, log price_s, log mkt_hub]; a spoke
   is "recovered" when the 95% CI on the log(mkt_hub) coefficient excludes 0
   with the true sign. The gate needs a MAJORITY of spokes non-recovered
   (``non_recov >= (n+1)//2``), not every spoke: in the shipped scan w9 and
   w10 each have one spoke recovered, w4 has one spoke whose CI excludes 0 with
   the wrong sign, and the failed seed 8 has 2 of 3 spokes recovered.

3. DECISION-RELEVANT. Acting on a gamma = 0 belief (coupling zeroed; the plan
   optimized under that belief, base_seed 202) loses more than 2% of M* and
   more than 4 SE under the true dynamics. This is a "no-coupling belief", not
   a flipped-sign belief.

The price-elasticity trap is reported but flagged prior-pinnable (law of demand).
Runtime: about 4.5 minutes of CPU per world at 400 Monte-Carlo seeds.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import numpy as np

from .. import latent_engine as le
from . import make_latent_world as mlw


# ---------------------------------------------------------------------------
# Identifiability of the coupling sign from the history (proper nested OLS)
# ---------------------------------------------------------------------------

def _ols(y, X):
    """OLS with heteroskedasticity-agnostic classical SEs. Returns (beta, se, rmse)."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    dof = max(1, n - k)
    s2 = float(resid @ resid) / dof
    XtX_inv = np.linalg.inv(X.T @ X)
    se = np.sqrt(np.maximum(0.0, np.diag(XtX_inv) * s2))
    rmse = float(np.sqrt(np.mean(resid ** 2)))
    return beta, se, rmse


def coupling_identifiability(world, obs, hub):
    """Can a best-effort analyst recover the SIGN of the hub->spoke marketing
    spillover from the observable history? For each spoke segment, regress
    log(signups_s) on [const, log(mkt_s), log(price_s), log(mkt_hub)]. The
    log(mkt_hub) coefficient IS the identified cross-segment effect. If its 95% CI
    includes 0 (both signs plausible) or its sign is wrong, the coupling sign is
    NOT recoverable — because hub marketing co-moves with the spoke's own drivers
    through the shared demand factor (no exogenous variation to break the confound).

    Returns per-spoke {est, se, t, ci, true_sign, recovered, ci_includes_0} and an
    aggregate `sign_non_recoverable` (majority of spokes not sign-recoverable).
    """
    mkt, price, signups = obs["marketing"], obs["price"], obs["signups"]
    T, S = signups.shape
    lmk_hub = np.log(mkt[:, hub])
    rows = {}
    non_recov = 0
    spokes = [s for s in range(S) if s != hub]
    for s in spokes:
        y = np.log(np.maximum(1e-6, signups[:, s]))
        X = np.column_stack([np.ones(T), np.log(mkt[:, s]),
                             np.log(price[:, s]), lmk_hub])
        beta, se, _ = _ols(y, X)
        est, est_se = float(beta[3]), float(se[3])
        t = est / est_se if est_se > 0 else 0.0
        ci = (est - 1.96 * est_se, est + 1.96 * est_se)
        true_sign = 1.0 if world.coupling[s][hub] > 0 else (-1.0 if world.coupling[s][hub] < 0 else 0.0)
        ci_incl_0 = ci[0] <= 0 <= ci[1]
        recovered = (not ci_incl_0) and (np.sign(est) == true_sign)
        if not recovered:
            non_recov += 1
        rows[world.segments[s].id] = {
            "est": est, "se": est_se, "t": t, "ci": [ci[0], ci[1]],
            "true_coupling": world.coupling[s][hub], "true_sign": true_sign,
            "ci_includes_zero": bool(ci_incl_0), "sign_recovered": bool(recovered)}
    return {"hub": world.segments[hub].id, "per_spoke": rows,
            "n_spokes": len(spokes), "n_sign_non_recoverable": non_recov,
            "sign_non_recoverable": non_recov >= (len(spokes) + 1) // 2}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def _detect_hub(world):
    """The hub is the column with the most nonzero coupling entries (the source of
    the cross-segment spillover in draw_world's hub-and-spoke structure)."""
    S = world.n()
    counts = [sum(1 for s in range(S) if world.coupling[s][j] != 0.0) for j in range(S)]
    return int(np.argmax(counts))


def certify(world: le.LatentWorld, n_seeds=400, verbose=True):
    obs = le.simulate_history(world)
    ls, c0 = obs["latent_state"], obs["cust_final"]
    budget = float(le.lsum(s.refmkt for s in world.segments))
    noise_floor = world.meas_noise  # measurement-noise RMS on log-signups (~0.02)

    def prof(plan, seed=101, ns=n_seeds):
        return le.held_out_profit(world, plan, ls, c0, n_seeds=ns, base_seed=seed)

    # ---- 1. verified-hard --------------------------------------------------
    star_plan, star, sstar = le.optimize_plan(world, ls, c0, n_seeds=n_seeds,
                                              base_seed=101, budget=budget)
    refs = mlw.reference_plans(world, budget)
    ref_vals = {name: prof(plan)[0] for name, plan in refs.items()}
    best_naive_name = max(ref_vals, key=ref_vals.get)
    best_naive = ref_vals[best_naive_name]
    # tau placed at the midpoint between the best naive plan and M*
    tau = best_naive + 0.5 * (star - best_naive)
    verified_hard = star > best_naive + 4 * sstar and (star - best_naive) / abs(star) > 0.03

    # ---- 2. gamma non-identifiability (the prior-free moat) ----------------
    # (a) SIGN NON-RECOVERABILITY: can a best-effort OLS analyst recover the sign of
    #     the hub->spoke spillover from history? (proper nested regression w/ SEs)
    hub = _detect_hub(world)
    ident = coupling_identifiability(world, obs, hub)
    gamma_obs_equiv = ident["sign_non_recoverable"]
    # (b) DECISION-RELEVANCE: acting on the wrong belief (gamma=0) costs real profit.
    zero_world = copy.deepcopy(world)
    zero_world.coupling = [[0.0] * world.n() for _ in range(world.n())]
    zero_plan, _, _ = le.optimize_plan(zero_world, ls, c0, n_seeds=n_seeds,
                                       base_seed=202, budget=budget)
    zero_true = prof(zero_plan, seed=101)[0]
    gamma_profit_gap = (star - zero_true) / abs(star)
    gamma_decision_relevant = (star - zero_true) > 4 * sstar and gamma_profit_gap > 0.02
    gamma_moat = gamma_obs_equiv and gamma_decision_relevant

    # ---- 3. price-elasticity trap (reported; prior-pinnable) ---------------
    # a naive analyst reads +slope from history and raises price. true profit of that.
    price_gap = (star - ref_vals["raise_price"]) / abs(star)

    report = {
        "world_id": world.id, "seed": world.seed, "n_segments": world.n(),
        "budget": budget, "n_seeds": n_seeds, "noise_floor_rms": noise_floor,
        "verified_hard": {
            "mstar": star, "mstar_stderr": sstar,
            "reference_true_profit": ref_vals,
            "best_naive": best_naive_name, "best_naive_profit": best_naive,
            "tau": tau,
            "mstar_over_best_naive_pct": (star - best_naive) / abs(star) * 100,
            "PASS": bool(verified_hard),
        },
        "gamma_moat": {
            "coupling": [[round(x, 4) for x in row] for row in world.coupling],
            "hub": ident["hub"], "identifiability": ident,
            "sign_non_recoverable": bool(gamma_obs_equiv),
            "n_sign_non_recoverable": ident["n_sign_non_recoverable"],
            "n_spokes": ident["n_spokes"],
            "mstar": star, "gamma_zero_belief_true_profit": zero_true,
            "true_profit_gap_pct": gamma_profit_gap * 100,
            "decision_relevant": bool(gamma_decision_relevant),
            "PASS": bool(gamma_moat),
            "interpretation": ("history cannot recover the SIGN of the cross-segment spillover "
                               "(hub marketing co-moves with spoke demand via the shared "
                               "factor), yet the belief changes the optimal allocation and its "
                               "realized profit"),
        },
        "price_trap": {
            "raise_price_true_profit": ref_vals["raise_price"],
            "mstar_over_raise_price_pct": price_gap * 100,
            "note": "prior-pinnable (law of demand) — compute-bound trap, not a pure moat",
        },
        "GATE_PASS": bool(verified_hard and gamma_moat),
    }
    if verbose:
        _print(report)
    return report


def _print(r):
    vh, gm = r["verified_hard"], r["gamma_moat"]
    print(f"\n=== step-0 certification: {r['world_id']} (seed {r['seed']}, "
          f"{r['n_segments']} segments, {r['n_seeds']} MC seeds) ===")
    print(f"[verified-hard] M*={vh['mstar']:.0f} (±{vh['mstar_stderr']:.0f}); "
          f"best naive '{vh['best_naive']}'={vh['best_naive_profit']:.0f}; "
          f"M* beats best naive by {vh['mstar_over_best_naive_pct']:.1f}%  "
          f"-> {'PASS' if vh['PASS'] else 'FAIL'}")
    for k, v in vh["reference_true_profit"].items():
        print(f"      naive {k:12s} true profit {v:.0f}")
    print(f"[gamma moat]   hub='{gm['hub']}'; sign recovered from history for "
          f"{gm['n_spokes'] - gm['n_sign_non_recoverable']}/{gm['n_spokes']} spokes "
          f"-> sign_non_recoverable={gm['sign_non_recoverable']}")
    for sid, row in gm["identifiability"]["per_spoke"].items():
        print(f"      spoke {sid:8s} true_gamma={row['true_coupling']:+.3f}  "
              f"OLS est={row['est']:+.3f}±{row['se']:.3f} (t={row['t']:+.1f}), "
              f"CI[{row['ci'][0]:+.2f},{row['ci'][1]:+.2f}] "
              f"{'incl 0' if row['ci_includes_zero'] else 'sign '+('OK' if row['sign_recovered'] else 'WRONG')}")
    print(f"               acting on gamma=0 belief earns {gm['gamma_zero_belief_true_profit']:.0f} "
          f"vs M* {gm['mstar']:.0f} -> {gm['true_profit_gap_pct']:.1f}% worse, "
          f"decision-relevant={gm['decision_relevant']}")
    print(f"               PRIOR-FREE MOAT -> {'PASS' if gm['PASS'] else 'FAIL'}")
    print(f"[price trap]   M* beats naive raise-price by {r['price_trap']['mstar_over_raise_price_pct']:.1f}% "
          f"(prior-pinnable; reported not decisive)")
    print(f"\nGATE: {'PASS — a non-recoverable + verified-hard world exists' if r['GATE_PASS'] else 'FAIL'}\n")


def configure_parser(ap):
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--n-seg", type=int, default=4)
    ap.add_argument("--n-seeds", type=int, default=400)
    ap.add_argument("--scan", type=int, default=0,
                    help="if >0, certify seeds 1..N and report the pass rate")
    ap.add_argument("--seed-salt", default=None,
                    help="certify a salted private-split world (see make_latent_world)")
    ap.add_argument("--out", default=None)
    return ap


def run(args) -> int:
    if args.scan:
        reports = []
        for sd in range(1, args.scan + 1):
            w = mlw.draw_world(sd, n_seg=args.n_seg, seed_salt=args.seed_salt)
            r = certify(w, n_seeds=args.n_seeds, verbose=False)
            reports.append(r)
            gm = r["gamma_moat"]
            print(f"seed {sd}: GATE={'PASS' if r['GATE_PASS'] else 'fail':4s}  "
                  f"hard={r['verified_hard']['PASS']} "
                  f"(M* +{r['verified_hard']['mstar_over_best_naive_pct']:.1f}%)  "
                  f"gamma_moat={gm['PASS']} "
                  f"(sign_non_recov={gm['sign_non_recoverable']} "
                  f"{gm['n_sign_non_recoverable']}/{gm['n_spokes']}, "
                  f"decision_gap {gm['true_profit_gap_pct']:.1f}%)")
        n_pass = sum(r["GATE_PASS"] for r in reports)
        print(f"\nGATE pass rate: {n_pass}/{len(reports)} worlds")
        if args.out:
            Path(args.out).write_text(json.dumps(reports, indent=2, default=float) + "\n")
        return 0

    w = mlw.draw_world(args.seed, n_seg=args.n_seg, seed_salt=args.seed_salt)
    r = certify(w, n_seeds=args.n_seeds)
    if args.out:
        Path(args.out).write_text(json.dumps(r, indent=2, default=float) + "\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    return run(configure_parser(ap).parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
