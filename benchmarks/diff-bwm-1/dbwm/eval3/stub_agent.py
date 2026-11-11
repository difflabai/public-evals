"""Deterministic, LLM-free stand-in agent for every eval-3 arm.

It plays the task the way the prompt describes, using only what an agent on
that arm has: ``brief.json`` (and ``task.json`` where staged) in the sandbox,
the oracle ops through ``call_oracle`` (which goes through the real per-trial
oracle server when driven by the runner, so the sealed mode applies), and the
funnel structure (``fixtures.FAITHFUL_FORMULAS``) -- the "known model" a
generative agent is asked to discover. It never reads private data.

Strategy (per-cell order from a sha256 of the cell id):

* gen-figures / gen-both -- build the faithful model from the brief's public
  economics, search a small family of allocation templates on it (capital
  shapes x intensities scaled to the growth cap; the minimal whole net hires
  that keep capacity, from the model's own customer trajectory), pick the
  first template its model says meets the goal and every constraint, write
  ``model.json`` and run it through the tool: ``model_eval`` (gen-figures
  judges the returned figures itself, as the prompt says) or ``model_eval``
  then ``model_test`` (gen-both reads the verdict). Claims are exactly what
  the tool reported. With a faithful model these plans are truly feasible.
* gen-code -- the same search in its own "code" (written to ``model.py`` as a
  record), but with a DELIBERATELY WRONG self-model that ignores churn,
  searching the weakest plans first. Its model says the plan passes; the true
  world says it misses the goal and the cash floor. This exercises the
  consistent-but-wrong overclaim path (pre-registered H3) end to end.
* ungrounded / grounded-iterate -- eval 2's own stub (``dbwm.eval2.EVAL.stub_agent``).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from dbwm.core.types import Cell
from dbwm.eval2 import EVAL as EVAL2
from dbwm.eval2 import prec50

from . import model_dsl
from .arms import BASELINE_ARMS
from .fixtures import FAITHFUL_FORMULAS, faithful_model

SHAPES = {
    "flat": (1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
    "front": (0.3, 1.0, 1.0, 0.7, 0.4, 0.2),
    "mid": (0.5, 1.0, 1.0, 1.0, 0.5, 0.25),
    "back": (0.2, 0.4, 0.6, 0.8, 1.0, 1.0),
    "zero": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
}
INTENSITIES = (1.0, 0.75, 0.5)
UNIT_MAX = 130000  # one unit's marketing per quarter at intensity 1, before scaling to the cap

# The stub's gen-code self-model: the faithful funnel with churn left out.
CHURN_BLIND_FORMULAS = dict(FAITHFUL_FORMULAS,
                            customers="prior(customers, customers_init) + new_customers")


def _seed(cell_id: str) -> int:
    return int.from_bytes(hashlib.sha256(cell_id.encode("utf-8")).digest()[:8], "big")


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _templates(cell_id: str, weakest_first: bool):
    """Deterministic (shapes, intensities) order: strongest (or weakest) total intensity first,
    rotated within each intensity level by the cell's seed."""
    shapes_all = list(itertools.product(SHAPES, repeat=3))
    k = _seed(cell_id) % len(shapes_all)
    shapes_all = shapes_all[k:] + shapes_all[:k]
    intens_all = sorted(itertools.product(INTENSITIES, repeat=3),
                        key=lambda t: (sum(t) if weakest_first else -sum(t), t))
    for intens in intens_all:
        for shapes in shapes_all:
            yield shapes, intens


def _capital(brief: dict, shapes, intens) -> dict[str, list[int]]:
    H = int(brief["horizon"])
    uids = [u["id"] for u in brief["units"]]
    cap = [int(Decimal(str(c))) for c in
           next(p for p in brief["pools"] if p["id"] == "growth_capital")["cap_per_quarter"]]
    out = {u: [0] * H for u in uids}
    for t in range(H):
        raw = {u: SHAPES[shapes[i]][min(t, 5)] * intens[i] * UNIT_MAX for i, u in enumerate(uids)}
        tot = sum(raw.values())
        f = min(1.0, cap[t] / tot) if tot > 0 else 0.0
        for u in uids:
            out[u][t] = int(raw[u] * f)  # whole dollars, rounded down: never over the cap
    return out


def _model(brief: dict, growth, hires, formulas) -> dict:
    spec = faithful_model(brief, growth, hires)
    spec["formulas"] = dict(formulas)
    return spec


@prec50
def _capacity_hires(brief: dict, growth, formulas) -> dict[str, list[int]] | None:
    """Minimal whole, non-negative net hires that keep headcount >= customers/cph on the model."""
    H = int(brief["horizon"])
    zero = {u["id"]: [0] * H for u in brief["units"]}
    ev = model_dsl.evaluate_model(_model(brief, growth, zero, formulas))
    if isinstance(ev, dict):
        return None
    out = {}
    for u in brief["units"]:
        cph = Decimal(str(u["customers_per_head"]))
        hc = Decimal(str(u["starting"]["headcount"]))
        hires = []
        for t in range(H):
            need = Decimal(math.ceil(ev.unit_periods[u["id"]][t]["customers"] / cph))
            h = max(Decimal(0), need - hc)
            hires.append(int(h))
            hc += h
        out[u["id"]] = hires
    return out


