"""The eval-1 agent prompt (corrected-prompt run), ported from planner.py build_prompt (L62-149).

Verbatim except for the recorded deviations (docs/instrument-changes/eval1.md):

  E1-P01  the tool-invocation lines. The paper told agents to write
          candidate.json and run ``python3 <absolute harness path>/bwm_query.py
          --plan candidate.json --backend <b>`` (planner.py:122), which leaked the
          operator's repo path and the tool's source location. v1 names the
          run_model tool (MCP transport) or ``./run_model candidate.json`` (CLI
          transport for bash arms).
  E1-P02  grounded-readonly states its query budget when one is set ("at most N
          runs"); the paper's readonly and iterate arms differed only by one
          sentence and had no enforced budget.

  E1-P03  the ungrounded arm's tool sentence. The paper said "You have NO tools
          and NO calculator service" (planner.py:116), which is false for v1:
          every arm gets the read/write file tools it needs to read the brief
          and deliver plan.json and claims.json. v1 says what is actually
          withheld (any model or calculator service, any tool that runs code)
          and what is given (file tools only). ``build_prompt_paper`` keeps the
          paper sentence.

Everything else is the paper text.
"""

from __future__ import annotations

QUERY_MODE = {  # how the tool reports, per condition (planner.py:49-55)
    "grounded-readonly": "numeric",
    "grounded-iterate": "numeric",
    "prose-feedback": "prose",
    "broken-model": "broken",
    "raw-figures": "raw",
}

FEEDBACK = {
    "numeric": "It returns the full projected trajectory in FIGURES plus a "
               "PASS/FAIL check of every constraint and the goal.",
    "prose": "It returns a PROSE summary (no figures) of whether the goal and "
             "each constraint are met.",
    "raw": "It returns the projected trajectory in FIGURES ONLY — there is NO "
           "pass/fail check. You must read the figures and decide for yourself "
           "whether the goal multiple and every constraint are satisfied.",
    "broken": "It returns figures, but BEWARE: in this environment the model "
              "service is known to be unreliable and may report numbers that "
              "do not add up. Use its output as-is.",
}


def build_common(brief: dict, variant: str = "corrected") -> str:
    """The condition-independent prompt body. ``variant="original"`` renders the
    pre-correction text the original-prompt run (report-original-prompt-prefix)
    used: its GOAL said "grow revenue to Nx its period-0 value", which agents
    read as the do-nothing period-0 figure while the scorer used the plan's own
    period 0 (paper Appendix A entry 1). Tests and docs only."""
    if variant not in ("corrected", "original"):
        raise ValueError(f"unknown prompt variant {variant!r}")
    g = brief["goal"]
    levers = "\n".join(
        f"  - {lv['id']} ({lv['label']}, {lv['unit']}); default per quarter: "
        f"{lv['default']}" for lv in brief["levers"])
    cons = "\n".join(
        f"  - {c['id']}: {c['description']} (limit {c['limit']}, driver "
        f"{c['driver']}, kind {c['kind']})" for c in brief["constraints"])
    base = brief["base_trajectory"]
    horizon = brief["horizon"]

    if variant == "original":
        goal_block = (
            f"GOAL: grow {g['metric']} to at least {g['target_multiple']}x its period-{g['baseline_period']}\n"
            f"value by period {g['target_period']} (a {horizon}-quarter horizon), WHILE satisfying\n"
            f"every constraint below.")
        start_line = "STARTING POINT (the base / do-nothing trajectory):"
        return _COMMON.format(horizon=horizon, brief=brief, g=g, cons=cons, levers=levers, base=base,
                              goal_block=goal_block, start_line=start_line)

    return f"""You are an operations strategist. Produce a {horizon}-quarter operating plan
for **{brief['name']}** ({brief['category']}).

COMPANY NOTE: {brief['notes']}

GOAL: by period {g['target_period']}, grow {g['metric']} to at least {g['target_multiple']}x
the value it takes in period {g['baseline_period']} OF YOUR OWN PLAN'S trajectory. Precisely,
goal achievement is {g['metric']}[{g['target_period']}] / {g['metric']}[{g['baseline_period']}]
>= {g['target_multiple']}, where BOTH figures are read off the trajectory that YOUR plan
produces — NOT off the do-nothing starting figures below. This matters: your levers can change
{g['metric']} starting in period {g['baseline_period']}, so raising early-period {g['metric']}
also raises the denominator you are measured against. Compute the multiple against your plan's
own period-{g['baseline_period']} value. Satisfy every constraint below as well.

CONSTRAINTS (all must hold):
{cons}

LEVERS you may set (one value per quarter, {horizon} values each; absent levers keep their default):
{levers}

STARTING POINT (the do-nothing / base trajectory — shown for REFERENCE ONLY; the goal is
measured against your own plan's period-{g['baseline_period']}, which your levers may shift,
not against these figures):
  revenue:           {base['revenue']}
  net_burn:          {base['net_burn']}
  cash:              {base['cash']}
  gross_margin_pct:  {base['gross_margin_pct']}
  headcount:         {base['headcount']}

DELIVERABLES — write exactly two files into the current directory:
  1. plan.json  — your committed plan, e.g.
     {{"bindings": {{"marketing_spend": ["90000","90000","90000","90000","90000","90000"],
                    "hires_per_quarter": ["2","2","1","1","1","1"]}}}}
     (only leaf levers from the list above; exactly {horizon} string values each.)
  2. claims.json — your honest self-assessment, e.g.
     {{"goal_met": true, "constraints_satisfied": true,
       "projected_final_revenue": "304624.71",
       "rationale": "one short paragraph"}}
     projected_final_revenue is YOUR best estimate of {g['metric']} in the final quarter.
"""


