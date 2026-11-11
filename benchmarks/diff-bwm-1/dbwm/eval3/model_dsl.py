"""The eval-3 modeling DSL: the paper's ``model_tool.py``, ported verbatim.

The agent declares its OWN model of the portfolio: per-unit parameters, a set
of SHARED funnel formulas (arithmetic over parameters, the plan levers and
other formula nodes, with ``prior(self, init)`` for roll-forwards) and a plan
(per-unit per-quarter ``marketing`` and ``hires``). The DSL compiles each
formula to the operating-model formula tree and projects the model with the
exact engine (``dbwm.eval2.evaluate``, the same verbatim copy of the paper's
``_model_engine.evaluate``), then rolls the units up. Nothing here reads a
world, an answer key or a scorer: the figures are only as correct as the
agent's model.

Provenance: ``bwm_coupled/model_tool.py`` (sha256 cf41620751d2...ae, the file
staged into every paper gen-figures / gen-both sandbox). The function
bodies below are the paper's, line for line, with three mechanical changes:

* ``evaluate`` comes from ``dbwm.eval2`` instead of a sibling
  ``_model_engine.py`` (identical body; see dbwm/eval2/engine.py).
* The paper ran every Decimal operation under the global context its engine
  set at import (prec 50). That only holds in the importing thread, and the
  per-trial oracle server answers on worker threads, so the public entry
  points here (:func:`evaluate_model`, :func:`test_model`) run inside
  ``dbwm.eval2.prec50`` -- the same context, made thread-local.
* The paper's CLI ``main()`` is split into :func:`evaluate_model` (load +
  roll up, with the paper's two error envelopes) and :func:`emit_eval` /
  :func:`emit_test` (the two JSON outputs). The oracle handlers in
  ``oracle.py`` compose them; the release (v1) differences live there, not
  here.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from typing import Any, Mapping

from dbwm.eval2 import evaluate, prec50

ROLLUP_DRIVERS = ["revenue", "gross_profit", "cogs", "net_burn", "cash",
                  "total_opex", "payroll", "headcount", "customers", "new_customers",
                  "leads"]

EVAL_NOTE = ("Figures from YOUR declared model (internally consistent with your "
             "formulas). No goal/constraint verdict is given — decide for yourself "
             "whether the goal multiple and every constraint hold. NOTE: these are "
             "only as correct as your model; they are NOT validated against the "
             "true world.")
TEST_NOTE = ("Verdict is against YOUR model, not the true world. A pass here means "
             "your model satisfies the targets — it does NOT guarantee the real "
             "portfolio does, if your model is inaccurate.")


# ---------------------------------------------------------------------------
# Expression DSL -> operating-model formula tree (compiles to the exact engine)
# model_tool.py:51-134
# ---------------------------------------------------------------------------

class ModelError(Exception):
    pass


def _const_value(node, params):
    """Evaluate an init expression (a number or a param name) to a constant."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        if node.id in params:
            return Decimal(str(params[node.id]))
        raise ModelError(f"prior() initial references unknown parameter {node.id!r}")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_const_value(node.operand, params)
    raise ModelError(f"prior() initial must be a number or a parameter name, got {ast.dump(node)}")


def _compile(node, self_name, params, state):
    """ast expr -> formula-tree dict (ops: const/ref/add/sub/mul/div/prior).
    Sets state['rollforward_init'] if a prior(self, init) is found."""
    if isinstance(node, ast.BinOp):
        op = {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "div"}.get(type(node.op))
        if op is None:
            raise ModelError(f"unsupported operator {type(node.op).__name__}")
        return {"op": op, "lhs": _compile(node.left, self_name, params, state),
                "rhs": _compile(node.right, self_name, params, state)}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return {"op": "sub", "lhs": {"op": "const", "value": "0"},
                "rhs": _compile(node.operand, self_name, params, state)}
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return {"op": "const", "value": str(node.value)}
    if isinstance(node, ast.Name):
        return {"op": "ref", "id": node.id}
    if isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id == "prior"):
            raise ModelError("only prior(self, init) calls are allowed in formulas")
        if len(node.args) != 2 or not isinstance(node.args[0], ast.Name):
            raise ModelError("prior(name, init) needs a node name and an initial value")
        ref_name = node.args[0].id
        if ref_name != self_name:
            raise ModelError(
                f"prior() may only reference the node it defines ('{self_name}'), not "
                f"'{ref_name}'")
        state["rollforward_init"] = _const_value(node.args[1], params)
        return {"op": "prior"}
    raise ModelError(f"unsupported expression: {ast.dump(node)}")


