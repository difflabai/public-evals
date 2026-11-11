"""The eval-3 oracle ops: ``model_eval`` and ``model_test`` (the paper's model_tool, served).

In the paper the agent ran ``python3 <sandbox>/model_tool.py --model model.json
--mode eval|test [--task task.json]`` with Bash. Both gen arms got the same
file, so gen-figures could run test mode on the staged task.json and the "no
verdict" condition was not locked (E23-D1). Here the tool is two ops on the
per-trial oracle server, exposed to no-bash arms as MCP tools:

* ``model_eval`` -- gen-figures and gen-both. The figures of the model the
  agent declares; no verdict. Identical output to ``--mode eval``.
* ``model_test`` -- gen-both only (the server is sealed to mode ``test``).
  ``--mode test``'s output: the fixed public goal and constraints checked on
  the agent's model.

Handlers are pure functions of ``(payload, OracleContext)``; the mode comes
from ``ctx.mode`` only (the server strips and logs ``mode``-like payload
keys). The model is passed as ``{"file": "model.json"}`` (a JSON file in the
sandbox; resolved and confined to it) or inline as ``{"model": {...}}``.

Release (v1) differences from the paper tool, each a row in
change-ledger/eval3.md; :func:`render` with ``ruleset="paper"``
reproduces the paper output exactly (tests/eval3/test_eval3_model_tool.py), the
live handlers serve v1:

V1  model_test checks the shipped public task (data/eval3/worlds/<w>/task.json,
    read harness-side). The paper read ``--task`` from the agent's own
    writable directory, so the "fixed" test was fixed only by convention.
V2  model_test adds one constraint, ``action_space``: every world unit declared
    with both levers, ``marketing`` >= 0, ``hires`` whole and >= 0 (no
    layoffs). These are the v1 scorer's rules (dbwm.eval2.validate_v1) and the
    release prompt states them; without the check a gen-both agent could PASS
    its own test with a plan the scorer rejects.
V3  Size limits (horizon, units, formulas, params, formula length) so one call
    cannot occupy the oracle server for minutes; the paper had none. Models
    within the limits get the paper output unchanged.
V4  A model whose plan cannot be checked crashed the paper CLI in test mode
    (``run_checks`` ran outside its try block: traceback, no JSON); here it is
    ``{"ok": false, "error": "test failed: ..."}``.
V5  File arguments are confined to the sandbox; errors never echo a host path.
V6  A formula naming something that is neither a parameter, a plan lever nor
    a formula made the paper engine report "cycle among [every node]" (the
    unknown name never resolves); here it is "model error: formula 'x'
    references unknown name(s) ...". Same models fail, with the true reason.
"""

from __future__ import annotations

import ast
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from dbwm.core.safe_io import SafeReadError, read_nofollow
from dbwm.core.types import OracleToolSpec

from . import model_dsl
from .task import load_task

MODES = ("eval", "test")

MAX_MODEL_FILE_BYTES = 1_000_000
LIMITS = {
    "horizon": 24,           # the worlds have 6 quarters
    "units": 12,             # the worlds have 3 units
    "formulas": 64,          # the faithful funnel has 11
    "params_per_unit": 64,   # the faithful funnel has 10
    "plan_levers_per_unit": 16,
    "formula_chars": 500,    # the longest faithful formula has 62
}

_MODEL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "file": {
            "type": "string",
            "description": "Relative path of your model file (JSON) in the working directory, "
                           "e.g. \"model.json\".",
        },
        "model": {
            "type": "object",
            "description": "Your model itself: {\"horizon\": ..., \"formulas\": {...}, \"units\": {...}}.",
        },
    },
    "additionalProperties": False,
}

MODEL_EVAL_TOOL = OracleToolSpec(
    name="model_eval",
    description=(
        "Evaluate the model YOU declare (horizon, shared formulas, per-unit params and plan) "
        "with an exact projection engine and return its combined trajectory. It gives no "
        "goal/constraint verdict, and the figures are only as correct as your model: nothing "
        "is validated against the true world. Pass {\"file\": \"model.json\"} (a JSON file you "
        "wrote in the working directory) or inline {\"model\": {...}}."
    ),
    input_schema=_MODEL_INPUT_SCHEMA,
    cli_name="model_eval",
    cli_usage="./model_eval model.json",
    cli_args="json_file",
    cli_payload_key="model",
)

