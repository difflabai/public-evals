"""Eval-2 arms: exactly what each condition gets.

The paper's arms differed in more than the oracle: ungrounded had Read/Write,
every grounded arm had Bash/Edit/Glob/Grep as well (coupled_planner.py:337),
so grounded agents could also compute figures themselves, import the oracle
source, or read the answer key next to world.json (the
self-compute confound). Scope decision 4 of the release: every canonical
eval-2 arm gets the same builtin tools, ``read`` and ``write``, and the
contrast between arms is exactly the ``world_query`` oracle tool and the
channel it is sealed to. No arm has bash, so all of them run at
``Isolation.HOST`` (Claude Code ``--restricted --tools Read,Write`` plus the
MCP tool); this is the "no-Bash grounded arm" the paper itself proposed.

| arm               | builtin     | oracle tool | sealed mode | query budget |
|-------------------|-------------|-------------|-------------|--------------|
| ungrounded        | read, write | --          | --          | --           |
| grounded-readonly | read, write | world_query | localizing  | 3 (enforced) |
| grounded-iterate  | read, write | world_query | localizing  | unlimited    |
| grounded-boolean  | read, write | world_query | boolean     | unlimited    |
| raw-figures       | read, write | world_query | raw         | unlimited    |
| prose-feedback    | read, write | world_query | prose       | unlimited    |
| broken-interface* | read, write | world_query | broken      | unlimited    |

(*) defined by the pre-registration (H4); ``canonical=False``, not in the
default regime, runs only on request.

The paper's readonly arm differed from iterate by one prompt sentence and
nothing else, so its call budget was not enforced (E23-D6). Here the oracle
server refuses calls beyond the budget.
"""

from __future__ import annotations

from dbwm.core.types import ArmSpec, Isolation, OracleToolSpec, ToolPolicy

READONLY_QUERY_BUDGET = 3

# The candidate is the same JSON shape as allocation.json. The agent either
# writes it to a file in its working directory and passes {"file": ...} (the
# paper workflow: "write it to candidate.json ... and run"), or passes it
# inline. The description is neutral: no mode, no paths, no breadcrumbs.
WORLD_QUERY_TOOL = OracleToolSpec(
    name="world_query",
    description=(
        "Run a candidate allocation through the executable model of the coupled "
        "portfolio and return the model's output for it. Pass the candidate as "
        "{\"file\": \"candidate.json\"} (a JSON file you wrote in the working directory, "
        "same shape as allocation.json) or inline as {\"candidate\": {...}}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "Relative path of a JSON file in the working directory holding "
                               "the candidate allocation (same shape as allocation.json).",
            },
            "candidate": {
                "type": "object",
                "description": "The candidate allocation itself (same shape as allocation.json).",
            },
        },
        "additionalProperties": False,
    },
    cli_name="world_query",
    cli_usage="./world_query candidate.json",
    cli_args="json_file",
    cli_payload_key="candidate",
)

RW = frozenset({"read", "write"})
GROUNDED_TOOLS = ToolPolicy(builtin=RW, oracle_tools=(WORLD_QUERY_TOOL,))

ARM_LIST = (
    ArmSpec(
        name="ungrounded",
        description="Read/write file tools only; no model to run. Must reason the coupled "
                    "consequences out from the brief.",
        tools=ToolPolicy(builtin=RW),
        oracle_mode=None,
        min_isolation=Isolation.HOST,
    ),
    ArmSpec(
        name="grounded-readonly",
        description="Read/write plus world_query sealed to the localizing channel (figures, "
                    "pass/fail, localization and hint), at most 3 calls.",
        tools=GROUNDED_TOOLS,
        oracle_mode="localizing",
        query_budget=READONLY_QUERY_BUDGET,
        min_isolation=Isolation.HOST,
    ),
    ArmSpec(
        name="grounded-iterate",
        description="Read/write plus world_query sealed to the localizing channel (figures, "
                    "pass/fail, localization and hint), unlimited calls.",
        tools=GROUNDED_TOOLS,
        oracle_mode="localizing",
        min_isolation=Isolation.HOST,
    ),
    ArmSpec(
        name="grounded-boolean",
        description="Read/write plus world_query sealed to the boolean channel: goal met and "
                    "per-constraint ok flags only, no figures.",
        tools=GROUNDED_TOOLS,
        oracle_mode="boolean",
        min_isolation=Isolation.HOST,
    ),
    ArmSpec(
        name="raw-figures",
        description="Read/write plus world_query sealed to the raw channel: combined and "
                    "per-unit trajectories, no pass/fail.",
        tools=GROUNDED_TOOLS,
        oracle_mode="raw",
        min_isolation=Isolation.HOST,
    ),
    ArmSpec(
        name="prose-feedback",
        description="Read/write plus world_query sealed to the prose channel: MET/NOT met and "
                    "OK/VIOLATED per constraint in words, figures withheld.",
        tools=GROUNDED_TOOLS,
        oracle_mode="prose",
        min_isolation=Isolation.HOST,
    ),
    ArmSpec(
        name="broken-interface",
        description="Read/write plus world_query sealed to a broken-interface world whose "
                    "rollup mis-scales one unit (lesion arm H4; optional, non-canonical, "
                    "not in the default regime).",
        tools=GROUNDED_TOOLS,
        oracle_mode="broken",
        min_isolation=Isolation.HOST,
        canonical=False,
    ),
)

ARMS = {a.name: a for a in ARM_LIST}
CANONICAL_ARMS = tuple(a.name for a in ARM_LIST if a.canonical)
GROUNDED_ARMS = tuple(a.name for a in ARM_LIST if a.oracle_mode is not None)

# Contrast definitions (paper confirm_report.py:32-36).
GROUNDED_CANONICAL = ("grounded-readonly", "grounded-iterate", "grounded-boolean",
                      "raw-figures", "prose-feedback")
FIGURES_NO_VERDICT = ("raw-figures",)
VERDICT_NO_FIGURES = ("grounded-boolean", "prose-feedback")