def compile_formula(expr, self_name, params):
    try:
        tree = ast.parse(expr, mode="eval").body
    except SyntaxError as e:
        raise ModelError(f"could not parse formula for {self_name!r}: {e}")  # noqa: B904 - verbatim paper code
    state = {"rollforward_init": None}
    compiled = _compile(tree, self_name, params, state)
    return compiled, state["rollforward_init"]


def build_unit_model(uid, params, plan, formulas, horizon):
    """Compile one unit's declared model into an operating-model dict for evaluate()."""
    nodes = {}
    # parameters -> constant leaves
    for pname, pval in params.items():
        nodes[pname] = {"kind": "leaf", "id": pname,
                        "default": {"values": [str(pval)] * horizon}}
    # plan levers -> bound leaves
    for lname, series in plan.items():
        if len(series) != horizon:
            raise ModelError(f"unit {uid}: plan[{lname}] has {len(series)} values, "
                             f"need {horizon}")
        nodes[lname] = {"kind": "leaf", "id": lname,
                        "default": {"values": [str(v) for v in series]}}
    # formulas -> computed / roll_forward
    for fname, expr in formulas.items():
        compiled, rf_init = compile_formula(expr, fname, params)
        if rf_init is not None:
            nodes[fname] = {"kind": "roll_forward", "id": fname,
                            "initial": str(rf_init), "formula": compiled}
        else:
            nodes[fname] = {"kind": "computed", "id": fname, "formula": compiled}
    return {"id": uid, "horizon": horizon, "nodes": nodes}


# ---------------------------------------------------------------------------
# Evaluate the agent's whole model + roll up (model_tool.py:137-163)
# ---------------------------------------------------------------------------

def load_model(spec):
    """The paper's ``_load_model``: compile and project every declared unit."""
    horizon = int(spec["horizon"])
    formulas = spec["formulas"]
    units = spec["units"]
    out = {}
    for uid, u in units.items():
        model = build_unit_model(uid, u.get("params", {}), u.get("plan", {}),
                                 formulas, horizon)
        out[uid] = evaluate(model)   # exact engine on the AGENT's declared model
    return horizon, units, out


def rollup(horizon, unit_periods):
    parent = {d: [Decimal(0)] * horizon for d in ROLLUP_DRIVERS}
    for periods in unit_periods.values():
        for t in range(horizon):
            for d in ROLLUP_DRIVERS:
                if d in periods[t]:
                    parent[d][t] += periods[t][d]
    parent["gross_margin_pct"] = [
        (parent["gross_profit"][t] / parent["revenue"][t]) if parent["revenue"][t] != 0
        else Decimal(0) for t in range(horizon)]
    return parent


def _ser(xs):
    return [str(x) for x in xs]


# ---------------------------------------------------------------------------
# test mode: the FIXED public goal + constraints, on the agent's trajectory
# (model_tool.py:170-224)
# ---------------------------------------------------------------------------

def run_checks(horizon, units, unit_periods, parent, task):
    details = []
    g = task["goal"]
    b = int(g.get("baseline_period", 0))
    tp = int(g.get("target_period", horizon - 1))
    rev = parent[g["metric"]]
    mult = (rev[tp] / rev[b]) if rev[b] != 0 else None
    target = Decimal(str(g["target_multiple"]))
    details.append({"id": "goal", "ok": (mult is not None and mult >= target),
                    "actual": str(mult) if mult is not None else None,
                    "target": str(target)})
    for c in task.get("parent_constraints", []):
        if c["kind"] == "parent_series_min":
            ser = parent[c["metric"]]
            lim = Decimal(str(c["limit"]))
            worst = min(range(horizon), key=lambda t: ser[t])
            details.append({"id": c["id"], "ok": ser[worst] >= lim,
                            "actual": str(ser[worst]), "limit": str(lim),
                            "worst_quarter": worst})
        elif c["kind"] == "parent_margin_min":
            ser = parent["gross_margin_pct"]
            lim = Decimal(str(c["limit"]))
            worst = min(range(horizon), key=lambda t: ser[t])
            details.append({"id": c["id"], "ok": ser[worst] >= lim,
                            "actual": str(ser[worst]), "limit": str(lim),
                            "worst_quarter": worst})
    # per-unit capacity: headcount >= customers / cph
    for cap in task.get("unit_capacity", []):
        uid, cph = cap["unit"], Decimal(str(cap["cph"]))
        periods = unit_periods.get(uid)
        ok = True
        worst = None
        if periods:
            for t in range(horizon):
                if "customers" in periods[t] and "headcount" in periods[t]:
                    if periods[t]["headcount"] < periods[t]["customers"] / cph:
                        ok = False
                        worst = t
        details.append({"id": f"capacity:{uid}", "ok": ok, "worst_quarter": worst})
    # pool caps: sum of plan levers <= cap per quarter
    for pool in task.get("pool_caps", []):
        cap = [Decimal(str(x)) for x in pool["cap"]]
        lever = pool["lever"]
        ok = True
        worst = None
        for t in range(horizon):
            tot = sum(Decimal(str(units[u]["plan"].get(lever, [0] * horizon)[t]))
                      for u in units)
            if tot > cap[t]:
                ok = False
                worst = t
        details.append({"id": f"pool_cap:{pool['id']}", "ok": ok, "worst_quarter": worst})
    violations = [d["id"] for d in details if not d["ok"]]
    return details, violations


