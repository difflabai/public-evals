# Run regimes

A *regime* is everything that defines a run: model, arms, instances, reps, budgets, isolation, auth mode, ruleset,
adapter, order seed, judge, and any `--regime-extra` settings (for example eval 5's pass-condition opt-out). It is built from the eval's `dbwm/evalN/regime.json` plus command-line overrides
(`dbwm.core.regime`), validated, hashed (`regime_hash`), and written into the run manifest. Resuming a run with a
different regime hash is refused.

**Every per-eval default below is provisional pending a calibration pilot.** Only smoke-sized live sessions (ten
short sessions at `host`, `--max-turns` 8 to 20) were run while the package was built, so the turn budgets are set
generously, not calibrated on runs of this harness.

## Planning a run

`dbwm run ... --dry-run` prints the plan and exits without launching or writing anything: cells per arm, the first
launches of the seeded order, the regime hash, isolation and conformance (with the reasons for a non-conforming
run), the per-attempt rails, and the dollar exposure (cells x USD rail, and with every retry). `--max-total-usd X`
adds a run-level rail: no new attempt starts once the run's attempts cost X (manifest `max_total_usd`,
`spent_usd`); it is a run option, not part of the regime hash. Both are .

A run is **conforming** when every arm runs at or above its minimum isolation, no `command` agent runs at `host`
(nothing confines it), and the API relay is on for `api_key`/`oauth_token` at `container`. The
manifest, every verdict, `report.md` and the dry-run plan record the conformance flag and its reasons; only
conforming runs with `cli_version_verified: true` are evidence under this document.

## How budgets work

- **Agent turns are the primary budget.** `--max-turns K` is passed to Claude Code (result
  `subtype: "error_max_turns"`), and the harness kills the agent's process group if it sees K+2 distinct assistant
  message ids, whatever the adapter. An attempt that runs out of turns is scored; a missing deliverable is a fail.
- **Wall clock and dollars are rails.** `--wall-clock-s` and `--usd-rail` (passed as `--max-budget-usd`) stop a
  runaway attempt. An attempt stopped by a rail is **scored** (no deliverable = fail) and **never retried**.
- **Only infrastructure outcomes are retried**: API errors, invalid-regime assertions and crashes, up to
  `--max-attempts` (default 3) per cell, every attempt kept. Auth failures and usage caps halt the run instead of
  using up attempts (see [runbook.md](runbook.md)).
- **No model default.** The runner refuses to start without `--model`. The served model is checked against the
  pin on every assistant message.
