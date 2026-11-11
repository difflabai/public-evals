#!/usr/bin/env python3
"""Aided-arm simulator: forward-simulate a candidate plan under the FITTED model.

The agent proposes a plan and runs this to read its expected held-out profit
under the calibrated model, iterating to find the best plan. This is the
agent's OWN model; it is NOT the true-dynamics oracle that scores the committed
plan, so a plan that looks optimal here is only as good as the fitted model.

USAGE  python3 model_sim.py --plan plan.json [--model fitted_model.json] [--n-seeds 200]
  plan.json = {"plan": {"<seg>": {"marketing": <usd>, "price": <mult>}, ...}}
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import heldout_sim as hs  # noqa: E402

BASE_SEED = 2026


def simulate(fm, raw, n_seeds=200, base_seed=BASE_SEED):
    """Expected held-out profit of plan `raw` under fitted model `fm` (parsed JSON)."""
    model = hs.model_from_dict(fm["fitted_world"])
    nowcast = (float(fm["latent_nowcast"][0]), [float(x) for x in fm["latent_nowcast"][1]])
    cust = [float(x) for x in fm["cust_current"]]
    budget = float(fm["budget"])

    plan = raw.get("plan", raw)
    # coerce to the {seg: {marketing, price}} shape the simulator expects
    clean = {}
    for seg in model.segments:
        p = plan.get(seg.id, {}) if isinstance(plan, dict) else {}
        clean[seg.id] = {"marketing": float(p.get("marketing", seg.refmkt)),
                         "price": float(p.get("price", seg.refprice))}
    total_mkt = sum(clean[s.id]["marketing"] for s in model.segments)

    profit, se, per_seg = hs.held_out_profit(model, clean, nowcast, cust,
                                             n_seeds=n_seeds, base_seed=base_seed)
    return {
        "expected_held_out_profit": round(profit, 2),
        "stderr": round(se, 2),
        "per_segment_profit": {s.id: round(float(per_seg[i]), 2)
                               for i, s in enumerate(model.segments)},
        "total_marketing_per_quarter": round(total_mkt, 2),
        "budget_cap": round(budget, 2),
        "over_budget": total_mkt > budget * 1.0001,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--model", default="fitted_model.json")
    ap.add_argument("--n-seeds", type=int, default=200)
    args = ap.parse_args()

    fm = json.loads(Path(args.model).read_text())
    raw = json.loads(Path(args.plan).read_text())
    print(json.dumps(simulate(fm, raw, n_seeds=args.n_seeds), indent=2))


if __name__ == "__main__":
    main()
