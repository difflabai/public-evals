"""Generator for eval-6 D1 latent-confounded worlds and the held-out decision task.

SCORER SIDE: with a world's seed this module regenerates its answer key exactly,
so it never enters a sandbox.

Paper layout (``emit``; byte-identical to the paper harness):
  <world>/public/history.csv     observable endogenous-policy history (agent sees this)
  <world>/public/task.json       structural FORM (no parameter values) + decision spec
  <world>/ANSWER_KEY/theta.json  true parameters (sequestered)
  <world>/ANSWER_KEY/mstar.json  the grid optimum M* + reference-plan true profits
Package layout (``emit_split``): public/ goes under data/eval6/worlds/<id>/ and
ANSWER_KEY/ under private/eval6/worlds/<id>/.

THE DECISION TASK (held-out horizon, K periods)
  Per segment, choose a constant MARKETING level and a PRICE multiplier for the
  K held-out quarters, subject to a total marketing budget cap. Scored by the
  true-dynamics Monte-Carlo oracle. Two decision-relevant quantities are
  confounded in the history:
    - price elasticity (< 0): prior-pinnable (law of demand);
    - hub -> spoke coupling (complement vs cannibalize): prior-free and not
      identified by the history. This is the information moat.

PRIVATE SPLITS (``--seed-salt``). The public worlds w1..w12 are derived from
their seed (rng = default_rng(seed*7919+17), world id "w<seed>"), so once this
generator is public their answer keys are public too: they are a dev split.
With a secret salt the theta draw uses default_rng([salt_int, seed]), the
history noise uses a salt-derived 63-bit seed, and the world id is opaque
("p" + 10 hex digits), so organizers can generate and certify a held-out split
nobody else can regenerate. Without a salt every output is byte-identical to
the paper harness.

Ported from the paper harness (``bwm_latent/make_latent_world.py``): draw_world,
reference_plans, total_marketing and emit are unchanged apart from the salt
branch, writing bytes (no platform newline translation), and float sums via
``latent_engine.lsum`` (pre-3.12 summation; see its docstring).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

import numpy as np

from .. import latent_engine as le
from ..latent_engine import Segment, LatentWorld


# ---------------------------------------------------------------------------
# Seed salting (private splits)
# ---------------------------------------------------------------------------

def _salt_digest(salt: str, label: str, seed: int) -> bytes:
    return hashlib.sha256(f"dbwm-eval6|{label}|{salt}|{int(seed)}".encode()).digest()


def salt_int(salt: str) -> int:
    """The salt as a 256-bit integer entropy word for numpy's SeedSequence."""
    return int.from_bytes(hashlib.sha256(f"dbwm-eval6|salt|{salt}".encode()).digest(), "big")


def salted_world_id(salt: str, seed: int) -> str:
    """Opaque world id: reveals neither the seed nor the salt."""
    return "p" + _salt_digest(salt, "id", seed).hex()[:10]


def salted_history_seed(salt: str, seed: int) -> int:
    """63-bit seed for the history noise of a salted world (world.seed)."""
    return int.from_bytes(_salt_digest(salt, "history", seed)[:8], "big") >> 1


# ---------------------------------------------------------------------------
# Draw a plausible, hard world
# ---------------------------------------------------------------------------

