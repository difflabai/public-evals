# Eval 1 pre-registration (v1.0)

> **Historical pre-registration, ported for the record.** This is the design document as it stood before the
> runs reported in the paper, written for the lab's internal harness. It is reproduced verbatim except for
> redactions of private system internals and private repository references (marked
> "[redacted: private system internals]"), harness paths shortened to the snapshot-relative form `scripts/...`, and
> links renamed to the files in this directory. Statements about status, budgets, tooling and how to run refer
> to that harness, not to diff-bwm-1. How this package differs from the design is in the package appendix at the
> end and in `docs/instrument-changes/`.
> Source: the harness snapshot's `evals/` directory (base commit 87a3ffac), original file name redacted.

---

# Pre-Registration & Design: Do Numerical Models Give Agents the Internal Consistency and Testability That Code Does?

**Project:** BWM (the lab's business-modelling product)
**Status:** Pre-registered design, v1.0. Falsification criteria and statistics committed BEFORE any live-agent run. The measurement instrument (oracle), the full harness (planner conditions, SUT, eval, orchestrator, report generator), and a no-LLM illustration are all built and self-verified; the **empirical** verdict (real-agent runs) is what remains to be executed.
**Supersedes / builds on:** [`eval1-original-plan.md`](eval1-original-plan.md) (the original grounded-vs-ungrounded plan, "Draft v0.1"). That plan named the hypothesis; this document makes it falsifiable, pins the answer key, fixes a deterministic measurement instrument, and commits decision thresholds in advance.

-----

## 1. Abstract

BWM rests on a single load-bearing claim: that a numerical operating model gives an agent the same two things code gives a programmer — **internal consistency** (the model enforces its own accounting identities, so an agent's projections cannot silently contradict each other) and **testability** (the agent can *execute* a candidate plan and observe pass/fail against a constraint suite before committing, exactly as one runs a test suite before shipping). This document pre-registers a between-subjects experiment that isolates one independent variable — the agent's access to the *executable* numerical model — across five conditions (prose-only, read-only what-if, full edit-run-test-fix, plus a prose-feedback ablation and a broken-model lesion), measured on a 10-company × 3-tier scenario matrix that is **feasible by construction**. Every committed plan is scored by a deterministic oracle that is cross-checked to <1e-12 against the product's live engine. The **headline measurement** is the *claim–reality gap*: the rate at which an agent asserts a success the oracle refutes (an **overclaim** — the analogue of shipping code that passes review but fails its tests), and the error between the agent's stated final revenue and the oracle's. The theory predicts grounding collapses this gap to near zero while prose leaves it large. We distinguish carefully between the *proven instrument* (the oracle, demonstrable today) and the *run-dependent empirical verdict* (PROVED / DISPROVED / PARTIAL), which only live-agent data can deliver.

-----

## 2. The Theory, Decomposed

> **THEORY:** "Numerical models will provide a source of internal consistency and testability for agents in the same manner that code does."

The theory bundles two distinct, independently falsifiable sub-claims. We separate them because they can fail independently, and the experiment is designed so each maps to its own hypothesis.

### 2a. Internal consistency

A BWM operating model is a typed driver DAG with a deterministic three-statement evaluator. Computed nodes are *defined by* their formulas — revenue, COGS, headcount, net burn, cash, and gross margin are not free variables an agent can assert independently; they are forced by the leaf drivers (the levers) and the articulation identities (customer roll-forward, `revenue = base × price`, `COGS = revenue × (1 − margin)`, headcount accumulation, cash roll-forward). An agent grounded in the model **cannot** hold mutually contradictory projections — it cannot, for example, claim rising revenue *and* falling COGS at a fixed margin, because the model would refuse to produce both. Prose has no such enforcement: an ungrounded agent can write any number next to any other number, and nothing checks the cross-line arithmetic.

**Falsifiable form:** if consistency is the active ingredient, then grounding should drive the agent's *stated* outcomes into agreement with the *forced* outcomes — measured as forecast error collapsing toward zero, and as overclaims (asserting a success the identities refute) becoming rare.

### 2b. Testability

The model is *executable*. A candidate plan can be evaluated — run — and the result checked against an explicit constraint suite before the agent commits. The agent does not have to reason in-head about whether net burn stays under the ceiling across six quarters; it can project the trajectory and read the answer. This is the difference between arguing a program is correct and running it.

**Falsifiable form:** if testability is the active ingredient, then conditions that let the agent execute-and-observe before committing should produce far fewer constraint violations than conditions that force in-head reasoning — and an iterate loop (execute, see the violation, fix, re-execute) should beat a single read-only pass.

### The code analogy, made crisp

The oracle's module docstring states the mapping the whole design rests on, and we adopt it verbatim as the conceptual contract:

| Software concept | BWM theory analogue |
|---|---|
| a program | a **plan** — a Scenario that rebinds leaf drivers (the levers) |
| running it | **evaluating it** — the deterministic three-statement engine projects every node across the 6-quarter horizon |
| the test suite | the **tier limits** — goal multiple + burn / cash / margin / hire constraints |
| pass / fail | the **verdict** — machine-checkable, reproducible, exact |
| shipping code that fails its tests | an **overclaim** — asserting a success the oracle refutes |

So "is this plan good?" is not a matter of prose persuasion. It is *decidable*. Decidability is the entire point of the theory, and it is what lets the instrument catch an agent that claims a success it never achieved.

-----

## 3. Why This Is the Decisive Test: the Claim–Reality Gap

The original plan (sections H1–H4 of `eval1-original-plan.md`) measured compliance, forecast accuracy, variance, and coherence. Those are real, but they share a weakness as a *test of this theory*: each can be partially confounded by an agent simply being a stronger reasoner. The sharpest, least-confoundable signal is one the original plan only gestured at — the gap between what an agent **claims** and what is **true**.

In prose, an agent's plan is **unfalsifiable**. It can assert "this plan doubles revenue while holding margin and staying under the burn ceiling" and there is no within-the-artifact mechanism that can contradict it. The agent can be confidently, fluently wrong, and nothing in its own output catches it. This is precisely the failure mode the theory says a numerical model removes: with an executable model, the assertion is *checkable* — and an assertion that is checkable is one the agent is far less likely to make falsely, because the act of checking exposes the falsehood before commit.

We therefore designate the claim–reality gap as the **headline result**:

- **overclaim_rate** — the fraction of committed plans where the agent claimed success (goal met AND all constraints satisfied) but the oracle refutes it. This is "shipped code that fails its tests."
- **forecast error** — the agent's stated final revenue versus the oracle's computed final revenue.

The theory makes a strong, near-binary prediction here: **grounding collapses the overclaim/forecast gap toward zero; ungrounded leaves it large.** That is a prediction prose-reasoning quality alone does not make. A merely-smarter ungrounded agent still cannot run its plan, so it can still overclaim. If the gap does *not* collapse under grounding, the theory is wrong about consistency. If it collapses *equally* under a broken (inconsistent) model, the theory is wrong about *why* — and we have a lesion that says so (Section 5).

-----

## 4. Task & Substrate

### 4a. Task: operational planning under constraints

Identical in spirit to the original plan. The agent is given a company (synthetic, backed by a ground-truth model), a growth goal, an explicit constraint set for the difficulty tier, and a list of permissible levers. It must commit a 6-quarter plan: a per-quarter binding of each lever, plus its **claims** — whether it believes the goal is met, whether it believes every constraint is satisfied, and its projected final revenue. The task is chosen because it requires temporal consistency, respects accounting identities, and has crisp, oracle-scorable failure modes.

### 4b. Substrate: a BWM operating model (the numerical model under test)

A company is an **operating model**: a typed driver DAG of leaf / computed / roll-forward nodes over a fixed horizon (6 quarters here). The plan's "company state + levers" map directly onto it:

| Plan concept | Operating-model reality |
|---|---|
| Company state | computed outputs: `revenue`, `headcount`, `net_burn`, `cash`, `gross_margin_pct`, COGS, opex, working-capital / PP&E lines |
| Levers | leaf drivers: e.g. `marketing_spend`, `hires_per_quarter`, `acv` / `take_rate` / `rate_per_consultant`, `capex`, `gross_margin`, `churn`, … |
| A plan / proposal | a **Scenario** that rebinds *leaf* drivers (`{driver: {values: […]}}`) — leaves only; the computed graph is invariant |
| Projected outcomes | an **Evaluation**: `periods[t].values{driver → decimal}` for every node |
| Ground-truth oracle | the deterministic evaluator itself — reproducible, side-effect-free |
| Multi-step coherence | the model's articulation identities (the hiring → 2-quarter-ramp → capacity chain is the coherence trap: payroll starts the quarter of hire, productivity two quarters later) |

The evaluator IS the oracle. `scripts/bwm_theory/oracle.py` is a faithful re-implementation of the product's operating-model evaluator, lifted verbatim from the matrix generator, and **cross-checked to <1e-12 against the product's live API**. `oracle.py selftest` re-verifies that faithfulness against both a vendored live anchor and the full 10-company calibration matrix (90 constraint cells + goal semantics), so any drift between oracle and live server is caught before it can corrupt a verdict. This is what makes the instrument *proven* independently of any empirical claim.

### 4c. The answer key: a machine-calibrated 10 × 3 matrix

Ten synthetic companies span the space (the scenario matrix), each authored at three difficulty tiers:

| Company | Category | Goal: revenue[5]/revenue[0] ≥ | Levers |
|---|---|---|---|
| `sprout-crm` | saas (seed) | 3.90 | marketing_spend, hires_per_quarter, acv |
| `northwind-analytics` | saas (Series B) | 2.70 | marketing_spend, hires_per_quarter, acv, deals_per_ae |
| `globalgrid-cloud` | saas (public-scale) | 1.45 | marketing_spend, hires_per_quarter, acv |
| `harborlist-marketplace` | marketplace | 3.20 | marketing_spend, hires_per_quarter, take_rate |
| `gigbridge-marketplace` | marketplace (labor) | 4.25 | marketing_spend, hires_per_quarter, fill_rate, take_rate |
| `atlas-advisory` | services (consulting) | 1.55 | hires_per_quarter, utilization, rate_per_consultant, bd_spend |
| `brightclean-services` | services (field) | 1.70 | hires_per_quarter, price_per_job, jobs_per_crew, marketing_spend |
| `voltcore-devices` | hardware | 1.20 | marketing_spend, hires_per_quarter, asp, consumable_per_unit, capex |
| `sensorgrid-iot` | hardware/hybrid | 1.25 | marketing_spend, hires_per_quarter, asp, sub_price, capex |
| `meshverse-platform` | ecosystem | 2.90 | devrel_spend, hires_per_quarter, rev_share, seat_price |

**Goal:** `revenue[target_period] / revenue[baseline_period] ≥ target_multiple` (baseline period 0, target period 5).

**Constraints** (the "test suite"), drawn from each tier's `eval-tiers.json`:

| Constraint id | Kind | Driver | Meaning |
|---|---|---|---|
| `burn_ceiling` | `series_max` | `net_burn` | max quarterly net burn ≤ limit |
| `cash_floor` | `series_min` | `cash` | min ending cash ≥ 0 every quarter |
| `margin_floor` | `series_min` | `gross_margin_pct` | min gross margin ≥ limit every quarter |
| `hire_cap` | `binding_max` | `hires_per_quarter` | max net hires/quarter (a plan binding) ≤ limit |

The three tiers (`soft` / `hard` / `hostile`) tighten these limits monotonically. Concretely, for `sprout-crm` the burn ceiling falls 331,008 → 257,696 → 200,622, the margin floor rises 0.77 → 0.79 → 0.81, and the hire cap falls 6 → 3 → 2.

**Feasible by construction — the crucial property.** Tiers are machine-calibrated, not hand-tuned. The generator defines, per company, a *naive* reference plan (brute-force spend/hires) and a *careful* one (staged ramp + pricing/efficiency mix), evaluates them with the cross-checked evaluator, and derives tier limits so that the pre-registered tier semantics hold: **base** passes every tier but misses the goal; **naive** reaches the goal, passes soft, violates hard AND hostile; **careful** reaches the goal and passes everything — *including hostile*. Because the `careful` plan demonstrably passes even the hostile tier, **every cell is solvable**, so any agent failure is attributable to the agent, not to an impossible task. The reference plans live only in the calibration report, which was **not** deployed with the live service — agents under eval cannot look up the answer.

-----

## 5. Conditions & Controls

The independent variable is the agent's access to the *executable* numerical model. Five conditions, between-subjects.

| Condition | Model access | What the agent does | Isolates |
|---|---|---|---|
| `ungrounded` | none (prose only) | Gets a prose company description + goal + constraints; reasons in-head; commits a plan + claims. The status quo: confident, unfalsifiable. | baseline |
| `grounded-readonly` | what-if (`POST /evaluate`) | May project candidate plans to read trajectories before committing — but no persisted iterate loop. "Run the code, read the output." | + testability (single pass) |
| `grounded-iterate` | full edit-run-test-fix | Evaluate → see violations → revise → re-evaluate → commit. **The "code" condition.** | + testability (loop) |
| `prose-feedback` *(ablation)* | evaluate access, **prose output** | Same evaluate access as readonly, but results returned as prose summaries, NOT numbers. | "numbers/executability" vs "any feedback" |
| `broken-model` *(lesion)* | model access, **inconsistent** | The model returns numbers that violate its own identities (it lies). The agent "verifies" against it and believes success. | "consistency" as the active ingredient |

The two non-standard arms carry the mechanistic argument:

- **`prose-feedback` (ablation).** If grounding's benefit is really about ergonomics or about *any* feedback loop, then handing back the same evaluation as prose should preserve most of the benefit. The theory predicts it does **not** — prose summaries of numbers do not transfer the consistency/decidability, so the agent still overclaims. This separates "the agent got feedback" from "the agent got *executable numbers*."
- **`broken-model` (lesion).** If grounding's benefit survives a model that is internally *inconsistent* (returns numbers contradicting its own identities), then the benefit was never about consistency — it was about having *a* number to anchor on. The theory predicts the lesion **kills** the benefit: an agent that verifies against a lying model overclaims like an ungrounded one. This is the cleanest possible test of *why* grounding helps.

**Parity / scaffold controls.** Same base model and same prompt scaffold across all conditions; **only the context and the tool surface vary.** Prose conditions encode the same facts (goal, constraints, levers, base trajectory) the grounded conditions can read from the model — so any difference is access to the *executable* model, not access to *information*. Run on **≥2 agent models** (one Anthropic, one non-Anthropic) so the result is not an artifact of a single model's idiosyncrasies. Dose-response across `[ungrounded, grounded-readonly, grounded-iterate]` gives a monotonicity check that a single A/B cannot.

-----

## 6. Metrics

All computed by the deterministic oracle from each committed `(plan, claims)` pair. Per-condition rates aggregate over companies × tiers × reps × agent-models unless otherwise noted.

| Metric | Definition |
|---|---|
| **overclaim_rate** | fraction of plans with `claimed_success ∧ ¬actually_success`. `claimed_success` = agent claimed goal met AND all constraints satisfied; `actually_success` = oracle says `fully_valid` (goal met AND zero violations). The headline. |
| **claim_accuracy** | fraction with `claimed_success == actually_success` (`claim_correct`). Calibration of the agent's self-assessment. |
| **violation_rate** | mean `hard_violation_count` per plan (number of the 4 tier constraints failed; an unevaluable plan counts all as failed). |
| **valid_rate** | fraction with `fully_valid` true (passes the whole suite *and* hits the goal). |
| **goal_rate** | fraction with `goal_met` true (hits the target multiple, ignoring constraints). |
| **forecast_mae** | mean of `forecast_pct_error` = `|claimed_final_revenue − oracle_final_revenue| / |oracle_final_revenue| × 100`, over plans that stated a forecast. |
| **forecast_within_10** | fraction with `forecast_pct_error ≤ 10`. |
| **outcome_cv** | coefficient of variation (σ/μ) of the oracle's final revenue across reps within a cell, averaged over cells. Stability of outcomes, not of claims. |
| **utilization** (grounded arms) | evaluate-call count, what-ifs run, % of committed decisions traceable to a model read. Diagnostic, not a hypothesis target — see Section 8. |

Note the oracle treats an **unevaluable plan** (malformed bindings, wrong series length, non-leaf driver) as the strongest failure: not even a program. It scores as goal-missed, all-constraints-failed, `fully_valid=false`; and if the agent *still* claimed success, that is an overclaim.

-----

## 7. Pre-Registered Hypotheses & Decision Rule

> The block below is committed verbatim and is shared, unaltered, with the report generator. Thresholds are fixed before any live run.

- **H1 (testability → compliance):** PASS iff `violation_rate(grounded-iterate) ≤ 0.5 × violation_rate(ungrounded)` AND the 95% bootstrap CI of the difference excludes 0.
- **H2 (consistency → calibrated claims) [HEADLINE]:** PASS iff `overclaim_rate(ungrounded) ≥ 0.30` AND `overclaim_rate(grounded-iterate) ≤ 0.05` AND the difference CI excludes 0.
- **H2b (forecast accuracy):** PASS iff `forecast_mae(grounded-iterate) ≤ 10` AND `forecast_mae(ungrounded) ≥ 25`.
- **H3 (stability):** PASS iff `outcome_cv(grounded-iterate) < outcome_cv(ungrounded)`.
- **H4 (feasibility on hostile):** PASS iff on the hostile tier `valid_rate(grounded-iterate) ≥ 0.5` AND `≥ 3 × valid_rate(ungrounded)`.
- **Dose-response:** `valid_rate` non-decreasing AND `overclaim_rate` non-increasing across `[ungrounded, grounded-readonly, grounded-iterate]`.
- **Mechanism (lesion/ablation):** `overclaim_rate(broken-model) ≥ 0.5 × overclaim_rate(ungrounded)` AND `overclaim_rate(prose-feedback) ≥ 0.5 × overclaim_rate(ungrounded)`.
- **VERDICT:** PROVED iff H1 ∧ H2 pass AND Mechanism passes; DISPROVED iff neither H1 nor H2 passes; PARTIAL otherwise.

**Statistics:** 95% percentile bootstrap, B = 2000 resamples, fixed seed. Resampling is at the trial level within each condition; CIs on between-condition differences are computed by resampling both arms under the same seed.

A note on the Mechanism clause, since it reads as inverted: the lesion and ablation are expected to *retain* high overclaim. Requiring `broken-model` and `prose-feedback` to keep at least *half* the ungrounded overclaim rate confirms that the benefit seen in `grounded-iterate` is specifically attributable to executable, internally-consistent numbers — not to feedback in general (ablation) and not to merely having a model to point at (lesion). If either of those arms also collapsed the overclaim, the mechanism story would be wrong even if H1/H2 passed, so VERDICT requires Mechanism for PROVED.

-----

## 8. Threats to Validity & Mitigations

Pre-registration is worthless if it hides the weaknesses. Each known threat, and what in the design addresses it:

| Threat | Why it matters | Mitigation | Residual risk |
|---|---|---|---|
| **Tool-ergonomics confound** | If grounded arms win, is it the *numbers* or just a nicer tool / a feedback loop? | The `prose-feedback` ablation gives the *same* evaluate access but returns prose. If the win survives, it's the executable numbers; if it collapses, we measured tool design. | Prose summary quality is itself a design choice; a deliberately rich summary could leak the signal. We fix a neutral summary template up front. |
| **Information parity** | Prose conditions might accidentally encode the same facts the model would reveal, erasing the contrast. | Prose conditions are built to encode *exactly* the goal, constraints, levers, and base trajectory — the same facts — so the only difference is *executability*, not *information*. | Subtle phrasing could still hint at answers; a second reviewer should diff the prose against the model brief before freeze. |
| **Mechanism ambiguity** | Even a real win doesn't tell us *why* grounding helps. | The `broken-model` lesion removes *consistency* while keeping *access*. If the benefit survives a lying model, it wasn't about consistency. | The lesion must be inconsistent in a way the agent can't trivially detect; an obviously-broken model would be ignored, not trusted. |
| **Agent ignores the substrate** | A grounded agent that never calls evaluate is effectively ungrounded; low utilization + low accuracy looks like substrate failure but is tool-adoption failure. | Track **utilization** (evaluate-call counts, % decisions traceable to a model read). Low-utilization cells are flagged and analyzed separately so the two failures stay distinguishable. | Utilization is observational; it explains but does not fix non-adoption. |
| **Scenario-authoring bias** | The matrix generator authors *both* the dynamics and the tier limits from one script — it is **not** substrate-blind. The dependencies list in the original plan called for one author blind to internals; that is **not satisfied here.** | Honest disclosure. The current matrix is Phase-0/1a material. A second, substrate-blind author should review or re-derive the matrix before drawing Phase-1b conclusions. Feasibility-by-construction limits the damage (the task is provably solvable), but tier *difficulty* could still be mis-calibrated. | Real and unresolved. Treat the matrix as a fixture under review, not a settled benchmark. |
| **No human baseline** | Without expert performance on the same task, "best condition" has no external anchor. | Acknowledged gap. A 3–5 person human subset (founders / consultants) on a scenario slice is a planned follow-on, not part of this pre-registration. | The eval ranks conditions against each other, not against ground-truth human skill. |

-----

## 9. How to Run

Three layers, increasing in cost and in evidentiary weight. **The first two prove the instrument and illustrate the predicted shape; only the third is empirical.**

### 9a. Oracle self-test — proves the measurement instrument

```bash
python3 scripts/bwm_theory/oracle.py selftest
```

Re-derives base/naive/careful × soft/hard/hostile for all 10 companies (90 constraint cells + goal semantics) and pins a seed model of the product revenue to the live API anchor at <1e-12. A pass means the oracle reproduces the live `bwm` engine *exactly*. This is the only claim in the document provable today, and it is what licenses every downstream verdict.

### 9b. Deterministic illustration — self-tests the pipeline & shows the predicted shape (NOT empirical)

```bash
python3 scripts/bwm_theory/demo.py --out /tmp/bwm-theory-demo --reps 8
python3 scripts/bwm_theory/report.py --verdicts /tmp/bwm-theory-demo/verdicts
```

`demo.py` calls **no LLM**. It runs explicit *reference policies* — small behavioral models of how an agent acts under each condition (e.g. "ungrounded commits the naive plan and believes it works because it cannot test it"; "grounded-iterate finds the careful plan and reads its forecast off the model") — and scores their plans with the real oracle. It exists to (1) self-test the scoring + reporting pipeline end-to-end with zero external dependencies, and (2) illustrate what the data looks like *if the theory's prediction holds*.

> **This is illustrative, not empirical.** Every verdict it emits is tagged `"data_source": "illustrative-reference-policy"`, and the report renders a loud amber banner saying so. It encodes the *expected* shape by assumption; it cannot confirm the theory. Treat its "PROVED" as "the pipeline and the prediction are internally coherent," never as evidence about real agents.

### 9c. Live-agent eval — the empirical test

The only layer that can move the verdict. It loops companies × tiers × conditions × reps, runs the planner agents against the product's **live** service, scores each committed plan with the oracle, and renders the report.

[redacted: private system internals] The original listed the private framework's system and evaluation files, the batch driver, the service endpoint and how to start it. What matters for reading the results: each trial was a `claude -p` session on the operator's logged-in subscription (API keys stripped from the child environment); the grounded agents reached the live model through the query tool `scripts/bwm_theory/bwm_query.py`; each planner (`scripts/bwm_theory/planner.py`) scored its own committed plan with the oracle; with no session, the planner fell back to a deterministic stub and the report carried a STUB banner.

-----

## 10. How to Read the Report

The report renders per-condition metrics with bootstrap CIs and applies the Section 7 decision rule. What to look at, and what each implies for the product:

- **Overclaim chart (the headline).** Bars of `overclaim_rate` per condition with CIs. The theory's signature is a tall `ungrounded` bar, a near-zero `grounded-iterate` bar, and — critically — `prose-feedback` and `broken-model` bars that stay tall. A tall-then-near-zero-then-tall-again profile is the cleanest possible "it's the executable, consistent numbers" picture.
- **Forecast scatter / MAE.** Stated vs oracle final revenue. Grounded points fall on the diagonal (they read the number off the model); ungrounded points scatter wide. This is H2b.
- **Violation bars (H1) and hostile valid_rate (H4).** Whether execution actually buys compliance, and whether the hostile tier — solvable by construction — gets solved more than 3× as often when the agent can run its plan.
- **Dose-response panel.** `valid_rate` rising and `overclaim_rate` falling monotonically across `[ungrounded → readonly → iterate]`. Monotonicity is stronger evidence than any single A/B.
- **Utilization panel.** Diagnostic. If a grounded arm underperforms *and* barely called evaluate, that is tool-adoption failure, not substrate failure — and the panel keeps the two distinguishable.

**What the verdict implies for the product:**

| Verdict | Reading | Product implication |
|---|---|---|
| **PROVED** (H1 ∧ H2 ∧ Mechanism) | Executable, consistent numbers measurably make agents' plans compliant *and* their claims calibrated, and the lesion/ablation confirm it's the consistency/executability — not feedback or anchoring. | The core BWM thesis is alive. Invest in the modeling layer and broader pipeline integration; the "numbers are the agent's test suite" framing is earned. |
| **PARTIAL** (e.g. H1 but not H2) | Grounding prevents bad plans but does not calibrate claims (or vice versa). | Narrows the story. The substrate is a *guardrail* but not a *truth serum* (or the reverse). Reframe the pitch to the proven half; investigate the failed half. |
| **DISPROVED** (neither H1 nor H2) | Access to the executable model did not improve compliance or calibration. | The substrate as currently designed does not deliver the claimed benefit. Re-examine the modeling layer (and re-run the tool-ergonomics ablation) before iterating on the eval. |

-----

## 11. Scope Limits

This eval tests one theory on one task. It does **not** test:

- **Learning over time.** There is no learning loop; we measure whether a *pre-built* model helps now, not whether the substrate makes agents better with experience.
- **Building models from scratch.** The operating models are given. Whether an agent can *construct* a faithful world model from raw company data is a separate, harder question.
- **Generalization beyond the synthetic distribution.** Ten synthetic companies on a quarterly 6-period horizon. Real companies, other horizons, and other domains are out of scope; the matrix is a fixture, and (per Section 8) not yet substrate-blind.
- **Token economics.** Grounded conditions cost more tool calls and tokens. We track utilization but do not price it; whether the accuracy gain is worth the cost at production scale is a follow-on.
- **Human-relative quality.** No human baseline in this pre-registration; conditions are ranked against each other, not against expert performance.

Each is its own follow-on eval. The discipline of this document is narrow on purpose: commit the falsification criteria, fix the instrument, and let one decisive measurement — the claim–reality gap — carry the verdict.

---

## Package appendix (added 2026-10 for diff-bwm-1)

How this package differs from the pre-registration. Row ids refer to `docs/instrument-changes/eval1.md`.

1. **A sixth arm.** `raw-figures` (figures without pass/fail) is a default arm, though the pre-registration does not
   list it (E1-R05).
2. **Metric definitions.** The pre-registration defines `violation_rate` as the mean number of violated constraints
   per plan and `outcome_cv` over final revenue; the package uses the definitions of the report behind the paper: the
   share of plans with at least one violation and the CV of the goal multiple (E1-R05).
3. **Prompt.** The prompt states the goal against the plan's own period 0, the convention the scorer uses. The
   original wording (N times "its period-0 value" beside the do-nothing trajectory) is kept only as the `original`
   variant of the paper prompt (E1-P04).
4. **Models.** Each run pins one model with `--model`; the pre-registration's call for at least two models, one
   non-Anthropic, needs more than one run.
5. **Arm isolation.** Every arm has only `read` and `write`; the model tool is served over MCP with its channel
   sealed server-side and no self-labelling; `grounded-readonly` has a 3-call budget, so it differs from
   `grounded-iterate` by more than one prompt sentence (E1-A01 to E1-A06, E1-T00 to E1-T04). Every brief carries
   its company's note, and four of the five notes in the default set name the efficient levers, against "agents
   cannot look up the answer".
6. **Normative engine.** The Python engine defines the worlds; Section 4b's live cross-check is not part of the
   package, and the calibration self-test is a regression check (E1-G02, E1-G03).
7. **Unbounded levers.** A final-quarter price spike reaches any goal multiple; the tiers do not prevent it (E1-L01).
8. **Statistics.** The eval-1 report uses the pre-registered percentile bootstrap (B = 2000, seed 12345).
9. **Scorer.** The `v1` scorer reads claims strictly and rejects non-lever bindings. New runs are a new series.