MODEL_TEST_TOOL = OracleToolSpec(
    name="model_test",
    description=(
        "Evaluate the model YOU declare and check the fixed public goal and constraints (the "
        "ones in task.json) on YOUR model's trajectory: returns the trajectory plus PASS/FAIL "
        "per check. A pass means your model meets the targets, not that the real portfolio "
        "does. Pass {\"file\": \"model.json\"} or inline {\"model\": {...}}."
    ),
    input_schema=_MODEL_INPUT_SCHEMA,
    cli_name="model_test",
    cli_usage="./model_test model.json",
    cli_args="json_file",
    cli_payload_key="model",
)

TOOLS = {MODEL_EVAL_TOOL.name: MODEL_EVAL_TOOL, MODEL_TEST_TOOL.name: MODEL_TEST_TOOL}


class PayloadError(ValueError):
    """The call did not name a readable model; the message is safe to show the agent."""


# ---------------------------------------------------------------------------
# payload -> model spec
# ---------------------------------------------------------------------------

def _read_sandbox_json(sandbox: Path | None, rel: Any) -> Any:
    """Parse a JSON file inside the sandbox; mirror the paper's messages for the agent's path."""
    if sandbox is None:
        raise PayloadError("file arguments are not available here; pass the model inline as {\"model\": {...}}")
    if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
        raise PayloadError("\"file\" must be a relative path such as \"model.json\"")
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise PayloadError("\"file\" must be a path inside the working directory, e.g. \"model.json\"")
    root = Path(sandbox).resolve()
    target = root / p
    try:
        real = target.resolve()
        real.relative_to(root)
    except (OSError, ValueError):
        raise PayloadError("\"file\" must be a path inside the working directory, e.g. \"model.json\"") from None
    if not real.exists():
        # the paper's message for a missing --model file (relative path as given)
        raise PayloadError(f"evaluation failed: [Errno 2] No such file or directory: {rel!r}")
    if not real.is_file():
        raise PayloadError(f"evaluation failed: {rel!r} is not a file")
    if real.stat().st_size > MAX_MODEL_FILE_BYTES:
        raise PayloadError(f"model error: {rel!r} is larger than {MAX_MODEL_FILE_BYTES} bytes")
    # Read through one O_NOFOLLOW descriptor, never by name again: a symlink
    # swapped in after the checks above is not followed (red-team RT-T2).
    try:
        raw = read_nofollow(real, MAX_MODEL_FILE_BYTES)
    except SafeReadError as e:
        if e.reason == "too_large":
            raise PayloadError(f"model error: {rel!r} is larger than {MAX_MODEL_FILE_BYTES} bytes") from None
        raise PayloadError(f"evaluation failed: [Errno 2] No such file or directory: {rel!r}") from None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - the paper's catch-all message
        raise PayloadError(f"evaluation failed: {e}") from e


def model_from_payload(payload: Mapping[str, Any], sandbox: Path | None) -> Any:
    """The model spec named by a call: ``{"file": ...}`` or ``{"model": ...}`` (exactly one)."""
    if not isinstance(payload, Mapping):
        raise PayloadError("arguments must be an object: {\"file\": \"model.json\"} or {\"model\": {...}}")
    has_file = payload.get("file") is not None
    has_model = payload.get("model") is not None
    if has_file and has_model:
        raise PayloadError("pass either \"file\" or \"model\", not both")
    if has_file:
        return _read_sandbox_json(sandbox, payload["file"])
    if has_model:
        model = payload["model"]
        if isinstance(model, str):
            try:
                model = json.loads(model)
            except Exception as e:  # noqa: BLE001
                raise PayloadError(f"evaluation failed: {e}") from e
        return model
    raise PayloadError("pass your model as {\"file\": \"model.json\"} or inline as {\"model\": {...}}")


# ---------------------------------------------------------------------------
# v1 checks
# ---------------------------------------------------------------------------

