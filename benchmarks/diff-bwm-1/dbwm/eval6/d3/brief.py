"""D3 brief: the six named figures, the pinned plan, the probe pair, and the task text.

``build_task_md_paper`` is the pilot's ``build_d3_task_md`` (harness aaa04e5,
``scripts/bwm_latent/d3_planner.py:62-143``) ported verbatim, and
``detect_probe_pair`` / ``pinned_plan`` are its helpers (``:62-66, 240-256``);
tests/eval6/test_eval6_d3.py pins the rendered text.

``build_task_md_v1`` (what the runnable arm stages) corrects the one inaccurate
sentence v7 names (Appendix E.4): the pilot said the coupling edge runs "into
{probe}'s latent demand", but ``latent_engine._signups`` applies coupling as a
multiplier on the probe's marketing-to-signups response that never enters latent
demand. It also states how the plan.json the session writes is scored (the D1
``v1`` scorer: budget cap, price band, marketing capped at the history's
per-segment maximum), because the runnable arm scores that plan (v7 G.2 item 2);
the lab harness did not score it.

Disclosure policy (explicit; v7 Appendix E.5): both briefs disclose each segment's
gross margin rounded to 4 dp and fixed opex to 2 dp (read from the sequestered
theta.json), the probe and partner segment names (which tells the agent that a
coupling edge exists, not its sign or size), and the pinned plan's reference
marketing rounded to 2 dp. ``disclosed_values`` lists those strings for the core
pre-check and audit; no full-precision theta value appears in either brief.
"""

from __future__ import annotations

import json

PINNED_PLAN_NAME = "reference_hold"

FIGURE_NAMES = [
    "price_elasticity_probe_segment",
    "marketing_exponent_probe_segment",
    "cross_segment_spillover_pct",
    "pinned_plan_total_profit_usd",
    "pinned_plan_total_revenue_usd",
    "pinned_plan_end_customers_probe_segment",
]

BRIEF_PAPER, BRIEF_V1 = "paper", "v1"
BRIEF_VERSIONS = (BRIEF_PAPER, BRIEF_V1)

PAPER_COUPLING_SENTENCE = ("A structural coupling edge exists from {partner} into {probe}'s latent demand. Its sign\n"
                           "and size are NOT given to you.")
V1_COUPLING_SENTENCE = ("A structural coupling edge exists from {partner}: {partner}'s marketing multiplies {probe}'s\n"
                        "marketing-to-signups response (it does not enter {probe}'s latent demand). Its sign\n"
                        "and size are NOT given to you.")
PAPER_PLAN_ITEM = "     Your own best plan. Every segment present; plain numbers.\n"
V1_PLAN_ITEM = ("     Your own best plan. Every segment present; plain numbers. It is scored like the eval-6\n"
                "     D1 plan: total marketing <= task.json's cap per quarter, price clamped to [0.7, 1.3], and\n"
                "     each segment's marketing capped at its maximum in history.csv.\n")


def pinned_plan(theta):
    segs = list(theta["segment_order"])
    return {s: {"marketing": theta["segments"][s]["refmkt"], "price": 1.0} for s in segs}


def detect_probe_pair(theta):
    """(probe, partner) = the segment with the strongest incoming coupling edge, and its
    source. The pair must be NAMED IN THE PROMPT or figures 1-3 are ill-posed, but the
    hub is derivable only from the sequestered coupling matrix — so naming it discloses
    that a structural edge EXISTS (not its sign or size). That is consistent with
    task.json already disclosing structural_form including '*coupling', and must be
    stated alongside the selection disclosure in any write-up."""
    segs = list(theta["segment_order"])
    C = theta["coupling"]
    best = (0.0, segs[0], segs[min(1, len(segs) - 1)])
    for i, s in enumerate(segs):
        for j, p in enumerate(segs):
            if i == j:
                continue
            if abs(C[i][j]) > best[0]:
                best = (abs(C[i][j]), s, p)
    return best[1], best[2]


