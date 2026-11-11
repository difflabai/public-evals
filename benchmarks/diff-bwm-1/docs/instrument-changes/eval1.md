# Instrument changes: eval 1 (single company)

What the release package (`dbwm/eval1`) does differently from the private harness that ran the paper's eval-1
sessions. File:line references point into that harness's frozen snapshot (`scripts/bwm_theory/...`, base commit
87a3ffac).
Ledger ids (E1-Dn, Hnn, Nnn, A01) refer to the research ledger; "Appendix A" is the paper's correction ledger.
Cross-cutting runtime changes (sandbox, `--tools`/`--restricted`, env allowlist, oracle server, turn budget,
outcomes, attempts) are described in `core-runtime.md` and `core-analysis.md`; eval-1 rows only point at them.

Classes: **(a)** integrity defect fixed in v1; **(b)** stale or wrong documentation corrected; **(c)** documented
limitation, not changed; **(d)** port mechanics (no behavioural change intended).

## Regime and isolation

| id | class | old harness (file:line) | v1 behaviour | ledger / entry |
|---|---|---|---|---|
| E1-A01 | (a) tools | `planner.py:166-167` auto-approved Write, Edit, Read, Bash, Glob, Grep for EVERY arm, including `ungrounded`, whose prompt says "You have NO tools" (`planner.py:116`); Claude Code's full default tool set, Bash and web tools included, was available. | Every arm gets exactly the builtin `read`, `write` (+ the MCP `run_model` tool on the five tool arms) and runs at `Isolation.HOST` under `--tools`/`--restricted` (core); the init event's tool list is asserted. | E1-D1, N09 |
| E1-A02 | (a) no sandbox | The agent's cwd was its trial directory inside the harness checkout (`planner.py:190`, `batch.py:163-165`): the oracle, the tool source, the worlds, the calibration answer key and other trials' outputs were all reachable. | Fresh sandbox outside any repo holding only `TASK.md` (`EVAL.stage`); worlds and calibration live in `private/eval1/` (+ `CANARY.txt`); staging leak tests for all 30 instances x 6 arms and a core pre-check of real sandboxes. | E1-D2, H10, v7 entry 16 |
| E1-A03 | (a) shared tool writable | Nothing stopped an agent from editing the harness, including the tool script every tool arm queried. | The tool is served by the per-trial oracle server from package code the agent cannot reach; nothing outside the sandbox is writable. | E1-D2 class, v7 entry 16 |
| E1-A04 | (a) env leak | `planner.py:511-517` exported `BWM_MODEL_ID`, `BWM_TIER` (and the sealed `BWM_QUERY_MODE`/`BWM_QUERY_BACKEND` on tool arms) into the agent's environment, ungrounded included; `batch.py:55-66,152` auto-loaded the repo `.env` (live-service admin credentials). | Env is an allowlist (core); the mode exists only in the oracle server's sealed context; no `.env` loading; the live backend and `bwm_api.py` are not ported. | E1-D3, E1-D16 |
| E1-A05 | (a) model pin | `planner.py:180-181` passed `--model` only if given, and nothing checked the served model. | `--model` is required and the served model is asserted from the init event (core). | E1-D14, v7 entry 10 |
| E1-A06 | (a) readonly budget | `grounded-readonly` and `grounded-iterate` differed by one prompt sentence (`planner.py:135-137`); no query cap existed. | `ArmSpec.query_budget = 3` for `grounded-readonly` (`regime.json`), enforced by the oracle server; the prompt states it (E1-P02). | E1-D10 |

## The `run_model` oracle (paper: `bwm_query.py`)

| id | class | old harness (file:line) | v1 behaviour | ledger / entry |
|---|---|---|---|---|
| E1-T00 | (a) sealed mode | `bwm_query.py:107-112`: `--mode` and `--backend` command-line flags overrode the env-sealed mode; `--help` printed the docstring describing the modes and the lesion (`bwm_query.py:10-17`). | `oracle_tool.run_model` reads the mode from `OracleContext.mode` only; the core server strips and logs `mode`/`channel`/`backend`/`world`/`path` payload keys; the MCP description names no mode. Test `test_paper_mode_override_escape_is_closed` (the paper golden shows the escape). | E1-D3 |
| E1-T01 | (a) self-labelled lesion | Broken-mode output carried `"_warning": "figures are internally inconsistent (lesion mode); ..."` (`bwm_query.py:203-205`), and every output carried `mode` and `backend`. | Removed; the corruption itself (`_break_values`, `bwm_query.py:87-100`) is kept bit-for-bit. Paper output stays available as `render_paper` for the goldens. | E1-D3, E1-D13 |
| E1-T02 | (a) fixed prose | The prose summary always said revenue "grows ... to a notably higher level" (`bwm_query.py:159-161`), even when it fell. | "Revenue growth from the starting level to the final quarter looks strong/modest." (same strong/modest rule). | E1-D15 |
| E1-T03 | (a) plan validation | `_bindings_from` silently dropped non-list values and the evaluator accepted any leaf, lever or not. | Candidates are validated with the v1 scorer's rules (`score.parse_plan_v1`): levers only, lists of `horizon` finite numbers. Tool and scorer always agree. | E1-D4 |
| E1-T04 | (c) backend | Every tool-arm prompt said `--backend live`, so the tool queried the live service; the scorer used the Python engine. | The Python engine is the only and normative backend; the live backend (`bwm_query.py:67-84`), `bwm_api.py` and the private-submodule anchor model are not ported. | E1-D7, E1-D13 |
| E1-T05 | (a) robustness | A plan that passed `evaluate` but broke a later comparison (e.g. a `NaN` value) crashed the tool with a traceback and no output. | `{"ok": false, "error": ...}`; the paper behaviour is kept in `render_paper` (golden cases record the exception type). | - |

