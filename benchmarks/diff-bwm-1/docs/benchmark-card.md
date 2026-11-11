# Benchmark card: diff-bwm-1 1.0.0

## Summary

diff-bwm-1 is six evals that put a coding agent in a business-planning sandbox and vary one thing at a time: how
hard the world is to hold in mind, what the agent can query (nothing, an executable model of the world, a broken
model, its own model, a runnable checklist), and whether the decisive information is in the brief at all. Each
eval scores the agent's deliverable deterministically (or, for eval 4's primary measure, with an LLM judge panel)
and compares it with the agent's own claim of success. The question behind the suite is whether an executable
numerical model gives a planning agent what code gives a programmer: internal consistency, and a way to test a
draft before shipping it.

Maintainer: Differential AI Lab. Design: *NuModel: Executable-Model Grounding for LLM Planning Agents* (the paper).
Version: 1.0.0. Licences: MIT (code), CC-BY-4.0 (data, prompts, documentation). The package holds the problems
(`data/`), the answers (`private/`) and the code that runs and scores them.

## Intended use

- **Instrument research on grounded planning**: measuring what access to an executable model, a runnable check or
  missing causal information does to plan validity and to the honesty of an agent's claims, with the arms of each
  eval as the manipulation.
- **Auditing the instrument**: the problems, the answers and the scorers ship, with the scorer the paper used
  (`paper`), the scorer eval 5's sessions originally ran where it differs (`as-run`, eval 5 only) and the corrected
  scorer (`v1`).
- **Running new agents as a new series**, with the reference adapter (Claude Code), the `command` adapter or the
  `openai_compat` loop, and reporting them with the regime, ruleset and isolation level.

## Not intended for

