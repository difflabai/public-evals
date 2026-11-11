# Eval 5 design and pre-registration

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

# Eval 5 — the decidable oracle on open-ended consulting work

*Status (at the time of writing): built + validated offline (2026-07-02). Dataset (4 scenarios) authored by a blueprint→author→adversarial-verify→fix multi-agent pipeline; verifier findings all resolved (see §8); deterministic validation clean on all 4 scenarios. No live sessions run yet. [redacted: private system internals]*

## 1. Why a fifth eval

The claim under test, all five evals: *"numerical models provide a source of
internal consistency and testability for agents in the same manner that code
does."*

- **Eval 1** (single operating model): [redacted: earlier runs]
- **Eval 2** (coupled portfolio): in the too-big-to-simulate-in-your-head
  regime [redacted: earlier runs]
- **Eval 3** (generative vs grounded): building your own model vs being
  handed an oracle.
- **Eval 4** (briefcase): ecological transfer to ONE published task
  (AA-Briefcase-Lite w1_t2) — a spreadsheet, i.e. a deliverable that is
  itself a numerical model.

The remaining gap: eval 4's deliverable is *naturally* numerical. If the
executable-checklist effect only shows up when the deliverable is a
spreadsheet, the theory is narrower than claimed. Eval 5 pushes the same
manipulation onto **open-ended consulting work** — deliverables that are
mostly prose judgment (a market-access strategy, a SOC 2 readiness
assessment, a software capability plan, a demand growth strategy) with a
machine-readable artifact riding along. Four scenarios, authored in the
AA-Briefcase-Lite shape but harder (see §2), each with a **source-anchored
deterministic oracle**.

## 2. The dataset — four scenarios in the briefcase shape

`data/difflab-consulting/` (text-only sources; committable; built
by a blueprint → author → adversarial-verify → fix multi-agent pipeline).
Each scenario: a `scenario_overview.md`, one task brief, ~26–32 source files
(client docs, CSV data pulls, interview transcripts, email threads, slack
exports), ≥7 planted decidable contradictions, ≥4 red herrings, a
confidently-wrong junior voice, ≥2 figures recoverable only by 3+-source
triangulation, a regulatory/contractual overlay, and a private answer key
(`anchors.json` bands + `contradictions.json` resolutions).

| scenario | engagement | structured deliverable | example planted traps |
|---|---|---|---|
| `solvara-market-access` | specialty-pharma launch: market access + prior-auth workflow design | `pa_workflow.yaml` | junior deck plans Part-B buy-and-bill for a pharmacy-benefit drug; copay-card support for Medicare patients (illegal — a `text_absent` check); conflicting payer-mix figures |
| `meridian-soc2` | SOC 2 Type II readiness for a patient-workflow comms startup | `controls_matrix.csv` | CTO claims encryption-at-rest everywhere, S3 inventory says otherwise; SMS vendor with no BAA; stale Type-I report asserting lapsed controls; ~10 gap_ids to discover |
| `castellan-software` | custom software needs of a transmission-line consultancy | `capability_plan.json` | vendor pitch overclaims FAC-008 support (forum digest debunks); PLS-CADD seat counts conflict; single-owner Access DB risk |
| `bluestem-demand` | demand growth for a foodservice wholesaler | `initiative_portfolio.json` | two churn definitions (19% vs 8.5%) needing the controller's reconciliation; VP-Sales "chain is our profitable growth engine" refuted by margin CSV; price-cut thesis the survey data refutes |

## 3. The oracle, and how it stays decidable on prose work

Each scenario's `checks.jsonl` is an AA-style binary rubric (14–16 items with
`score_1/score_0` criteria) where ≥11 items also carry an **`auto` spec** in
a small DSL (`structured_path/number/enum/fields`, `csv_required/condition`,
`text_number_near/contains/absent/regex_count`, `all_of/any_of`).
`checker.py` compiles the auto specs into deterministic PASS/FAIL over the
submission. Decidability is bought three ways:

1. **A machine-readable co-deliverable** (workflow YAML / controls CSV /
   capability JSON / portfolio JSON) whose required schema is stated in the
   task brief — structure, coverage, and figure bands are decidable.
