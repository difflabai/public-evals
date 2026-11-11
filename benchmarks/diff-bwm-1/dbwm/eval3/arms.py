"""Eval-3 arms: exactly what each condition gets.

Eval 3 makes *model source* a factor: none (prose only), self-built (the agent
builds and runs its own model; never the true world) or oracle (the true
world through eval 2's ``world_query``). The pre-registered generative arms
(numerical-models-llm-planning/eval3-generative-vs-grounded.md, sections 3-5):

| arm          | builtin            | oracle ops               | sealed mode | staged (besides TASK.md)  | isolation |
|--------------|--------------------|--------------------------|-------------|---------------------------|-----------|
| gen-figures  | read, write        | model_eval               | eval        | brief.json                | HOST      |
| gen-both     | read, write        | model_eval, model_test   | test        | brief.json, task.json     | HOST      |
| gen-code     | read, write, bash  | --                       | --          | brief.json, task.json     | CONTAINER |
| ungrounded*  | read, write        | --                       | --          | brief.json                | HOST      |
| grounded-iterate* | read, write   | world_query (eval 2)     | localizing  | brief.json                | HOST      |

(*) eval 2's arms, run under the same regime and model as the generative arms
so all four contrasts come from one series (E23-D16). They are eval 2's
ArmSpecs with ``canonical=False`` (they belong to eval 2) and are in the
default regime nonetheless.

What changed from the paper's arms (change-ledger/eval3.md):

* Paper gen-figures and gen-both had ``Write,Edit,Read,Bash,Glob,Grep``
  (coupled_planner.py:337) and the full model_tool.py with both modes plus
  task.json in the sandbox, so an agent could run ``--mode test`` (the
  withheld verdict) and its own Python: "figures, no verdict" and "DSL only"
  were not enforced. Here the two DSL
  arms have no bash (BUILD-SPEC decision 4) and reach the tool only as MCP
  ops; gen-figures has no ``model_test`` op and no task.json.
* Paper gen-code ran on the host with unrestricted Bash and a "time-limited"
  promise nothing enforced (E23-D17). Here it needs ``Isolation.CONTAINER``
  and its prompt states the actual per-command limit (prompt.py,
  ``COMMAND_TIMEOUT_S``). It gets no model tool, as pre-registered ("No
  oracle, no world spec, no model_tool", section 5) and as the paper ran it
  (coupled_planner.py:86-89).
"""

from __future__ import annotations

import dataclasses

from dbwm.core.types import ArmSpec, Isolation, ToolPolicy
from dbwm.eval2 import ARMS as EVAL2_ARMS

from .oracle import MODEL_EVAL_TOOL, MODEL_TEST_TOOL

RW = frozenset({"read", "write"})

GEN_ARMS = ("gen-figures", "gen-both", "gen-code")
DSL_ARMS = ("gen-figures", "gen-both")
BASELINE_ARMS = ("ungrounded", "grounded-iterate")

ARM_LIST = (
    ArmSpec(
        name="gen-figures",
        description="Read/write plus model_eval: the figures of the model the agent declares, "
                    "no verdict; no task.json, no test op, no code execution.",
        tools=ToolPolicy(builtin=RW, oracle_tools=(MODEL_EVAL_TOOL,)),
        oracle_mode="eval",
        min_isolation=Isolation.HOST,
    ),
    ArmSpec(
        name="gen-both",
        description="Read/write plus model_eval and model_test: the agent's model's figures and "
                    "a PASS/FAIL of the fixed public goal and constraints on that model; "
                    "task.json staged; no code execution.",
        tools=ToolPolicy(builtin=RW, oracle_tools=(MODEL_EVAL_TOOL, MODEL_TEST_TOOL)),
        oracle_mode="test",
        min_isolation=Isolation.HOST,
    ),
    ArmSpec(
        name="gen-code",
        description="Read/write plus bash (a Python interpreter with a per-command time limit) "
                    "in a container; no model tool, no oracle; task.json staged.",
        tools=ToolPolicy(builtin=frozenset({"read", "write", "bash"})),
        oracle_mode=None,
        min_isolation=Isolation.CONTAINER,
    ),
    dataclasses.replace(EVAL2_ARMS["ungrounded"], canonical=False),
    dataclasses.replace(EVAL2_ARMS["grounded-iterate"], canonical=False),
)

ARMS = {a.name: a for a in ARM_LIST}
CANONICAL_ARMS = tuple(a.name for a in ARM_LIST if a.canonical)
DEFAULT_ARMS = GEN_ARMS + BASELINE_ARMS

# Contrasts (pre-registration sections 1, 3 and 7; advisor eval3 spec item 6). The registered hypotheses come
# first: H1 per arm (gen-figures and gen-both against unaided) and H2 = grounded-iterate - gen-both (v7 section
# 5). The pooled readings follow, labelled; none stands in for a registered hypothesis.
CONTRASTS = (
    ("gen-figures - ungrounded (H1, registered)", ("gen-figures",), ("ungrounded",)),
    ("gen-both - ungrounded (H1, registered)", ("gen-both",), ("ungrounded",)),
    ("grounded-iterate - gen-both (H2, registered)", ("grounded-iterate",), ("gen-both",)),
    ("generative - ungrounded (generative effect, pooled)", GEN_ARMS, ("ungrounded",)),
    ("grounded-iterate - generative (grounding premium, pooled; not the registered H2)", ("grounded-iterate",),
     GEN_ARMS),
    ("gen-both - gen-figures (testability effect)", ("gen-both",), ("gen-figures",)),
    ("gen-code - gen-both (tool-form effect)", ("gen-code",), ("gen-both",)),
)


def branch(arm: str) -> str:
    """The paper's verdict ``branch`` label (coupled_planner.py:575-576)."""
    if arm in GEN_ARMS:
        return "generative"
    if EVAL2_ARMS.get(arm) is not None and EVAL2_ARMS[arm].oracle_mode is not None:
        return "grounded"
    return "unaided"


__all__ = ["ARMS", "ARM_LIST", "CANONICAL_ARMS", "DEFAULT_ARMS", "GEN_ARMS", "DSL_ARMS",
           "BASELINE_ARMS", "CONTRASTS", "branch"]
