# Eval 4: AA-Briefcase-Lite transfer (w1_t2, `market_model.xlsx`)

Does a runnable QA checklist help an agent build a better spreadsheet than the same checklist handed over as
prose? Eval 4 asks this on a third-party task, outside the benchmark's own synthetic worlds: Artificial
Analysis's AA-Briefcase-Lite, week 1 task 2. The agent gets one week of private-equity commercial diligence on
a fictional New Zealand egg producer and must build `market_model.xlsx` (historical market sizing, a five-year
forecast, cage-ban transition scenarios) from a pool of source files with planted contradictions.

Package: `dbwm/eval4/`. Agent-visible data: `data/eval4/` (fetched). Scorer and judge only: `private/eval4/`
(fetched, plus `CANARY.txt`). Tests: `tests/eval4/`. What changed from the paper's harness:
[`docs/instrument-changes/eval4.md`](../instrument-changes/eval4.md).

## The task and the dataset

- **Dataset.** `ArtificialAnalysis/AA-Briefcase-Lite` on Hugging Face, Apache-2.0, pinned to revision
  `4dec557b47d43867a1648c0974db1d8208c8b677` (2026-06-19). Every eval-4 run in the paper used this
  revision. The package never redistributes the dataset, because its source pool contains real third-party
  documents. `dbwm fetch eval4` downloads the 166 files eval 4 needs and checks each one against the sha256
  manifest `dbwm/eval4/aa_manifest.json`. The manifest was cross-checked against the Hugging Face tree at that
  revision: LFS sha256 for LFS files, git blob sha1 for the rest, with 0 mismatches. Use
  `dbwm fetch eval4 --from-dir DIR` to install from a local copy (each file is still verified) and
  `--verify-only` to re-check an installed copy. Both target directories are gitignored.
- **Split.** `data/eval4/aa-briefcase-lite/` (agent-visible) holds the 147 source files, the two summary docs
  and the `w1_t2` brief. `private/eval4/aa-briefcase-lite/` (scorer and judge only) holds `checks.jsonl`,
  `tasks.jsonl`, `README.md`, `grader/`, the four prompts and the six published `w1_t2` submissions.
- **Source pool.** 147 files, staged as `shared/` (130 files, including an 81-file Slack export) and
  `week/` (17 files), exactly as the paper staged them.
- **Deliverables.** `market_model.xlsx`, plus `claims.json` (a self-assessment):
  `believes_meets_requirements`, three `key_figures` (`bottom_up_market_size_nzd_m`,
  `aurora_volume_share_pct`, `fy26_free_range_premium_pct`), a `rationale`, and for the checklist arms a
  per-item `checklist_self_assessment` and a `qa_runs` count.
- **The rubric, precisely.** At the pinned revision, `checks.jsonl` has **63 rows** covering all four week-1
  tasks:

  | task | binary | pairwise | total |
  |---|---|---|---|
  | w1_t1 | 11 | 2 | 13 |
  | w1_t2 | 12 | 2 | 14 |
  | w1_t3 | 22 | 2 | 24 |
  | w1_t4 | 10 | 2 | 12 |
  | **total** | **55** | **8** | **63** |

  The paper's "published 63-check rubric" is this whole file. **Eval 4 grades only the 12 binary `w1_t2`
  rows**, W1-T2-A-01..A-09 and W1-T2-C-01..C-03. The 2 pairwise `w1_t2` rows (AQ-01, P-01)
  are never used. These counts are asserted by `tests/eval4/test_eval4_evaldef.py`.

## Arms and what each one gets

Every arm needs bash, because the agent has to script the workbook with python3 and openpyxl. Every arm
therefore has `min_isolation = CONTAINER`. `host-unsafe` runs are recorded as non-conforming.

| arm (paper name) | staged in the sandbox | runnable check | tools |
|---|---|---|---|
| `baseline` | TASK.md + 147 sources | none | read, write, edit, bash, glob, grep |
| `checklist-prose` | + CHECKLIST.md | none ("there is no tool to run it") | same |
| `checklist-verdict` | + the same CHECKLIST.md + the `./qa_check` CLI shim | `qa_check`, sealed mode `verdict`: `CHECKLIST RESULT: n/12 PASS` and one PASS/FAIL line per item | same + oracle `qa_check` |
| `checklist-executable` | + the same CHECKLIST.md + the `./qa_check` CLI shim | `qa_check`, sealed mode `figures`: the verdict output plus each item's criteria note, measured values and a consistency line | same + oracle `qa_check` |