@prec50
def _judge(brief: dict, trajectory: dict) -> tuple[bool, bool, str | None]:
    """(goal_met, parent constraints ok, final revenue) read off a combined trajectory."""
    g = brief["goal"]
    rev = [Decimal(str(x)) for x in trajectory.get("revenue", [])]
    b, tp = int(g["baseline_period"]), int(g["target_period"])
    if len(rev) <= tp or rev[b] == 0:
        return False, False, None
    goal = rev[tp] / rev[b] >= Decimal(str(g["target_multiple"]))
    ok = True
    for c in brief["parent_constraints"]:
        lim = Decimal(str(c["limit"]))
        key = c["metric"] if c["kind"] == "parent_series_min" else "gross_margin_pct"
        ser = [Decimal(str(x)) for x in trajectory.get(key, [])]
        if not ser or min(ser) < lim:
            ok = False
    return goal, ok, str(rev[tp])


def _search(cell: Cell, brief: dict, formulas, weakest_first: bool):
    """First template whose plan the given self-model says passes: (growth, hires, n_evaluated)."""
    H = int(brief["horizon"])
    uids = [u["id"] for u in brief["units"]]
    hcap = [int(Decimal(str(c))) for c in
            next(p for p in brief["pools"] if p["id"] == "eng_headcount")["cap_per_quarter"]]
    n = 0
    first = None
    for shapes, intens in _templates(cell.cell_id, weakest_first):
        growth = _capital(brief, shapes, intens)
        hires = _capacity_hires(brief, growth, formulas)
        n += 1
        if hires is None or any(sum(hires[u][t] for u in uids) > hcap[t] for t in range(H)):
            continue
        first = first or (growth, hires)
        ev = model_dsl.evaluate_model(_model(brief, growth, hires, formulas))
        if isinstance(ev, dict):
            continue
        goal, ok, _ = _judge(brief, {k: [str(x) for x in v] for k, v in ev.parent.items()})
        if goal and ok:
            return growth, hires, n
    if first is None:
        return {u: [0] * H for u in uids}, {u: [0] * H for u in uids}, n
    return first[0], first[1], n


def _allocation(growth, hires) -> dict:
    return {"allocation": {"growth_capital": {u: [str(x) for x in s] for u, s in growth.items()},
                           "eng_headcount": {u: [str(x) for x in s] for u, s in hires.items()}}}


def stub_agent(cell: Cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
    sandbox = Path(sandbox)
    if cell.arm in BASELINE_ARMS:
        EVAL2.stub_agent(Cell("eval2", cell.instance, cell.arm, cell.rep), sandbox, call_oracle)
        return
    brief = json.loads((sandbox / "brief.json").read_text(encoding="utf-8"))

    if cell.arm == "gen-code":
        growth, hires, n = _search(cell, brief, CHURN_BLIND_FORMULAS, weakest_first=True)
        spec = _model(brief, growth, hires, CHURN_BLIND_FORMULAS)
        ev = model_dsl.evaluate_model(spec)
        goal, ok, final = (False, False, None) if isinstance(ev, dict) else \
            _judge(brief, {k: [str(x) for x in v] for k, v in ev.parent.items()})
        (sandbox / "model.py").write_text(
            "# dbwm stub agent: the funnel it modelled (churn left out on purpose)\n"
            f"FORMULAS = {json.dumps(CHURN_BLIND_FORMULAS, indent=4)}\n", encoding="utf-8")
        _write_json(sandbox / "allocation.json", _allocation(growth, hires))
        _write_json(sandbox / "claims.json", {
            "goal_met": bool(goal), "constraints_satisfied": bool(ok),
            "projected_combined_final_revenue": final, "model_runs": n,
            "rationale": "Modelled the funnel in my own code and committed the weakest template "
                         "my model says passes."})
        return

    growth, hires, _n = _search(cell, brief, FAITHFUL_FORMULAS, weakest_first=False)
    spec = _model(brief, growth, hires, FAITHFUL_FORMULAS)
    _write_json(sandbox / "model.json", spec)
    calls = 0
    goal = ok = False
    final = None
    try:
        out = call_oracle("model_eval", {"file": "model.json"})
        calls += 1
        if isinstance(out, dict) and out.get("ok"):
            goal, ok, final = _judge(brief, out.get("trajectory") or {})
        if cell.arm == "gen-both":
            out = call_oracle("model_test", {"file": "model.json"})
            calls += 1
            if isinstance(out, dict) and out.get("ok"):
                goal = bool((out.get("goal") or {}).get("met"))
                ok = not out.get("violations")
    except Exception:  # noqa: BLE001 - e.g. the server refused; claim nothing
        goal = ok = False
    _write_json(sandbox / "allocation.json", _allocation(growth, hires))
    _write_json(sandbox / "claims.json", {
        "goal_met": bool(goal), "constraints_satisfied": bool(ok),
        "projected_combined_final_revenue": final, "model_runs": calls,
        "rationale": "Built the funnel model from the brief's economics, searched allocation "
                     "templates on it and committed the first one the tool's output showed as "
                     "meeting the goal and every constraint."})


__all__ = ["stub_agent", "CHURN_BLIND_FORMULAS", "SHAPES", "INTENSITIES"]