def draw_world(seed, n_seg=4, horizon=72, held_out=8, coupling_lo=0.40, coupling_scale=0.60,
               factor_sigma=0.32, factor_load_lo=1.0, factor_load_hi=1.9,
               spoke_idio=0.05, hub_idio=0.012, meas_noise=0.05, policy_noise=0.015,
               seed_salt=None):
    """Draw a hard, confounded world. The HUB segment's marketing spillover onto the
    spokes' demand is the prior-free moat; it is non-identified because the hub's
    latent demand (hence its historical marketing) is driven ALMOST ENTIRELY by the
    shared market factor (tiny idiosyncratic `hub_idio`). With no idiosyncratic hub
    variation, the history contains no exogenous swing in hub marketing to identify
    the causal cross-effect — hub marketing merely proxies the common factor, which
    also drives spoke demand. In the held-out horizon the agent SETS hub marketing
    independently of the factor, so the coupling becomes causally decisive.

    ``seed_salt`` (None = the paper's public derivation) switches the theta RNG to
    default_rng([salt_int, seed]) and gives the world an opaque id and a
    salt-derived history seed."""
    if seed_salt is None:
        rng = np.random.default_rng(seed * 7919 + 17)
        world_id, world_seed = f"w{seed}", seed
    else:
        rng = np.random.default_rng([salt_int(seed_salt), int(seed)])
        world_id, world_seed = salted_world_id(seed_salt, seed), salted_history_seed(seed_salt, seed)
    ids = ["core", "smb", "ent", "growth", "mid", "intl"][:n_seg]
    hub = int(rng.integers(0, n_seg))
    segs = []
    for i, sid in enumerate(ids):
        segs.append(Segment(
            id=sid,
            base_signups=float(rng.uniform(120, 300)),
            acv=float(rng.uniform(900, 1400)),
            mktexp=float(rng.uniform(0.35, 0.55)),
            elast=float(rng.uniform(-1.8, -1.2)),           # stronger true price elasticity
            churn0=float(rng.uniform(0.04, 0.09)),
            churn_price_exp=float(rng.uniform(1.3, 2.2)),   # stronger price->churn (real trap)
            rho=float(rng.uniform(0.5, 0.8)),
            sigma=(hub_idio if i == hub else float(rng.uniform(spoke_idio * 0.8, spoke_idio * 1.2))),
            factor_load=float(rng.uniform(factor_load_lo, factor_load_hi)),
            refmkt=float(rng.uniform(40000, 90000)),
            refprice=1.0,
            phi=float(rng.uniform(0.6, 1.0)),
            psi=float(rng.uniform(0.35, 0.6)),
            gm=float(rng.uniform(0.7, 0.82)),
            fixed=float(rng.uniform(30000, 60000)),
            cust0=float(rng.uniform(1500, 4000))))
    S = len(segs)
    coupling = [[0.0] * S for _ in range(S)]
    # single sign per world (complement vs cannibalize is a genuine coin-flip a priori)
    sign = float(rng.choice([-1.0, 1.0]))
    for s in range(S):
        if s == hub:
            continue
        coupling[s][hub] = sign * float(rng.uniform(coupling_lo, coupling_scale))
    return LatentWorld(id=world_id, segments=segs, coupling=coupling,
                       horizon=horizon, held_out=held_out, factor_sigma=factor_sigma,
                       meas_noise=meas_noise, policy_noise=policy_noise, seed=world_seed)


# ---------------------------------------------------------------------------
# Reference/naive plans (for hardness verification)
# ---------------------------------------------------------------------------

def reference_plans(world: LatentWorld, budget):
    """Named naive heuristics an unaided analyst might commit. Each respects the
    total marketing budget. Returns {name: plan}."""
    S = world.n()
    refmkt = np.array([s.refmkt for s in world.segments])
    base = refmkt / refmkt.sum() * budget  # keep reference proportions, scaled to budget
    plans = {}
    # do-nothing-ish: reference levers scaled to budget, price at reference
    plans["reference"] = {s.id: {"marketing": float(base[i]), "price": s.refprice}
                          for i, s in enumerate(world.segments)}
    # naive-OLS: history says price up => signups up, so RAISE price; spend to budget
    plans["raise_price"] = {s.id: {"marketing": float(base[i]), "price": s.refprice * 1.3}
                            for i, s in enumerate(world.segments)}
    # concentrate budget in the biggest segment (bets coupling is a complement)
    conc = np.zeros(S); conc[int(np.argmax([s.base_signups * s.acv for s in world.segments]))] = budget
    plans["concentrate"] = {s.id: {"marketing": float(conc[i]), "price": s.refprice}
                            for i, s in enumerate(world.segments)}
    # spread evenly (bets coupling cannibalizes)
    plans["spread"] = {s.id: {"marketing": float(budget / S), "price": s.refprice}
                       for i, s in enumerate(world.segments)}
    return plans