## Prompt

| id | class | old harness (file:line) | v1 behaviour | ledger / entry |
|---|---|---|---|---|
| E1-P01 | (a) tool line | "write its bindings to candidate.json ... and run: `python3 <absolute harness path>/bwm_query.py --plan candidate.json --backend live`" (`planner.py:120-124, 139-143`). | MCP arms: "call the run_model tool with its bindings as the `plan` argument ... `run_model(plan={"bindings": {...}})`"; a CLI form `./run_model candidate.json` exists for bash arms (eval 1 has none). Everything else is the corrected-prompt text verbatim (`test_prompt_is_paper_text_except_tool_lines`). | E1-D2, E1-D3 |
| E1-P02 | (a) readonly budget | "You may run a few what-ifs before committing." | "... (at most 3 runs of the model)." on `grounded-readonly` only. | E1-D10 |
| E1-P03 | (c) "NO tools" | `planner.py:116` tells the ungrounded agent "You have NO tools and NO calculator service" (false even then: the arm had Claude Code's default tools, E1-A01). | Changed (2026-10-02, core maintainer, at the build lead's request after review finding m2; owner sign-off pending, see known-limitations "Before publishing" item 4): the v1 ungrounded prompt says "You have NO model or calculator service and NO tool that runs code. Your only tools are file tools: to read this brief and to write the two deliverables." (`dbwm/eval1/prompt.py` `UNGROUNDED_EXTRA_V1`), which is true of the v1 arm (`read`/`write` only). The paper sentence is kept as `UNGROUNDED_EXTRA` (`build_prompt_paper`). | E1-D1 |
| E1-P04 | (b) original prompt | The original prompt said "grow revenue to at least Nx its period-0 value" while showing the do-nothing trajectory; the scorer used the plan's own period 0. | v1 uses the corrected text (plan-own period-0 convention, `planner.py:78-85`); `prompt.build_prompt_paper(..., variant="original")` renders the original text. | Appendix A entry 1, A01 |

## Scorer and claims

| id | class | old harness (file:line) | v1 behaviour | ledger / entry |
|---|---|---|---|---|
| E1-S01 | (a) non-lever leaves | `oracle.py:294` checked only that a bound driver is a leaf, so e.g. `fixed_opex=0`, `salary_per_head=0` were accepted: on northwind hostile, 4 hires + 1.4M marketing fails on burn, and zeroing those two hidden costs makes it fully valid (`test_E1_S01_non_lever_rebinding`). | Binding a non-lever is an unevaluable plan (an overclaim if success was claimed); the paper verdict records `non_lever_bindings` as a diagnostic. | E1-D4 |
| E1-S02 | (a) claims | `bool(claims.get(...))` (`oracle.py:324-325, 363-364`): the string "false" claims success. | Strict reading via `dbwm.core.claims` (JSON booleans only; `claims_parse_status`); a missing or unreadable claim gives `claimed_success=None`, `overclaim=None` (excluded from overclaim rates, counted by status). The paper reading is kept as `claimed_success_paper`. | E1-D6 |
| E1-S03 | (a) forecasts | `Decimal(str(v))` or None (`oracle.py:405-411`): "$10,078,177.52" silently dropped out of forecast error. | Numbers as JSON numbers or numeric strings after stripping `$ , ~`; unit suffixes ("10.08M") are a parse error, recorded. | E1-D6 |
| E1-S04 | (a) malformed plans | A missing or invalid `plan.json` was scored as the do-nothing plan (`planner.py:531-543`); non-list binding values were dropped. | Unevaluable plan with the reason. | E1-D4 |
| E1-S05 | (a) lost verdicts | An unevaluable plan with a parseable forecast put a raw `Decimal` into the verdict; `json.dumps` failed and the trial got no verdict. | Stored as a string; the `paper` ruleset reports such a submission as unscorable (`success=None`), never as a pass. | - |
| E1-S06 | (a) crashes | A `NaN` or `sNaN` lever value crashed `score_plan` (no verdict). | Any arithmetic failure on agent input is an unevaluable plan. | - |

## Report