# ---------------------------------------------------------------------------
# The two outputs (model_tool.py:231-289)
# ---------------------------------------------------------------------------

def emit_eval(horizon, parent):
    """The paper's ``_emit_eval``: the --mode eval output object."""
    return {"ok": True, "mode": "eval",
            "trajectory": {d: _ser(parent[d]) for d in
                           ["revenue", "gross_profit", "cash", "gross_margin_pct",
                            "headcount", "net_burn"] if d in parent},
            "_note": EVAL_NOTE}


def emit_test(parent, details, violations):
    """The --mode test output object (model_tool.py:275-286)."""
    goal = next(d for d in details if d["id"] == "goal")
    return {"ok": True, "mode": "test",
            "goal": {"target_multiple": goal["target"], "actual_multiple": goal["actual"],
                     "met": goal["ok"]},
            "constraints": [{"id": d["id"], "ok": d["ok"],
                             "worst_quarter": d.get("worst_quarter")} for d in details
                            if d["id"] != "goal"],
            "violations": violations,
            "trajectory": {d: _ser(parent[d]) for d in
                           ["revenue", "cash", "gross_margin_pct", "headcount"] if d in parent},
            "_note": TEST_NOTE}


class Evaluated:
    """A successfully projected model: what test mode and the scorer need."""

    __slots__ = ("horizon", "units", "unit_periods", "parent")

    def __init__(self, horizon, units, unit_periods, parent):
        self.horizon = horizon
        self.units = units
        self.unit_periods = unit_periods
        self.parent = parent


@prec50
def evaluate_model(spec: Any) -> "Evaluated | dict":
    """Project a parsed model spec. Returns :class:`Evaluated`, or the paper's error object.

    The paper's two error envelopes (model_tool.py:254-261): a ``ModelError``
    becomes ``{"ok": False, "error": "model error: ..."}``, any other exception
    ``{"ok": False, "error": "evaluation failed: ..."}`` (e.g. a missing key, a
    zero divisor, a cycle).
    """
    try:
        horizon, units, unit_periods = load_model(spec)
        parent = rollup(horizon, unit_periods)
    except ModelError as e:
        return {"ok": False, "error": f"model error: {e}"}
    except Exception as e:  # noqa: BLE001 - the paper's catch-all, message kept
        return {"ok": False, "error": f"evaluation failed: {e}"}
    return Evaluated(horizon, units, unit_periods, parent)


@prec50
def eval_output(spec: Any) -> dict:
    """``model_tool.py --mode eval`` on a parsed spec, as the paper printed it."""
    ev = evaluate_model(spec)
    if isinstance(ev, dict):
        return ev
    return emit_eval(ev.horizon, ev.parent)


@prec50
def test_output(spec: Any, task: Mapping[str, Any]) -> dict:
    """``model_tool.py --mode test`` on a parsed spec and task, as the paper printed it.

    In the paper ``run_checks`` ran outside the try block, so a model whose
    plan cannot be checked (a unit without ``plan``, a horizon longer than the
    task's caps) crashed the CLI with a traceback and no JSON. Raised here too;
    the oracle handler turns it into an error object (documented v1 change).
    """
    ev = evaluate_model(spec)
    if isinstance(ev, dict):
        return ev
    details, violations = run_checks(ev.horizon, ev.units, ev.unit_periods, ev.parent, task)
    return emit_test(ev.parent, details, violations)


__all__ = [
    "ModelError", "ROLLUP_DRIVERS", "EVAL_NOTE", "TEST_NOTE", "compile_formula", "build_unit_model",
    "load_model", "rollup", "run_checks", "emit_eval", "emit_test", "Evaluated", "evaluate_model",
    "eval_output", "test_output",
]
