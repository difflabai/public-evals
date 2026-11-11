"""Agent-facing briefs for eval-6 D1 (TASK.md and the stdin prompt).

Two versions (``BRIEF_VERSIONS``):

``paper``: ported VERBATIM from the paper harness (d1_planner.py:71-181): the
bounded briefs with the price band (an earlier unaided brief had none).

``v1`` (what ``EVAL.stage`` / ``EVAL.prompt`` stage for new runs): the paper
text plus two disclosures the paper brief lacked (v7 Appendix A entry 21; G.3):
  * profit accounting: the per-segment gross margin (4 dp) and fixed opex (2 dp)
    the oracle charges, rounded as D3's brief rounds them, and "total profit =
    revenue x gm - marketing - fixed" in place of "total gross profit" (the paper
    brief disclosed neither, so an agent could not project the profit the
    oracle computes);
  * the per-segment marketing bound: each segment's maximum in
    public/history.csv, which the v1 scorer applies (score_d1.bound_marketing);
    the paper brief bounded price only.
Both arms get both disclosures (the arms differ by the fitted model only). The
rounded values are listed as disclosed (``disclosed_values``) for the core
pre-check and audit.

The briefs contain no tool-invocation lines that changed: there is no
harness-served oracle in eval 6, and the aided brief's ``python3 model_sim.py
--plan plan.json`` still works because model_sim.py is staged at the sandbox
root as before. The only staging change is that model_sim.py now imports
``heldout_sim.py`` instead of the full engine.
"""

from __future__ import annotations

import json

PRICE_LO, PRICE_HI = 0.7, 1.3   # same values as latent_engine.PRICE_LO/HI
BRIEF_PAPER, BRIEF_V1 = "paper", "v1"
BRIEF_VERSIONS = (BRIEF_PAPER, BRIEF_V1)


def build_task_md(task):
    """The self-supply brief. Instructs the agent to identify structure from the
    confounded history and commit a held-out plan. Deliberately carries NO dynamics
    equations and NO answer key — only the public decision spec."""
    segs = task["segments"]
    K = task["decision_horizon"]
    cap = task["budget_constraint"]["cap"]
    lo, hi = task.get("price_bounds", [PRICE_LO, PRICE_HI])
    refmkt = task["reference_marketing"]
    ref_lines = "\n".join(f"    - {sid}: reference marketing ~= {refmkt[sid]} usd/quarter"
                          for sid in segs)
    plan_shape = ", ".join(f'"{sid}": {{"marketing": <usd/quarter>, "price": <multiplier>}}'
                           for sid in segs)
    return f"""# Self-supply held-out planning task — world {task['world_id']}

You are the growth strategist for a {len(segs)}-segment subscription business. You are
handed the business's OWN history and must decide the next {K} quarters of policy. You
have Bash and Python (numpy is available on the host) — this is your ONLY calculator.
There is no model service and no answer key; your numbers are only as good as your own
analysis of the data.

## What you are given (in ./public/)
  - history.csv : {task['history_periods']} quarters of observed per-segment
      period, segment, marketing (usd/quarter), price (multiplier around 1.0),
      signups, customers, churn, revenue.
  - task.json   : the decision spec (segments, horizon, budget cap, reference marketing).

Segments: {", ".join(segs)}
{ref_lines}

## Structural form (qualitative — NO parameter values are given)
{task['structural_form']}
Latent per-segment demand is UNOBSERVED and persistent. IMPORTANT: the historical policy
set marketing and price AS A FUNCTION OF that latent demand, so the raw history is
CONFOUNDED — a naive regression of signups on price/marketing recovers the policy's
footprint, not the causal levers. Part of the job is to reason about what the history can
and cannot identify before you trust any fitted relationship.

## Your decision
Choose, PER SEGMENT, a constant marketing level (usd/quarter) and a price multiplier
(1.0 = reference) to apply to EACH of the next {K} held-out quarters.

## Constraints
  - Total marketing across segments must be <= {cap} usd per quarter (the budget cap).
  - The price multiplier is only calibrated in [{lo}, {hi}] and is CLAMPED to that band —
    values outside are not meaningful. Choose each segment's price within [{lo}, {hi}].
  - Objective: {task['objective']}.
  - Starting customers are the last row per segment in history.csv; latent demand
    continues (unobserved) from its end state.

## Deliverables — write EXACTLY these two files into the CURRENT directory:
  1. plan.json
     {{"plan": {{{plan_shape}}}}}
     Every segment must appear; marketing and price are plain numbers.
  2. claims.json
     {{"projected_held_out_profit": <your best estimate of total gross profit over the
         {K} held-out quarters>,
       "rationale": "<one short paragraph: what you identified from the history, what you
         could NOT identify, and why your levers follow>",
       "analysis_runs": <how many times you ran analysis/Python code, integer>}}

Commit your honest best plan. Do NOT overclaim a profit you cannot support from the data.
"""


