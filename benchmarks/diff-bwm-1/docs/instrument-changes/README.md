# Instrument changes: index

Everything the release package does differently from the harness that ran the paper's sessions (the lab's internal
harness, frozen at base commit `87a3ffac` plus an uncommitted diff; see [CHANGELOG.md](../../CHANGELOG.md), "v0").
Each file below is a table with one row per change: an id, a class, the old behaviour with a file:line reference into
the frozen snapshot, the release behaviour, and the research-ledger or Appendix A entry. "Appendix A" and "v7 entry" name entries in the correction ledger of NuModel
paper v7 (final, 2026-10-06). Ids are stable: a withdrawn row leaves a gap. The files are written by the owners of the
code they describe; this page only indexes and summarizes them.

| file | scope | row ids |
|---|---|---|
| [core-runtime.md](core-runtime.md) | sandboxes, tool restriction, isolation, environment, auth, oracle server, budgets, process teardown, stream parsing, outcomes, attempts, launch order, run provenance, other agents, container image; post-review fixes (api_key without `--bare`, CLI version check, sidecar listeners, token redaction, dry run and run-level rail, provider egress, API relay and its server-tool filter, oracle robustness, tools-dir staging, safe reads, command-at-container conformance); final-paper alignment (operator auto-memory assertion, sub-agents, the thinking-token knob, rulesets per eval, disclosed values in the leak scans) | CR-01 to CR-45 (final-paper alignment: CR-14 restated, CR-41 to CR-45) |
| [core-analysis.md](core-analysis.md) | claims parsing, statistics, verdicts, reports, judges, audit, the openai_compat adapter, hygiene, eval hooks; oracle tool inputs in the audit; final-paper alignment (zero-cost completions flagged, exact tests and Newcombe intervals) | CA-01 to CA-32 (final-paper alignment: CA-31, CA-32) |
| [small-fixes.md](small-fixes.md) | post-review fixes across core and evals: `command` at `host` non-conforming, `--regime-extra` and openai_compat flags, value-canonical duplicate gate, openai_compat D2 judge, per-scenario deliverables, eval-6 report, eval-6 regime note | SF-01 to SF-08 |
| [eval1.md](eval1.md) | eval 1: regime and isolation, the `run_model` oracle, prompt, scorer and claims, report, engine and worlds, limitations; final-paper alignment (overclaim over filed claims and stated beliefs) | E1-A, E1-T, E1-P, E1-S, E1-R, E1-G, E1-L (final-paper alignment: E1-R07) |
| [eval2.md](eval2.md) | eval 2 and the shared coupled instrument; private split by configuration file; safe file reads; final-paper alignment (do-nothing levers disclosed, overclaim basis and exact tests in the run report) | E2-01 to E2-34 (final-paper alignment: E2-33, E2-34) |
| [eval3.md](eval3.md) | eval 3 (every eval-2 row applies too); safe file reads; final-paper alignment (inherited lever disclosure) | E3-01 to E3-30 (final-paper alignment: E3-30) |
| [eval4.md](eval4.md) | eval 4: dataset and staging, deterministic scorer, attrition and budgets, judge, analysis, limitations; recalculation smoke and external-workbook guard; final-paper alignment (sub-agents) | E4P-01 to E4P-48 (final-paper alignment: E4P-48) |
| [eval5.md](eval5.md) | eval 5: oracle and registry v1.0, arms and prompt, publication hygiene, ELO twin, documentation; safe deliverable reads; final-paper alignment (the two A-13 repairs, pass-condition disclosure, ruleset names, A-13 token gaps and probes) | E5-01 to E5-50 (final-paper alignment: E5-47 to E5-50) |
| [eval6.md](eval6.md) | eval 6: sandbox contents and the aided arm, briefs, D1 scorer, aggregation, D2 (D2-04, the OpenAI-compatible judge backend, is superseded by SF-04); final-paper alignment (marketing bound, disclosed profit definition, neutral D2 verdict, penalty calibration, projection sort, unreachable targets); the D3 dispersion gate | E6-, S-, P-, C-, D2- (final-paper alignment: E6-25 to E6-31); D3-01 to D3-06 |

## How to read a row

**Class.** (a) integrity defect fixed in `v1`; (b) stale or wrong documentation corrected; (c) documented limitation
that cannot be fixed after the fact; (d) port mechanics, no behavioural change intended.

**Old behaviour.** Old *scorers* are kept: every eval keeps the scorer behind the paper's numbers as the `paper`
ruleset, and eval 5 also keeps the scorer its sessions ran with as `as-run`. Old *conditions* are not: the old
leaky arms (Bash in "no tools" controls, staged checker source, overridable mode flags, judges with file access) are
documented here and not runnable (BUILD-SPEC scope decision 2).

## Cross-cutting summary

What changed for every eval, in the order an attempt meets it:

1. **Sandbox** (CR-01 to CR-03). A fresh temporary directory per attempt, outside any repository, holding only the
   files in the arm's manifest, checked before launch for extra files, the canary, answer-key values and host paths
   in the prompt. Only declared deliverables are collected; a retry can never be scored on an earlier attempt's file.
   *Old harness:* the agent's cwd was inside the harness checkout, next to world files, answer keys and earlier
   trials.
2. **Tools and isolation** (CR-04 to CR-06, CR-23). An exact tool list per arm, with `--restricted`,
   `--strict-mcp-config` and `--disable-slash-commands`; no bash in the canonical arms of evals 1-3; bash only in a
   container with an allowlisting egress proxy. *Old harness:* `--allowedTools` was additive to operator settings, so "no
   tools" controls had Bash and web tools; operator skills, plugins and instructions reached agents.
