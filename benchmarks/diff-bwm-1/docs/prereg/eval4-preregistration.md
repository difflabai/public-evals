# Eval 4 design and pre-registration

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

# Eval 4 — ecological transfer: the executable checklist on AA-Briefcase-Lite

*Status (at the time of writing): built + validated offline (stub pipeline end-to-end). No live sessions run yet. [redacted: private system internals]*

## 1. Why a fourth eval

The claim under test, all four evals: *"numerical models provide a source of
internal consistency and testability for agents in the same manner that code
does."*

- **Eval 1** (single operating model): [redacted: earlier runs]
- **Eval 2** (coupled portfolio): in the too-big-to-simulate-in-your-head
  regime [redacted: earlier runs]
- **Eval 3** (generative vs grounded): separates building your own model from
  being handed an oracle.

All three use **our synthetic worlds and our oracle**. The standing external
objection: maybe the effect lives only in worlds built to have a decidable
oracle. Eval 4 answers it by moving to a published third-party benchmark of
realistic knowledge work — **AA-Briefcase-Lite** (Artificial Analysis,
Apache-2.0): one week of PE due diligence on a fictional NZ egg producer,
147 source files with deliberate contradictions, graded by a published
63-item rubric. We take **w1_t2**, the numerical deliverable: build
`market_model.xlsx` (3-year historical sizing, 5-year forecast, cage-ban
transition scenarios). Here the agent must *build the numerical model itself*
from messy sources, and ground truth is the benchmark's own 12 binary rubric
checks — not our oracle.

## 2. The manipulation: same criteria, varying executability

Evals 1–3 varied access to an executable *model*. In an open-ended deliverable
there is no external model to hand the agent — but the theory's active
ingredient [redacted: earlier runs] is the **decidable
verdict**: the ability to *test* a draft and get a PASS/FAIL. The transferable
form of that ingredient is an **executable checklist** over the deliverable —
tests for a spreadsheet, exactly what tests are for code.

`checker.py` implements the machine-checkable core of the 12 published checks
as a registry of 12 deterministic tests (K-SCEN … K-PREM): source-anchored
numeric bands (Aurora 600–650k birds; producer shares in the Calthorpe expert
ranges; bottom-up market size NZ$513–567M; FY26 free-range premium 17–23%;
FX from the firm's central workbook), required structure (≥3 substantively
named scenarios; wholesale+shelf columns; top-down vs bottom-up
reconciliation), and render integrity (no error cells, no Sheet1, formula-
driven not pasted). The SAME registry renders to prose. Conditions:

| condition | gets | tests? |
|---|---|---|
| `baseline` | the published task brief only | — (the leaderboard condition) |
| `checklist-prose` | + `CHECKLIST.md` (criteria as text, thresholds included) | read only |
| `checklist-executable` | + `CHECKLIST.md` + `qa_check.py` (per-item PASS/FAIL + measured figures) | yes, figures |
| `checklist-verdict` | + `CHECKLIST.md` + `qa_check.py` (per-item PASS/FAIL only) | yes, verdict-only |

Prose vs executable is **information-matched** (one registry, two renderings);
the contrast isolates executability. verdict-vs-figures carries the
eval-1/eval-2 mechanism question [redacted: earlier runs] into the ecological setting.

All arms have full local tooling (they must script the workbook), so a prose
agent CAN hand-roll its own checks — as in real work. This is an
*effectiveness* design: provided-executable-testability vs DIY. Transcript
tracing (`own-compute` analog: `qa_check_calls`, workbook writes) lets us
measure uptake rather than assume it.

## 3. Outcome measures

