# Eval 5: consulting oracle (checklist form on open-ended consulting work)

**Question.** Eval 4's checklist manipulation, moved to open-ended consulting deliverables: does giving an agent
the client's QA criteria help it produce correct, well-grounded work, and does it matter whether the criteria are
prose, a runnable check that says PASS/FAIL, or a runnable check that also shows what it measured? And does the
agent know when its work falls short?

## The task

Four authored, text-only engagements (`data/eval5/`, dataset v1.0; all entities fictional). Each scenario has about
30 deliberately contradictory sources (`shared/`, `engagement/`), a brief, and asks for a prose deliverable, a
structured deliverable and `claims.json` (believes_meets_requirements, headline key figures, per-item
self-assessment in the checklist arms).

| scenario | client | deliverables | checks (auto) | headline key figures |
|---|---|---|---|---|
| `solvara-market-access` | Veloxa Therapeutics: launch market access + prior-authorization workflow | `market_access_plan.md`, `pa_workflow.yaml` | 17 (16) | 4 |
| `meridian-soc2` | Meridian Health Loop: SOC 2 Type II readiness | `soc2_readiness_report.md`, `controls_matrix.csv` | 16 (15) | 3 |
| `castellan-software` | Castellan Grid Consulting: software needs assessment | `software_needs_assessment.md`, `capability_plan.json` | 17 (16) | 3 |
| `bluestem-demand` | Bluestem Foodservice: demand growth without destroying margin | `demand_growth_plan.md`, `initiative_portfolio.json` | 17 (16) | 3 |

(Counts from `private/eval5/scenarios/*/registry_v1.0.jsonl` and `data/eval5/manifest.json`: 67 checks, 63
machine-checkable, 13 headline figures.)

## Arms and what each one sees

All arms: Claude Code builtin tools read, write, edit, bash, glob, grep (the paper's tool set; agents validate YAML/
JSON with python3), `min_isolation=CONTAINER` (bash is never path-confined), first prompt "Read TASK.md in the
current directory and complete the task it describes. Work only inside the current directory."

| arm | TASK.md + sources | CHECKLIST.md | PASSES WHEN lines (new runs, default) | `./qa_check` (harness-side, registry v1.0) | grader spec / checker source |
|---|---|---|---|---|---|
| baseline | yes | no | no | no | no |
| checklist-prose | yes | yes | yes | no | no |
| checklist-verdict | yes | yes, byte-identical | yes, byte-identical | yes, sealed mode `verdict`: PASS/FAIL per item | no |
| checklist-executable | yes | yes, byte-identical | yes, byte-identical | yes, sealed mode `figures`: PASS/FAIL + what was measured in the submission (grader bands/regexes/token lists redacted) | no |

No arm receives the check registry or the engine source, so there is no verdict lock to bypass
(`docs/instrument-changes/eval5.md` E5-02 to E5-04; BUILD-SPEC decision 2).

**Pass conditions.** New runs' CHECKLIST.md carries, under every item, `    PASSES WHEN: <condition>`: the item's
registry v1.0 pass criterion (its `score_1_criteria`; 17/16/17/17 lines, manual items included), byte-identical in
the three checklist arms. This is the paper's forward design (the lab's 2026-10-03 fix gives the prose arm each
item's pass condition) with the package's stronger property kept: no arm ever receives a registry or engine, so the
runnable arms hold no literal the prose arm cannot see (E5-48). The matcher literals still absent from the checklist
text fall from 304 (descriptions only) to 201 (with PASSES WHEN) over the 63 auto checks; fully disclosed checks
rise from 13 to 15 (`dbwm eval5-validate` info). The PASSES WHEN texts are registry text, so the leak scans treat
them as answer-key values everywhere except where they are disclosed on purpose: they are passed to the core
pre-check and audit as disclosed values for the checklist arms (`EVAL.disclosed_values_for`, recorded in
`sandbox.json`). **Opt-out:** `--regime-extra` with `{"eval5": {"checklist_pass_conditions": false}}` stages the
as-run layout (one line per check, no pass conditions) of registry v1.0 in all three checklist arms; the setting is
part of the regime and its hash. Runs with and without pass conditions are different series.

**CHECKLIST.md is answer-bearing.** Its descriptions state an in-band value for nearly every numeric item (34 of 34
by a loose match, 31 of 34 strict) and list meridian's ten planted gaps; the pass conditions disclose more.
Checklist-vs-baseline is therefore an answer-disclosure (conformance) contrast; the baseline arm is the only
unaided-accuracy measure. The description text is kept (renames only) for comparability.

## Scoring

Deterministic DSL checker (`dbwm/eval5/engine.py`) over the auto specs; manual items are listed, never scored.

