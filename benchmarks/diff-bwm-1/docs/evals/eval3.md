# Eval 3: generative vs grounded

**Question.** Evals 1 and 2 measured the value of running an executable model of the business, but that tool bundles
two things: a numerical model to think with, and an external, trusted oracle standing in for reality. Eval 3 splits
them. If the agent builds and runs *its own* model, never validated against the true world, does that help, and how
wrong (and how confident) is it when its model is wrong?

Pre-registration: `numerical-models-llm-planning/eval3-generative-vs-grounded.md` (sections 1-9; the research
repository, not shipped).

## The task

Exactly eval 2's task on eval 2's worlds (see [eval2.md](eval2.md)): commit a 6-quarter allocation of two shared pools
across three coupled business units (`allocation.json`) and a self-assessment (`claims.json`). Achievement is scored
by the true world, with eval 2's scorer. Eval 3 varies only **where the agent's model comes from**.

Instances: the three pre-registered worlds `orion-holdings`, `vega-group`, `lyra-partners` (eval3-generative-vs-
grounded.md section 9; `dbwm/eval3/__init__.py` `WORLD_IDS`). Their briefs are eval 2's (`data/eval2/worlds/<w>/
brief.json`); the public task file `data/eval3/worlds/<w>/task.json` holds only the goal, floors, customers-per-head
and pool caps that the prompt already states.

## Arms and what each one gets

| arm | model source | builtin tools | oracle ops (MCP) | staged besides `TASK.md` | isolation |
|---|---|---|---|---|---|
| `gen-figures` | self-built (DSL) | read, write | `model_eval`: the figures of the model the agent declares, no verdict | `brief.json` | HOST |
| `gen-both` | self-built (DSL) | read, write | `model_eval`, `model_test`: the agent's model's figures and a PASS/FAIL of the fixed public goal and constraints computed on that model | `brief.json`, `task.json` | HOST |
| `gen-code` | self-built (own code) | read, write, bash | none | `brief.json`, `task.json` | CONTAINER |
| `ungrounded`* | none | read, write | none | `brief.json` | HOST |
| `grounded-iterate`* | the true world | read, write | eval 2's `world_query`, localizing channel | `brief.json` | HOST |

(*) eval 2's arms, unchanged (`canonical=False` because they belong to eval 2), included in eval 3's default regime
so that every contrast, the registered H1 and H2 included, comes from one series.

**The modeling DSL** (`dbwm/eval3/model_dsl.py`, the paper's `model_tool.py` ported verbatim). The agent writes
`model.json`: a horizon, ONE set of shared formulas (arithmetic over parameters, the plan levers `marketing` and
`hires`, and other formulas; `prior(NODE, INIT)` for roll-forwards) and per-unit parameters and plan. The tool
compiles the formulas to the exact projection engine (eval 2's, which the paper cross-checked against the live
product to <1e-12) and evaluates **the agent's model**, never the world. A faithful 11-formula model reproduces the
true world exactly (`dbwm/eval3/fixtures.py`; checked in selftest), so every gap in the generative arms is the
agent's modelling error, not the tool's.

What changed against the paper's arms: the paper gave every gen arm unrestricted `Bash`
(plus Edit/Glob/Grep, WebFetch in the tool list) on the host and staged the full `model_tool.py` with both modes plus
`task.json` into gen-figures, so a gen-figures agent could run the withheld `--mode test` and any DSL-arm agent could
compute in its own Python. Here the DSL arms have no bash at all and reach the tool only as sealed MCP ops;
gen-figures has no test op and no `task.json`; gen-code runs only in a container with a stated per-command limit
(120 s; the paper promised a "time-limited" sandbox and enforced none). The prompt also states each unit's do-nothing
levers, which the paper never disclosed (eval 2 P4, inherited).

## Scoring

`dbwm.eval3.score.score_eval3` = eval 2's `score_submission` (rulesets `paper`, bit for bit, and `v1`) plus:

- `declared_model` (paper): `model.json` among the deliverables;
- `self_model_fidelity_pct_error` (paper): the verdict's `forecast_pct_error`, i.e. |claimed final combined revenue -
  true| / true x 100, from what the agent typed into `claims.json`;
- v1 only (new): `declared_model_valid`; `model_plan_fidelity_pct_error`, the agent's own `model.json` evaluated on
  the plan it committed vs the true world on that plan (the pre-registered H4 mediator measured directly, independent
  of what the agent typed); `self_model_fidelity_pct_error_paper` (v1's own forecast is None for plans outside its
  action space).

`ScoreResult.success` = the verdict's `fully_valid`; `overclaim` = strict claim (JSON booleans) and not success. The
v1 prompt states the v1 action space (every unit in both pools, growth capital >= 0, whole non-negative net hires),
taken verbatim from eval 2's release prompt.

## Metrics and contrasts

`dbwm report --run DIR` (`dbwm/eval3/report.py`) prints, per ruleset: achievement per arm (core tables, both
denominators); **overclaim over both bases**, per trial (scored cells with a readable claim) and per failure (failed
committed plans that filed a readable `claims.json`), per arm and pooled over the generative arms;
declared-model rate and the two fidelity medians; oracle calls per arm; and the pre-registered contrasts, registered
hypotheses first. Each contrast prints Fisher's exact test (two-sided) and the Newcombe 95% interval first, and beside
them the registered world-clustered bootstrap (B = 2000, seed 20260701), under both
denominators; with three worlds the bootstrap has only ten distinct cluster resamples:

| contrast | definition |
|---|---|
| H1, registered | `gen-figures` - `ungrounded`; `gen-both` - `ungrounded` |
| H2, registered | `grounded-iterate` - `gen-both` |
| generative effect (pooled) | `gen-figures + gen-both + gen-code` (pooled) - `ungrounded` |
| grounding premium (pooled; not the registered H2) | `grounded-iterate` - the generative arms pooled |
| testability effect | `gen-both` - `gen-figures` |
| tool-form effect | `gen-code` - `gen-both` |

A contrast whose arms were not run is printed as "not in this run", never as 0.

## Regime (provisional)

`dbwm/eval3/regime.json`: the 5 arms above, 3 worlds, 6 reps (the pre-registered 3-world x 6-rep pilot), 90 cells;
`max_turns` 40, wall clock 3600 s and $8 per attempt as rails (a trial that hits one is scored, never retried),
isolation `container` (gen-code has bash), ruleset `v1`, no default model. To run only the no-bash arms on a laptop:
`--isolation host --arms gen-figures,gen-both,ungrounded,grounded-iterate`. All values are provisional pending a
calibration pilot. The paper ran under a dollar cap and a wall clock; this package budgets turns.

## Limitations

- **New runs are a new series**: sealed DSL arms with no bash, gen-code confined to a container, a turn budget instead
  of the paper's dollar budget, the do-nothing levers disclosed in the prompt and the v1 scorer by default. They are
  not comparable to the paper's runs.
- **Three worlds.** The world-clustered bootstrap has only ten distinct resamples, so the exact tests are primary.
- **gen-code needs Docker** (CONTAINER isolation; API-key or OAuth-token auth). The per-command limit the prompt
  states (120 s) is enforced by the runner from `EVAL.command_timeout_s` (Claude Code
  `BASH_DEFAULT_TIMEOUT_MS`/`BASH_MAX_TIMEOUT_MS` with background tasks disabled; openai_compat `bash_timeout_s`) and
  recorded in the manifest and each verdict. The container path was verified with the fake CLI in the real
  image (`tests/integration/test_integration_container.py` runs gen-code's stub through `python3 model.py`), not
  with a live model.
- **Contamination.** The worlds are eval 2's shipped development split; the audit's duplicate corpus is eval 2's
  careful optimum.
- **Live runs so far: one smoke session.** A single gen-figures session on orion-holdings at `host` (2026-10-03,
  `--max-turns 20`) exercised the `model_eval` MCP op end to end; it ran out of turns and is not a result. The
  regime is provisional.

## Running it

```
dbwm selftest --eval 3             # instrument checks (faithful model, staging leaks, sealed ops, stub e2e, ...)
dbwm sandbox-preview --eval 3 --instance orion-holdings --arm gen-figures
dbwm run --eval 3 --model <model> [--isolation host --arms gen-figures,gen-both,ungrounded,grounded-iterate]
```
