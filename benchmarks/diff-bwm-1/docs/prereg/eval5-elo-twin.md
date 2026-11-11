# Eval 5 ELO twin design

> **Historical pre-registration, ported for the record.** This is the design document as it stood before the
> runs reported in the paper, written for the lab's internal harness. It is reproduced verbatim except for
> redactions of private system internals and private repository references (marked
> "[redacted: private system internals]"), harness paths shortened to the snapshot-relative form `scripts/...`, and
> links renamed to the files in this directory. Statements about status, budgets, tooling and how to run refer
> to that harness, not to diff-bwm-1. How this package differs from the design is in the package appendix at the
> end and in `docs/instrument-changes/`.
> Source: the harness snapshot's `evals/` directory (base commit 87a3ffac), original file name redacted.

---

# Difflab-consulting-benchmark — open-ended consulting work, ELO-scored

*Status (at the time of writing): built + validated offline (2026-07-02). Dataset shared with eval 5 (authored + adversarially verified, deterministic validation clean on all 4 scenarios). Pairwise-judge + Bradley-Terry pipeline (Elo, 1500-centered, one virtual tie per pair as regularizing prior) validated offline with a mock judge over stub tiers. No live competitor field run yet. Instruments: `pairwise_judge.py`, `elo_report.py`. [redacted: private system internals]*

## 1. What it is

A benchmark of **open-ended consulting deliverables** over the four
`data/difflab-consulting` scenarios (specialty-pharma market access
& prior-auth workflow design; SOC 2 readiness for a patient-workflow comms
startup; custom-software needs of a transmission-line consultancy; demand
growth for a foodservice wholesaler). Unlike the BWM oracle evals there is
deliberately **no decidable pass/fail on quality**: the deliverable is real
consulting judgment, and quality is measured the way such work is actually
evaluated — by comparative preference. Scoring is **ELO** (Bradley-Terry
over pairwise LLM judgments).

Every competitor gets the identical baseline condition (task brief + source
pool, no checklist, offline instruction), produces the scenario's prose
deliverable + machine-readable artifact, and is compared round-robin.

## 2. Why ELO and not a rubric score

- Absolute rubric scores on open-ended work compress at the top and are
  judge-calibration-sensitive; pairwise preference is a strictly easier and
  more reliable judgment (the AA leaderboard itself is rubric-based — this
  benchmark deliberately measures what rubrics miss).
- Ratings compose across scenarios and across time: new competitors join by
  playing the existing field; no re-anchoring.
- The scenarios plant contradictions, red herrings, and an unreliable junior
  voice — the differentiator is *grounded judgment*, which shows up in
  side-by-side comparison far more sharply than in absolute grading.

## 3. Protocol

1. **Submissions:** the `difflab-consulting-benchmark` eval runs each
   scenario once per competitor (`consulting_planner.py`, baseline
   condition, out-of-repo sandbox, answer-key-leak + network audits, cap
   detection). Per-trial framework grading is a *gate only* (submission
   complete, trial valid) — never the score.
2. **Pairwise judging** (`pairwise_judge.py`): per scenario, every
   competitor pair, judged in **both presentation orders** by an LLM judge
   (default `claude-sonnet-5`, metered API) on: accuracy/grounding,
   completeness, actionability, internal consistency,
   clarity/professionalism + an overall winner (tie allowed). The judge sees
   the scenario overview, the task brief, and the two submissions — never
   claims.json, never the rubric, never the answer key.
3. **Rating** (`elo_report.py`): Bradley-Terry MLE (ties = half-win) →
   Elo scale (1500-centered), bootstrap 95% CIs, win matrix, per-dimension
   win rates, per-scenario ladders.

## 4. Validity guards

- **Position bias:** every pair judged in both orders; the report prints
  both-order agreement and first-position win rate (0.5 = unbiased). A
  ladder with low both-order agreement is flagged, not trusted.
- **Length/confidence bias:** judge prompt explicitly instructs
  substance-per-requirement over volume and evidence over confident tone;
  per-dimension wins let a length-driven overall winner be spotted (wins
  professionalism but loses grounding).
- **Self-preference:** competitors include non-Claude-authored stubs; when
  cross-vendor agents are added, judge-model sensitivity should be checked
  by re-judging a sample with a second judge model.