def limit_problem(spec: Any) -> str | None:
    """V3: a short reason if the model exceeds the size limits, else None.

    Only well-typed parts are measured; anything malformed is left to the
    paper's own error path so its messages stay the paper's.
    """
    if not isinstance(spec, Mapping):
        return None
    h = spec.get("horizon")
    try:
        if h is not None and not isinstance(h, bool) and int(h) > LIMITS["horizon"]:
            return f"horizon {int(h)} exceeds the limit of {LIMITS['horizon']}"
    except (TypeError, ValueError):
        pass
    units = spec.get("units")
    formulas = spec.get("formulas")
    if isinstance(units, Mapping) and len(units) > LIMITS["units"]:
        return f"{len(units)} units exceed the limit of {LIMITS['units']}"
    if isinstance(formulas, Mapping):
        if len(formulas) > LIMITS["formulas"]:
            return f"{len(formulas)} formulas exceed the limit of {LIMITS['formulas']}"
        for name, expr in formulas.items():
            if isinstance(expr, str) and len(expr) > LIMITS["formula_chars"]:
                return (f"formula {name!r} is {len(expr)} characters; the limit is "
                        f"{LIMITS['formula_chars']}")
    if isinstance(units, Mapping):
        for uid, u in units.items():
            if not isinstance(u, Mapping):
                continue
            params, plan = u.get("params"), u.get("plan")
            if isinstance(params, Mapping) and len(params) > LIMITS["params_per_unit"]:
                return f"unit {uid}: {len(params)} params exceed the limit of {LIMITS['params_per_unit']}"
            if isinstance(plan, Mapping) and len(plan) > LIMITS["plan_levers_per_unit"]:
                return (f"unit {uid}: {len(plan)} plan levers exceed the limit of "
                        f"{LIMITS['plan_levers_per_unit']}")
    return None


def unknown_reference_problem(spec: Any) -> str | None:
    """V6: the first formula that names something no unit defines, else None.

    Checked per unit (its params + plan levers + the shared formulas). Only
    run when every formula parses and the spec is well formed; otherwise the
    paper's own error path speaks.
    """
    if not isinstance(spec, Mapping):
        return None
    formulas, units = spec.get("formulas"), spec.get("units")
    if not isinstance(formulas, Mapping) or not isinstance(units, Mapping):
        return None
    refs: dict[str, list[str]] = {}
    for fname, expr in formulas.items():
        if not isinstance(expr, str):
            return None
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            return None
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                continue
            if isinstance(node, ast.Name):
                names.append(node.id)
        # prior(NODE, INIT): the function name and its arguments are not references
        prior_args = {a.id for n in ast.walk(tree) if isinstance(n, ast.Call)
                      for a in ([n.func] + list(n.args)) if isinstance(a, ast.Name)}
        refs[fname] = [n for n in names if n not in prior_args]
    for uid, u in units.items():
        if not isinstance(u, Mapping):
            return None
        params = u.get("params", {})
        plan = u.get("plan", {})
        if not isinstance(params, Mapping) or not isinstance(plan, Mapping):
            return None
        known = set(params) | set(plan) | set(formulas)
        for fname, names in refs.items():
            missing = sorted({n for n in names if n not in known})
            if missing:
                return (f"formula {fname!r} references unknown name(s) "
                        f"{', '.join(repr(m) for m in missing)} in unit {uid} (not a parameter, "
                        f"a plan lever or a formula)")
    return None