- **Rulesets.** `paper` is the scorer behind the paper's final numbers, bit for bit, defects included. For eval 5
  that is the 2026-10-03 fixed oracle (the oracle of record): registry v0.1 with the lab's SOLVARA-A-13 line
  (`registry_v0.1-fixed.jsonl`, `registry_version` "0.1+A13-fix") + the as-run engine. The scorer that ran the
  sessions is `as-run`: registry v0.1 (auto specs byte-identical to the run; hashes pinned in
  `private/eval5/as-run-pins.json`) + the same engine, `registry_version` "0.1". `as-run` exists for eval 5 only
  (other evals refuse it). `v1` = registry v1.0 + the fixed engine (A-13 path-scoped, identifier-safe numbers,
  scale-aware money amounts, word-boundary tokens with hyphen normalization, one-directional enums, non-vacuity
  gates, polarity-aware copay check, ...); default for new runs and the only ruleset the oracle serves.
- **success** = all auto items pass (all-items pass, the secondary metric reported next to item-pass). Missing
  deliverables = fail.
- **item-pass** (pooled passes / checks; the pre-registered primary measure) in `details.item_pass`.
- **calibration**: strict claims (JSON booleans only); `overclaim` = claimed success but failed; the report adds
  P(believes success given >= 1 failed item) with counts, because the coarse overclaim is mechanically 0 whenever
  every item passes.
- **key figures** in the source-anchored bands, unit-normalized under v1.
- Validation: `dbwm eval5-validate` (lints + 57 golden probes). v1.0: 0 errors, 57/57 probes graded correctly. The
  oracle of record (`--ruleset paper`) misgrades 35 of 57, pinned as `validate.PAPER_KNOWN_MISGRADES` (30 of the 48
  original probes, and 5 A-13 adoptions the lab spec passes: E5-47); as-run v0.1: 17 lint errors and 17 of the
  mapping audit's 20 probes misgraded (16 under the oracle of record).

**SOLVARA-A-13 exists in two repaired forms.** The oracle of record uses the lab's spec (three path-scoped legs,
three buy-and-bill tokens); registry v1.0 uses the package's (also `actor`/`system`, "J-code", "medical benefit",
"Part B", a non-vacuity gate; tokens revised 2026-10-08, E5-50). They differ in general: the lab spec passes `{}`
and adoption phrased as "medical benefit", "Part B" or "J-code" or placed in a step's actor or system (E5-47, with
the probe table).

## Running it

```
dbwm run --eval 5 --model <M> [--arms ...] [--reps 4] --isolation container    # new series, ruleset v1
dbwm run --eval 5 ... --regime-extra no-pass-conditions.json     # {"eval5": {"checklist_pass_conditions": false}}
dbwm score --run <dir> --ruleset v1|paper|as-run                 # paper = oracle of record; as-run = as the sessions ran
dbwm report --run <dir>          # core tables + eval5_report.{json,md}: item-pass, all-items, P(believes|fail),
                                 # scenario-clustered contrasts under both denominators
dbwm eval5-validate [--ruleset v1|paper|as-run]
dbwm eval5-elo --batch <run dir> --out <dir> [--judge claude_cli|anthropic_api|mock-checker] [--passes 3]
```

Regime defaults (`dbwm/eval5/regime.json`, provisional): 4 arms x 4 scenarios x 4 reps, max_turns 80, wall clock
3600 s and $8 as rails (the paper's per-trial timeout and `--max-budget-usd`). PyYAML is required
(`diff-bwm-1[eval5]`).

## Limitations

- The four scenarios and their registries were LLM-authored; briefs telegraph some traps (solvara `t1.md`:
  "not a Part B buy-and-bill product"). Publishing the registries makes them a contaminated dev set; held-out
  scoring needs new scenarios.
- The oracle grades the structured artifact more than the prose; manual items (one per scenario) are not scored.
- CHECKLIST.md is answer-bearing, more so with pass conditions (above). No arm isolates "knowing the criteria" from
  "knowing the answers".
- When every arm passes every item, eval 5 cannot measure executability, the verdict-versus-figures channel or
  calibration; a harder scenario set would be needed.
- Eval 5 has no registry-independent quality measure. The ELO twin (`dbwm eval5-elo`) reports both-order
  agreement and labels a ladder below the 0.75 gate POSITION-UNSTABLE; its judge runs isolated (E5-37).
- Runs of this package are a new series with information-matched arms; they are not comparable to the paper's runs.
- CONTAINER isolation was verified with the fake claude only on the build machine (no API key there;
  `tests/integration/test_integration_container.py` runs the checklist-verdict stub through the shell and
  `./qa_check` against the sidecar); no live model has run eval 5 under this package. See docs/regime.md.
- `qa_check` and the scorer read each deliverable through one `O_NOFOLLOW` descriptor with an `fstat` check, so a
  symlink swapped in after the path check is not followed (E5-46, red-team finding RT-T; bash arms can create
  symlinks).
- The TSC quick-reference sheet was paraphrased by the port; a legal review is still advisable before release
  (owner decision D8).