def build_aided_task_md(task):
    """The AIDED (G) brief: the agent is handed a CALIBRATED interlocking model
    (fitted from history + controlled experiments — so it carries the coupling and
    price-elasticity SIGNS the passive history cannot identify) plus a simulator to
    forward-test candidate plans. It optimizes against the fitted model and commits;
    the committed plan is scored by the TRUE oracle."""
    segs = task["segments"]
    K = task["decision_horizon"]
    cap = task["budget_constraint"]["cap"]
    lo, hi = task.get("price_bounds", [PRICE_LO, PRICE_HI])
    plan_shape = ", ".join(f'"{sid}": {{"marketing": <usd/quarter>, "price": <multiplier>}}'
                           for sid in segs)
    return f"""# Aided held-out planning task — world {task['world_id']}

You are the growth strategist for a {len(segs)}-segment subscription business, deciding
the next {K} quarters of policy. You have a CALIBRATED MODEL of the business and a
simulator. You have Bash and Python.

## What you are given
  - ./public/history.csv, ./public/task.json — the business history + decision spec.
  - ./fitted_model.json — a calibrated interlocking model of the business, fitted from the
    history PLUS controlled experiments. It carries facts the raw history alone CANNOT
    identify: the correct SIGN of the cross-segment marketing spillover (complement vs
    cannibalize) and the correct (negative) price elasticity. Magnitudes carry estimation
    noise — it is a good model, not perfect. See its validation_report.
  - ./model_sim.py — a simulator. Write a candidate plan.json and run:
        python3 model_sim.py --plan plan.json
    It returns the plan's EXPECTED held-out profit (and per-segment + budget check) UNDER
    THE FITTED MODEL. Use it to search for the best plan.

Segments: {", ".join(segs)}

## Your decision
Choose, PER SEGMENT, a constant marketing level (usd/quarter) and a price multiplier for
each of the next {K} held-out quarters. Total marketing across segments <= {cap}/quarter.
The price multiplier is only calibrated in [{lo}, {hi}] and is CLAMPED to that band — keep
each segment's price within [{lo}, {hi}]. Objective: {task['objective']}. Iterate with the
simulator; commit the best plan you find.

## Deliverables — write EXACTLY these two files into the CURRENT directory:
  1. plan.json   {{"plan": {{{plan_shape}}}}}
  2. claims.json {{"projected_held_out_profit": <the fitted model's expected profit for your
       committed plan>, "rationale": "<what the model told you; how you used the coupling
       and price signs>", "analysis_runs": <how many times you ran model_sim.py>}}
Commit the plan your calibrated model says is best.
"""


# ---------------------------------------------------------------------------
# v1 briefs: the paper text plus the profit accounting and the marketing bound
# ---------------------------------------------------------------------------

def world_accounting(world_id: str) -> dict:
    """What the v1 briefs disclose for one world: per-segment gross margin rounded to 4 dp and
    fixed opex to 2 dp (from the answer key, exactly as D3's brief rounds them; v7 Appendix E.1),
    and the per-segment marketing bound (public/history.csv maximum, score_d1.history_marketing_max)."""
    from . import _paths
    from .score_d1 import history_marketing_max
    theta = json.loads(_paths.answer_key(world_id, "theta.json").read_text())
    segs = list(theta["segment_order"])
    bound = history_marketing_max(world_id)
    return {"gm": {s: round(theta["segments"][s]["gm"], 4) for s in segs},
            "fixed": {s: round(theta["segments"][s]["fixed"], 2) for s in segs},
            "marketing_max": {s: bound[s] for s in segs}}


def _fmt_usd(x: float) -> str:
    return f"{x:.2f}"


def disclosed_values(accounting: dict) -> list[str]:
    """The literal strings a v1 brief discloses on purpose (json reprs of the rounded gm / fixed, and
    the marketing bounds as rendered), for ArmSpec-level disclosure in the core pre-check / audit."""
    out: list[str] = []
    for v in list(accounting["gm"].values()) + list(accounting["fixed"].values()):
        out.append(json.dumps(v))
    out += [_fmt_usd(v) for v in accounting["marketing_max"].values()]
    return list(dict.fromkeys(out))