2. **Source-anchored bands** from `anchors.json` — the true value's band
   passes, the planted trap values fall outside (verified adversarially at
   authoring time).
3. **Prose probes** — decidable text checks on the prose deliverable
   (required topics, forbidden recommendations, figures-near-context).

Items that stay judgment-only carry `auto: null` and are listed as MANUAL —
the oracle never guesses.

## 4. The manipulation: same criteria, varying executability

Identical to eval 4, so results are directly comparable across the two
regimes (numerical-deliverable vs prose-deliverable):

| condition | gets | tests? |
|---|---|---|
| `baseline` | task brief only | — |
| `checklist-prose` | + `CHECKLIST.md` (registry rendered as text) | read only |
| `checklist-executable` | + `CHECKLIST.md` + `qa_check.py` (PASS/FAIL + figures) | yes, figures |
| `checklist-verdict` | + `CHECKLIST.md` + `qa_check.py` (PASS/FAIL only) | yes, verdict-only |

One registry, two renderings — information-matched; the contrast isolates
executability. The verdict arm's vendored engine is locked to verdict mode
(wrapper bypass is traced).

## 5. Outcome measures

1. **Achievement:** oracle item pass rate (per-condition, Wilson CIs;
   `report_oracle.py`). The executable arm optimizes against the same
   registry, so the CONDITION CONTRAST is the result, not absolute levels.
   The registry-independent quality read on the SAME submissions comes from
   the ELO twin (`difflab-consulting-benchmark`, [eval5-elo-twin.md](eval5-elo-twin.md)) — pairwise preference by a
   judge that never sees the registry.
2. **Calibration:** per-item overclaim (claimed-pass on an oracle-failed
   item), coarse overclaim, claim accuracy — `claims.json` in every arm.
3. **Grounding:** 3 headline figures per scenario self-reported in
   `claims.json`, banded against `anchors.json`.
4. **Process:** `qa_check_calls` (dose), turns, wrapper bypass, network use.

## 6. Pre-registration

- **H1 (achievement):** checklist-executable > checklist-prose on oracle
  item pass rate. Eval-2-sized if testability binds on multi-constraint
  prose work; eval-1-sized if capable agents satisfy prose criteria unaided.
- **H2 (calibration):** per-item overclaim ≈ 0 in executable arms; > 0 in
  prose/baseline if self-assessing 14+ fine-grained criteria over a large
  open-ended artifact without a decidable test is hard (eval-2 regime).
- **H3 (mechanism):** verdict-only vs figures gap, sign pre-registered
  *unknown* [redacted: earlier runs].
- **H4 (prose is not nothing):** checklist-prose > baseline (criteria
  content is information, not testability).
- **H5 (regime comparison, cross-eval):** the executable-vs-prose gap here
  (prose-judgment deliverables) vs eval 4 (numerical deliverable). If the
  gap collapses here while eval 4's holds, the theory is about numerical
  artifacts specifically, not deliverables generally.
- **Decision rule:** theory GENERALIZES if H1 > 0 with 95% CI excluding 0
  pooled over scenarios AND the ELO read on the same submissions does not
  contradict it (executable-arm submissions not preference-worse than prose);
  NARROW-TO-NUMERICAL if H1 ≈ 0 here while eval-4 H1 > 0; SUPPORTED-WEAK if
  only calibration (H2) separates.

## 7. Validity guards (lessons from evals 1–4 baked in)

- **Answer-key isolation:** trials run in a mkdtemp sandbox OUTSIDE the repo
  containing only `shared/`, `engagement/`, `TASK.md`, and condition extras.
  `checks.jsonl` and `answer_key/` never enter it; `LEAK_TOKENS` audit
  (incl. `answer_key`, `checks.jsonl`, `difflab-consulting`) invalidates any
  trial that reached them.
- **Tautology control:** the oracle shares its registry with the executable
  arm's tool BY DESIGN (that is the manipulation); cross-arm comparisons use
  the same fixed registry, and the ELO twin provides the registry-independent
  measure. Stub-tier validation (below) is mechanics-only.
