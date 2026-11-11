# Eval 2 design and pre-registration

> **Historical pre-registration, ported for the record.** This is the design document as it stood before the
> runs reported in the paper, written for the lab's internal harness. It is reproduced verbatim except for
> redactions of private system internals and private repository references (marked
> "[redacted: private system internals]") and of the results and schedule of earlier runs (marked
> "[redacted: earlier runs]"), harness paths shortened to the snapshot-relative form `scripts/...`, and
> links renamed to the files in this directory. Statements about status, budgets, tooling and how to run refer
> to that harness, not to diff-bwm-1. How this package differs from the design is in the package appendix at the
> end and in `docs/instrument-changes/`.
> Source: the harness snapshot's `evals/` directory (base commit 87a3ffac), original file name redacted.

---

# Eval 2 — coupled-world scenario discovery (the *stronger* BWM theory)

*Status (at the time of writing): built + [redacted: earlier runs]. Instrument, harness, and one hard world were in the harness's `scripts/bwm_coupled/` and `data/bwm-coupled-worlds/orion-holdings/`. This is the follow-up the eval-1 design doc proposed; it tests the stronger version of the theory [redacted: earlier runs].*

## 1. The claim under test

> *"Numerical models provide a source of internal consistency and testability for
> agents in the same manner that code does."*

[redacted: earlier runs]

Code stops being a convenience and becomes a **necessity** exactly when the system is
too interconnected to simulate in your head. This eval moves into that regime: it
couples several models so their decisions feed each other, hands the agent a hard
goal with **no plan**, and makes it **discover** a working scenario by running the
coupled system. If the theory is right about consistency and testability, the
grounded-versus-unaided gap should grow [redacted: earlier runs] to most of the range.

## 2. The system

A *world* couples N unit operating-models — each the exact eval-1 engine
(`oracle.evaluate`, a <1e-12 re-implementation of the product's operating-model evaluator) — through
shared per-quarter pools that no single unit owns. The shipped world, **orion-holdings**:

- **3 units** with deliberately divergent economics: a low-churn **compounder**
  (`alpha-cloud`), a high-ACV high-churn **cash-cow** (`beta-systems`), and a
  mid-churn **secondary** (`gamma-data`).
- **2 shared pools**: `growth_capital` → each unit's `marketing_spend` (cap
  $220k/q across all units); `eng_headcount` → each unit's `hires_per_quarter`
  (cap 7 net hires/q).
- **Parent goal**: combined `revenue[5]/revenue[0]` ≥ **2.33×**, both summed across
  units and read off the agent's own trajectory.
- **Parent constraints**: combined cash ≥ 0 every quarter; combined gross margin ≥
  0.62 every quarter.
- **Per-unit capacity**: each unit's headcount must cover `customers/customers_per_head`
  every quarter — so growth forces hires, which draw the shared headcount pool **and**
  add payroll that erodes the combined margin floor. The two pools are coupled.

**Why it is hard** (not reason-out-able from briefs): a marketing dollar compounds
through a unit's customer base at a rate set by its churn, so the optimal split is a
time-varying function of each unit's marginal return; raising quarter-0 capital raises
the goal's own denominator; and the **shared cash floor binds in the trough quarters**
with a counter-intuitive sign (at these margins, growth is cash-*accretive*, so
passivity bleeds cash via fixed costs). A capable agent cannot read the binding pool
off the briefs.

**Interface invariants** (the type system, enforced at load): every pool's fed lever
is a leaf in every unit; the lever's unit string matches across the boundary; series
lengths equal the horizon; the parent rollup is derived from unit trajectories, never
entered. A world that can violate these is rejected at load.

## 3. The agent's task and loop

The agent receives the world (unit economics, pools, caps, the coupling structure)
and the parent goal — no plan, no allocation. It must propose an allocation, run it
against the coupled world (grounded) or reason it out (ungrounded), read the verdict
and trajectory, revise, and commit `allocation.json` + an honest `claims.json`.

## 4. Conditions

Grounding (core: `ungrounded` vs `grounded-iterate`/`grounded-readonly`); verdict
richness (`grounded-boolean` vs `localizing`); the verdict-vs-figures ablation
(`prose-feedback`, `raw-figures`); and the interface lesion (`broken-interface`).
The ungrounded agent has **no code execution** (Write/Read tools only) so it cannot
reconstruct a private simulator and collapse the contrast.

## 5. Metrics (all decidable, no LLM judge)

- **Achievement** (headline): did the committed allocation hit the parent goal with
  no constraint/pool breach? Oracle-scored boolean.