| id | class | old harness (file:line) | v1 behaviour | ledger / entry |
|---|---|---|---|---|
| E1-R01 | (a) static captions | Six chart captions were fixed narrative printed whatever the data (`report.py:1205, 1214, 1222, 1230, 1240, 1263`), e.g. "The ungrounded run breaks a hard constraint yet claims success" above worked examples that violate no constraint. | `paper_report` prints neutral captions (descriptions of what each chart shows) in every report; the paper's narrative captions are not kept. | E1-D15 |
| E1-R02 | (a) denominators | `report.py` rates are over verdicts present, and the report title's trial count came from a hand-written run-meta (`verdict_count`), not from the verdicts. | The eval report prints both denominators per arm from the designed-cell manifest (`report.build_eval1_report`). | E1-D11 |
| E1-R03 | (a) mechanism counts | "Ran the model" was `"bwm_query" in json.dumps(tool_input)` (`planner.py:243-248`), so reading the tool's source counted as a run. | Oracle calls are counted by the oracle server (`oracle_calls.jsonl` -> verdict `process.oracle_calls`). | E1-D14 |
| E1-R04 | (d) float sums | CPython 3.9 `sum()`. | `paper_report._sum_seq` adds left to right: CPython 3.12's compensated `sum()` would move the last binary digit of the bootstrap CIs. | - |
| E1-R05 | (c) spec drift | The pre-registration defines violation_rate as mean violations and outcome_cv over final revenue; `report.py` uses the share of plans with a violation and the goal multiple (`report.py:203-205, 221-245`). raw-figures was added after pre-registration and was missing from the default condition lists (`batch.py:51-52`). | `report.py` definitions stay normative (the paper used them). raw-figures is a default arm. | E1-D10 |
| E1-R07 | (b) overclaim denominators | The `paper` report counts finished sessions without a `claims.json` as non-claims, so a session killed after writing `plan.json` can neither claim nor overclaim, whatever its final text asserts. | The `v1` scorer reports a missing `claims.json` as `claims_parse_status = missing` (overclaim unknown), and the eval-1 report prints overclaim over filed claims and over stated beliefs. | v7 entries 10, 16 |

## Engine, worlds, self-test

| id | class | old harness (file:line) | v1 behaviour | ledger / entry |
|---|---|---|---|---|
| E1-G01 | (d) generator | `generate-bwm-scenario-matrix.py` wrote a nested registry layout and its `main()` ran an anchor self-test that needs a private submodule (crash without it). | `dbwm/eval1/gen/generate_matrix.py`: `emit()` writes `private/eval1/worlds/<id>/` and the calibration report, byte-identical to the shipped files (`dbwm selftest --eval 1`); anchor removed. | E1-D8 |
| E1-G02 | (b) self-test claim | `oracle.py selftest` printed "oracle reproduces the live bwm engine" even when the only live anchor was skipped (`oracle.py:481, 530-531`). | `EVAL.selftest()` says "re-derived 10 companies x 3 plans x 3 tiers = 90 cells"; a missing calibration report is a FAILED check, never a skip. | E1-D7 |
| E1-G03 | (c) normative engine | The 90-cell calibration check is circular: the calibration report was produced by the same evaluator. | The Python engine is declared normative: it defines the worlds. | E1-D7 |
| E1-G05 | (a) decimal context | `getcontext().prec = 50` at import (`oracle.py:49`) set the precision for the importing thread only. | Every engine entry point runs under a fixed 50-digit context (`engine.prec50`), so the threaded oracle server and any caller get the paper's figures (`test_engine_ignores_caller_decimal_context`). | - |
| E1-G06 | (a) stub seeding | `demo.py:85` seeded its policies with `hash((company, tier, condition, rep))`, which changes with PYTHONHASHSEED. | `demo.py` is not ported; the stub agent reads only TASK.md and the oracle, seeds with `hashlib`, and has a raw-figures policy (`test_stub_deterministic_across_hash_seeds`). | E1-D9 |

## Documented limitations (unchanged in v1)

| id | class | behaviour | why not fixed | ledger / entry |
|---|---|---|---|---|
| E1-L01 | (c) unbounded levers | Lever values have no bounds in either ruleset; because the goal is revenue[5]/revenue[0] of the plan's own trajectory, a final-quarter price spike reaches any multiple (the `spike_q5` golden inputs). | Fixing it needs a world revision with calibrated per-lever bands, which would change what "valid" means. v1 adds diagnostics: `lever_max_ratio_to_default`, `ratio_levers_above_one` (a utilization, fill rate or take rate above 100%). | E1-D5 |
| E1-L02 | (c) lesion design | The broken mode inflates every figure by a per-driver factor fixed by sorted key position (`bwm_query.py:87-100`): the goal multiple is inflated x1.0952 (atlas, sprout), x1.0870 (northwind), x1.0800 (gigbridge, voltcore) with the Python engine's key set (asserted in `test_broken_lesion_inflates_goal_multiple_by_company_factor`), and margins look implausible. | Kept bit-for-bit so the arm stays the paper's manipulation; a symmetric, seeded lesion is future work. | E1-D13 |
| E1-L03 | (c) readonly vs iterate | The two arms differ by one prompt sentence (and in v1 the query budget). | It is the paper's design. | E1-D10 |