- **TASK.md** is the paper's text (`briefcase_planner.build_task_md`). It holds the dataset's scenario and
  week overviews, the `w1_t2` brief, the deliverables spec and the claims schema. The only change is in the
  two tool arms: the paper said "`qa_check.py` ... `python3 qa_check.py market_model.xlsx`", and v1 says
  "`qa_check` ... `./qa_check` (it checks `market_model.xlsx` in this directory)". Run with
  `style="paper"`, `build_task_md` gives the paper's text.
- **CHECKLIST.md** is identical in the three checklist arms. Its bytes equal the paper's `checker.py --mode
  prose` output (sha256 `71c8e823...`). It paraphrases the rubric and states the numeric anchors that resolve
  the planted contradictions (limitations, E4-11).
- **No checker source in any sandbox.** In the paper, both tool arms got the full checker as `_qa_impl.py`:
  token lists, thresholds, regexes, rubric ids, and the statement that the official grade is an LLM judge. The
  prose arm got none of this, which is the disclosure asymmetry of E4-09 (E4P-05). In v1 the check runs in the
  harness (`dbwm/eval4/oracle.py`) on a private copy of the sandbox's `market_model.xlsx`. Its mode comes from
  the arm and is sealed in the oracle server. Payload keys such as `mode` or `path` are ignored, and the server
  logs them as override attempts. The agent sees only the CLI shim and its printed output.
- **First-turn prompt** (all arms): "Read TASK.md in the current directory and complete the task it
  describes. Work only inside the current directory." This is unchanged from the paper.

## Scoring

### Deterministic (`score()`, every attempt)

The checker (`dbwm/eval4/checker.py`) is the paper's 12-check registry. Its checks, criteria, thresholds and
finders are source-identical to the paper's, and it is never staged. K-checks map onto rubric items as follows:
K-SCEN to A-01 and A-09, K-INTEG and K-TRACE both to A-07, every other check 1:1 (`checker.K_TO_RUBRIC`).

- `success` is all 12 K-checks passing, the paper's secondary "all-12" measure. A missing workbook is a fail.
- `claimed_success` is `believes_meets_requirements`, parsed strictly through `dbwm.core.claims`.
  `claimed_success_paper` is the paper's `bool()` reading of the same field.
- `overclaim` means claimed success while the checker fails at least one item. It measures **consistency
  with the provided tool, not calibration**: the runnable arms can run the very checker it is measured
  against (E4-08). The verdict details keep the paper's per-item numbers under `self_consistency_with_tool`.
- Formula recalculation: openpyxl-written workbooks carry no cached formula values, so the checker
  recalculates them with the optional `formulas` package (`pip install 'diff-bwm-1[recalc]'`, pinned
  `formulas==1.3.4`, `schedula==1.6.15`). Every verdict records `recalc_status` (`not_needed`, `ok`, `empty`,
  `failed`, `unavailable`, `rejected_external_links`).
- External workbook references (E4P-45, red-team finding RT-X). `formulas` follows references to other
  workbooks: `xl/externalLinks` parts, inline `'[book.xlsx]Sheet'!A1` and absolute references, defined names and
  `INDIRECT(...)`, literal or computed. A workbook with any of these is refused by `qa_check` ("refers to another
  workbook") and never recalculated; under `v1` it fails with `details.external_workbook_refs`, under `paper` its
  visible values are scored as the paper would have. The checker also runs `formulas` through a single-book model
  that refuses any second book, discards a solution that touched one, and the scorer evaluates a copy of the
  workbook in an empty temp directory.

Differences between the two rulesets:

| | `paper` | `v1` |
|---|---|---|
| claim of success | `bool(believes_meets_requirements)`, so the JSON string `"false"` counts as a claim | JSON booleans only. Anything else gives `claims_parse_status = error` and no claim |
| invalid `claims.json` | silently treated as no claim, so not an overclaim | `claims_parse_status = error`, reported |
| missing workbook | no checker result, so `overclaim` is None | fail. A claimed success counts as an overclaim |
| workbook unreadable by the checker | not all-12 | unscorable (`success = None`), reported |
| uncached formulas, `formulas` not installed | scored without the computed outputs | unscorable, with a reason |
| uncached formulas, `formulas` installed but broken here (e.g. scipy cannot load) | scored without the computed outputs | unscorable, with the cause |
| uncached formulas the workbook itself makes unrecalculable (prose after `=`) | scored without the computed outputs | the same, plus `details.recalc_note` |
| key figures | in-band check only if the value is a JSON number | strict numbers (`$`, `,` and `~` stripped; unit suffixes are errors), in-band, and present in the workbook |

### Primary: the dataset's rubric, judged (`dbwm eval4-judge --run DIR`)

After a run, `judge_run` (`dbwm/eval4/judge.py`) grades every scored, non-excluded attempt against the 12 rubric
rows. It uses the dataset's own `judge_system.txt` and `judge_user.txt` and the paper's workbook serialization
(values with recalculated formulas, row-numbered cells per sheet, a formatting summary, a cap at 160,000
characters). One call is made per rubric row per pass, with **3 passes** by default. The default judge is
`claude-sonnet-4-6`, the paper's judge model.

The judge runs through the `dbwm.core.judge` adapters. `claude_cli` runs `claude -p --tools "" --restricted ...`
in an empty temp directory, saves the stream, and treats any tool use as a judge_error. `anthropic_api` and the
offline `mock-pass` / `mock-fail` adapters are also available. A judge_error is retried. If it persists, the
item is missing, never a fail. Per item, the panel verdict is `panel_majority(votes, min_valid=2)`: a tie or
fewer than two valid votes is a judge_error. A session that stopped without a workbook for a design reason (the
turn budget, a rail, or not submitting) fails every item (intention-to-treat over design stops only). A session
that ends on an infrastructure error (an API error result) is classified `infra_api` by the core and retried, so
a new run never scores a crash as 0/12 (E4P-21).

`EVAL.report(run_dir)` gives:

- judged item-pass per arm, under two denominators: scored cells only, and designed cells where a missing or
  unscored cell fails every item;
- Wilson intervals;
- two-sample trial-clustered bootstrap CIs for H1 (executable − prose), H3 (executable − verdict) and H4
  (prose − baseline);
- coarse and per-item overclaim measured against the judge (the per-item rate excludes K-checks mapped to
  A-07);
- checker figures, qa_check calls, turns, cost and recalc status.

## Regime (provisional; `dbwm/eval4/regime.json`)

- 4 arms × 8 reps on `w1_t2`, i.e. 32 cells (the pre-registered design).
- `max_turns` 80, the primary budget.
- Rails: wall clock 3,600 s and USD 8, both from the paper.
- Isolation `container`; ruleset `v1`.
- Judge `claude-sonnet-4-6` × 3. There is no query budget on `qa_check`, and the paper had none either.

These defaults are provisional pending a calibration pilot (docs/regime.md). The runner refuses to start
without `--model`. New runs are a **new series**: different sandboxes, a sealed harness-side check, an
isolated judge, and a turn budget instead of a dollar budget. They are not comparable to the paper's runs.

## Limitations

- **Anchors in the checklist (E4-11).** CHECKLIST.md tells every checklist arm the numeric anchors that resolve
  the planted contradictions, so "prose vs baseline" measures being told much of the answer key. This is by
  design, and it is kept for comparability.
- **The checker under-specifies the rubric (E4-10).** It does not measure A-07's formatting categories,
  legend and consistency, the C-02 calculation chain, or the C-03 per-tier downside, so a runnable arm can pass
  every check and still fail those rubric items.
- **Baseline is not the AA leaderboard condition (E4-12).** The harness, budget and tooling all differ.
- **One judge model.** The judge is one model × 3 samples, not the dataset's published multi-model protocol
  (E4-05), and it may be weaker than the agents it grades.
- **Recalculation depends on `formulas`.** Its results depend on the EUPL-licensed `formulas` package version. A
  workbook that raises inside `formulas` is scored on its cached values only, as in the paper. "Working" matters:
  `formulas` imports even when its scipy dependency cannot load, and then every formula that names a function
  fails. `dbwm selftest --eval 4` runs a two-cell recalculation smoke and reports such an install as "INSTALLED BUT
  UNUSABLE" with the cause; the tests skip the recalculation checks with the same cause (fix the environment, e.g.
  reinstall scipy).
- **The dataset is fetched, not redistributed.** If the Hugging Face repository disappears, eval 4 cannot be
  rebuilt without a mirror. The sha256 manifest pins exactly which bytes are needed.
- **CONTAINER verification.** No API key or OAuth token exists on the build machine, so container end-to-end
  runs were verified only with the fake `claude` in the real image (`tests/integration/test_integration_container.py`
  runs the checklist-executable stub through the shell and `./qa_check` against the sidecar) and the API relay with
  the real CLI against a local stand-in for the API; no live model has run eval 4 under this package.

## Commands

```
dbwm fetch eval4                       # download + verify the pinned dataset subset
dbwm selftest --eval 4                 # offline instrument checks
dbwm run --eval 4 --model <M> [--arms ...] [--reps 8]      # agents (container isolation)
dbwm eval4-judge --run runs/<id> [--adapter claude_cli]     # the judge panel (primary measure)
dbwm report --run runs/<id>
```
