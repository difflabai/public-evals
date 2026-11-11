"""Deterministic stub agents for eval-6 D1 (no LLM).

They behave like a (weak) agent: they read ONLY what is staged in the sandbox
and write the two deliverables there, so a stub run exercises staging, the
staged aided tools and the scorer end to end. Randomness is derived from
sha256(cell_id) so reruns are identical and reps differ; float sums use
math.fsum so the plans are identical on every supported Python.

  unaided: the budget-scaled reference allocation from task.json (the rounded
           reference marketing the brief shows) with a price chosen from
           {0.90, 0.95, 1.00} by the cell hash (the paper's self-supply stub
           used a fixed 0.95, d1_planner.py:288-295, but on the TRUE refmkt,
           which an agent cannot see).
  aided:   loads the staged heldout_sim.py and fitted_model.json, compares six
           candidate plans (3 prices x {reference, hub tilt in the direction of
           the model's coupling sign}) under the fitted model, commits the best,
           then runs the staged ``model_sim.py --plan plan.json`` CLI once and
           claims its expected profit.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from dbwm.core.types import Cell

PRICES = (0.90, 0.95, 1.00)


def _h(cell: Cell, label: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{cell.cell_id}|{label}".encode()).digest()[:8], "big")


def _write(sandbox: Path, name: str, obj) -> None:
    (sandbox / name).write_text(json.dumps(obj, indent=2) + "\n")


def _history_last(sandbox: Path, window: int = 4) -> dict:
    rows = list(csv.DictReader((sandbox / "public" / "history.csv").open(newline="")))
    by_seg: dict[str, list[dict]] = {}
    for r in rows:
        by_seg.setdefault(r["segment"], []).append(r)
    return {s: rs[-window:] for s, rs in by_seg.items()}


def unaided(cell: Cell, sandbox: Path) -> None:
    task = json.loads((sandbox / "public" / "task.json").read_text())
    cap = float(task["budget_constraint"]["cap"])
    ref = {s: float(task["reference_marketing"][s]) for s in task["segments"]}
    tot = math.fsum(ref.values())
    price = PRICES[_h(cell, "price") % len(PRICES)]
    plan = {s: {"marketing": ref[s] / tot * cap, "price": price} for s in task["segments"]}
    last = _history_last(sandbox)
    K = int(task["decision_horizon"])
    # crude projection: recent revenue run-rate at 75% margin minus spend (deliberately naive)
    rev = math.fsum(math.fsum(float(r["revenue"]) for r in last[s]) / len(last[s]) for s in task["segments"])
    projected = round(K * (0.75 * rev - cap), 2)
    _write(sandbox, "plan.json", {"plan": plan})
    _write(sandbox, "claims.json", {
        "projected_held_out_profit": projected,
        "rationale": ("Stub: reference marketing allocation scaled to the cap with a uniform "
                      f"price of {price}; no identification attempted."),
        "analysis_runs": 1,
    })


def _load_staged_sim(sandbox: Path):
    path = sandbox / "heldout_sim.py"
    name = "_dbwm_stub_heldout_sim_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules[cls.__module__],
    # so the module must be registered before it executes.
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def aided(cell: Cell, sandbox: Path) -> None:
    hs = _load_staged_sim(sandbox)
    fm = json.loads((sandbox / "fitted_model.json").read_text())
    model = hs.model_from_dict(fm["fitted_world"])
    nowcast = (float(fm["latent_nowcast"][0]), [float(x) for x in fm["latent_nowcast"][1]])
    cust = [float(x) for x in fm["cust_current"]]
    budget = float(fm["budget"])
    ids = [s.id for s in model.segments]
    S = len(ids)
    counts = [sum(1 for s in range(S) if model.coupling[s][j] != 0.0) for j in range(S)]
    hub = counts.index(max(counts))
    signs = list(fm["validation_report"]["coupling_signs"].values())
    complement = signs.count("complement") >= signs.count("cannibalize")
    refm = [s.refmkt for s in model.segments]
    base = [m / math.fsum(refm) * budget for m in refm]
    tilt = list(base)
    shift = 0.3 * (budget - base[hub]) if complement else -0.5 * base[hub]
    tilt[hub] += shift
    others = [i for i in range(S) if i != hub]
    for i in others:
        tilt[i] -= shift * base[i] / math.fsum(base[j] for j in others)
    seed = 3000 + _h(cell, "sim") % 1000
    best = None
    for alloc_name, alloc in (("reference", base), ("hub_tilt", tilt)):
        for price in (0.85, 1.0, 1.15):
            plan = {ids[i]: {"marketing": max(0.0, alloc[i]), "price": price} for i in range(S)}
            v, _, _ = hs.held_out_profit(model, plan, nowcast, cust, n_seeds=24, base_seed=seed)
            if best is None or v > best[0]:
                best = (v, plan, alloc_name, price)
    _, plan, alloc_name, price = best
    _write(sandbox, "plan.json", {"plan": plan})
    proc = subprocess.run([sys.executable, "model_sim.py", "--plan", "plan.json", "--n-seeds", "50"],
                          cwd=str(sandbox), capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"staged model_sim.py failed: {proc.stderr[-500:]}")
    sim = json.loads(proc.stdout)
    _write(sandbox, "claims.json", {
        "projected_held_out_profit": sim["expected_held_out_profit"],
        "rationale": (f"Stub: best of 6 candidates under the fitted model ({alloc_name} allocation, "
                      f"price {price}); coupling read as {'complement' if complement else 'cannibalize'}."),
        "analysis_runs": 7,
    })


def stub_agent(cell: Cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
    """EvalDef.stub_agent. Eval 6 has no oracle tools, so call_oracle is never used."""
    sandbox = Path(sandbox)
    if cell.arm == "unaided":
        unaided(cell, sandbox)
    elif cell.arm == "aided":
        aided(cell, sandbox)
    else:
        raise KeyError(f"unknown eval6 arm {cell.arm!r}")
