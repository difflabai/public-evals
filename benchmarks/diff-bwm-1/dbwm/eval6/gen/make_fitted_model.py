"""The aided arm's fitted model (AIDED_v1 = paper, AIDED_v2 = release).

The aided artifact is a CALIBRATED model the platform built from the history PLUS
experiment receipts (controlled price/marketing variation the passive history
lacks). It carries the two facts the unaided agent cannot recover from the
confounded history: the SIGN of the hub -> spoke coupling and the (negative)
SIGN of the price elasticity. Magnitudes carry lognormal estimation noise, and
it carries a NOWCAST of the current latent state (true + N(0, nowcast_sd)).

v1 (paper, ``fitted_v1``): byte-identical to the paper harness's
    AIDED/fitted_model.json. Its ``fitted_world`` is serialized with
    latent_engine.world_to_dict, which includes the TRUE world ``seed`` and the
    exact history-only parameters (phi, psi, cust0, policy_noise, meas_noise,
    history_policy). With the engine staged next to it, ``simulate_history``
    replays the true latent path bit-exactly (w1 F_final 1.0691461795252533). Kept under private/ only, never staged.

v2 (release, ``fitted_v2``): the same draws (identical noised magnitudes,
    coupling, nowcast, cust_current, budget, validation_report), minus every
    field the held-out horizon does not use: ``seed``, ``policy_noise``,
    ``meas_noise``, ``history_policy`` and per-segment ``phi``, ``psi``,
    ``cust0``. ``refprice`` (1.0 for every segment) and ``factor_rho`` /
    ``factor_sigma`` (held-out latent dynamics, exact in v1 too) are kept. It is
    staged with ``heldout_sim.py``, which has no history simulator.

Ported from the paper harness (``bwm_latent/make_fitted_model.py``); fit_world,
insample_r2 and _hub are unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .. import latent_engine as le

# params whose MAGNITUDE gets multiplicative estimation noise but whose SIGN is preserved
NOISY_MAG = ("base_signups", "acv", "mktexp", "churn0", "churn_price_exp",
             "refmkt", "gm", "fixed", "factor_load", "sigma", "rho")
SIGNED = ("elast",)   # keep the (negative) sign; noise the magnitude

# v2 strips everything only the HISTORY simulator uses (the replay leak).
V2_DROP_WORLD_KEYS = ("seed", "policy_noise", "meas_noise", "history_policy")
V2_DROP_SEGMENT_KEYS = ("phi", "psi", "cust0")

DEFAULT_NOISE = 0.08
DEFAULT_NOWCAST_SD = 0.04
DEFAULT_ARG_SEED = 0


def fit_world(world: le.LatentWorld, rng, noise=0.08):
    """Return a fitted copy of the world: correct signs, magnitude noise."""
    d = le.world_to_dict(world)
    for sid, sv in d["segments"].items():
        for k in NOISY_MAG:
            sv[k] = float(sv[k]) * float(np.exp(rng.normal(0, noise)))
        # elasticity: preserve the negative sign, noise the magnitude
        mag = abs(float(sv["elast"])) * float(np.exp(rng.normal(0, noise)))
        sv["elast"] = -mag
        sv["mktexp"] = min(0.95, max(0.05, sv["mktexp"]))   # keep concave & valid
    # coupling: preserve SIGN and rough magnitude, add noise (the experiment-identified fact)
    S = len(d["coupling"])
    for i in range(S):
        for j in range(S):
            v = d["coupling"][i][j]
            if v != 0.0:
                d["coupling"][i][j] = float(np.sign(v) * abs(v) * np.exp(rng.normal(0, noise)))
    return le.world_from_dict(d)


def insample_r2(fitted: le.LatentWorld, obs):
    """In-sample fit quality: R^2 of the fitted model's predicted log-signups vs observed,
    reconstructing latent from the fitted policy inverse. Reported in the validation
    report (calibration-split style — NOT held-out-future)."""
    mkt, price, signups = obs["marketing"], obs["price"], obs["signups"]
    T, S = signups.shape
    y, yhat = [], []
    for s in range(S):
        seg = fitted.segments[s]
        Lhat = np.maximum(1e-6, (mkt[:, s] / seg.refmkt) ** (1.0 / max(seg.phi, 1e-3)))
        pred = (seg.base_signups * Lhat
                * (mkt[:, s] / seg.refmkt) ** seg.mktexp
                * (price[:, s] / seg.refprice) ** seg.elast)
        y.append(np.log(np.maximum(1e-6, signups[:, s])))
        yhat.append(np.log(np.maximum(1e-6, pred)))
    y = np.concatenate(y); yhat = np.concatenate(yhat)
    ss_res = float(np.sum((y - yhat) ** 2)); ss_tot = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def _hub(world):
    S = world.n()
    counts = [sum(1 for s in range(S) if world.coupling[s][j] != 0.0) for j in range(S)]
    return int(np.argmax(counts))


def fitted_v1(theta: dict, mstar: dict, noise=DEFAULT_NOISE, nowcast_sd=DEFAULT_NOWCAST_SD,
              arg_seed=DEFAULT_ARG_SEED) -> dict:
    """The paper's fitted_model.json content (leaky: carries the world seed).

    Only ``latent_state``, ``cust_final`` and ``budget`` are read from mstar.json,
    so this does not depend on the slow M* search."""
    true_world = le.world_from_dict(theta)
    obs = le.simulate_history(true_world)

    rng = np.random.default_rng(true_world.seed * 811 + arg_seed + 1)
    fitted = fit_world(true_world, rng, noise=noise)

    # nowcast of the current latent state (true + noise) — the reconciliation estimate
    F0, u0 = mstar["latent_state"][0], list(mstar["latent_state"][1])
    F_nc = float(F0 + rng.normal(0, nowcast_sd))
    u_nc = [float(u + rng.normal(0, nowcast_sd)) for u in u0]

    return {
        "world_id": true_world.id,
        "note": ("Calibrated interlocking model fitted from history + controlled experiments. "
                 "Coupling and price-elasticity SIGNS are experiment-identified (not recoverable "
                 "from the passive history); magnitudes carry estimation noise."),
        "fitted_world": le.world_to_dict(fitted),
        "latent_nowcast": [F_nc, u_nc],
        "cust_current": [float(x) for x in mstar["cust_final"]],
        "budget": float(mstar["budget"]),
        "held_out": true_world.held_out,
        "validation_report": {
            "insample_logsignups_r2": insample_r2(fitted, obs),
            "coupling_signs": {fitted.segments[s].id: ("complement" if fitted.coupling[s][
                _hub(fitted)] > 0 else "cannibalize")
                for s in range(fitted.n()) if s != _hub(fitted)},
            "price_elasticity_sign": "negative (experiment-identified)",
            "basis": "in-sample + calibration-split coverage; NOT validated on the held-out future",
        },
    }


def strip_to_v2(v1: dict) -> dict:
    """AIDED_v2 from AIDED_v1: drop the seed and every history-only parameter."""
    out = json.loads(json.dumps(v1))
    fw = out["fitted_world"]
    for k in V2_DROP_WORLD_KEYS:
        fw.pop(k, None)
    for sv in fw["segments"].values():
        for k in V2_DROP_SEGMENT_KEYS:
            sv.pop(k, None)
    return out


def fitted_v2(theta: dict, mstar: dict, **kw) -> dict:
    return strip_to_v2(fitted_v1(theta, mstar, **kw))


def to_bytes(d: dict) -> bytes:
    return (json.dumps(d, indent=2) + "\n").encode()


def configure_parser(ap):
    ap.add_argument("--world", help="paper layout dir with ANSWER_KEY/ (writes AIDED/ or AIDED_v2/)")
    ap.add_argument("--theta", help="package layout: ANSWER_KEY/theta.json")
    ap.add_argument("--mstar", help="package layout: ANSWER_KEY/mstar.json")
    ap.add_argument("--out", help="output fitted_model.json path (package layout)")
    ap.add_argument("--version", choices=["v1", "v2"], default="v2",
                    help="v1 = paper (leaky, carries the seed); v2 = release")
    ap.add_argument("--noise", type=float, default=DEFAULT_NOISE, help="lognormal sd on magnitudes")
    ap.add_argument("--nowcast-sd", type=float, default=DEFAULT_NOWCAST_SD,
                    help="noise on the latent nowcast")
    ap.add_argument("--seed", type=int, default=DEFAULT_ARG_SEED)
    return ap


def run(args) -> int:
    if args.world:
        wd = Path(args.world)
        theta = json.loads((wd / "ANSWER_KEY" / "theta.json").read_text())
        mstar = json.loads((wd / "ANSWER_KEY" / "mstar.json").read_text())
        out = wd / ("AIDED" if args.version == "v1" else "AIDED_v2") / "fitted_model.json"
    elif args.theta and args.mstar and args.out:
        theta = json.loads(Path(args.theta).read_text())
        mstar = json.loads(Path(args.mstar).read_text())
        out = Path(args.out)
    else:
        raise SystemExit("give --world, or --theta --mstar --out")
    build = fitted_v1 if args.version == "v1" else fitted_v2
    d = build(theta, mstar, noise=args.noise, nowcast_sd=args.nowcast_sd, arg_seed=args.seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(to_bytes(d))
    print(f"[{d['world_id']}] fitted model ({args.version}) -> {out.name}  "
          f"insample_R2={d['validation_report']['insample_logsignups_r2']:.3f}  "
          f"coupling={d['validation_report']['coupling_signs']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    return run(configure_parser(ap).parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
