"""Harness-side ``qa_check`` oracle for the two runnable arms.

The handler runs inside the oracle server (host process at HOST level, the
sidecar container at CONTAINER level), never in the sandbox. It reads the
agent's deliverables from the sandbox root, runs registry v1.0 with the v1
engine, and returns the text the paper's ``qa_check.py`` printed:

- ``verdict``: ``CHECKLIST RESULT: n/m PASS (k manual items not machine-checked)``
  and one PASS/FAIL line per item -- identical in layout to the paper tool;
- ``figures``: the same plus each item's criteria line and what the checker
  measured in the submission, with the grader's own bands, regexes and
  required-value lists redacted (engine.figures_render_v1). The paper tool
  printed those spec literals; under v1 no arm receives the registry.

The mode is read from ``ctx.mode`` ONLY (sealed per arm by the server); the
payload is ignored entirely, so ``{"mode": "figures"}`` from a verdict-arm
agent changes nothing (the server also logs such keys as override attempts).
The handler takes no path from the agent: it reads exactly the three
deliverable names of the cell's scenario from the sandbox root, and a
deliverable that is a symlink, resolves outside the sandbox, is not a regular
file or exceeds the size cap is treated as absent (the CONTAINER sidecar
mounts the package read-only next to the sandbox; a symlink to a reference
submission must not be graded as the agent's own work).
"""

from __future__ import annotations

from typing import Any, Mapping

from dbwm.core.types import OracleContext

from . import dataset as D
from . import engine as E

MODES = ("verdict", "figures")
ORACLE_RULESET = E.RULESET_V1  # new runs are always served by the release registry/engine


def check_submission(instance: str, sandbox, ruleset: str = ORACLE_RULESET) -> tuple[dict[str, Any], dict[str, str]]:
    """Run the checklist on the deliverables at the sandbox root. Returns (result, rejected)."""
    meta = D.scenario_meta(instance)
    sub = E.SafeSubmission(sandbox, allowed=tuple(meta["deliverables"]))
    res = E.Engine(ruleset).run_checks(D.registry(instance, ruleset), sub, submission_label=".")
    return res, dict(sub.rejected)


def qa_check(payload: Mapping[str, Any], ctx: OracleContext) -> str:
    """OracleHandler for op ``qa_check``. Returns the paper tool's printed text (v1 rendering)."""
    mode = ctx.mode
    if mode not in MODES:
        # A misconfigured server, never agent input: refuse rather than guess.
        raise RuntimeError(f"qa_check: sealed mode must be one of {MODES}, got {mode!r}")
    res, rejected = check_submission(ctx.cell.instance, ctx.sandbox)
    text = E.paper_cli_render(res, "verdict") if mode == "verdict" else E.figures_render_v1(res)
    for name, why in sorted(rejected.items()):
        text += f"  note: {name} was not read ({why}); write it as a regular file in the working directory.\n"
    st = ctx.state.setdefault("qa_check", {"calls": 0, "history": []})
    st["calls"] += 1
    st["history"].append({"passed": res["summary"]["passed"], "total": res["summary"]["total"]})
    return text


def handlers_for(oracle_mode: str | None) -> dict:
    """Ops served for an arm: qa_check for the two runnable arms, nothing else."""
    if oracle_mode in MODES:
        return {"qa_check": qa_check}
    return {}
