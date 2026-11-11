"""Generate (and verify) the five coupled worlds; re-certify hardness under v1.

Ported from the paper harness ``bwm_coupled/make_worlds.py``. CONFIGS and the
search/floor logic are verbatim; the paper wrote every output next to each
other in the repo data dir, which is how the answer key (reference.json) ended
up one ``ls`` away from the agent (E23-D2). Here the generator writes the
package layout under ``--out``:

    <out>/data/eval2/worlds/<id>/brief.json              agent-visible
    <out>/private/eval2/worlds/<id>/world.json           oracle/scorer only
    <out>/private/eval2/worlds/<id>/reference.json       scorer only (answer key)
    <out>/private/eval2/confirmatory-worlds.json

``tests/eval2/test_eval2_engine_world.py`` (test_regeneration_is_byte_identical)
regenerates into a temp dir and byte-compares with the shipped files.
Deterministic, stdlib only, ~10 s.

Usage
    python -m dbwm.eval2.make_worlds --out DIR          # generate + verify
    python -m dbwm.eval2.make_worlds --certify-v1 [--json FILE]
                                                        # re-certify under v1

Private split (review finding M3). The worlds are not drawn from a seed (each is a
hand-set economic configuration searched for a hard target), so there is nothing to
salt; a held-out split is a set of PRIVATE configurations in the shape of CONFIGS:

    python -m dbwm.eval2.make_worlds --configs private.json --out DIR
    DBWM_DATA_DIR=DIR/data DBWM_PRIVATE_DIR=DIR/private \
        python -m dbwm.eval2.make_worlds --configs private.json --certify-v1 --json DIR/cert.json

``private.json`` is a JSON list of configs (or {"configs": [...]}) with the keys of
CONFIGS. ``--out`` with ``--configs`` also copies the base model into DIR/private so
the tree is self-contained. The runnable instance ids are the five public ids
(``dbwm.eval2.WORLD_IDS``): reuse them as labels for private configs to run the split
with ``dbwm run --eval 2`` and the two environment variables, without code changes.
Keep the configs file with the answer keys (it regenerates them).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from decimal import Decimal
from pathlib import Path

from . import make_world_lib as mw
from .engine import prec50
from .score import score_scenario, score_scenario_v1
from .world import World, build_brief, load_world

H = mw.H


def _unit(u):
    """Expand a compact unit spec into a world unit dict (overrides on sprout-crm)."""
    return {
        "id": u["id"], "name": u["name"], "base_model": "sprout-crm",
        "customers_per_head": str(u["cph"]),
        "overrides": {
            "churn_rate": str(u["churn"]), "acv": str(u["acv"]),
            "conversion_rate": str(u["conv"]), "cost_per_lead": str(u["cpl"]),
            "gm_rate": str(u["gm"]), "fixed_opex": str(u["fixed"]),
            "salary_per_head": str(u["salary"]), "marketing_spend": str(u["mkt0"]),
            "hires_per_quarter": "1",
            "customers_initial": str(u["cust0"]), "headcount_initial": str(u["hc0"]),
            "cash_initial": str(u["cash0"]),
        },
    }


def spec_from_config(cfg, target):
    return {
        "schema": "bwm-coupled-world/1",
        "id": cfg["id"], "name": cfg["name"], "horizon": H,
        "interface": "consistent",
        "units": [_unit(u) for u in cfg["units"]],
        "pools": [
            {"id": "growth_capital", "label": "Growth capital", "unit": "usd/quarter",
             "feeds_lever": "marketing_spend", "marginal_return_lever": True,
             "cap": [str(cfg["growth_cap"])] * H},
            {"id": "eng_headcount", "label": "Engineering headcount",
             "unit": "people/quarter (net hires)", "feeds_lever": "hires_per_quarter",
             "cap": [str(cfg["headcount_cap"])] * H},
        ],
        "goal": {"metric": "revenue", "target_multiple": str(target),
                 "baseline_period": 0, "target_period": H - 1,
                 "description": ("combined revenue[final]/revenue[0] on your own plan's "
                                 "trajectory must be >= target_multiple")},
        "parent_constraints": [
            {"id": "cash_floor", "kind": "parent_series_min", "metric": "cash",
             "limit": "0", "description": "combined ending cash must stay >= 0 every quarter"},
            {"id": "margin_floor", "kind": "parent_margin_min", "limit": str(cfg["margin_floor"]),
             "description": f"combined gross margin must stay >= {cfg['margin_floor']} every quarter"},
        ],
        "unit_constraints": [{"kind": "unit_headcount_capacity"}],
        "notes": ("Each unit's headcount must cover customers/customers_per_head; net hires "
                  "draw the shared headcount pool and add payroll (eroding the margin floor). "
                  "Growth dollars compound through each unit's customer base at a rate set by "
                  "its churn, so the optimal split changes over the horizon. Raising quarter-0 "
                  "marketing raises your own quarter-0 combined revenue (the goal denominator)."),
    }


@prec50
def generate(cfg, log=print):
    """Run the search + floor logic for one config (make_worlds.generate verbatim).
    Returns (final_spec, reference) if the hardness floor holds, else None.

    Runs in the paper's decimal context (prec 50): the paper module set it
    globally at import, so the target arithmetic below ran at prec 50 too."""
    world = World(spec_from_config(cfg, "1.0"))
    best, feasible_mults, _ = mw.search_optimum(world)
    if best is None:
        log(f"  [{cfg['id']}] SKIP — no feasible allocation found")
        return None
    mstar, opt_capital, opt_scenario, opt_verdict = best
    naive = mw.naive_baselines(world)
    naive_feas = [Decimal(r["multiple"]) for r in naive.values() if r["fully_valid"]]
    best_naive = max(naive_feas) if naive_feas else Decimal("0")
    min_feasible = min(feasible_mults)
    floor_anchor = max(min_feasible, best_naive)
    target = (floor_anchor + (mstar - floor_anchor) * Decimal("0.6")).quantize(Decimal("0.01"))

    world2 = World(spec_from_config(cfg, str(target)))
    opt_v = score_scenario(world2, opt_scenario)
    naive2 = mw.naive_baselines(world2)
    naive_pass = [n for n, r in naive2.items()
                  if score_scenario(world2, r["scenario"])["fully_valid"]]
    floor_ok = opt_v["fully_valid"] and not naive_pass
    log(f"  [{cfg['id']}] M*={float(mstar):.3f} min_feas={float(min_feasible):.3f} "
        f"-> target={float(target):.3f} | careful_passes={opt_v['fully_valid']} "
        f"naive_pass={naive_pass or 'none'} | FLOOR {'OK' if floor_ok else 'NOT MET'}")
    if not floor_ok:
        return None
    reference = {
        "world_id": cfg["id"], "target_multiple": str(target),
        "careful_optimum": {"multiple": opt_v["goal_multiple_actual"],
                            "fully_valid": opt_v["fully_valid"],
                            "allocation": opt_scenario["allocation"],
                            "parent_trajectory": opt_v["parent_trajectory"]},
        "naive_baselines": {n: {"multiple": r["multiple"],
                                "fully_valid": score_scenario(world2, r["scenario"])["fully_valid"]}
                            for n, r in naive2.items()},
        "hardness_floor": {"best_feasible_naive_multiple": str(best_naive),
                           "min_feasible_multiple": str(min_feasible),
                           "careful_optimum_multiple": opt_v["goal_multiple_actual"],
                           "chosen_target": str(target)},
        "_note": "NOT shown to the agent.",
    }
    return spec_from_config(cfg, str(target)), reference


# ---------------------------------------------------------------------------
# The variant configs (orion = the pilot world; the rest perturb economics/caps)
# ---------------------------------------------------------------------------

CONFIGS = [
    {  # 1. orion-holdings — the pilot world (kept identical)
        "id": "orion-holdings", "name": "Orion Holdings — 3-unit shared-resource portfolio",
        "growth_cap": 220000, "headcount_cap": 7, "margin_floor": "0.62",
        "units": [
            {"id": "alpha-cloud", "name": "Alpha Cloud (compounding SaaS)", "cph": 45,
             "churn": "0.03", "acv": 520, "conv": "0.05", "cpl": 20, "gm": "0.86",
             "fixed": 35000, "salary": 20000, "mkt0": 40000, "cust0": 200, "hc0": 9, "cash0": 320000},
            {"id": "beta-systems", "name": "Beta Systems (high-ACV, high-churn)", "cph": 35,
             "churn": "0.17", "acv": 1500, "conv": "0.04", "cpl": 32, "gm": "0.72",
             "fixed": 60000, "salary": 23000, "mkt0": 45000, "cust0": 150, "hc0": 11, "cash0": 300000},
            {"id": "gamma-data", "name": "Gamma Data (mid-churn analytics)", "cph": 40,
             "churn": "0.08", "acv": 880, "conv": "0.045", "cpl": 26, "gm": "0.80",
             "fixed": 45000, "salary": 21000, "mkt0": 42000, "cust0": 180, "hc0": 10, "cash0": 300000},
        ],
    },
    {  # 2. tighter pools + more cash (binding shifts toward the pool caps)
        "id": "vega-group", "name": "Vega Group — tight-pool portfolio",
        "growth_cap": 195000, "headcount_cap": 6, "margin_floor": "0.60",
        "units": [
            {"id": "vela-saas", "name": "Vela SaaS (compounder)", "cph": 50,
             "churn": "0.025", "acv": 560, "conv": "0.052", "cpl": 19, "gm": "0.87",
             "fixed": 38000, "salary": 20000, "mkt0": 40000, "cust0": 210, "hc0": 9, "cash0": 360000},
            {"id": "rigel-hw", "name": "Rigel Hardware (high-ACV cash-cow)", "cph": 32,
             "churn": "0.19", "acv": 1650, "conv": "0.038", "cpl": 34, "gm": "0.70",
             "fixed": 64000, "salary": 24000, "mkt0": 45000, "cust0": 150, "hc0": 12, "cash0": 330000},
            {"id": "spica-data", "name": "Spica Data (secondary)", "cph": 42,
             "churn": "0.085", "acv": 900, "conv": "0.044", "cpl": 25, "gm": "0.79",
             "fixed": 46000, "salary": 21000, "mkt0": 42000, "cust0": 180, "hc0": 10, "cash0": 330000},
        ],
    },
    {  # 3. wider churn spread + higher margin floor (margin binds harder)
        "id": "lyra-partners", "name": "Lyra Partners — wide-spread portfolio",
        "growth_cap": 235000, "headcount_cap": 7, "margin_floor": "0.66",
        "units": [
            {"id": "lyra-core", "name": "Lyra Core (very-low-churn compounder)", "cph": 48,
             "churn": "0.02", "acv": 500, "conv": "0.055", "cpl": 18, "gm": "0.88",
             "fixed": 34000, "salary": 20000, "mkt0": 40000, "cust0": 220, "hc0": 9, "cash0": 330000},
            {"id": "draco-sys", "name": "Draco Systems (very-high-churn cash-cow)", "cph": 33,
             "churn": "0.22", "acv": 1700, "conv": "0.036", "cpl": 33, "gm": "0.74",
             "fixed": 62000, "salary": 23000, "mkt0": 45000, "cust0": 150, "hc0": 11, "cash0": 300000},
            {"id": "orion-mid", "name": "Orion Mid (secondary)", "cph": 40,
             "churn": "0.09", "acv": 860, "conv": "0.046", "cpl": 26, "gm": "0.81",
             "fixed": 44000, "salary": 21000, "mkt0": 42000, "cust0": 180, "hc0": 10, "cash0": 300000},
        ],
    },
    {  # 4. leaner cash (cash floor binds earlier) + mid acvs
        "id": "cygnus-holdings", "name": "Cygnus Holdings — lean-cash portfolio",
        "growth_cap": 210000, "headcount_cap": 7, "margin_floor": "0.61",
        "units": [
            {"id": "deneb-saas", "name": "Deneb SaaS (compounder)", "cph": 46,
             "churn": "0.035", "acv": 540, "conv": "0.05", "cpl": 21, "gm": "0.85",
             "fixed": 36000, "salary": 20000, "mkt0": 40000, "cust0": 200, "hc0": 9, "cash0": 285000},
            {"id": "sadr-systems", "name": "Sadr Systems (cash-cow)", "cph": 34,
             "churn": "0.16", "acv": 1450, "conv": "0.041", "cpl": 31, "gm": "0.73",
             "fixed": 58000, "salary": 23000, "mkt0": 45000, "cust0": 155, "hc0": 11, "cash0": 270000},
            {"id": "albireo-data", "name": "Albireo Data (secondary)", "cph": 41,
             "churn": "0.075", "acv": 900, "conv": "0.045", "cpl": 25, "gm": "0.80",
             "fixed": 45000, "salary": 21000, "mkt0": 42000, "cust0": 185, "hc0": 10, "cash0": 270000},
        ],
    },
    {  # 5. richer pools + larger units (different scale)
        "id": "perseus-portfolio", "name": "Perseus Portfolio — large-scale",
        "growth_cap": 260000, "headcount_cap": 8, "margin_floor": "0.63",
        "units": [
            {"id": "mirfak-cloud", "name": "Mirfak Cloud (compounder)", "cph": 48,
             "churn": "0.03", "acv": 560, "conv": "0.05", "cpl": 20, "gm": "0.86",
             "fixed": 42000, "salary": 21000, "mkt0": 48000, "cust0": 240, "hc0": 11, "cash0": 380000},
            {"id": "algol-systems", "name": "Algol Systems (cash-cow)", "cph": 34,
             "churn": "0.18", "acv": 1600, "conv": "0.04", "cpl": 32, "gm": "0.72",
             "fixed": 70000, "salary": 24000, "mkt0": 52000, "cust0": 175, "hc0": 13, "cash0": 360000},
            {"id": "atik-data", "name": "Atik Data (secondary)", "cph": 40,
             "churn": "0.08", "acv": 900, "conv": "0.045", "cpl": 26, "gm": "0.80",
             "fixed": 52000, "salary": 22000, "mkt0": 50000, "cust0": 210, "hc0": 12, "cash0": 360000},
        ],
    },
]


# ---------------------------------------------------------------------------
# Private configurations (review M3)
# ---------------------------------------------------------------------------

WORLD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
CONFIG_KEYS = ("id", "name", "growth_cap", "headcount_cap", "margin_floor", "units")
UNIT_KEYS = ("id", "name", "cph", "churn", "acv", "conv", "cpl", "gm", "fixed", "salary", "mkt0", "cust0", "hc0",
             "cash0")


def validate_configs(cfgs) -> list[dict]:
    """Check a list of world configs (CONFIGS shape); return it. Raises ValueError."""
    if not isinstance(cfgs, list) or not cfgs:
        raise ValueError("configs must be a non-empty JSON list (or {\"configs\": [...]})")
    seen = set()
    for i, c in enumerate(cfgs):
        if not isinstance(c, dict):
            raise ValueError(f"config {i} is not an object")
        missing = [k for k in CONFIG_KEYS if k not in c]
        if missing:
            raise ValueError(f"config {i} lacks {missing}")
        wid = c["id"]
        if not isinstance(wid, str) or not WORLD_ID_RE.match(wid):
            raise ValueError(f"config {i}: world id {wid!r} must match {WORLD_ID_RE.pattern} (it names a directory)")
        if wid in seen:
            raise ValueError(f"duplicate world id {wid!r}")
        seen.add(wid)
        if not isinstance(c["units"], list) or not c["units"]:
            raise ValueError(f"config {wid}: units must be a non-empty list")
        for u in c["units"]:
            um = [k for k in UNIT_KEYS if k not in u] if isinstance(u, dict) else list(UNIT_KEYS)
            if um:
                raise ValueError(f"config {wid}: a unit lacks {um}")
    return cfgs


def load_configs(path) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_configs(raw.get("configs") if isinstance(raw, dict) else raw)


def write_worlds(out: Path, log=print, configs=None) -> list[str]:
    """Generate every config; write the verified ones under ``out`` (package layout).

    ``configs`` (default: the shipped CONFIGS) is a private split's configuration
    list; then the base model is copied too, so ``out/private`` is self-contained.
    """
    out = Path(out)
    verified = []
    cfgs = CONFIGS if configs is None else validate_configs(configs)
    if configs is not None:
        from .world import base_models_dir

        dst = out / "private" / "eval2" / "base_models"
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(base_models_dir().glob("*.json")):
            shutil.copyfile(f, dst / f.name)
    for cfg in cfgs:
        res = generate(cfg, log=log)
        if res is None:
            continue
        spec, reference = res
        verified.append(cfg["id"])
        pub = out / "data" / "eval2" / "worlds" / cfg["id"]
        priv = out / "private" / "eval2" / "worlds" / cfg["id"]
        pub.mkdir(parents=True, exist_ok=True)
        priv.mkdir(parents=True, exist_ok=True)
        (priv / "world.json").write_text(json.dumps(spec, indent=2) + "\n")
        (priv / "reference.json").write_text(json.dumps(reference, indent=2, default=str) + "\n")
        (pub / "brief.json").write_text(json.dumps(build_brief(World(spec)), indent=2) + "\n")
    (out / "private" / "eval2").mkdir(parents=True, exist_ok=True)
    (out / "private" / "eval2" / "confirmatory-worlds.json").write_text(
        json.dumps(verified, indent=2) + "\n")
    return verified


# ---------------------------------------------------------------------------
# v1 re-certification
# ---------------------------------------------------------------------------

@prec50
def certify_v1(world_ids=None, log=print, configs=None) -> dict:
    """Re-run the hardness certificate under the v1 ruleset for the shipped worlds.

    Mirrors :func:`generate` step by step with every candidate scored by v1:

    1. the 729-template search on the provisional world (target 1.0) must give
       the same M*, min-feasible multiple and number of feasible templates as
       the same search scored by the paper ruleset (the action spaces coincide:
       template capital is >= 0 and derived hires are whole and >= 0);
    2. the target recomputed from those numbers and from the v1-feasible naive
       heuristics on the provisional world (exactly the paper's anchor rule)
       must equal the shipped target;
    3. on the shipped world the careful optimum must pass v1 and the four named
       naive heuristics must fail v1;
    4. the added naive family, a flat equal split at x% of the growth cap with
       the certificate's capacity-derived hires (:func:`make_world_lib.flat_split_candidates`),
       must fail v1 at every x in 1..100. Its pass range under the paper
       scorer with capacity-exact hires (layoffs allowed) is recorded to
       document E23-D5.

    Returns a JSON-serializable certificate (``ok`` is the conjunction).
    """
    from .world import load_reference

    cert = {"schema": "dbwm-eval2-certificate-v1/1", "worlds": {}, "ok": True}
    cfgs = CONFIGS if configs is None else validate_configs(configs)
    ids = world_ids or [c["id"] for c in cfgs]
    for wid in ids:
        world = load_world(wid)
        ref = load_reference(wid)
        target = Decimal(world.goal["target_multiple"])
        cfg = next(c for c in cfgs if c["id"] == wid)
        w1 = World(spec_from_config(cfg, "1.0"))
        best_p, feas_p, _ = mw.search_optimum(w1, ruleset="paper")
        best_v, feas_v, _ = mw.search_optimum(w1, ruleset="v1")

        def _naive_v1(w):
            return {n: score_scenario_v1(w, {"allocation": {
                "growth_capital": cap, "eng_headcount": mw.derive_headcount(w, cap)[0]}})
                for n, cap in mw.naive_capital_allocations(w).items()}

        # anchor exactly as generate() does: naive heuristics on the target-1.0 world
        naive_anchor = _naive_v1(w1)
        naive_feas = [Decimal(v["goal_multiple_actual"]) for v in naive_anchor.values()
                      if v["fully_valid"]]
        best_naive = max(naive_feas) if naive_feas else Decimal("0")
        min_feas = min(feas_v)
        floor_anchor = max(min_feas, best_naive)
        tgt = (floor_anchor + (best_v[0] - floor_anchor) * Decimal("0.6")).quantize(Decimal("0.01"))
        naive_v1 = _naive_v1(world)
        careful = score_scenario_v1(world, {"allocation": ref["careful_optimum"]["allocation"]})
        flat = mw.flat_split_candidates(world)
        flat_v1_pass = [r["pct"] for r in flat if r["v1"]["fully_valid"]]
        flat_layoff_pass = [r["pct"] for r in flat if r["paper_layoffs"]["fully_valid"]]
        best_flat_v1 = max((Decimal(r["v1"]["multiple"]) for r in flat
                            if r["v1"]["multiple"] is not None and not r["v1"]["violations"]),
                           default=None)
        checks = {
            "same_mstar_as_paper": best_v[0] == best_p[0],
            "same_feasible_count_as_paper": len(feas_v) == len(feas_p),
            "same_min_feasible_as_paper": min(feas_v) == min(feas_p),
            "mstar_equals_reference": str(best_v[0]) == ref["hardness_floor"]["careful_optimum_multiple"],
            "min_feasible_equals_reference": str(min_feas) == ref["hardness_floor"]["min_feasible_multiple"],
            "recomputed_target_equals_shipped": tgt == target,
            "careful_optimum_passes_v1": bool(careful["fully_valid"]),
            "named_naive_fail_v1": not any(v["fully_valid"] for v in naive_v1.values()),
            "flat_split_fails_v1_at_every_pct": not flat_v1_pass,
        }
        ok = all(checks.values())
        cert["ok"] = cert["ok"] and ok
        cert["worlds"][wid] = {
            "target": str(target),
            "mstar_v1": str(best_v[0]),
            "min_feasible_v1": str(min_feas),
            "best_feasible_naive_v1_at_target_1": str(best_naive),
            "feasible_templates_v1": len(feas_v),
            "templates_clearing_target_v1": sum(1 for m in feas_v if m >= target),
            "careful_optimum_v1": {"fully_valid": careful["fully_valid"],
                                   "multiple": careful["goal_multiple_actual"]},
            "named_naive_v1": {n: {"fully_valid": v["fully_valid"],
                                   "multiple": v["goal_multiple_actual"],
                                   "violations": v["violations"]}
                               for n, v in naive_v1.items()},
            "flat_split_v1_passing_pcts": flat_v1_pass,
            "flat_split_v1_best_constraint_feasible_multiple": (
                str(best_flat_v1) if best_flat_v1 is not None else None),
            "flat_split_paper_with_layoffs_passing_pcts": flat_layoff_pass,
            "checks": checks,
            "ok": ok,
        }
        log(f"  [{wid}] M*_v1={float(best_v[0]):.4f} feasible={len(feas_v)} "
            f"target={target} flat_v1_pass={flat_v1_pass or 'none'} "
            f"flat_paper_layoffs_pass={_ranges(flat_layoff_pass)} -> {'OK' if ok else 'FAIL'}")
    return cert


def _ranges(pcts):
    if not pcts:
        return "none"
    out, start, prev = [], pcts[0], pcts[0]
    for p in pcts[1:] + [None]:
        if p is not None and p == prev + 1:
            prev = p
            continue
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
        if p is not None:
            start = prev = p
    return ",".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m dbwm.eval2.make_worlds",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="write data/ and private/ trees under this directory")
    ap.add_argument("--certify-v1", action="store_true",
                    help="re-certify the shipped worlds under the v1 ruleset")
    ap.add_argument("--json", help="with --certify-v1: write the certificate JSON here")
    ap.add_argument("--configs", help="private split: a JSON list of world configs (CONFIGS shape) used instead "
                                      "of the shipped ones by --out and --certify-v1")
    args = ap.parse_args(argv)
    if not args.out and not args.certify_v1:
        ap.error("pass --out DIR and/or --certify-v1")
    configs = None
    if args.configs:
        try:
            configs = load_configs(args.configs)
        except (OSError, ValueError) as e:
            ap.error(f"--configs: {e}")
    n = len(CONFIGS if configs is None else configs)
    rc = 0
    if args.out:
        print(f"== generating {n} candidate worlds into {args.out} ==")
        verified = write_worlds(Path(args.out), configs=configs)
        print(f"\n== {len(verified)}/{n} worlds verified-hard: {verified} ==")
        rc = 0 if verified else 1
    if args.certify_v1:
        print("== re-certifying hardness under the v1 ruleset ==")
        cert = certify_v1(configs=configs)
        if args.json:
            Path(args.json).write_text(json.dumps(cert, indent=2) + "\n")
        print(f"== v1 certificate: {'OK' if cert['ok'] else 'FAILED'} ==")
        rc = rc or (0 if cert["ok"] else 2)
    return rc


if __name__ == "__main__":
    sys.exit(main())
