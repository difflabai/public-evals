"""The eval-1 "run the model" oracle tool (op ``run_model``), served by the core oracle server.

Port of the paper's agent-facing tool (bwm_query.py, local backend). The tool
evaluates a candidate plan on the company's model and reports, by SEALED mode:

  numeric  full projected figures + per-constraint pass/fail + goal check
  prose    pass/fail in prose, no figures
  raw      projected figures only, no pass/fail
  broken   figures corrupted by ``_break_values`` (deterministic, identity-
           violating), with goal/constraint checks computed on the corrupted
           figures

The mode comes ONLY from ``ctx.mode`` (fixed by the harness per arm). Payload
keys can never select it; the core server additionally strips and logs
mode/backend/... keys. ``render_paper`` reproduces the paper tool's stdout for
golden tests; ``render_v1`` is what agents get. v1 differences
(change-ledger/eval1.md):

  E1-T01  no "_warning ... (lesion mode)" key in broken output, and no "mode" /
          "backend" keys in any output (all three labelled the arm's channel);
          broken output is otherwise byte-for-byte the paper's corruption.
  E1-T02  prose summary drops the fixed "grows ... to a notably higher level"
          sentence (printed even when revenue fell); keeps the strong/modest
          reading the paper derived from the goal multiple.
  E1-T03  the plan is validated with the v1 scorer's rules (lever whitelist,
          list values, finite numbers), so tool and scorer always agree.
  E1-T04  no live backend: the Python engine is the only and normative backend.
          (The paper's tool queried the live service.)
  E1-T05  v1 never crashes on agent input: a failure after evaluation (e.g.
          a NaN lever value reaching a comparison) is an {"ok": false} reply;
          the paper tool died with a traceback and printed nothing.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Mapping

from dbwm.core.types import OracleContext

from .engine import check_constraints, evaluate, goal_multiple, load_model_and_tiers, prec50
from .score import REPORT_DRIVERS, parse_plan_v1, split_instance

MODES = ("numeric", "prose", "raw", "broken")

RAW_NOTE = ("Figures only. No pass/fail check is provided. Determine "
            "yourself, from these figures, whether the goal multiple and "
            "every constraint are satisfied.")
BROKEN_WARNING = ("figures are internally inconsistent (lesion mode); "
                  "they do not satisfy the model's accounting identities")


def _bindings_from(plan):
    """bwm_query.py _bindings_from, verbatim (paper tool's plan reading)."""
    raw = plan.get("bindings", plan) if isinstance(plan, dict) else {}
    out = {}
    for k, v in raw.items():
        if k in ("id", "name"):
            continue
        if isinstance(v, dict) and "values" in v:
            v = v["values"]
        if isinstance(v, list):
            out[k] = [str(x) for x in v]
    return out


@prec50
def _break_values(periods):
    """Lesion (verbatim): perturb each series by a per-driver factor so the
    numbers no longer satisfy the model's identities. Deterministic, but
    internally INCONSISTENT. Factor for the j-th sorted driver at period i is
    1 + ((j % 5) + 1)/20 + i/50, so the corruption only ever inflates positive
    figures (paper Appendix A entry 2)."""
    broken = []
    factors = {}
    for i, snap in enumerate(periods):
        row = {}
        for j, (k, v) in enumerate(sorted(snap.items())):
            f = factors.setdefault(k, Decimal("1.0") + Decimal((j % 5) + 1) / Decimal(20))
            row[k] = v * (f + Decimal(i) / Decimal(50))
        broken.append(row)
    return broken


class _EvalFailed(Exception):
    """evaluate() rejected the plan: the paper tool printed {"ok": false, "error": ...}."""


@prec50
def _evaluate_report(model, tiers_doc, tier_name, bindings, mode):
    """Shared core of both renderers. Returns (periods, report_periods, details, gm, target, goal_ok, traj).

    Only ``evaluate`` failures are reported as ``_EvalFailed`` (bwm_query.py:132-139
    guarded exactly that call); anything later propagates, as it did in the
    paper tool (which then died with a traceback and printed nothing).
    """
    goal = tiers_doc["goal"]
    tier = tiers_doc["tiers"][tier_name]
    try:
        periods = evaluate(model, bindings)
    except Exception as e:  # noqa: BLE001 - mirrors bwm_query.py:137-139
        raise _EvalFailed(str(e)) from e
    periods = [{d: p[d] for d in p} for p in periods]
    report_periods = _break_values(periods) if mode == "broken" else periods
    details = check_constraints(model, report_periods, bindings, tier)
    gm = goal_multiple(report_periods, goal)
    target = Decimal(str(goal["target_multiple"]))
    goal_ok = gm is not None and gm >= target
    drivers = REPORT_DRIVERS + [d for d in bindings if d not in REPORT_DRIVERS]
    traj = {d: [str(p[d]) for p in report_periods] for d in drivers if d in report_periods[0]}
    return periods, report_periods, details, gm, target, goal_ok, traj


@prec50
def _render(model, tiers_doc, model_id, tier_name, bindings, mode, *, paper: bool,
            backend: str = "local"):
    if mode not in MODES:
        raise ValueError(f"unknown run_model mode {mode!r}")
    try:
        periods, report_periods, details, gm, target, goal_ok, traj = _evaluate_report(
            model, tiers_doc, tier_name, bindings, mode)
    except _EvalFailed as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        if paper:
            raise  # the paper tool crashed here (traceback, no output)
        # v1 never crashes on agent input (E1-T05).
        return {"ok": False, "error": f"the model could not evaluate this plan ({type(e).__name__})"}

    if mode == "prose":
        lines = [f"Projection for {model['name']} over {len(periods)} quarters:"]
        rev = report_periods[-1].get("revenue")
        rev0 = report_periods[0].get("revenue")
        if rev and rev0:
            mult = rev / rev0 if rev0 else None
            look = "strong" if (mult and mult >= target) else "modest"
            if paper:
                lines.append(f"- Revenue grows from its starting level to a notably higher "
                             f"level by the final quarter; growth looks {look}.")
            else:
                lines.append(f"- Revenue growth from the starting level to the final "
                             f"quarter looks {look}.")
        lines.append(f"- The growth goal appears {'MET' if goal_ok else 'NOT met'}.")
        for d in details:
            lines.append(f"- {d['id']}: {'within limits' if d['ok'] else 'OVER the limit'} "
                         f"({d['description']})")
        lines.append("(Figures withheld; qualitative summary only.)")
        return "\n".join(lines)

    if mode == "raw":
        out = {"ok": True}
        if paper:
            out.update({"mode": "raw", "backend": backend})
        out.update({"model_id": model_id, "tier": tier_name, "trajectory": traj,
                    "_note": RAW_NOTE})
        return out

    out = {"ok": True}
    if paper:
        out.update({"mode": mode, "backend": backend})
    out.update({
        "model_id": model_id,
        "tier": tier_name,
        "goal": {"target_multiple": str(target),
                 "actual_multiple": str(gm) if gm is not None else None,
                 "met": goal_ok},
        "constraints": [{"id": d["id"], "limit": d["limit"], "actual": d["actual"],
                         "ok": d["ok"]} for d in details],
        "violations": [d["id"] for d in details if not d["ok"]],
        "trajectory": traj,
    })
    if paper and mode == "broken":
        out["_warning"] = BROKEN_WARNING
    return out


def render_paper(model_id: str, tier_name: str, plan: Any, mode: str):
    """The paper tool's output object (or prose text) for ``plan``.

    ``json.dumps(result, indent=2)`` (or the text itself for prose) equals the
    paper tool's stdout minus the trailing newline. Golden tests only; never
    served to agents.
    """
    model, tiers_doc = load_model_and_tiers(model_id)
    return _render(model, tiers_doc, model_id, tier_name, _bindings_from(plan), mode, paper=True)


def render_v1(model_id: str, tier_name: str, plan: Any, mode: str):
    """What a v1 agent receives for ``plan`` in ``mode``."""
    model, tiers_doc = load_model_and_tiers(model_id)
    bindings, err = parse_plan_v1(plan, model, tiers_doc)
    if err is not None:
        return {"ok": False, "error": err}
    return _render(model, tiers_doc, model_id, tier_name, bindings, mode, paper=False)


def run_model(payload: Mapping[str, Any], ctx: OracleContext):
    """Oracle handler for op ``run_model``. Pure: (payload, ctx) -> result.

    The only payload key read is ``plan`` (an object, or a JSON string holding
    one). The mode is ``ctx.mode`` and nothing else.
    """
    mode = ctx.mode
    if mode not in MODES:
        raise RuntimeError(f"eval1 run_model: sealed mode {mode!r} is not one of {MODES}")
    company, tier = split_instance(ctx.cell.instance)
    plan = payload.get("plan") if isinstance(payload, Mapping) else None
    if plan is None:
        return {"ok": False, "error": "missing 'plan': pass the candidate plan, e.g. "
                                      "{\"bindings\": {\"<lever>\": [one value per quarter]}}"}
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"plan is not valid JSON: {e}"}
    return render_v1(company, tier, plan, mode)