def _dec(x: Any) -> Decimal | None:
    if isinstance(x, bool):
        return None
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def action_space_reasons(spec: Mapping[str, Any], task: Mapping[str, Any]) -> list[str]:
    """V2: the v1 scorer's action-space rules applied to the model's plan."""
    world_units = [c["unit"] for c in task.get("unit_capacity", [])]
    levers = [p["lever"] for p in task.get("pool_caps", [])]
    units = spec.get("units") if isinstance(spec, Mapping) else None
    reasons: list[str] = []
    if not isinstance(units, Mapping):
        return ["model_has_no_units"]
    for uid in sorted(u for u in units if u not in world_units):
        reasons.append(f"unknown_unit:{uid}")
    for uid in world_units:
        u = units.get(uid)
        if not isinstance(u, Mapping):
            reasons.append(f"missing_unit:{uid}")
            continue
        plan = u.get("plan") if isinstance(u.get("plan"), Mapping) else {}
        for lever in levers:
            series = plan.get(lever)
            if not isinstance(series, list):
                reasons.append(f"missing_lever:{uid}:{lever}")
                continue
            for t, x in enumerate(series):
                d = _dec(x)
                if d is None:
                    reasons.append(f"not_a_number:{lever}:{uid}:q{t}")
                    continue
                if d < 0:
                    reasons.append(("layoff" if lever == "hires" else "negative") + f":{lever}:{uid}:q{t}")
                if lever == "hires" and d != d.to_integral_value():
                    reasons.append(f"fractional_hires:{lever}:{uid}:q{t}")
    return reasons


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render(spec: Any, op: str, ruleset: str, task: Mapping[str, Any] | None = None) -> dict:
    """The tool's output for one parsed model.

    ``ruleset="paper"``: exactly what the paper's model_tool printed (as an
    object), including raising where the paper CLI crashed. ``ruleset="v1"``:
    the live behaviour (V2-V4).
    """
    if op not in TOOLS:
        raise ValueError(f"unknown op {op!r}")
    if ruleset not in ("paper", "v1"):
        raise ValueError(f"unknown ruleset {ruleset!r}")
    if op == "model_test" and task is None:
        raise ValueError("model_test needs the public task")
    if ruleset == "paper":
        return model_dsl.eval_output(spec) if op == "model_eval" else model_dsl.test_output(spec, task)
    problem = limit_problem(spec)
    if problem:
        return {"ok": False, "error": f"model error: model too large: {problem}"}
    unknown = unknown_reference_problem(spec)
    if unknown:
        return {"ok": False, "error": f"model error: {unknown}"}
    if op == "model_eval":
        return model_dsl.eval_output(spec)
    try:
        out = model_dsl.test_output(spec, task)
    except Exception as e:  # noqa: BLE001 - V4: the paper crashed here
        return {"ok": False, "error": f"test failed: {type(e).__name__}: {e}"}
    if out.get("ok") is not True:
        return out
    reasons = action_space_reasons(spec, task)
    entry = {"id": "action_space", "ok": not reasons, "worst_quarter": None}
    if reasons:
        entry["reasons"] = reasons[:12] + ([f"... {len(reasons) - 12} more"] if len(reasons) > 12 else [])
    out["constraints"].append(entry)
    if reasons:
        out["violations"].append("action_space")
    return out


def _count(ctx, key: str) -> None:
    state = getattr(ctx, "state", None)
    if isinstance(state, dict):
        state[key] = state.get(key, 0) + 1


def handle_model_eval(payload: Mapping[str, Any], ctx) -> Any:
    """OracleHandler for ``model_eval`` (gen-figures and gen-both)."""
    if ctx.mode not in MODES:
        raise ValueError(f"oracle misconfigured: sealed mode {ctx.mode!r} is not a model-tool mode")
    _count(ctx, "model_eval_calls")
    try:
        spec = model_from_payload(payload, getattr(ctx, "sandbox", None))
    except PayloadError as e:
        return {"ok": False, "error": str(e)}
    return render(spec, "model_eval", "v1")


def handle_model_test(payload: Mapping[str, Any], ctx) -> Any:
    """OracleHandler for ``model_test`` (gen-both only: sealed mode ``test``)."""
    if ctx.mode != "test":
        raise ValueError(f"oracle misconfigured: model_test is sealed off in mode {ctx.mode!r}")
    _count(ctx, "model_test_calls")
    try:
        spec = model_from_payload(payload, getattr(ctx, "sandbox", None))
    except PayloadError as e:
        return {"ok": False, "error": str(e)}
    return render(spec, "model_test", "v1", task=load_task(ctx.cell.instance))


HANDLERS = {"model_eval": handle_model_eval, "model_test": handle_model_test}

__all__ = [
    "MODES", "LIMITS", "MODEL_EVAL_TOOL", "MODEL_TEST_TOOL", "TOOLS", "HANDLERS", "PayloadError",
    "model_from_payload", "limit_problem", "unknown_reference_problem", "action_space_reasons", "render",
    "handle_model_eval", "handle_model_test",
]
