"""Eval-1 arms: exact tool policies, sealed oracle modes, budgets.

Scope decisions 2 and 4 (BUILD-SPEC): only fixed arms are runnable, and the
canonical arms are no-bash so eval 1 runs at Isolation.HOST under Claude Code's
--restricted file confinement. Every arm gets the built-in ``read`` and
``write`` file tools (to read TASK.md and deliver plan.json/claims.json); the
five tool arms additionally get exactly one oracle tool, ``run_model``, served
over MCP, whose report channel is sealed server-side per arm. The contrast
between arms is therefore exactly the oracle tool.

Paper comparison (docs/instrument-changes/eval1.md E1-A01, E1-A02, E1-T00): every paper
arm, including ``ungrounded``, ran with Write,Edit,Read,Bash,Glob,Grep on the
host inside the harness checkout, and the tool's mode could be overridden from
the command line. Those regimes are not recreated; new runs are a new series.
"""

from __future__ import annotations

import json
from pathlib import Path

from dbwm.core.types import ArmSpec, Isolation, OracleToolSpec, ToolPolicy

REGIME_PATH = Path(__file__).with_name("regime.json")

ARM_ORDER = (
    "ungrounded",
    "grounded-readonly",
    "grounded-iterate",
    "prose-feedback",
    "raw-figures",
    "broken-model",
)

ORACLE_MODE = {
    "ungrounded": None,
    "grounded-readonly": "numeric",
    "grounded-iterate": "numeric",
    "prose-feedback": "prose",
    "raw-figures": "raw",
    "broken-model": "broken",
}

RUN_MODEL = OracleToolSpec(
    name="run_model",
    description=(
        "Run this company's executable model on a candidate plan (lever bindings) "
        "and return the model's report for that plan."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "plan": {
                "type": "object",
                "description": (
                    "Candidate plan, same shape as plan.json: "
                    "{\"bindings\": {\"<lever id>\": [\"<value>\", ... one string per quarter]}}. "
                    "Levers you leave out keep their default."
                ),
            }
        },
        "required": ["plan"],
    },
    cli_name="run_model",
    cli_usage="./run_model candidate.json",
    cli_args="json_file",
    cli_payload_key="plan",
)

_FILES = frozenset({"read", "write"})

DESCRIPTIONS = {
    "ungrounded": (
        "Task prompt only (goal, constraints, levers with defaults, base trajectory); "
        "built-in read/write file tools; no model tool."
    ),
    "grounded-readonly": (
        "Task prompt plus the run_model tool reporting full projected figures and a "
        "pass/fail check of the goal and every constraint, {budget_text}; "
        "read/write file tools."
    ),
    "grounded-iterate": (
        "Task prompt plus the run_model tool reporting full projected figures and a "
        "pass/fail check of the goal and every constraint, unlimited calls, prompt asks "
        "to iterate until confident; read/write file tools."
    ),
    "prose-feedback": (
        "Task prompt plus the run_model tool reporting pass/fail of the goal and each "
        "constraint in prose, with no figures; read/write file tools."
    ),
    "raw-figures": (
        "Task prompt plus the run_model tool reporting the projected trajectory figures "
        "only, with no pass/fail check; read/write file tools."
    ),
    "broken-model": (
        "Task prompt plus the run_model tool reporting figures and a pass/fail check "
        "computed on deterministically corrupted figures that violate the model's "
        "identities (the prompt warns the service may be unreliable); read/write file tools."
    ),
}


def load_regime_defaults() -> dict:
    return json.loads(REGIME_PATH.read_text(encoding="utf-8"))


def _query_budgets() -> dict[str, int | None]:
    extra = load_regime_defaults().get("extra", {})
    budgets = extra.get("query_budget", {}) or {}
    out: dict[str, int | None] = {}
    for arm, b in budgets.items():
        if arm not in ARM_ORDER or ORACLE_MODE[arm] is None:
            raise ValueError(f"regime.json query_budget names a non-tool arm {arm!r}")
        if b is not None and (not isinstance(b, int) or isinstance(b, bool) or b < 1):
            raise ValueError(f"regime.json query_budget[{arm!r}] must be a positive int or null")
        out[arm] = b
    return out


def build_arms() -> dict[str, ArmSpec]:
    budgets = _query_budgets()
    arms: dict[str, ArmSpec] = {}
    for name in ARM_ORDER:
        mode = ORACLE_MODE[name]
        tools = ToolPolicy(builtin=_FILES, oracle_tools=(RUN_MODEL,) if mode else ())
        budget = budgets.get(name)
        arms[name] = ArmSpec(
            name=name,
            description=DESCRIPTIONS[name].format(
                budget_text=f"limited to {budget} calls" if budget is not None else "unlimited calls"),
            tools=tools,
            oracle_mode=mode,
            query_budget=budget,
            min_isolation=Isolation.HOST,
            canonical=True,
        )
    return arms