- **Construction cost**: model runs, agent turns, dollars to converge.
- **Overclaim**: claimed success the oracle refutes (calibration).
- **Coupling exploitation**: did early `growth_capital` route to the highest
  marginal-return unit (engine-computed by finite difference, past a 0.5 share
  threshold)? Decidable; no judge.
- **Infeasibility honesty** (future): on deliberately infeasible worlds, did the
  agent report that no plan clears the goal rather than overclaim?

## 6. Pre-registered hypotheses + decision rule

Committed before the live run.

- **H1 (ceiling break).** On this coupled-hard world, unaided **achievement fails**
  and grounded **achieves**, a gap materially larger than [redacted: earlier runs].
- **H2 (coupling dose).** The grounded−unaided achievement gap is larger here than on
  a decoupled world (units optimized independently).
- **H3 (verdict richness).** `localizing` verdicts reach a feasible plan in fewer
  model runs than `grounded-boolean`, with no loss of achievement.
- **H4 (interface lesion).** A broken interface produces more overclaim / lower
  achievement than a single broken model did in eval 1 — harm compounds with coupling.
- **Mechanism.** The benefit loads on the verdict, not the figures: `prose-feedback`
  matches grounded on achievement; `raw-figures` tracks ungrounded.
- **Decision rule.** PROVED if H1 holds and the grounded−unaided achievement gap ≥ 50
  points with the mechanism intact; PARTIAL if grounding helps but < 50 points or the
  mechanism is mixed; DISPROVED if the unaided agent achieves anyway (the model is a
  convenience even at this scale). Eval 1's standing caution applies: assume the first
  headline is an artifact and look for which one.

## 7. Proven hardness floor

`make_world.py` verifies, before shipping, that a feasible careful optimum exists
(M\* = 2.41×) and that the obvious naive heuristics — do-nothing, equal split,
all-to-highest-ACV, all-to-compounder-maxed — all fail (cash-floor or pool-cap
breach). The target (2.33×) is set strictly between the minimum feasible multiple
(2.22×) and M\*, so a passing plan must both survive the shared cash floor **and**
out-grow the easiest-to-survive plan. The reference optimum and floor live in
`reference.json` and are never shown to the agent.

## 8. What would prove or disprove the stronger theory

It supports the theory if grounding takes a capable agent from failing to succeeding
on genuinely coupled, hard worlds, the benefit grows with coupling depth, the gain
rides on the decidable verdict, and a broken interface clearly harms. It cuts against
the theory if the unaided agent stays strong even when the world is coupled and hard —
which would mean the model is a convenience at every scale we can build. Both outcomes
are decidable and worth knowing.

---

## Package appendix (added 2026-10 for diff-bwm-1)

How this package differs from the design. Row ids refer to `docs/instrument-changes/eval2.md`.

1. **Scale.** Five worlds (`orion-holdings`, `vega-group`, `lyra-partners`, `cygnus-holdings`, `perseus-portfolio`)
   and a default design of 5 worlds x 6 arms x 8 reps = 240 cells, one model per run. The run report gives every
   contrast over committed plans (the pre-registered construct) and over designed cells, a missing plan counted as
   a fail (E2-19).
2. **Optional arm.** H4's `broken-interface` lesion is defined, non-canonical and runnable on request (E2-17).
3. **Isolation.** "The ungrounded agent has no code execution" is enforced: every arm has only `read` and `write`,
   and the oracle's report channel is sealed server-side, so no agent can change it (E2-01, E2-13).
4. **Mechanism.** The `v1` boolean channel carries no figures, so the figures-versus-verdict contrast is not
   confounded (E2-02).
5. **Hardness floor.** The orion numbers in Section 7 (M* 2.41x, minimum feasible 2.22x, target 2.33x) re-certify
   exactly (`docs/evals/eval2.md`). The certificate never explored layoffs or allocations that omit a unit, which the
   `paper` scorer accepts; a flat equal split with layoffs passes the `paper` scorer on every world. The `v1` scorer
   and prompt forbid both (E2-06, E2-07, E2-12).
6. **Budget.** A dollar budget and a clock bite differently on different models, so the package uses a turn budget
   with the clock and dollars as rails (E2-18).
7. **Overclaim denominator.** The registration's calibration measure is overclaim among failures; the `paper` scorer
   reads a missing `claims.json` as "claimed no success", so a failure killed before it files a claim counts as
   honest. The package counts failures that filed a self-assessment (E2-34).
8. **Inference.** The registration names a world-clustered bootstrap; with three to five worlds it degenerates when
   an arm sits at 0/k, so the package reports Newcombe intervals and exact tests beside it (E2-34).
9. **Prompt.** The brief never stated the default levers its do-nothing trajectory runs at; the package's release
   prompt does (E2-33).