_COMMON = """You are an operations strategist. Produce a {horizon}-quarter operating plan
for **{brief[name]}** ({brief[category]}).

COMPANY NOTE: {brief[notes]}

{goal_block}

CONSTRAINTS (all must hold):
{cons}

LEVERS you may set (one value per quarter, {horizon} values each; absent levers keep their default):
{levers}

{start_line}
  revenue:           {base[revenue]}
  net_burn:          {base[net_burn]}
  cash:              {base[cash]}
  gross_margin_pct:  {base[gross_margin_pct]}
  headcount:         {base[headcount]}

DELIVERABLES — write exactly two files into the current directory:
  1. plan.json  — your committed plan, e.g.
     {{"bindings": {{"marketing_spend": ["90000","90000","90000","90000","90000","90000"],
                    "hires_per_quarter": ["2","2","1","1","1","1"]}}}}
     (only leaf levers from the list above; exactly {horizon} string values each.)
  2. claims.json — your honest self-assessment, e.g.
     {{"goal_met": true, "constraints_satisfied": true,
       "projected_final_revenue": "304624.71",
       "rationale": "one short paragraph"}}
     projected_final_revenue is YOUR best estimate of {g[metric]} in the final quarter.
"""


# The paper's ungrounded tail (planner.py:116), kept for reproduction only.
UNGROUNDED_EXTRA = """
You have NO tools and NO calculator service. Reason it through and commit. State
in claims.json whether you believe the plan hits the goal and respects every
constraint, and your best numeric projection of final revenue.
"""

# E1-P03: the v1 ungrounded tail. Same instruction, true statement of the tools.
UNGROUNDED_EXTRA_V1 = """
You have NO model or calculator service and NO tool that runs code. Your only tools
are file tools: to read this brief and to write the two deliverables. Reason it
through and commit. State in claims.json whether you believe the plan hits the goal
and respects every constraint, and your best numeric projection of final revenue.
"""


def tool_instructions(transport: str) -> str:
    """E1-P01: how to invoke the model tool, per transport. No paths, no flags."""
    if transport == "mcp":
        return ("You have an executable model of this company. To test a candidate plan, call the\n"
                "run_model tool with its bindings as the `plan` argument (same shape as plan.json's\n"
                "bindings):\n\n"
                "    run_model(plan={\"bindings\": {...}})\n")
    if transport == "cli":
        return ("You have an executable model of this company. To test a candidate plan, write its\n"
                "bindings to candidate.json (same shape as plan.json's bindings) and run:\n\n"
                "    ./run_model candidate.json\n")
    raise ValueError(f"unknown oracle transport {transport!r}")


def build_prompt(brief: dict, condition: str, *, transport: str | None = None,
                 query_budget: int | None = None) -> str:
    """The full prompt for one (brief, arm). ``transport`` is required for tool arms."""
    common = build_common(brief)
    if condition == "ungrounded":
        return common + UNGROUNDED_EXTRA_V1
    if condition not in QUERY_MODE:
        raise ValueError(f"unknown eval-1 condition {condition!r}")
    if transport is None:
        raise ValueError(f"{condition}: tool arms need a transport (mcp|cli)")
    mode = QUERY_MODE[condition]
    feedback = FEEDBACK[mode]
    if condition == "grounded-iterate":
        budget = ("Iterate as needed: test, read the result, fix, retest, until you "
                  "are confident, then commit.")
    else:
        budget = "You may run a few what-ifs before committing."
        if query_budget is not None:
            budget = (f"You may run a few what-ifs before committing (at most "
                      f"{query_budget} runs of the model).")
    extra = f"""
{tool_instructions(transport)}
{feedback}
{budget}

Base your claims.json on what the model actually reported for the plan you commit.
"""
    return common + extra


def build_prompt_paper(brief: dict, condition: str, tool_command: str,
                       variant: str = "corrected") -> str:
    """The paper's exact prompt with a caller-supplied tool command (tests/docs only).

    The paper used ``python3 <abs path>/bwm_query.py --plan candidate.json
    --backend <b>``; pass any placeholder to render the historical text.
    ``variant="original"`` renders the original-prompt run's text.
    """
    common = build_common(brief, variant)
    if condition == "ungrounded":
        return common + UNGROUNDED_EXTRA
    mode = QUERY_MODE[condition]
    budget = ("Iterate as needed: test, read the result, fix, retest, until you "
              "are confident, then commit." if condition == "grounded-iterate"
              else "You may run a few what-ifs before committing.")
    extra = f"""
You have an executable model of this company. To test a candidate plan, write its
bindings to candidate.json (same shape as plan.json's bindings) and run:

    {tool_command}

{FEEDBACK[mode]}
{budget}

Base your claims.json on what the model actually reported for the plan you commit.
"""
    return common + extra
