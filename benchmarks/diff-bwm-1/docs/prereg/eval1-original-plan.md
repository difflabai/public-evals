# Eval 1 precursor: the original evaluation plan (draft v0.1)

> **Historical pre-registration, ported for the record.** This is the design document as it stood before the
> runs reported in the paper, written for the lab's internal harness. It is reproduced verbatim except for
> redactions of private system internals and private repository references (marked
> "[redacted: private system internals]"), harness paths shortened to the snapshot-relative form `scripts/...`, and
> links renamed to the files in this directory. Statements about status, budgets, tooling and how to run refer
> to that harness, not to diff-bwm-1. How this package differs from the design is in the package appendix at the
> end and in `docs/instrument-changes/`.
> Source: the harness snapshot's `evals/` directory (base commit 87a3ffac), original file name redacted.

---

# Evaluation Plan: Grounded vs. Ungrounded Agent on Operational Planning

**Project:** BWM (the lab's business-modelling product)
**Status:** Draft v0.1
**Purpose:** Determine whether a queryable numerical operational model measurably improves agent performance on constrained operational planning, vs. the same agent with prose-only context.

-----

## Objective

Test the core hypothesis behind BWM: that numerical operational models provide internal consistency and testability for agents in the same manner that code does. Specifically, evaluate whether grounding an agent in such a model improves constraint compliance, forecast accuracy, output stability, and multi-step coherence on a representative operational task.

-----

## Task

**Operational planning under constraints.** The agent receives:

- A company description (synthetic, backed by a ground-truth simulator)
- A goal (e.g., “double ARR in 18 months while holding 30% gross margin”)
- A set of permissible levers (hiring, pricing, channel mix, capex)

The agent produces a 6-quarter plan: levers per quarter, projected outcomes, and rationale.

Chosen because it requires temporal consistency, respects accounting identities, and has crisp failure modes (budget violation, capacity ceiling, margin breach) scorable against ground truth.

-----

## Conditions (between-subjects)

|ID|Condition           |Description                                                                      |
|--|--------------------|---------------------------------------------------------------------------------|
|A |Prose-only          |Plain-English company description. No frameworks named.                          |
|B |Framework prose     |A + BMC/Porter/Moore framing of the same company. No queryable numbers.          |
|C |Read-only grounding |B + query access to the operational model. Agent can read state and run what-ifs.|
|D |Read/write grounding|C + ability to propose changes, observe propagation, iterate before committing.  |

Same base model, same prompt scaffolding across conditions. Only context and tool surface vary.

-----

## Hypotheses (pre-registered)

- **H1 — Constraint compliance.** C/D produce ≥2x fewer hard-constraint violations than A/B.
- **H2 — Forecast accuracy.** C/D projected outcomes match the oracle simulator within ±10%, vs. ±40% for A/B.
- **H3 — Variance.** Across 50 runs per scenario, C/D show lower coefficient of variation in lever choice and projected outcomes.
- **H4 — Multi-step coherence.** C/D plans propagate correctly across quarters (e.g., Q1 hires → Q3 capacity actually used downstream).
- **Null.** No significant difference. Triage: substrate is wrong, task doesn’t exercise it, or tool design failed.

-----

## Metrics

|Metric                          |Measurement                                               |
|--------------------------------|----------------------------------------------------------|
|Hard constraint violations      |Static check vs. oracle constraints                       |
|Forecast error                  ||agent projection − oracle| / oracle                      |
|Output variance                 |Coefficient of variation across N=50 runs per cell        |
|Multi-step coherence            |Q-by-Q internal consistency check                         |
|Confidence calibration          |Brier score on agent’s stated probabilities               |
|Substrate utilization (C/D only)|Query count, what-ifs run, % decisions citing model output|
|Cost                            |Tokens + tool calls per task                              |

-----

## Scenarios

10 synthetic companies, each instantiated in BWM with a ground-truth simulator behind it:

- 3 SaaS (seed, Series B, public-scale)
- 2 marketplaces
- 2 services
- 2 hardware/hybrid
- 1 ecosystem play

Each scenario authored at 3 difficulty tiers:

- **Soft constraints** — naive plans usually work
- **Hard constraints** — naive plans sometimes violate
- **Hostile constraints** — naive plans guaranteed to fail; only careful reasoning succeeds

-----

## Statistical design

- N=50 runs per (scenario × tier × condition) cell
- Total: 10 × 3 × 4 × 50 = **6,000 trials** for the full eval
- Paired bootstrap confidence intervals (LLM outputs are not Gaussian)
- Pre-register hypotheses, scenarios, and primary metrics before running
- Run on 2 agent models (e.g., Sonnet 4.6 + one non-Anthropic) to test generalization

-----

## Dependencies

These must exist before Phase 1 runs:

1. **Oracle simulator** — ground-truth dynamics for each scenario. *As built, BWM's operating model already provides this:* the operating model is a typed driver DAG with a **deterministic three-statement evaluator**. Evaluating a scenario is reproducible and side-effect-free, so the evaluator *is* the oracle — there is no separate simulator to build. What remains is authoring the per-scenario constraint set (hire caps, burn ceiling, margin hold) as checks layered on the evaluation output.
1. **Documented composition rules** for BMC + Porter + Moore. They overlap (e.g., BMC “key partnerships” ≈ Porter “supplier power” ≈ Moore “ecosystem members”). Without explicit rules, the eval measures framework conflation rather than the substrate.
1. **Scenario authoring guidelines** with two independent authors (one blind to substrate internals) to prevent designer bias.
1. **Tool API surface for C/D** — read-only query interface and read/write proposal interface, stable enough that ergonomics aren’t the dominant variable. *As built,* this was a gated admin API of the product: read = model detail + evaluate (what-ifs); read/write = persist a plan + evaluate-by-id. [redacted: private system internals]

-----

## Substrate & API (as implemented)

[redacted: private system internals] This section of the original described the private product's deployment, admin API, endpoints, authentication, model registry and the private framework files that targeted them. None of it is needed to read the plan or to run diff-bwm-1, which evaluates the same operating models with its own Python engine (`dbwm/eval1/engine.py`).

What the section said that is not private, in brief: a company is an **operating model**, a typed driver DAG of leaf, computed and roll-forward nodes over N periods; a plan is a **scenario** that rebinds leaf drivers only; projected outcomes are an evaluation of every node per period; the oracle is the deterministic evaluator. The 10-company x 3-tier matrix was generated by a script (3 SaaS: `sprout-crm`, `northwind-analytics`, `globalgrid-cloud`; 2 marketplaces: `harborlist-marketplace`, `gigbridge-marketplace`; 2 services: `atlas-advisory`, `brightclean-services`; 2 hardware/hybrid: `voltcore-devices`, `sensorgrid-iot`; 1 ecosystem: `meshverse-platform`), all quarterly with a 6-period horizon and a hiring -> 2-quarter-ramp -> capacity chain. Tiers were machine-calibrated from a naive and a careful reference plan per company, so that hostile is feasible by construction, and the reference plans were kept out of the agents' reach. The one-author-blind-to-internals rule was *not* satisfied by this generator; the original flagged the matrix as Phase 0/1a material pending a second, substrate-blind author.

-----

## Threats to validity

- **Tool ergonomics confound.** If C/D win, is it grounding or tool affordances? Mitigation: ablation where C/D tools return *prose summaries* instead of numbers. If the win survives, the numerical substrate is real; if it collapses, the eval measured tool design.
- **Information parity.** Prose conditions may inadvertently encode the same facts. Have a third party verify parity across A/B/C/D before scenario freeze.
- **Scenario authoring bias.** Mitigated by the two-author rule above.
- **Agent ignores substrate.** Track utilization. Low utilization + low accuracy = tool-design failure, not substrate failure. These must be distinguishable in the data.
- **No human baseline.** Without expert performance on the same task, “best condition” has no anchor. Run 3–5 humans (founders, consultants) on a scenario subset.
- **Backtest contamination.** When extending to real companies, training-data leakage is real. Use post-cutoff companies or anonymized scenarios.

-----

## Phasing

### Phase 0 — Week 1: Sanity check

2 scenarios, 1 tier, 4 conditions, N=10. Manual inspection. Goal: confirm task is well-formed and conditions are actually distinguishable.

### Phase 1a — Weeks 2–3: Cheap diagnostics first

Before running the full planning eval, run two lower-cost tasks on the same scenarios:

- **Constraint violation detection** — given a proposed plan, identify violations
- **Counterfactual prediction** — “What happens to ARR if we 2x marketing for 2 quarters?”

These exercise the same substrate with simpler scoring. If they show no signal, the planning eval will not show signal either, and Phase 1b should be paused for triage.

### Phase 1b — Weeks 3–4: Full planning eval

10 scenarios × 3 tiers × 4 conditions × 50 runs. Primary signal for H1–H4.

### Phase 2 — Weeks 5–6: Robustness

Adversarial scenarios + human baseline subset.

### Phase 3 — Week 7+: Real-company backtest

Build the model for a real SaaS at time T using only data available at T. Ask forward questions. Score against T+n actuals. Highest data-prep cost; only run if Phases 1–2 show signal.

-----

## Decision criteria

- **H1 and H2 confirmed with target effect sizes** → substrate hypothesis is alive. Invest in Phase 2–3 and broader integration into the lab's pipeline.
- **Partial confirmation** (e.g., H1 but not H2) → narrows the claim. Substrate prevents bad plans but doesn’t improve forecasts. Useful, but reframes the product story.
- **Null result** → run the tool-ergonomics ablation as the next diagnostic. If null persists, the substrate as currently designed does not deliver the claimed benefit; revisit the modeling layer before iterating on the eval.

-----

## Scope limits

This plan does **not** test:

- Whether the substrate helps agents *learn* over time (no learning loop in scope)
- Whether agents can construct world models from scratch — only whether a pre-built one helps
- Generalization beyond the synthetic scenario distribution
- Production token economics at scale

Each is its own follow-on eval.

-----

## Open questions

- Should C and D share the same query API, or is read/write a distinct surface worth varying independently of read-only?
- How much of the model state is exposed by default vs. discoverable? Discoverability is itself a tool-design variable that could confound the comparison.
- What’s the right unit of “violation” — a single broken constraint per plan, or count weighted by severity?

---

## Package appendix (added 2026-10 for diff-bwm-1)

This plan was superseded before any run by the eval-1 pre-registration ([eval1-preregistration.md](eval1-preregistration.md)),
which made it falsifiable. How the plan maps onto eval 1 in this package:

- **Conditions.** A (prose-only) is `ungrounded`; C and D are `grounded-readonly` and `grounded-iterate`; the
  tool-ergonomics ablation is `prose-feedback`; `broken-model` (a lesion) and `raw-figures` are added. B (framework
  prose) has no arm.
- **Scale.** The default design is 3 reps x 15 instances (5 companies x 3 tiers) x 6 arms = 270 cells per run, one
  model per run.
- **Metrics.** Hard-constraint violations, forecast error and output variance (with the definitions in the
  pre-registration's package appendix); confidence calibration is overclaim on a binary claim instead of a Brier
  score; multi-step coherence is the hostile tier's feasibility test.
- **Not in the package.** Phase 1a diagnostics, Phase 2 robustness and human baselines, Phase 3 real-company
  backtest, and a second, substrate-blind scenario author.
- **diff-bwm-1** evaluates the same operating models with its own engine and has no connection to the product or its
  service. Instrument changes: `docs/instrument-changes/eval1.md`.