- **Effort** is unset unless `--effort` is given, and is then part of the regime hash. (The old harness let effort
  and output-token variables leak in from the operator's environment; instrument change .)

## Why turns and not dollars

The paper pinned a dollar budget and a clock as its proxy for equal effort per condition. How hard such a proxy
binds depends on which model runs, so the package pins the iteration budget, lets cost float and records it as an
outcome, and screens at the budget the full matrix will use. Cost is recorded in every verdict (`process.cost_usd`)
and reported per arm.

## Per-eval defaults (`dbwm/evalN/regime.json`)

| | eval 1 | eval 2 | eval 3 | eval 4 | eval 5 | eval 6 (D1) | eval 6 D3 (non-canonical) |
|---|---|---|---|---|---|---|---|
| default arms | 6 | 6 | 5 (3 generative + eval 2's `ungrounded`, `grounded-iterate`) | 4 | 4 | 2 | 1 (`self-supply`) |
| default instances | 15 (5 companies x 3 tiers) | 5 worlds | 3 worlds | `w1_t2` | 4 scenarios | 11 worlds | 8 worlds (`w1`-`w7`, `w9`) |
| reps | 3 | 8 | 6 | 8 | 4 | 3 | 5 (the gate needs five reports per world) |
| designed cells | 270 | 240 | 90 | 32 | 64 | 66 | 40 |
| `max_turns` (primary) | 30 | 40 | 40 | 80 | 80 | 60 | 60 |
| wall-clock rail (s) | 2400 | 3600 | 3600 | 3600 | 3600 | 2400 | 2400 |
| USD rail | 2.0 | 8.0 | 8.0 | 8.0 | 8.0 | 4.0 | none (cap the run with `--max-total-usd`) |
| isolation | `host` | `host` | `container` (`host` for the no-bash arms only) | `container` | `container` | `container` | `container` |
| ruleset | `v1` | `v1` | `v1` | `v1` | `v1` | `v1` | `v1` |
| oracle query budget | 3 for `grounded-readonly` | 3 for `grounded-readonly` | none | none | none | no oracle | no oracle |
| other | | | per-command bash limit 120 s (`gen-code`) | judge `claude-sonnet-4-6` x 3 passes, `claude_cli`, min 2 valid votes | judge `claude-sonnet-4-6` (ELO pass only); `CHECKLIST.md` states pass conditions (opt out with `--regime-extra`, `{"eval5": {"checklist_pass_conditions": false}}`) | D2 judge model is the treatment, given per command | run with `--eval dbwm.eval6.d3`; never run live |

`ruleset` is the scorer a run's verdicts use; `dbwm score --ruleset` re-scores a finished run under another one the
eval defines. Every eval defines `paper` (the scorer the paper used, bit for bit) and `v1`; eval 5 also defines
`as-run` (registry v0.1 as its sessions originally ran); any other ruleset is refused with a message naming the
eval's rulesets.

Defaults common to every eval (`RegimeConfig`): adapter `claude_code`, auth `session`, concurrency 1, order seed 0,
3 attempts per cell.

Where each budget comes from (the notes inside each `regime.json`). Every `max_turns` is set generously so that it
is not the binding constraint; rails are scored, never retried.

- **Eval 1, 30.** The 2400 s rail is the paper's extended backfill timeout, and the USD rail is the paper's
  `--max-budget-usd 2`. The 3-call readonly budget is new: the paper enforced none.
- **Eval 2, 40.** The 3600 s rail sits well above the paper's 1500 s clock.
- **Eval 3, 40.** The 3600 s rail sits above the paper's 1800 s and 1500 s clocks. The 120 s per-command limit is
  stated in the `gen-code` prompt and enforced by the runner (`EVAL.command_timeout_s`: Claude Code's
  `BASH_DEFAULT_TIMEOUT_MS` and `BASH_MAX_TIMEOUT_MS`, background tasks disabled; openai_compat's `bash_timeout_s`).
- **Eval 4, 80.** The rails are the paper's $8 and 3600 s.
- **Eval 5, 80.** The rails are the paper's 3600 s timeout and $8.
- **Eval 6, 60.** The rails are 2400 s (twice the paper's 1200 s) and the paper's $4.
- **Eval 6 D3, 60.** No per-attempt USD rail is set on D3 (cap the run with `--max-total-usd`); the clock is 2400 s,
  twice the original harness's inner 1200 s timeout.

## Calibration pilot (do this before any batch you intend to report)

1. **Cells.** Per eval, 3 cells: the hardest default instance, crossed with the control arm, the most tool-intensive
   arm, and one other arm, one rep each, with the model you will report. Use the eval's default rails and
   `--max-turns` at twice the default.
2. **Record**, per cell, from the attempt directory: whether it committed the deliverable, the turn of the first
   deliverable write, total turns (`verdict.process.turns`), cost and wall clock.
3. **Decide.** If fewer than 2 of the 3 cells commit, do not run the batch: the regime is budget-floored. Raise the
   rails or the turn budget and pilot again. Otherwise set K to at least 1.5 times the largest turns-to-commit
   observed, and not below the default.
4. **Pin.** Add a row to "Pinned regimes" below (eval, model, effort, K, rails, isolation, auth mode, CLI version,
   pilot run ids) **before** launching the batch, and pass every value explicitly on the command line (check it
   with `--dry-run`, and set `--max-total-usd` from the exposure it prints). Pilot cells are not part of the batch
   and are not reported.

## Pinned regimes

None yet. Each row is added before its batch runs.

| date | eval | model | effort | K | wall clock / USD | isolation | auth | Claude Code | pilot run ids |
|---|---|---|---|---|---|---|---|---|---|

Runs of this package are a new series under any regime.
