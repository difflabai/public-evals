"""D1 headline: the aided-minus-unaided realized-profit uplift, per world and pooled.

The paper's harness had no contrast script (ledger E6-22). This module
computes the contrast as follows:

    uplift_w = (mean_reps true_profit_aided - mean_reps true_profit_unaided)
               / mean_reps true_profit_unaided * 100
    mean     = statistics.mean(uplift_w)   (exact rational mean, correctly rounded)
    median   = statistics.median(uplift_w)
    CI       = world-clustered percentile bootstrap: resample worlds with
               replacement, mean of their uplifts; numpy default_rng(seed),
               B resamples, 2.5/97.5 percentiles (numpy "linear").

No-plan policy (ledger E6-03). A trial that committed nothing usable is scored
by the paper as the do-nothing reference plan. ``reference`` keeps such trials
at that score; ``exclude`` drops them, and a world left without one of its arms
is dropped from the contrast. Reports always show both.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Iterable

NO_PLAN_POLICIES = ("reference", "exclude")
# How the per-world percentage is evaluated.
UPLIFT_FORMS = {
    "ratio_x100": lambda a, u: (a - u) / u * 100.0,
}
DEFAULT_B = 5000
DEFAULT_SEED = 0
MOAT_LOW_LT = 10.0     # the paper's "low-moat (<10%)"
MOAT_HIGH_GE = 12.0    # the paper's "high-moat (>=12%)"


def world_key(w: str):
    m = re.fullmatch(r"w(\d+)", w)
    return (0, int(m.group(1)), w) if m else (1, 0, w)


def _mean(xs):
    """statistics.mean: exact, so independent of summation order and of Python 3.12's
    switch to compensated float ``sum``."""
    return statistics.mean(xs)


# ---------------------------------------------------------------------------
# Per-world uplift
# ---------------------------------------------------------------------------

def uplift_from_trials(trials: Iterable[dict], no_plan_policy: str = "reference",
                       form: str = "ratio_x100") -> dict:
    """trials: dicts with world, arm ('aided'|'unaided'), true_profit, no_plan (bool) and
    optionally excluded (bool, e.g. audit-flagged or unscored; always dropped)."""
    if no_plan_policy not in NO_PLAN_POLICIES:
        raise ValueError(f"no_plan_policy must be one of {NO_PLAN_POLICIES}")
    pct = UPLIFT_FORMS[form]
    by: dict[str, dict[str, list[float]]] = {}
    dropped_trials = []
    for t in trials:
        if t.get("excluded"):
            dropped_trials.append({**_ident(t), "reason": t.get("exclusion_reason", "excluded")})
            continue
        if t.get("no_plan") and no_plan_policy == "exclude":
            dropped_trials.append({**_ident(t), "reason": "no_plan"})
            continue
        if t.get("true_profit") is None:
            dropped_trials.append({**_ident(t), "reason": "unscored"})
            continue
        by.setdefault(t["world"], {"aided": [], "unaided": []})[t["arm"]].append(float(t["true_profit"]))
    per_world, n, dropped_worlds = {}, {}, []
    for w in sorted(by, key=world_key):
        a, u = by[w]["aided"], by[w]["unaided"]
        if not a or not u:
            dropped_worlds.append(w)
            continue
        ma, mu = _mean(a), _mean(u)
        per_world[w] = pct(ma, mu)
        n[w] = {"aided": len(a), "unaided": len(u)}
    return {"per_world": per_world, "n_trials": n, "dropped_worlds": dropped_worlds,
            "dropped_trials": dropped_trials, "no_plan_policy": no_plan_policy, "form": form}


def _ident(t):
    return {k: t.get(k) for k in ("world", "arm", "rep")}


# ---------------------------------------------------------------------------
# Pooled statistics
# ---------------------------------------------------------------------------

def cluster_bootstrap_ci(per_world: dict, B: int = DEFAULT_B, seed: int = DEFAULT_SEED):
    import numpy as np
    ws = sorted(per_world, key=world_key)
    vals = np.array([per_world[w] for w in ws], float)
    n = len(vals)
    if n == 0:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(B, n))
    means = vals[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return [float(lo), float(hi)]


def summarize(per_world: dict, B: int = DEFAULT_B, seed: int = DEFAULT_SEED,
              exclude_worlds: Iterable[str] = ()) -> dict:
    pw = {w: v for w, v in per_world.items() if w not in set(exclude_worlds)}
    ws = sorted(pw, key=world_key)
    vals = [pw[w] for w in ws]
    if not vals:
        return {"n_worlds": 0, "per_world": {}}
    return {
        "n_worlds": len(vals),
        "uplift_mean": _mean(vals),
        "uplift_median": statistics.median(vals),
        "ci95": cluster_bootstrap_ci(pw, B, seed),
        "per_world": {w: pw[w] for w in ws},
        "bootstrap": {"B": B, "seed": seed, "method": "world-clustered percentile (numpy default_rng)"},
        "excluded_worlds": sorted(set(exclude_worlds) & set(per_world), key=world_key),
    }


def moat_buckets(per_world: dict, moats: dict, low_lt=MOAT_LOW_LT, high_ge=MOAT_HIGH_GE) -> dict:
    out = {"thresholds": {"low_lt": low_lt, "high_ge": high_ge}}
    for name, pred in (("low", lambda m: m < low_lt), ("mid", lambda m: low_lt <= m < high_ge),
                       ("high", lambda m: m >= high_ge)):
        ws = [w for w in sorted(per_world, key=world_key) if w in moats and pred(moats[w])]
        vals = [per_world[w] for w in ws]
        out[name] = {"n": len(vals), "worlds": ws,
                     "mean_uplift": _mean(vals) if vals else None}
    return out


def moats_from_certify_scan(path: Path) -> dict:
    """{world_id: gamma=0-belief decision gap %} from a certify scan (normative moat)."""
    return {r["world_id"]: float(r["gamma_moat"]["true_profit_gap_pct"])
            for r in json.loads(Path(path).read_text()) if r.get("GATE_PASS")}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def trials_from_run(run_dir: Path) -> list[dict]:
    """dbwm.verdict/1 files from a run directory. Picks, per cell, the verdict of the
    scored attempt (lowest attempt number with scored=True); unscored cells are kept
    as excluded so the exclusion table shows them."""
    best: dict[str, dict] = {}
    for vf in sorted(Path(run_dir).rglob("verdict.json")):
        try:
            d = json.loads(vf.read_text())
        except (ValueError, OSError):
            continue
        if d.get("eval") not in (None, "eval6"):
            continue
        cell = d.get("cell") or {}
        cid = cell.get("cell_id") or vf.parent.name
        att = d.get("attempt", 0) or 0
        prev = best.get(cid)
        rank = (0 if d.get("scored") else 1, att)
        if prev is None or rank < prev["_rank"]:
            best[cid] = {"_rank": rank, "d": d}
    out = []
    for cid, rec in sorted(best.items()):
        d = rec["d"]
        cell = d.get("cell") or {}
        det = d.get("details") or {}
        audit = d.get("audit") or {}
        flagged = bool(audit.get("flagged") or audit.get("excluded"))
        excluded = (not d.get("scored")) or flagged
        reason = None
        if not d.get("scored"):
            reason = f"unscored:{d.get('outcome')}"
        elif flagged:
            reason = "audit_flagged"
        out.append({"world": cell.get("instance"), "arm": cell.get("arm"), "rep": cell.get("rep"),
                    "true_profit": det.get("true_profit"), "no_plan": bool(det.get("no_plan")),
                    "gap_to_mstar_pct": det.get("gap_to_mstar_pct"),
                    "projection_ratio": det.get("projection_ratio"),
                    "budget_projected": det.get("budget_projected"),
                    "raw_price_out_of_band": det.get("raw_price_out_of_band"),
                    "outcome": d.get("outcome"), "ruleset": d.get("ruleset"),
                    "excluded": excluded, "exclusion_reason": reason, "cell_id": cid})
    return out


def contrast_both_policies(trials: list[dict], B=DEFAULT_B, seed=DEFAULT_SEED,
                           form: str = "ratio_x100") -> dict:
    out = {}
    for pol in NO_PLAN_POLICIES:
        u = uplift_from_trials(trials, pol, form)
        s = summarize(u["per_world"], B, seed)
        s.update({k: u[k] for k in ("n_trials", "dropped_worlds", "dropped_trials", "no_plan_policy")})
        out[pol] = s
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def configure_parser(ap):
    ap.add_argument("--run", required=True, help="a dbwm run directory (dbwm.verdict/1 files)")
    ap.add_argument("--bootstrap", type=int, default=DEFAULT_B)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--drop", default="", help="comma-separated worlds to drop (robustness)")
    ap.add_argument("--out", default=None)
    return ap


def run(args) -> int:
    drop = [w for w in args.drop.split(",") if w]
    res = contrast_both_policies(trials_from_run(Path(args.run)), args.bootstrap, args.seed)
    if drop:
        for k, v in list(res.items()):
            res[k + f"_drop_{'_'.join(drop)}"] = summarize(v["per_world"], args.bootstrap, args.seed,
                                                           exclude_worlds=drop)
    text = json.dumps(res, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    return run(configure_parser(ap).parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
