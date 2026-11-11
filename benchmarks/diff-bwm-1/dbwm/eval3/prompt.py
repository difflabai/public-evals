"""The eval-3 task prompt: the paper's ``build_prompt`` for gen conditions, plus the release rendering.

:func:`paper_prompt` is ``coupled_planner.build_prompt`` for a gen condition:
the condition-independent body (``dbwm.eval2.common_prompt``, verbatim) plus
``_gen_extra`` (coupled_planner.py:94-147, verbatim). The paper printed the
absolute path of the sandbox's model_tool.py into the gen-figures/gen-both
tails; it is rendered here as ``<SANDBOX>/model_tool.py``, a fixed placeholder
for the sandbox path.

:func:`build_prompt` is what runnable arms send. It is the paper text with
exactly these changes (rows in docs/instrument-changes/eval3.md):

G1  gen-figures: the run line ``python3 <SANDBOX>/model_tool.py --model
    model.json --mode eval`` becomes ``the model_eval tool with {"file":
    "model.json"}`` (MCP; no path, no mode flag: the server is sealed).
G2  gen-both: ``python3 <SANDBOX>/model_tool.py --model model.json --task
    task.json --mode test`` becomes ``the model_test tool with {"file":
    "model.json"}`` (the test uses the shipped public task, not the sandbox
    copy). The paper prompt named only test mode although the tool also had
    eval mode; likewise here (model_eval is in the agent's tool list).
G3  gen-code: "time-limited per run." becomes "each command is stopped after
    {COMMAND_TIMEOUT_S} seconds." The paper never enforced a limit (E23-D17);
    the release states the one the harness applies.
P3  eval 2's action-space sentence (every unit in both pools, growth capital
    >= 0, net hires whole and >= 0, no layoffs) in the deliverables section:
    the v1 scorer rejects layoffs and omitted units, so the agent is told.
    Taken from ``dbwm.eval2.build_prompt`` (never retyped), so the two evals
    state the rule identically.
P4  eval 2's do-nothing-lever disclosure (``dbwm.eval2.prompt`` P4): each
    unit's do-nothing levers (marketing_spend, hires_per_quarter) after its
    do-nothing cash line, and the sentence that the allocation replaces them.
    Without it an agent could add its plan on top of these undisclosed
    defaults (v7 Appendix A entry 18). Inherited with the release
    body, like P3.

The two re-run baselines (``ungrounded``, ``grounded-iterate``) use eval 2's
prompts unchanged (``dbwm.eval2.paper_prompt`` / ``build_prompt``).
"""

from __future__ import annotations

from typing import Any, Mapping

from dbwm.eval2 import build_prompt as eval2_build_prompt
from dbwm.eval2 import common_prompt
from dbwm.eval2 import paper_prompt as eval2_paper_prompt

from .arms import ARMS, BASELINE_ARMS, GEN_ARMS

SANDBOX_TOOL_PLACEHOLDER = "<SANDBOX>/model_tool.py"

# The per-command limit the gen-code prompt states (G3). The harness must
# enforce the same value: Claude Code reads BASH_DEFAULT_TIMEOUT_MS and
# BASH_MAX_TIMEOUT_MS (both in 2.1.287); the openai_compat adapter's
# bash_timeout_s defaults to 120 s. EVAL.command_timeout_s(cell) exposes it.
COMMAND_TIMEOUT_S = 120

PAPER_TIME_LIMIT = "time-limited per\nrun."
RELEASE_TIME_LIMIT = f"each command\nis stopped after {COMMAND_TIMEOUT_S} seconds."

MCP_EVAL_LINE = 'the model_eval tool with {"file": "model.json"}'
MCP_TEST_LINE = 'the model_test tool with {"file": "model.json"}'


def _paper_run_line(condition: str, tool_path: str = SANDBOX_TOOL_PLACEHOLDER) -> str:
    if condition == "gen-figures":
        return f"python3 {tool_path} --model model.json --mode eval"
    return f"python3 {tool_path} --model model.json --task task.json --mode test"


def _gen_code_tail(time_limit: str) -> str:
    """coupled_planner._gen_extra for gen-code; ``time_limit`` is the text between "...model.py`), " and
    " You have NO external model service" (it spans the paper's line break)."""
    return ("\nYOU HAVE A PYTHON INTERPRETER (run code via Bash, e.g. `python3 model.py`), " + time_limit
            + """ You have NO external model service and NO pre-built model — WRITE YOUR OWN CODE to model
this coupled portfolio and evaluate candidate plans. Your code is your only calculator; there is
no ground-truth oracle to check against, so your numbers are only as correct as your code. Base
claims.json on what your code computes; set model_runs to how many times you ran it.
""")