def build_task_md_paper(theta, probe, partner, K):
    """Self-supply task text. Starts from d1_planner's own self-supply sandbox text so
    the inputs and framing are identical to the paper's D1 unaided arm, then replaces
    the deliverables block with D3's six figures."""
    segs = list(theta["segment_order"])
    gm = {s: round(theta["segments"][s]["gm"], 4) for s in segs}
    fixed = {s: round(theta["segments"][s]["fixed"], 2) for s in segs}
    plan = pinned_plan(theta)
    plan_txt = json.dumps({s: {"marketing": round(v["marketing"], 2), "price": v["price"]}
                           for s, v in plan.items()}, indent=2)
    return f"""# Task — structural read + pinned-plan forecast (self-supply)

You have `public/history.csv` ({theta['horizon']} periods x {len(segs)} segments:
period,segment,marketing,price,signups,customers,churn,revenue) and `public/task.json`.
You have NO external model and NO query tool. Use your own analysis.

## Pinned accounting — use EXACTLY these, do not infer them
Per quarter, per segment:  profit = revenue * gm - marketing - fixed
  gm    = {json.dumps(gm)}
  fixed = {json.dumps(fixed)}   (USD per quarter, per segment)
Totals are summed over ALL {len(segs)} segments and ALL {K} held-out quarters.
All money figures are USD, undiscounted. Report plain numbers, no strings or units.

## The probe pair
probe segment   = "{probe}"
partner segment = "{partner}"
A structural coupling edge exists from {partner} into {probe}'s latent demand. Its sign
and size are NOT given to you.

## The PINNED evaluation plan — forecast THIS, not your own plan
```json
{plan_txt}
```
Held constant for all {K} held-out quarters. Forecast its outcome even if you would
choose differently; your own preferred plan goes in plan.json separately.

## Deliverables — write EXACTLY these two files into the CURRENT directory
  1. plan.json
     {{"plan": {{"<segment>": {{"marketing": <usd/quarter>, "price": <multiplier>}}, ...}}}}
     Your own best plan. Every segment present; plain numbers.
  2. claims.json — all six figures, plus the three trailing fields:
     {{"price_elasticity_probe_segment": <causal elasticity of {probe} SIGNUPS to the
         price multiplier; negative; the exponent itself, e.g. -1.4>,
       "marketing_exponent_probe_segment": <causal exponent of {probe} signups to
         marketing spend; in (0,1)>,
       "cross_segment_spillover_pct": <percent change in {probe}'s signups from a
         +10% marketing increase in {partner}, holding {probe}'s own levers fixed.
         Signed. If you judge it NOT IDENTIFIABLE from this history, answer 0 and say
         so in the rationale>,
       "pinned_plan_total_profit_usd": <total profit of the PINNED plan, all segments,
         all {K} quarters, using the pinned gm/fixed above>,
       "pinned_plan_total_revenue_usd": <total revenue of the PINNED plan, same scope>,
       "pinned_plan_end_customers_probe_segment": <{probe}'s customer count at the END
         of quarter {K} under the PINNED plan>,
       "confidence": {{"<figure name>": <0.0-1.0 your confidence per figure>}},
       "first_action": "<the single highest-value first action, one sentence>",
       "rationale": "<what you identified, what you could NOT identify, and why>"}}

Report your honest best estimate for each. Do NOT anchor on round numbers, and do not
report a figure you cannot support — say so in the rationale instead.
"""


def build_task_md_v1(theta, probe, partner, K):
    """The paper text with the coupling sentence corrected and the plan's scoring stated."""
    text = build_task_md_paper(theta, probe, partner, K)
    for old, new in ((PAPER_COUPLING_SENTENCE.format(probe=probe, partner=partner),
                      V1_COUPLING_SENTENCE.format(probe=probe, partner=partner)),
                     (PAPER_PLAN_ITEM, V1_PLAN_ITEM)):
        if text.count(old) != 1:
            raise ValueError("paper D3 brief changed; the v1 rendering no longer applies")
        text = text.replace(old, new, 1)
    return text


def task_md(theta, version: str = BRIEF_V1) -> str:
    """The brief for a world (probe pair detected from the coupling matrix)."""
    if version not in BRIEF_VERSIONS:
        raise ValueError(f"D3 brief version must be one of {BRIEF_VERSIONS}")
    probe, partner = detect_probe_pair(theta)
    build = build_task_md_paper if version == BRIEF_PAPER else build_task_md_v1
    return build(theta, probe, partner, theta["held_out"])


def disclosed_values(theta) -> list[str]:
    """The literal strings the brief discloses on purpose: rounded gm (4 dp) and fixed (2 dp) as
    json renders them, the pinned plan's rounded reference marketing, and the probe/partner names."""
    segs = list(theta["segment_order"])
    vals = [json.dumps(round(theta["segments"][s]["gm"], 4)) for s in segs]
    vals += [json.dumps(round(theta["segments"][s]["fixed"], 2)) for s in segs]
    vals += [json.dumps(round(theta["segments"][s]["refmkt"], 2)) for s in segs]
    vals += list(detect_probe_pair(theta))
    return list(dict.fromkeys(vals))