1. **Achievement (PRIMARY = the benchmark's own grade):** pass rate over the
   12 official binary checks, graded by `judge.py` using the dataset's
   published judge prompts (task + rubric item + serialized artifact, judge
   never sees sources). Deterministic checker pass rate is SECONDARY —
   the executable arm optimizes against the checker's own registry, so
   checker-graded comparisons vs that arm are tautology-prone (eval-1's
   bwm_query/oracle lesson). The judge is registry-independent: it grades
   the *published* criteria, incl. aspects the checker can't decide.
2. **Calibration:** agents submit `claims.json` (per-item self-assessment in
   checklist arms; coarse believes-meets-requirements everywhere; three key
   figures). Per-item overclaim = claimed-pass on an item the grader fails.
   Coarse overclaim works in every arm including baseline.
3. **Consistency:** formula-driven ratio (refreshable-model requirement),
   error cells, and whether the claimed key figures (a) appear in the
   workbook and (b) land in the source-anchored bands.
4. **Process:** `qa_check_calls` (dose), turns, wrapper-bypass and
   network-use flags.

## 4. Pre-registration

- **H1 (transfer/achievement):** checklist-executable > checklist-prose on
  official-judge item pass rate. The theory predicts a positive gap;
  eval-2-sized [redacted: earlier runs] if testability binds here, eval-1-sized
  [redacted: earlier runs] if a capable agent can satisfy prose criteria unaided.
- **H2 (calibration):** per-item overclaim executable ≈ 0; prose > 0 if
  self-assessment of 12 fine-grained criteria over a large artifact is hard
  without a decidable test (eval-2 regime), ≈ 0 if agents stay honest
  [redacted: earlier runs].
- **H3 (mechanism):** verdict-only vs figures gap, sign pre-registered as
  *unknown* [redacted: earlier runs].
- **H4 (prose is not nothing):** checklist-prose > baseline (criteria
  knowledge alone helps — this gap is information, not testability).
- **Decision rule:** theory TRANSFERS if H1 gap > 0 with 95% CI excluding 0
  on the judged measure; SUPPORTED-WEAK if only the checker measure shows it;
  DOES-NOT-TRANSFER if H1 ≈ 0 while H4 > 0 (criteria content, not
  executability, is what mattered).

## 5. Validity guards (lessons from evals 1–3 baked in)

- **Answer-key isolation (eval-3's oracle-leak lesson):** trials run in a
  mkdtemp sandbox OUTSIDE the repo containing only `shared/`, `week/`,
  `TASK.md`, and condition extras. The rubric, grader configs, judge prompts,
  and published submissions never enter it; `LEAK_TOKENS` audit over the
  transcript invalidates any trial that reached them.
- **Tautology control (eval-1's shared-evaluate lesson):** primary outcome is
  the dataset's own LLM judge, not our checker.
- **Spec-ambiguity control (eval-1's baseline-denominator lesson):** the
  criteria the agent is judged on are the criteria it is shown, verbatim, in
  the checklist arms; anchor bands are stated numerically in both renderings.
- **Session economics (evals 1–2):** `claude -p` sessions with API keys
  stripped; cap detection marks capped trials as attrition
  (`session_capped`), excluded not failed. Budget ≈ 40 substantial trials
  per 5-hour window; a 4×8 design (32 trials) fits one window per rep-block.
- **Wrapper integrity:** verdict-only arm's tool is a wrapper over
  `_qa_impl.py`; direct `_qa_impl` invocation is traced
  (`qa_impl_direct_calls`) so mode contamination is measurable.
- **Offline fidelity:** the published scenario is offline-container; on the
  host we instruct + audit (`network_use`) rather than enforce. Flagged
  trials analyzed both ways.

## 6. Checker validation (offline, 2026-07-01)

Against the dataset's 6 published submissions the deterministic core
discriminates in the expected order (recalc via the `formulas` package was
required for some — openpyxl-written workbooks carry no cached values).
Spot-checked FAILs are real rubric misses (NZD-only presentation; UK
spellings; no explicit per-capita dietary driver; pasted-constants workbooks
with zero formulas).

## 7. Run plan

1. **n=1 pilot** (4 sessions, one per condition) [redacted: earlier runs].
2. Judge the pilot (`judge.py`, metered; ~$1–1.5/trial at Sonnet pricing).
3. If the pipeline holds: **4 conditions × 8 reps = 32 sessions** (one
   cap-window per ~10 trials of this size, so pace across windows with the
   eval-2-style resumable pattern if needed), then `report_briefcase.py`
   + judge + write-up.
4. Optional lesion later: a `broken-checker` arm (mis-anchored bands) to
   port eval-1's broken-model result.

---

## Package appendix (added 2026-10 for diff-bwm-1)

How this package differs from the design. Row ids refer to `docs/instrument-changes/eval4.md`.

1. **Information match.** No checker source enters any sandbox: the check runs harness-side with its mode sealed,
   and the runnable arms see only its printed output, so they hold no text the prose arm lacks (E4P-05).
2. **"Verbatim".** `CHECKLIST.md` is the checker's prose rendering, a paraphrase of the rubric that also states the
   numeric anchors resolving the planted contradictions; it is not the rubric text (E4P-38).
3. **Isolation.** Every arm runs in a container; the audit checks tool inputs, not substrings (E4P-03, E4P-04). The
   verdict arm holds no checker, so no lock is needed (E4P-06).
4. **Judge.** The judge runs with no tools in an empty directory (E4P-25).
5. **H2 grader.** Per-item overclaim is judge-referenced; the figure against the checker the arms can run is kept as
   `self_consistency_with_tool` (E4P-18). The checker itself under-specifies the rubric (E4P-39).
6. **Design.** The default regime is the pre-registered 32-session design (4 arms x 8 reps), one model per run; the
   optional `broken-checker` arm is not in the package. No arm's tool list holds a sub-agent tool (E4P-48), and
   capped attempts stay in the append-only attempt log (E4P-20).
7. **Dataset.** Pinned by revision and hash (E4P-01). New runs are a new series.