def _accounting_block(task, accounting: dict, aided: bool) -> str:
    segs = task["segments"]
    K = task["decision_horizon"]
    gm = {s: accounting["gm"][s] for s in segs}
    fixed = {s: accounting["fixed"][s] for s in segs}
    tail = ("\nThe scorer charges exactly these values; fitted_model.json's own gm and fixed carry its\n"
            "estimation noise.") if aided else ""
    return f"""## Profit accounting — the oracle uses EXACTLY these; do not infer them
Per quarter, per segment:  profit = revenue * gm - marketing - fixed
  gm    = {json.dumps(gm)}
  fixed = {json.dumps(fixed)}   (USD per quarter, per segment)
Total profit is summed over ALL {len(segs)} segments and ALL {K} held-out quarters.{tail}

"""


def _bound_lines(task, accounting: dict, indent: str) -> str:
    return "\n".join(f"{indent}- {sid}: <= {_fmt_usd(accounting['marketing_max'][sid])} usd/quarter"
                      for sid in task["segments"])


def _replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        raise ValueError("paper brief changed; the v1 rendering no longer applies")
    return text.replace(old, new, 1)


def build_task_md_v1(task, accounting: dict) -> str:
    """The v1 self-supply brief: the paper brief plus the pinned profit accounting and the
    per-segment marketing bound (see the module docstring)."""
    K = task["decision_horizon"]
    cap = task["budget_constraint"]["cap"]
    text = build_task_md(task)
    text = _replace_once(
        text, f"  - Total marketing across segments must be <= {cap} usd per quarter (the budget cap).\n",
        f"  - Total marketing across segments must be <= {cap} usd per quarter (the budget cap).\n"
        f"  - Marketing per segment is only calibrated up to that segment's maximum in history.csv and\n"
        f"    is CAPPED there when your plan is scored (spend above it buys nothing):\n"
        f"{_bound_lines(task, accounting, '      ')}\n")
    text = _replace_once(text, f"  - Objective: {task['objective']}.\n",
                         f"  - Objective: {task['objective']}; profit as defined under \"Profit accounting\".\n")
    text = _replace_once(text, "## Deliverables — write EXACTLY these two files",
                         _accounting_block(task, accounting, aided=False)
                         + "## Deliverables — write EXACTLY these two files")
    text = _replace_once(
        text, f"<your best estimate of total gross profit over the\n         {K} held-out quarters>",
        f"<your best estimate of total profit = revenue x gm -\n         marketing - fixed, "
        f"summed over all segments and the {K} held-out quarters>")
    return text


def build_aided_task_md_v1(task, accounting: dict) -> str:
    """The v1 aided brief: the paper brief plus the same accounting and marketing bound."""
    cap = task["budget_constraint"]["cap"]
    text = build_aided_task_md(task)
    text = _replace_once(
        text, f"each of the next {task['decision_horizon']} held-out quarters. Total marketing across segments "
              f"<= {cap}/quarter.\n",
        f"each of the next {task['decision_horizon']} held-out quarters. Total marketing across segments "
        f"<= {cap}/quarter.\n"
        f"Marketing per segment is only calibrated up to that segment's maximum in history.csv and is\n"
        f"CAPPED there when your plan is scored (model_sim.py does not apply this cap):\n"
        f"{_bound_lines(task, accounting, '  ')}\n")
    text = _replace_once(text, "## Deliverables — write EXACTLY these two files",
                         _accounting_block(task, accounting, aided=True)
                         + "## Deliverables — write EXACTLY these two files")
    return text


def brief(task, arm: str, accounting: dict | None = None, version: str = BRIEF_V1) -> str:
    """The brief (TASK.md and the first prompt) for a D1 arm. ``version`` "paper" is the paper's
    text; "v1" (runs) needs ``accounting`` (world_accounting(world_id))."""
    if version not in BRIEF_VERSIONS:
        raise ValueError(f"brief version must be one of {BRIEF_VERSIONS}")
    if arm not in ("unaided", "aided"):
        raise KeyError(f"unknown eval6 arm {arm!r}")
    if version == BRIEF_PAPER:
        return build_task_md(task) if arm == "unaided" else build_aided_task_md(task)
    if accounting is None:
        raise ValueError("the v1 brief needs the world's accounting (prompt.world_accounting)")
    return build_task_md_v1(task, accounting) if arm == "unaided" else build_aided_task_md_v1(task, accounting)


def brief_for_world(world_id: str, arm: str, version: str = BRIEF_V1) -> str:
    """The brief for one world (loads task.json and, for v1, the accounting)."""
    from . import _paths
    task = json.loads(_paths.world_public(world_id, "task.json").read_text())
    return brief(task, arm, world_accounting(world_id) if version == BRIEF_V1 else None, version)
