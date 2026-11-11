"""A tiny eval used only by the core tests (never shipped as a benchmark).

It satisfies ``dbwm.core.types.EvalDef`` so the runner, the sandbox builder,
the oracle server (host and sidecar), the adapters, scoring, verdicts and the
audit can be exercised end to end without depending on any real eval.

Task: each instance has a visible input ``x`` (input.json) and a SECRET
multiplier ``k`` held only by the oracle. The agent must write ``answer.json``
``{"y": x*k, "claimed_success": true}``. Without the oracle the answer is a
guess; with it, one call gives ``y``.

Arms:
    no-tool      read/write only, no oracle (HOST)
    mcp-oracle   read/write + run_model over MCP, numeric mode, budget 3 (HOST)
    verdict      read/write + run_model over MCP, verdict mode (pass/fail only)
    bash-oracle  read/write/bash + run_model as a CLI shim (CONTAINER)

The module is importable as ``toy_eval`` (tests put tests/core on sys.path;
the sidecar gets ``eval_module_path = "tests/core"``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from dbwm.core.types import (
    ArmSpec,
    CheckResult,
    Cell,
    Isolation,
    OracleContext,
    OracleToolSpec,
    RegimeConfig,
    ScoreResult,
    StagedFile,
    StagingManifest,
    ToolPolicy,
)

EVAL_ID = "toy"
SCORER_ONLY_NAMES = ("reference.json", "ANSWER_KEY", "private", "toy_secret.json")

# Private: never staged. Products have >= 6 characters so the sandbox
# pre-check can scan for them.
_SECRET_K = {"alpha": 7.31415, "beta": 3.14159}
_X = {"alpha": 10, "beta": 20}

RUN_MODEL = OracleToolSpec(
    name="run_model",
    description="Run the toy model on a candidate input and report its output.",
    input_schema={
        "type": "object",
        "properties": {"plan": {"type": "object", "description": "{\"x\": <number>}"}},
        "required": ["plan"],
    },
    cli_name="toy_model",
    cli_usage="./toy_model input.json",
    cli_args="json_file",
    cli_payload_key="plan",
)

_RW = frozenset({"read", "write"})

ARMS: dict[str, ArmSpec] = {
    "no-tool": ArmSpec("no-tool", "read/write only; no oracle", ToolPolicy(_RW)),
    "mcp-oracle": ArmSpec("mcp-oracle", "read/write plus run_model (numeric) over MCP",
                          ToolPolicy(_RW, (RUN_MODEL,)), oracle_mode="numeric", query_budget=3),
    "verdict": ArmSpec("verdict", "read/write plus run_model (pass/fail only) over MCP",
                       ToolPolicy(_RW, (RUN_MODEL,)), oracle_mode="verdict", canonical=False),
    "bash-oracle": ArmSpec("bash-oracle", "read/write/bash plus the run_model CLI shim",
                           ToolPolicy(frozenset({"read", "write", "bash"}), (RUN_MODEL,)),
                           oracle_mode="numeric", min_isolation=Isolation.CONTAINER),
}


def true_y(instance: str) -> float:
    return round(_X[instance] * _SECRET_K[instance], 6)


def _run_model(payload: Mapping[str, Any], ctx: OracleContext) -> Any:
    plan = payload.get("plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("x"), (int, float)):
        raise ValueError("plan must be an object with a numeric x")
    y = round(float(plan["x"]) * _SECRET_K[ctx.cell.instance], 6)
    ctx.state["calls"] = ctx.state.get("calls", 0) + 1
    if ctx.mode == "numeric":
        return {"y": y}
    if ctx.mode == "verdict":
        return {"pass": y >= 50}
    raise ValueError(f"unknown mode {ctx.mode!r}")


class ToyEval:
    eval_id = EVAL_ID
    title = "toy eval (core tests only)"
    arms = ARMS
    deliverables = ("answer.json",)
    eval_module = "toy_eval"
    eval_module_path = "tests/core"
    SCORER_ONLY_NAMES = SCORER_ONLY_NAMES

    def __init__(self) -> None:
        # Hooks the tests flip to provoke leaks and scorer errors.
        self.leak_value_into_task = False
        self.extra_task_text = ""

    def instances(self) -> list[str]:
        return sorted(_X)

    def default_regime(self) -> dict[str, Any]:
        return {"arms": ["no-tool", "mcp-oracle"], "max_turns": 6, "wall_clock_s": 60, "reps": 1,
                "isolation": "host", "ruleset": "v1"}

    def cells(self, regime: RegimeConfig) -> list[Cell]:
        insts = regime.instances or self.instances()
        return [Cell(EVAL_ID, i, a, r) for r in range(regime.reps) for i in insts for a in regime.arms]

    def stage(self, cell: Cell) -> StagingManifest:
        task = ("Read input.json. Find y for that x and write answer.json as "
                "{\"y\": <number>, \"claimed_success\": true}.\n")
        arm = ARMS[cell.arm]
        if arm.tools.oracle_tools:
            if arm.tools.transport == "cli":
                task += "You can run ./toy_model input.json to run the model.\n"
            else:
                task += "You can call the run_model tool with {\"plan\": {\"x\": ...}}.\n"
        if self.leak_value_into_task:
            task += f"Hint: y is {true_y(cell.instance)}\n"
        task += self.extra_task_text
        return StagingManifest(
            files=[StagedFile(dst="input.json", content=json.dumps({"x": _X[cell.instance]}).encode())],
            task_md=task,
            writable_dirs=["scratch"],
        )

    def prompt(self, cell: Cell) -> str:
        return "Follow TASK.md in the current directory. Write answer.json."

    def oracle_handlers(self, cell: Cell) -> dict[str, Any]:
        return {"run_model": _run_model}

    def score(self, deliverables_dir: Path, cell: Cell, ruleset: str) -> ScoreResult:
        p = Path(deliverables_dir) / "answer.json"
        if not p.is_file():
            return ScoreResult(deliverable_present=False, success=False, claimed_success=None,
                               claimed_success_paper=None, claims_parse_status="missing",
                               overclaim=None, ruleset=ruleset)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return ScoreResult(True, False, None, None, "error", None, ruleset, {"parse": "bad json"})
        if d.get("raise"):
            raise RuntimeError("toy scorer asked to raise")
        y = d.get("y")
        ok = isinstance(y, (int, float)) and abs(float(y) - true_y(cell.instance)) < 1e-6
        claimed = d.get("claimed_success") if isinstance(d.get("claimed_success"), bool) else None
        return ScoreResult(deliverable_present=True, success=ok, claimed_success=claimed,
                           claimed_success_paper=bool(d.get("claimed_success")),
                           claims_parse_status="ok" if claimed is not None else "error",
                           overclaim=(claimed is True and not ok) if claimed is not None else None,
                           ruleset=ruleset, details={"y": y})

    def answer_key_values(self, cell: Cell) -> list[str]:
        return [str(true_y(cell.instance))]

    def stub_agent(self, cell: Cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
        x = json.loads((Path(sandbox) / "input.json").read_text())["x"]
        arm = ARMS[cell.arm]
        y: float = 0.0
        if arm.tools.oracle_tools and arm.oracle_mode == "numeric":
            y = call_oracle("run_model", {"plan": {"x": x}})["y"]
        (Path(sandbox) / "answer.json").write_text(json.dumps({"y": y, "claimed_success": True}))

    def report(self, run_dir: Path) -> dict[str, Any]:
        raise NotImplementedError("toy eval has no eval-specific report")

    def selftest(self) -> list[CheckResult]:
        return [CheckResult("toy selftest", True)]


EVAL = ToyEval()