def total_marketing(world, plan):
    return le.lsum(float(plan[s.id]["marketing"]) for s in world.segments)


# ---------------------------------------------------------------------------
# File builders (bytes, so emit/verify share one code path)
# ---------------------------------------------------------------------------

def history_csv_bytes(world: LatentWorld, obs) -> bytes:
    """public/history.csv exactly as the paper wrote it (csv module, CRLF rows)."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(["period", "segment", "marketing", "price", "signups",
                "customers", "churn", "revenue"])
    for t in range(world.horizon):
        for si, seg in enumerate(world.segments):
            w.writerow([t, seg.id, round(obs["marketing"][t, si], 2),
                        round(obs["price"][t, si], 5), round(obs["signups"][t, si], 3),
                        round(obs["customers"][t, si], 3), round(obs["churn"][t, si], 5),
                        round(obs["revenue"][t, si], 2)])
    return buf.getvalue().encode()


def task_dict(world: LatentWorld):
    budget = float(le.lsum(s.refmkt for s in world.segments))  # decision budget = hist reference total
    return {
        "world_id": world.id,
        "structural_form": world.to_public()["form"],
        "segments": [s.id for s in world.segments],
        "history_periods": world.horizon,
        "decision_horizon": world.held_out,
        "decision": ("Choose, per segment, a constant MARKETING level (usd/quarter) and a "
                     "PRICE multiplier (1.0 = reference) for each of the "
                     f"{world.held_out} held-out quarters."),
        "budget_constraint": {"metric": "total_marketing_per_quarter", "cap": round(budget, 2),
                              "note": "sum of per-segment marketing must be <= cap"},
        "price_bounds": [le.PRICE_LO, le.PRICE_HI],
        "price_bounds_note": (f"the price multiplier is only calibrated in "
                              f"[{le.PRICE_LO}, {le.PRICE_HI}] and is CLAMPED to that band; "
                              "values outside are not meaningful (the model is not valid there)"),
        "objective": "maximize total expected gross profit over the held-out horizon",
        "starting_state_note": ("customers at the end of history are given in history.csv; "
                                "latent demand continues from its (unobserved) end state"),
        "reference_marketing": {s.id: round(s.refmkt, 2) for s in world.segments},
    }


def task_json_bytes(world: LatentWorld) -> bytes:
    return (json.dumps(task_dict(world), indent=2) + "\n").encode()


def theta_dict(world: LatentWorld):
    # theta = the sequestered answer key. Augment to_answer_key() with the world-level
    # DYNAMICS params (segment order + the AR/noise/history-policy fields) so that
    # le.world_from_dict(theta) reconstructs the EXACT world M* was scored against —
    # without these, world_from_dict falls back to engine defaults (e.g. factor_sigma
    # 0.16 vs this world's 0.32) and the held-out oracle drifts ~1% off the stored M*.
    theta = world.to_answer_key()
    theta.update({
        "segment_order": [s.id for s in world.segments],
        "policy_noise": world.policy_noise, "meas_noise": world.meas_noise,
        "factor_rho": world.factor_rho, "factor_sigma": world.factor_sigma,
        "history_policy": world.history_policy,
    })
    return theta


def theta_json_bytes(world: LatentWorld) -> bytes:
    return (json.dumps(theta_dict(world), indent=2) + "\n").encode()


def mstar_dict(world: LatentWorld, obs, n_seeds=400, with_optimum=True):
    """mstar.json content. ``with_optimum=False`` skips the slow grid search (about
    2 minutes per world) and the reference-plan Monte-Carlo, returning only the
    fields that come straight from the history (used by fast regeneration checks)."""
    ls, c0 = obs["latent_state"], obs["cust_final"]
    budget = float(le.lsum(s.refmkt for s in world.segments))
    refs = reference_plans(world, budget)
    out = {"world_id": world.id, "budget": budget}
    if with_optimum:
        star_plan, star, sstar = le.optimize_plan(world, ls, c0, n_seeds=n_seeds,
                                                  base_seed=101, budget=budget)
        ref_vals = {}
        for name, plan in refs.items():
            v, se, _ = le.held_out_profit(world, plan, ls, c0, n_seeds=n_seeds, base_seed=101)
            ref_vals[name] = {"true_profit": v, "stderr": se,
                              "over_budget": total_marketing(world, plan) > budget * 1.0001}
        out.update({"mstar_plan": star_plan, "mstar_true_profit": star, "mstar_stderr": sstar})
    out.update({
        "L_final": [float(x) for x in obs["L_final"]], "cust_final": [float(x) for x in c0],
        "reference_plans": {k: refs[k] for k in refs},
    })
    if with_optimum:
        out["reference_true_profit"] = ref_vals
    out.update({"latent_state": [ls[0], list(ls[1])], "n_seeds": n_seeds})
    return out


def mstar_json_bytes(world: LatentWorld, obs, n_seeds=400) -> bytes:
    return (json.dumps(mstar_dict(world, obs, n_seeds), indent=2) + "\n").encode()


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

def emit(world: LatentWorld, outdir: Path, n_seeds=400):
    """Paper layout: <outdir>/public + <outdir>/ANSWER_KEY (byte-identical to the paper)."""
    obs = le.simulate_history(world)
    pub = Path(outdir) / "public"; ak = Path(outdir) / "ANSWER_KEY"
    pub.mkdir(parents=True, exist_ok=True); ak.mkdir(parents=True, exist_ok=True)
    (pub / "history.csv").write_bytes(history_csv_bytes(world, obs))
    (pub / "task.json").write_bytes(task_json_bytes(world))
    (ak / "theta.json").write_bytes(theta_json_bytes(world))
    ms = mstar_dict(world, obs, n_seeds)
    (ak / "mstar.json").write_bytes((json.dumps(ms, indent=2) + "\n").encode())
    return {"world_id": world.id, "budget": ms["budget"], "mstar": ms["mstar_true_profit"],
            "mstar_se": ms["mstar_stderr"], "refs": ms["reference_true_profit"]}


def emit_split(world: LatentWorld, data_worlds: Path, private_worlds: Path, n_seeds=400):
    """Package layout: data_worlds/<id>/public and private_worlds/<id>/ANSWER_KEY."""
    obs = le.simulate_history(world)
    pub = Path(data_worlds) / world.id / "public"
    ak = Path(private_worlds) / world.id / "ANSWER_KEY"
    pub.mkdir(parents=True, exist_ok=True); ak.mkdir(parents=True, exist_ok=True)
    (pub / "history.csv").write_bytes(history_csv_bytes(world, obs))
    (pub / "task.json").write_bytes(task_json_bytes(world))
    (ak / "theta.json").write_bytes(theta_json_bytes(world))
    ms = mstar_dict(world, obs, n_seeds)
    (ak / "mstar.json").write_bytes((json.dumps(ms, indent=2) + "\n").encode())
    return {"world_id": world.id, "budget": ms["budget"], "mstar": ms["mstar_true_profit"]}


def configure_parser(ap):
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", help="paper layout: <out>/public and <out>/ANSWER_KEY")
    ap.add_argument("--data-out", help="package layout: worlds dir for public/ (agent-visible)")
    ap.add_argument("--private-out", help="package layout: worlds dir for ANSWER_KEY/ (scorer-only)")
    ap.add_argument("--seed-salt", default=None,
                    help="secret salt for a private split (opaque world id, salted RNG)")
    ap.add_argument("--n-seg", type=int, default=4)
    ap.add_argument("--n-seeds", type=int, default=400)
    return ap


def run(args) -> int:
    world = draw_world(args.seed, n_seg=args.n_seg, seed_salt=args.seed_salt)
    if args.out:
        summ = emit(world, Path(args.out), n_seeds=args.n_seeds)
    elif args.data_out and args.private_out:
        summ = emit_split(world, Path(args.data_out), Path(args.private_out), n_seeds=args.n_seeds)
    else:
        raise SystemExit("give --out (paper layout) or both --data-out and --private-out")
    print(json.dumps(summ, indent=2, default=float))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    return run(configure_parser(ap).parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