- **Anchoring the ladder:** deterministic stub tiers (strong/medium/weak,
  synthesized from the oracle registry) pin the floor and give an
  interpretable reference: any real agent should beat `stub-weak`
  decisively; `stub-strong` marks "checklist-satisfying but judgment-free"
  quality — a real agent that loses to it is producing well-formed but
  hollow work.
- **Leakage:** same sandbox + `rubric_leak` audit as eval 5; a leaked trial
  is excluded from judging.
- **Mock judge quarantine:** `--judge mock` (checker-score-based) exists
  only for pipeline validation; `elo_report.py` warns on mixed judge types
  and `--judge api` filters to real judgments.

## 5. Relationship to the BWM program

The benchmark is the **registry-independent quality measure** for the same
submissions eval 5 scores with the decidable oracle (shared-trial design:
eval-5 baseline-arm outputs are ELO competitors' outputs). This closes eval
5's tautology loop — and the preference-vs-oracle agreement is itself a
finding: if oracle-passing submissions also win preference, the executable
checklist is buying real quality; if they win the oracle but lose preference
to prose-arm submissions, the checklist is being gamed (Goodhart evidence),
which is exactly the failure mode eval 4's judge-vs-checker contrast was
built to catch.

## 6. Budget & run plan

Two meters, tracked separately:

**Session budget (subscription, `claude -p` cap windows).** Each competitor
trial is a subscription session; empirically ≈ 40 substantial trials fit one
5-hour cap window (evals 2/4), and the weekly cap binds before the 5-hour one
during heavy multi-agent use. The live field (3 session competitors × 4
scenarios = 12 trials) fits one window. `consulting_planner.py --budget-usd`
(default $8) is the per-session runaway guard. Capped trials are marked
`session_capped` = attrition; re-running the same command after a reset is
idempotent.

**API budget (metered, judging only).** n competitors → 4 · n(n−1)
ordered-pair judgments. Per judgment (measured shape: ~14k fresh input +
~5k cached scenario prefix + ~0.6k output — `pairwise_judge.py` puts a cache
breakpoint on the per-scenario context, so the round-robin pays one cache
write per scenario per 5-minute window and reads at 0.1× thereafter):

| judge model | $/judgment | field of 4 (48) | field of 6 (120) | field of 8 (224) |
|---|---|---|---|---|
| `claude-sonnet-5` ($3/$15; intro $2/$10 to 2026-08-31) | ~$0.05–0.08 | ~$3–4 | ~$6–10 | ~$12–18 |
| `claude-haiku-4-5` ($1/$5) | ~$0.02 | ~$1 | ~$2–4 | ~$4–7 |

Controls: `pairwise_judge.py --max-usd N` (or `DIFFLAB_JUDGE_MAX_USD`) hard-
stops at the cap and resumes idempotently on the next run; every
comparisons.jsonl row records its `usage` and `cost_usd` (pricing table +
`DIFFLAB_PRICE_IN/OUT` override), and `elo_report.py` prints the batch's
total judging cost. Judge-sensitivity re-runs (a Haiku second judge over a
sample) cost ~1/3 of the primary pass.

**Phases:**
- **Phase 1 (offline, done):** mock-judge smoke over stub tiers — pipeline
  recovers strong > medium > weak with both-order agreement 1.0. $0.
- **Phase 2:** live field = claude-default / claude-sonnet / claude-haiku
  sessions + the three stubs (one cap window, 12 sessions), then API judging
  (6 competitors → 120 judgments ≈ $6–10 Sonnet-5) + `elo_report.py`.
- **Phase 3 (open):** add external competitors by dropping trial dirs into
  the batch root; each new competitor adds 4 · 2 · n_existing judgments
  (~$1.6–2.5 per new competitor at a field of 6, Sonnet-5).

---

## Package appendix (added 2026-10 for diff-bwm-1)

How this package differs from the design. Row ids refer to `docs/instrument-changes/eval5.md`.

1. **Judge isolation.** `dbwm eval5-elo` runs the ladder through the core judge adapters: no tools, an empty working
   directory, the stream saved, and any tool use voids the pass (E5-37).
2. **Position-bias guard.** Both-order agreement is in every table, and any ladder below 0.75 is labelled
   POSITION-UNSTABLE (E5-38).
3. **Mock smoke.** The package's mock smoke runs 24 comparisons.