def _dsl_tail(condition: str, H: int, unit_ids: list[str], run: str) -> str:
    """coupled_planner._gen_extra for gen-figures / gen-both with the given run line."""
    units_example = ",\n     ".join(
        f'"{u}": {{ "params": {{ ... }}, "plan": {{ "marketing": [{H} values], "hires": [{H} values] }} }}'
        for u in unit_ids)
    schema = f"""Write your model to model.json:
{{
  "horizon": {H},
  "formulas": {{ "<node>": "<expression>", ... }},   // ONE funnel, applied to EVERY unit
  "units": {{
     {units_example}
  }}
}}
Expression syntax: arithmetic (+ - * /) over parameter names, the plan levers `marketing` and
`hires`, and other formula nodes you define. For a quantity that rolls forward quarter to
quarter, write prior(NODE, INIT) — its previous-quarter value, starting at INIT in quarter 0
(INIT is a number or a parameter name). Syntax example (NOT your model, just to show prior()):
   "balance": "prior(balance, starting_balance) + deposits"
Deciding which parameters and formulas capture each unit's economics from the figures above IS
your task. Name your two plan levers exactly `marketing` and `hires`."""
    if condition == "gen-figures":
        what = ("It returns the projected COMBINED trajectory of the model YOU declared "
                "(internally consistent with your formulas) — but NO goal/constraint verdict, "
                "and it is NOT validated against the true world; it is only as correct as your "
                "model. Read the figures and judge feasibility yourself.")
    else:  # gen-both
        what = ("It returns your model's trajectory AND a PASS/FAIL of the goal and every "
                "constraint computed on YOUR model. A PASS means your MODEL satisfies the "
                "targets — it does NOT guarantee the real portfolio does if your model is "
                "inaccurate.")
    return f"""
YOU HAVE A NUMERICAL-MODELING TOOL, but NO pre-built model and NO ground-truth oracle: you must
BUILD your own model of this portfolio, then run it:

    {run}

{what}

{schema}

Iterate: refine your model and your plan, re-run, then commit when confident. Base claims.json on
what your model reported; set model_runs to how many times you ran it.
"""


def _check_arm(condition: str) -> None:
    if condition not in ARMS:
        raise ValueError(f"unknown eval-3 arm {condition!r}; known: {list(ARMS)}")


def paper_prompt(brief: Mapping[str, Any], condition: str) -> str:
    """The paper's prompt for an eval-3 condition (the model_tool path as ``<SANDBOX>/model_tool.py``)."""
    _check_arm(condition)
    if condition in BASELINE_ARMS:
        return eval2_paper_prompt(brief, condition)
    common = common_prompt(brief)
    if condition == "gen-code":
        return common + _gen_code_tail(PAPER_TIME_LIMIT)
    unit_ids = [u["id"] for u in brief["units"]]
    return common + _dsl_tail(condition, int(brief["horizon"]), unit_ids, _paper_run_line(condition))


def release_common(brief: Mapping[str, Any]) -> str:
    """eval 2's release body (common prompt + P3 + P4), obtained from eval 2's public API only."""
    paper_ung = eval2_paper_prompt(brief, "ungrounded")
    common = common_prompt(brief)
    tail = paper_ung[len(common):]
    rel = eval2_build_prompt(brief, "ungrounded")
    if not paper_ung.startswith(common) or not rel.endswith(tail):  # pragma: no cover - guards eval 2 drift
        raise AssertionError("eval 2 prompt layout changed: cannot derive the release body")
    return rel[: len(rel) - len(tail)]


def build_prompt(brief: Mapping[str, Any], condition: str) -> str:
    """The release prompt for arm ``condition`` (changes G1-G3, P3 and P4 in the module docstring)."""
    _check_arm(condition)
    if condition in BASELINE_ARMS:
        return eval2_build_prompt(brief, condition)
    common = release_common(brief)
    if condition == "gen-code":
        return common + _gen_code_tail(RELEASE_TIME_LIMIT)
    unit_ids = [u["id"] for u in brief["units"]]
    run = MCP_EVAL_LINE if condition == "gen-figures" else MCP_TEST_LINE
    return common + _dsl_tail(condition, int(brief["horizon"]), unit_ids, run)


def strip_release_changes(text: str, condition: str, brief: Mapping[str, Any]) -> str:
    """Undo G1-G3, P3 and P4 on a gen-arm release prompt (the golden test against the paper text)."""
    if condition not in GEN_ARMS:
        raise ValueError("strip_release_changes is for the gen arms (baselines: see dbwm.eval2)")
    out = text.replace(release_common(brief), common_prompt(brief), 1)
    if condition == "gen-code":
        return out.replace(RELEASE_TIME_LIMIT, PAPER_TIME_LIMIT, 1)
    line = MCP_EVAL_LINE if condition == "gen-figures" else MCP_TEST_LINE
    return out.replace(line, _paper_run_line(condition), 1)


__all__ = ["paper_prompt", "build_prompt", "release_common", "strip_release_changes",
           "COMMAND_TIMEOUT_S", "SANDBOX_TOOL_PLACEHOLDER", "MCP_EVAL_LINE", "MCP_TEST_LINE"]