- **Authoring-side validity:** every scenario passed adversarial
  verification: anchors traced to sources, check bands contain true values
  and exclude trap values, contradiction quotes present with decidable
  resolutions, no meta-leakage in sources, task brief states the full
  structured schema (agents never need the registry to know the format).
- **Spec-ambiguity control:** the criteria the agent is judged on are the
  criteria shown verbatim in the checklist arms; thresholds stated in both
  renderings.
- **Session economics:** `claude -p` sessions, API keys stripped; cap
  detection → `session_capped` = attrition, excluded not failed. 4×4 = 16
  trials/rep-block fits one cap window.
- **Offline fidelity:** network use instructed against + audited, analyzed
  both ways when flagged.

## 8. Offline validation (2026-07-02)

**Authoring-time verification.** Three adversarial LLM verifiers per scenario
(grounding/decidability, contradictions/triangulation, hygiene/realism)
surfaced 15 serious findings, all resolved: two immaterial contradictions
made scoreable (Solvara WAC → new check A-15; bridge duration folded into
A-06); a statute-table inconsistency reconciled end-to-end (CSV, email,
answer key: 7-day modal floor with FL/MO/TN 10–14-day outliers); near-vacuous
checks tightened (Castellan C-01 FAC-008 co-location regex, A-07 annual-intake
context); an existential-vs-universal DSL gap closed with a new
`structured_number_all` op (Castellan A-03); a hidden-answer-key band widened
to the in-source guidance (A-11 → $0.9–1.9M per the COO's memo); two
over-claimed "triangulation" anchors hardened for real (Meridian's 214 and
departed-credentials counts now stated in NO source — derivation required;
Castellan's license CSV logs denials/waits, not needed seats); a plural-token
miss and a percent-fallback overmatch fixed in checks/engine. Deterministic
validation (`validate_dataset.py`: DSL conformance, anchor-band coherence,
quote traceability, file-plan coverage, leakage): **0 errors / 0 warnings on
all 4 scenarios** (~30 sources each, 16–17 checks, 15–16 auto).

**Independent quality review (2026-07-03).** A second review pass — 12
reviewer agents, 3 lenses per scenario (consultant realism, solvability/
fairness, difficulty vs the AA exemplar) — scored the scenarios 6.5–8.5/10
and surfaced 8 serious + ~40 moderate findings, all serious and all
grading-affecting moderates fixed:

- **Engine fairness (all scenarios):** `text_number_near` now accepts
  heading+next-line layouts and spelled-out small integers; explicit empty
  lists count as provided fields; a new `structured_text_absent` op scopes
  trap-absence to structured paths (naming a rejected idea in a risk note no
  longer fails); `csv_condition` supports content-keyed row matching
  (`column: "*"`).
- **Misgrading specs:** Meridian's five gap-ID-keyed checks were rekeyed to
  content matching and accept the standards-defensible `partial` status; the
  S3 encryption gap was re-based from an AWS anachronism ("no default
  encryption" is impossible post-2023) onto account-default SSE-S3 vs the
  POL-09 CMK requirement; Castellan's cost-band floor, SharePoint trap,
  synonym-blind coverage check, and TB-intake context were loosened to
  accept correct professional phrasings; Bluestem's price-cut trap moved to
  name-scoped absence and its pilot-evidence leg accepts elasticity/GP-impact
  formulations; Solvara's network-size check is now backed by an explicit
  task-brief requirement and the addressable band admits the defensibly
  better L40.1-filtered computation.
- **World coherence:** roster-consistent incident customers, constant
  timesheet practice labels, a tying churn ladder, covenant-testable
  financials, physically consistent LiDAR volumes, and reconciled
  epidemiology tables.
- **New trap-punishing checks:** Castellan C-04 (corrected GridVault year-one
  cost) and Bluestem C-07 (red-herring initiatives absent).

Known calibration limitations (accepted, documented rather than fixed): the
task briefs enumerate the conflicts to reconcile, so trap *detection* is
partially pre-solved and the discriminating skill is resolution quality; a
few headline ballparks are stated verbally by expert voices (precise values
still require computation); and scenario_overview cast tables signpost
reliability. These compress difficulty toward diligent execution rather than
open-ended discovery — acceptable for the condition-contrast design, noted
for anyone reusing the dataset as a pure discovery benchmark.

**Mechanics validation** (synthetic stubs from the registry — tautological by
design, validates instruments not agents; post-review numbers):

| scenario | checks (auto) | strong | medium | weak |
|---|---|---|---|---|
| solvara-market-access | 17 (16) | 16/16 | 12/16 | 1/16 |
| meridian-soc2 | 16 (15) | 15/15 | 11/15 | 0/15 |
| castellan-software | 17 (16) | 16/16 | 11/16 | 2/16 |
| bluestem-demand | 17 (16) | 16/16 | 8/16 | 1/16 |

Strong = full pass also proves every auto check is *satisfiable*. End-to-end
condition trials run clean through sandbox→submission→oracle→
`report_oracle.py`; the mock-ELO smoke over stub tiers
checks the ladder ordering. Mock/stub numbers are pipeline validation ONLY.

## 9. Budget & run plan

**This eval costs zero metered API** — trials are subscription sessions and
the oracle is deterministic. The only budget is cap windows: ≈ 40 substantial
`claude -p` trials per 5-hour window (evals 2/4), with the *weekly* cap the
binding constraint in practice (scenario authoring burned four windows in a
day). Per-session runaway guard: `consulting_planner.py --budget-usd`
(default $8). Capped trials are `session_capped` attrition; identical re-runs
after a reset are the resume path.

1. **n=1 pilot** (4 conditions × 1 scenario, `meridian-soc2` — 4 sessions,
   ~½ window) [redacted: earlier runs].
2. If the pipeline holds: **4 conditions × 4 scenarios = 16 sessions** per
   rep-block (≈ one window each); 2–3 rep-blocks paced across windows, then
   `report_oracle.py` + write-up.
3. In the same windows, the baseline-arm submissions double as
   `claude-default` entries for the ELO benchmark (shared-trial design); the
   ELO judging pass is the only metered step and is budgeted in
   `evals/difflab-consulting-benchmark.md` §6 (~$6–10 for a field of six,
   Sonnet-5, with `pairwise_judge.py --max-usd` as the hard cap).

---

## Package appendix (added 2026-10 for diff-bwm-1)

How this package differs from the design. Row ids refer to `docs/instrument-changes/eval5.md`.

1. **Primary metric.** Item-pass, as registered.
2. **Information match.** No arm receives the registry or the engine; the check runs harness-side with its mode
   sealed and serves registry v1.0; the executable arm's figures omit the grader's literals (E5-02 to E5-04).
3. **"Shown verbatim" holds for the checklist, and the checklist carries answers.** `CHECKLIST.md` states an in-band
   value for every banded check and lists meridian's planted gaps, so checklist-vs-baseline is an answer-disclosure
   contrast (E5-44).
4. **Section 2's copay trap** is described as a `text_absent` check; in registry v0.1 SOLVARA-C-01 is
   `text_contains_any` and polarity-blind (E5-11).
5. **Registry defects.** In registry v0.1, SOLVARA-A-13 fails a plan that correctly rejects the buy-and-bill trap; the
   lab's 2026-10-03 repair is the package ruleset `paper`. SOLVARA-A-14's matcher misses the hyphenated
   "prior-authorization". Registry v1.0 fixes both and the other check defects, with its own A-13 spec (E5-01,
   E5-15, E5-47, E5-50).
6. **ELO.** H5 (the cross-eval comparison) and the ELO condition of the decision rule rest on the ELO twin
   ([eval5-elo-twin.md](eval5-elo-twin.md)).
7. **Data hygiene for publication.** The dataset ships as v1.0: real-entity collisions renamed, `.example` domains,
   RFC 5737 addresses, a paraphrased reference sheet, two brief edits, a fictional-entities statement
   (`docs/datasheet.md`). Every arm runs in a container.
