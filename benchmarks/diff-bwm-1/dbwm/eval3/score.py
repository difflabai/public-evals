"""Eval-3 scoring: eval 2's scorer (same oracle, same rulesets) plus self-model fidelity.

Achievement, overclaim and forecast error are eval 2's, computed against the
TRUE world on the committed allocation (``dbwm.eval2.score_submission``:
``paper`` bit for bit, ``v1`` corrected). Eval 3 adds, for the generative
arms, the fields the paper's planner added (coupled_planner.py:580-583):

* ``declared_model`` -- ``model.json`` exists among the deliverables (paper).
* ``self_model_fidelity_pct_error`` -- the paper's operational fidelity: the
  verdict's ``forecast_pct_error`` (|claimed final combined revenue - true| /
  true x 100), i.e. what the agent TYPED into claims.json. None when the claim
  is missing or unparseable.

and, under ``v1`` only (new; package-only, not in v7):

* ``declared_model_valid`` / ``declared_model_error`` -- model.json parses
  and projects with the DSL (within the oracle's size limits).
* ``model_plan_fidelity_pct_error`` -- the agent's OWN model evaluated on the
  plan it committed (each unit's ``marketing``/``hires`` replaced by the
  committed allocation) vs the true world on that plan, final combined
  revenue. This measures the self-model directly (pre-registered H4's
  mediator) instead of the number the agent typed. ``model_plan_fidelity_status``
  says why it is None when it is (no model, model invalid, units or levers
  not matching the allocation). The true world is evaluated on the plan as
  written, so a plan outside the v1 action space (which fails v1) still gets
  a fidelity.
* ``self_model_fidelity_pct_error_paper`` -- the paper-style fidelity of the
  same submission (v1's own forecast error is None for plans it rejects).

Every arm also gets the paper's ``branch`` label. Nothing here reads a
transcript; the oracle-call counts are process metrics the runner records in
``verdict.process.oracle_calls``.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from dbwm.eval2 import load_world, prec50, score_submission

from . import model_dsl
from .arms import DSL_ARMS, GEN_ARMS, branch
from .oracle import limit_problem

MODEL_FILE = "model.json"
LEVERS = {"growth_capital": "marketing", "eng_headcount": "hires"}


def _read_model(sub_dir: Path) -> tuple[Any, str | None]:
    p = Path(sub_dir) / MODEL_FILE
    if not p.is_file():
        return None, "no model.json"
    try:
        return json.loads(p.read_bytes().decode("utf-8")), None
    except Exception as e:  # noqa: BLE001
        return None, f"model.json unparseable: {e}"


@prec50
def model_plan_fidelity(spec: Any, allocation: Any, oracle_final: Any, world) -> tuple[str | None, str]:
    """(pct_error, status): the agent's model on its committed plan vs the true world.

    ``allocation`` is the committed allocation.json object; ``oracle_final`` the
    true final combined revenue on that plan as written (the paper verdict's
    ``oracle_combined_final_revenue``, which exists even for plans v1 rejects).
    """
    if spec is None:
        return None, "no model"
    if not isinstance(spec, dict) or not isinstance(spec.get("units"), dict):
        return None, "model has no units object"
    if oracle_final is None:
        return None, "true world not evaluable on the committed plan"
    if limit_problem(spec):
        return None, "model exceeds the size limits"
    alloc = allocation.get("allocation", allocation) if isinstance(allocation, dict) else None
    if not isinstance(alloc, dict):
        return None, "allocation not an object"
    world_units = [u["id"] for u in world.units]
    if sorted(spec["units"]) != sorted(world_units):
        return None, "model units differ from the world's units"
    sub = copy.deepcopy(spec)
    for pool, lever in LEVERS.items():
        by_unit = alloc.get(pool)
        if not isinstance(by_unit, dict):
            return None, f"allocation lacks {pool}"
        for uid in world_units:
            unit = sub["units"][uid]
            if not isinstance(unit, dict):
                return None, f"model unit {uid} is not an object"
            plan = unit.setdefault("plan", {})
            if not isinstance(plan, dict) or lever not in plan:
                return None, f"model unit {uid} has no '{lever}' lever"
            series = by_unit.get(uid)
            if isinstance(series, dict) and "values" in series:
                series = series["values"]
            if not isinstance(series, list):
                return None, f"allocation lacks {pool}:{uid}"
            plan[lever] = list(series)
    ev = model_dsl.evaluate_model(sub)
    if isinstance(ev, dict):
        return None, f"model does not evaluate on the committed plan ({ev.get('error')})"
    g = world.goal
    tp = int(g.get("target_period", world.horizon - 1))
    metric = g["metric"]
    if metric not in ev.parent or tp >= len(ev.parent[metric]):
        return None, f"model has no {metric}[{tp}]"
    pred = ev.parent[metric][tp]
    true = Decimal(str(oracle_final))
    if true == 0:
        return None, "true final revenue is zero"
    return str(abs(pred - true) / abs(true) * Decimal(100)), "ok"


def score_eval3(world_id: str, sub_dir: Path, ruleset: str, arm: str, rep: int) -> tuple[dict, dict]:
    """Score one submission: ``(verdict, ScoreResult kwargs)``."""
    sub_dir = Path(sub_dir)
    world = load_world(world_id)
    verdict, kw = score_submission(world, sub_dir, ruleset, condition=arm, rep=rep)
    details = kw["details"]
    verdict["branch"] = branch(arm)
    details["branch"] = verdict["branch"]
    if arm not in GEN_ARMS:
        return verdict, kw
    declared = (sub_dir / MODEL_FILE).exists()
    verdict["declared_model"] = declared
    verdict["self_model_fidelity_pct_error"] = verdict.get("forecast_pct_error")
    details["declared_model"] = declared
    details["self_model_fidelity_pct_error"] = verdict["self_model_fidelity_pct_error"]
    if ruleset != "v1":
        return verdict, kw
    # The true world on the plan AS WRITTEN (the paper scorer evaluates layoffs and
    # omitted units; v1 refuses to, so its forecast is None for such plans). Fidelity
    # is a property of the agent's model, so it is measured against this.
    pv, _ = score_submission(world, sub_dir, "paper", condition=arm, rep=rep)
    spec, err = _read_model(sub_dir)
    valid = None
    if spec is not None:
        problem = limit_problem(spec)
        ev = model_dsl.evaluate_model(spec) if not problem else {"error": problem}
        valid = not isinstance(ev, dict)
        err = None if valid else ev.get("error")
    if arm not in DSL_ARMS and spec is None:
        fid, status = None, "no model.json (gen-code models in its own code)"
    elif details.get("committed_error"):
        fid, status = None, "no committed allocation"
    else:
        try:
            alloc = json.loads((sub_dir / "allocation.json").read_bytes().decode("utf-8"))
        except Exception:  # noqa: BLE001
            alloc = None
        fid, status = model_plan_fidelity(spec, alloc, pv.get("oracle_combined_final_revenue"), world) \
            if alloc is not None else (None, "no committed allocation")
    extra = {"declared_model_valid": valid, "declared_model_error": err,
             "model_plan_fidelity_pct_error": fid, "model_plan_fidelity_status": status,
             "self_model_fidelity_pct_error_paper": pv.get("forecast_pct_error")}
    verdict.update(extra)
    details.update(extra)
    return verdict, kw


__all__ = ["score_eval3", "model_plan_fidelity", "MODEL_FILE"]