- **Leaderboards or model rankings.** The shipped worlds are a contaminated development split (answer keys ship or
  regenerate) and no private held-out split is hosted yet; see [threat-model.md](threat-model.md#contamination).
- **Pooling** new runs with the paper's runs. The instrument changed.
- **Business, legal, regulatory, medical or financial advice.** All companies and scenarios are fictional.
- **Training data.** The canary string in the README and in every `private/evalN/CANARY.txt` marks the package.

## Metrics and denominators

Every rate is reported over **scored attempts** and over **designed cells** (a missing or unscored cell counts as a
failure), with Wilson 95% intervals. **Overclaim** (claimed success and failed) is reported **of all scored cells**
and **of failures**, with counts. Claims are read strictly in `v1` (JSON booleans; plain numbers); the paper's
truthiness reading is kept beside it.

| eval | success (`ScoreResult.success`) | primary measure | other measures | contrasts |
|---|---|---|---|---|
| 1 | plan fully valid: goal met on the plan's own trajectory and no constraint violated | valid rate and overclaim, by arm | forecast error; the paper's pre-registered decision rule (H1-H4, dose-response, mechanism) | bootstrap differences vs `ungrounded` |
| 2 | allocation fully valid | success over designed cells (the within-budget reading) | success of committed plans (the registered construct); overclaim among failures with a filed claim; forecast error | world-clustered bootstrap: ceiling (iterate - ungrounded), any-grounded, mechanism (figures vs verdict channels), with Fisher exact tests and Newcombe intervals beside it |
| 3 | allocation fully valid (eval 2's scorer) | achievement by arm | overclaim on both bases; declared-model rate; self-model fidelity | generative effect, grounding premium, testability effect, tool-form effect |
| 4 | the deterministic checker passes all 12 items | judged item-pass on the 12 graded rubric items, 3-pass majority (`panel_majority`, at least 2 valid votes); a session that ends without a workbook by design fails every item, an infrastructure crash is retried | checker item-pass and all-12; calibration against the judge (coarse and per item); qa_check calls; cost | H1 executable - prose, H3 executable - verdict, H4 prose - baseline (trial-clustered bootstrap) |
| 5 | all machine-checkable items pass | all-items pass by arm | item-pass; P(believes success given at least one failed item); key figures in band; optional ELO quality ladder | scenario-clustered contrasts under both denominators |
| 6 | D1: the plan's true profit reaches the certification target (derived; undefined in `v1` on a world whose target exceeds the marketing-bounded optimum) | D1: realized-profit uplift, aided vs unaided, under both no-plan policies | gap to M*; projection gap (`projected / true - 1`; under the `paper` brief a profit-definition gap, not calibration); D2: Kendall tau-b against V* for the ledger, the sort of the handed projection and LLM judges; D3: agreement of five self-supply sessions | world-clustered bootstrap |

## Arms and the information each receives

Every arm of an eval gets the same prompt body; the arms differ only in the tools and files listed. Full per-arm
tables, including exactly what each oracle channel returns, are in the eval cards.

| eval | arms (builtin tools) | what varies between arms | isolation |
|---|---|---|---|
| 1 | `ungrounded`, `grounded-readonly`, `grounded-iterate`, `prose-feedback`, `raw-figures`, `broken-model` (read, write) | the `run_model` oracle and its sealed channel: none; figures plus pass/fail (3 calls, or unlimited); pass/fail in words; figures only; figures from a corrupted model | `host` |
| 2 | `ungrounded`, `grounded-readonly`, `grounded-iterate`, `grounded-boolean`, `raw-figures`, `prose-feedback` (read, write) | the `world_query` oracle and its channel: none; localizing (figures, pass/fail, binding quarter, sensitivity, hint) with 3 or unlimited calls; booleans only; figures only; words only | `host` |
| 3 | `gen-figures`, `gen-both` (read, write), `gen-code` (read, write, bash), plus eval 2's `ungrounded` and `grounded-iterate` | where the model comes from: a self-declared DSL model evaluated by the harness (figures; figures plus a public-goal test), self-written code, none, or the true world | `host` for the DSL arms, `container` for `gen-code` |
| 4 | `baseline`, `checklist-prose`, `checklist-verdict`, `checklist-executable` (read, write, edit, bash, glob, grep) | no checklist; the checklist as prose; the same checklist plus a runnable check printing PASS/FAIL per item; the same check also printing what it measured | `container` |
| 5 | the same four arms as eval 4 | as eval 4, on four consulting scenarios | `container` |
| 6 | `unaided`, `aided` (read, write, edit, bash, glob, grep); D3's non-canonical `self-supply` | the aided arm also gets a calibrated model package: the coupling and price signs, noised magnitudes, a nowcast of the latent state and a simulator; D3 gives every session the unaided inputs and asks for the structure and pinned-plan forecasts | `container` |

Caveats that change what a contrast means:

- **Eval 4 and eval 5 checklists carry answers.** Eval 4's `CHECKLIST.md` states the numeric anchors that resolve
  the planted contradictions; eval 5's states an in-band value for every banded check, and in `v1` also each check's
  pass condition, identically in the three checklist arms. Checklist-vs-baseline is therefore partly an
  answer-disclosure contrast; prose vs runnable is the clean comparison.
- **Eval 1 readonly vs iterate** differ by one prompt sentence and the 3-call budget.
- **Eval 6's aided model** is the paper's design with the world seed and history parameters removed. In `v1` both
  arms' briefs disclose margins and fixed costs, and marketing is bounded at the history's per-segment maximum, so `v1` uplift differs from uplift under the `paper` scorer, which leaves marketing unbounded.

## Known failure modes

Of agents, which the evals are built to expose:

- **Overclaiming** when the agent cannot run a trusted check: stating a success the scorer refutes (evals 1 and 2),
  or claiming a self-built model's failure as a success (eval 3).
- **Trusting a broken model** (eval 1's lesion arm).
- **A consistent but wrong self-built model**, which passes its own test (eval 3).
- **Rebuilding the oracle from the brief**: a capable agent can reconstruct a disclosed, deterministic model, so
  arms that differ only by tool access can converge.
- **Treating a pretty deliverable as done**: a high checker score with a lower judged score (eval 4).

Of the instrument, closed or documented here:

- leaks through the file system, override flags, staged grader source and judges with file access
  ([threat-model.md](threat-model.md));
- budgets whose bite depends on the model that runs (a dollar budget and a clock; [regime.md](regime.md));
- scorer defects that fail correct answers or pass wrong ones (`paper` vs `v1`, per eval);
- LLM judges whose ranking can change with presentation order (eval 5's ELO reports both orders), and judges
  handed a projection they were told was crude (eval 6's D2);
- defaults the oracle applies that the brief does not state (eval 2 and 3's do-nothing levers; eval 6's profit
  definition).

## Out of scope

- Whether agents learn over time; whether they build world models from raw data outside eval 3's setting; real
  companies; horizons other than the eval's; production cost.
- Human baselines: none exist for any eval.
- Generalization beyond the shipped synthetic distribution and one third-party task.

## Ethical considerations

All companies, people and scenarios in the authored data are fictional, and the eval-5 scenarios carry a
fictional-entities statement; real standards bodies, programs and vendors appear only in neutral roles. Eval 5's
data was renamed and paraphrased for hygiene before release, and has had no formal legal review. AA-Briefcase-Lite's
source pool contains real third-party documents; it is fetched from its publisher, not redistributed. The package
contains no personal data. In `container` runs an agent's only network peer is the sidecar: its API relay forwards
pinned-model requests, and its proxy tunnels only allowlisted hosts and logs every request.