3. **Environment and credentials** (CR-07, CR-08, CA-19 to CA-21). Built from an allowlist plus the chosen auth mode;
   credentials never in argv, on disk or in an agent's shell. *Old harness:* the operator's environment, including effort
   variables and, for the Fireworks loop, an API key, reached agents.
4. **Oracles** (CR-09 to CR-11). One server per attempt with a per-trial token and the channel sealed server-side;
   override keys stripped and logged; query budgets enforced; agents see only an MCP tool or a CLI shim. *Old
   harness:* agents ran the tool scripts themselves and could change the channel with a flag.
5. **Regime verification** (CR-12, CR-13, CA-18, CR-41, CR-42). The init event must show exactly the expected
   tools, cwd, model, skills and MCP servers, and an auto-memory path that belongs to the attempt, or the attempt is
   voided and retried; no tool list contains a sub-agent tool; the model is pinned and every served model checked.
   *Old harness:* nothing checked what an agent actually had; the model could fall back to the CLI default; a
   planner launched inside the harness checkout attached the operator's auto-memory, and an agent could spawn
   sub-agents.
6. **Budgets** (CR-14). Turns are the primary budget; wall clock and dollars are rails, scored and never retried.
   *Old harness:* a dollar budget and a clock, whose bite depends on the model that runs.
7. **Process, outcomes and attempts** (CR-15 to CR-22). Process-group teardown; outcomes from structured stream
   fields; append-only attempts with infra-only retries; usage caps and auth failures halt instead of consuming
   attempts; seeded interleaved launch order; a manifest with the full regime. *Old harness:* cap detection read
   the agent's text, a logged-out session was scored as a failure, verdicts were overwritten in place, and batches ran
   world by world.
8. **Claims, statistics, reports** (CA-01 to CA-11). Strict claims beside the paper's truthiness reading;
   deterministic bootstraps; both denominators and both overclaim bases in every report. *Old harness:* the JSON
   string `"false"` counted as a claim of success.
9. **Judges** (CA-12 to CA-15). No tools, an empty working directory, the stream saved, any tool use voids the pass,
   ties and invalid votes are judge errors, never fails. *Old harness:* judges could read files.
10. **Audit and hygiene** (CA-16, CA-17, CA-25). Path access judged from tool inputs, not text matches; a duplicate
    gate; a release scan for operator paths, private names and keys.
11. **Other agents** (CR-25, CA-22 to CA-24, SF-01, SF-02). The `command` adapter and the `openai_compat` loop run
    under the same sandbox, allowlist, oracle and rails; a `command` agent at `host` is recorded non-conforming;
    openai_compat is configured from the command line.
12. **Post-review fixes, 2026-10-02/03** (CR-29 to CR-40, CA-30, SF-01 to SF-08, E4P-44, E4P-45, E5-46, E2-31,
    E2-32, E3-28). From the code review, the red team and the fresh-clone check: no `--bare` for `api_key` (it
    drops Write/Glob/Grep); `claude --version` compared with the verified list and flagged; sidecar listeners bound
    to the trial network; oracle token and credential values redacted after each attempt; `--dry-run` and
    `--max-total-usd`; provider endpoints for Bedrock/Vertex; the sidecar API relay so the agent container never
    holds the credential; oracle server depth and size limits, nested override keys logged; tools dir staged outside
    `runs/`; `O_NOFOLLOW` reads of agent files in evals 2, 3, 4 and 5; eval 4's recalculation smoke and
    external-workbook guard; eval 2's private split by configuration file; the audit's oracle-input path checks.
13. **Final-paper alignment, 2026-10-08** (CR-14, CR-41 to CR-45, CA-31, CA-32, E1-R07, E2-33, E2-34, E3-30, E4P-48,
    E5-47 to E5-50, E6-25 to E6-31, D3-01 to D3-06). Rulesets per eval (`paper` = the scorer behind the paper's
    final numbers; `as-run` = the scorer eval 5's sessions ran with); disclosed values exempt from the leak scans;
    the auto-memory assertion; exact tests; eval 5's oracle of record and pass-condition checklist; eval 6's
    marketing bound, disclosed profit definition and neutral D2 verdict; D3's brief, scorer and non-canonical arm.

## Per-eval highlights

- **Eval 1**: the lesion is unlabelled and the readonly arm has a server-enforced 3-call budget; the ungrounded arm
  really has no tools; `v1` rejects non-lever bindings and reads claims strictly; overclaim is reported over filed
  claims and over stated beliefs.
- **Eval 2**: the `v1` scorer and prompt enforce the action space the hardness certificate assumed (no layoffs, every
  unit in both pools) and disclose the do-nothing levers the oracle replaces; the boolean channel carries no figures.
- **Eval 3**: `gen-figures` gets figures only (the old harness staged the test mode too); the DSL arms have no bash;
  `gen-code`'s per-command limit is enforced.
- **Eval 4**: the dataset is pinned by revision and hash; no checker source reaches any sandbox; the judge panel
  uses `panel_majority`, a session that ends without a workbook by design fails every item, and infrastructure
  crashes are retried.
- **Eval 5**: registry v1.0 fixes the check defects (A-13 and A-14 among them); the runnable arms get no grader
  specification, and every checklist arm gets the same pass conditions; `paper` is the oracle of record and `as-run`
  the scorer the sessions ran with; the data were renamed and paraphrased for publication.
- **Eval 6**: the aided model carries no world seed; the `v1` briefs disclose margins and fixed costs, and the `v1`
  scorer bounds marketing at the history's maximum, uses a private seed and reports every no-plan fallback; both
  no-plan policies are reported; D2 reports state where judge minus ledger lies instead of a hard-coded verdict;
  D3's brief and scorer are ported, with a non-canonical arm.
