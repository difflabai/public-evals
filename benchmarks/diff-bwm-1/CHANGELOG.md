# Changelog

All notable changes to diff-bwm-1. Versions follow semantic versioning: a major version changes what a run
measures (tasks, arms, scorers' `v1` semantics, regime defaults that alter difficulty); a minor version adds evals,
arms, adapters or rulesets without changing existing ones; a patch fixes defects that do not change any verdict.
Every verdict records its ruleset and an `engine_hash` of the scorer sources, so results from different versions
can be told apart.

## 1.0.0 (unreleased; release date to be set by the owner)

First standalone release. Before tagging, resolve the owner decisions listed in
[docs/known-limitations.md, "Before publishing"](docs/known-limitations.md#before-publishing).

Added:

- Six evals as packages `dbwm.eval1` to `dbwm.eval6`, each with arms, staging manifests, prompts, oracle handlers,
  a `paper` ruleset that keeps the paper's scorer bit for bit (defects included) and a corrected `v1` ruleset, a
  deterministic stub agent, `selftest()` and a card in `docs/evals/`.
- The runtime harness (`dbwm.core`): per-attempt sandboxes outside any repository with a pre-launch leak check;
  a per-trial oracle server with a sealed channel, served to no-bash arms as MCP tools and to bash arms through a
  CLI shim; `host`, `container` and `host-unsafe` isolation, the container level with a sidecar and an allowlisting
  egress proxy; the `claude_code`, `command`, `openai_compat`, `stub` and `replay` adapters; turn budgets with wall
  clock and dollar rails; structured outcome classification; append-only attempts with infra-only retries; seeded
  interleaved launch order; run manifests.
- The analysis layer: strict and paper claim parsing, deterministic statistics, the `dbwm.verdict/1` schema,
  reports under both denominators, isolated LLM judges (`claude_cli`, `anthropic_api`, `mock`), the per-attempt
  audit, and the `dbwm hygiene` release scan.
- Documentation: README, benchmark card, datasheet, threat model, regime, runbook, known limitations, redacted
  pre-registrations, and an index of instrument changes.

Alignment with the final paper (2026-10-08). The package was checked against NuModel paper v7 (final, 2026-10-06)
and brought in line with it (instrument-change rows CR-14, CR-41 to CR-45, CA-31, CA-32, E1-R07, E2-33, E2-34,
E3-30, E4P-48, E5-47 to E5-50, E6-25 to E6-31, D3-01 to D3-06):

- Rulesets: `paper` is the scorer behind the paper's final numbers; for eval 5 that is the 2026-10-03 fixed oracle
  (the oracle of record), and the scorer that ran the sessions is the new `as-run` ruleset (eval 5 only; other evals
  refuse it). `v1` is unchanged in role. Evals declare their rulesets; `dbwm run` and `dbwm score` refuse undeclared
  ones.
- Eval 5: `CHECKLIST.md` states each check's pass condition, identically in the three checklist arms, with a
  `--regime-extra` opt-out; registry v1.0's A-13 token gaps fixed, with new golden probes.
- Eval 4: a session that ends without a workbook by design fails every item (intention-to-treat), while
  infrastructure crashes are retried, not scored; every tool list is exact, so no session can spawn a sub-agent.
- Eval 2 and eval 3: overclaim counted over failures that filed a claim; Fisher exact tests and Newcombe intervals
  beside the bootstrap; eval 3's registered H2 in the report; the `v1` prompts disclose the do-nothing levers the
  oracle replaces.
- Eval 1: overclaim over filed claims and over stated beliefs.
- Eval 6: the projection gap renamed and described as a profit-definition gap, with margins and fixed costs disclosed
  in the `v1` briefs and marketing bounded in the `v1` scorer; D2's projection sort, penalty calibration and a
  neutral verdict in place of the hard-coded one; D3's brief and scorer and a non-canonical self-supply arm
  (`dbwm.eval6.d3`, never run live).
- Core: the auto-memory assertion on the init event, the zero-cost-completion audit flag, exact statistics
  (`dbwm.core.stats`), and disclosed values in the leak scans.

Public distribution (2026-11). The package holds the problems (`data/`), the answers (`private/`) and the code that
stages, runs, serves oracles for, scores, audits and reports new runs. It carries no results of the paper's runs.

Changed relative to v0 (the paper's runs): every item in the change ledger (kept in the research repository, not published here).
New runs are a new series.

Fixed before release, after the independent review, the red-team round and the fresh-clone check (2026-10-02/03):

- `api_key` runs no longer pass `--bare` to Claude Code (on 2.1.287 it drops Write, Glob and Grep, so every such
  attempt failed the init check); `--claude-bare` opts in (CR-29). `claude --version` is compared with the verified
  list (2.1.287); other versions run with a warning and `cli_version_verified: false` in the manifest, verdicts and
  `report.md`; `DISABLE_AUTOUPDATER=1` for agents (CR-30).
- Container: the sidecar's proxy and oracle listen only on the trial network and accept only its subnet (CR-31);
  the oracle token is redacted from the attempt directory after each attempt (CR-32); Bedrock and Vertex endpoints
  are added to the egress allowlist from the auth mode, unverified (CR-34); the agent container holds only a
  placeholder credential and talks to a sidecar API relay that forwards pinned-model requests and tallies usage,
  with credential values redacted from the attempt directory and an audit flag if one appears (CR-35, red team
  RT-E); the tools directory is staged beside the sandboxes, not under `runs/` (CR-37, RT-M).
- The API relay refuses server-side tools and the `mcp_servers` / `container` fields, so it cannot be used to
  route around the egress allowlist; a `command` agent at `container` with a real credential is NON-CONFORMING
  (CR-39, CR-40, PR #6 review).
- `dbwm run --dry-run` (plan, conformance, USD exposure; writes nothing) and `--max-total-usd` run-level rail
  (CR-33); `--regime-extra FILE.json`, `--openai-base-url`, `--openai-key-env` (SF-02); `--adapter command` at
  `--isolation host` recorded as non-conforming (SF-01).
- Oracle server: JSON depth and line-size limits with a structured "malformed request" reply; nested override keys
  logged by path (CR-36, RT-O1/O2). The audit checks the file arguments of oracle tools (CA-30, RT-A1) and catches
  re-typed copies of eval 2/3 allocations by value (SF-03).
- Eval 2, 3, 4 and 5 handlers read agent files through one `O_NOFOLLOW` descriptor (E2-32, E3-28, E5-46, CR-38;
  RT-T/T2). Eval 4: a workbook that refers to another workbook is refused by `qa_check` and fails under `v1`; the
  checker never opens a second file and scores a temp copy (E4P-45, RT-X); `dbwm selftest --eval 4` runs a
  recalculation smoke and reports a broken `formulas` install as `INSTALLED BUT UNUSABLE`; under `v1` only a broken
  environment makes uncached-formula workbooks unscorable (E4P-44).
- Eval 2: `make_worlds --configs FILE` generates and certifies a private split without code changes (E2-31). Eval
  6: `dbwm eval6-d2 judge run --judge openai_compat` (SF-04), `report.md` without the duplicated generic tables
  (SF-06), corrected regime note (SF-08). Eval 5: per-scenario deliverables declared (SF-05).
- Packaging and tests: a non-editable `pip install .` is refused with a clear message (the package runs from a
  checkout); the new `tests/integration/` suite runs every canonical arm through the CLI with the stub and the bash
  arms in the real image (markers `slow`, `docker`, `live`).
- Licence metadata: `pyproject.toml` declares `MIT AND CC-BY-4.0` with `license-files` LICENSE, LICENSE-DATA, NOTICE
  and LICENSES/Apache-2.0.txt (setuptools 77 or later).

Not included: the paper's leaky live arms (Bash-everywhere controls, staged checker source, mode flags agents
could override, judges with file access). They are documented, not runnable.

## v0 (the paper's harness; described, not shipped)

The paper used the lab's internal harness, not this package. Its state is frozen as base commit `87a3ffac15098b8c5353ac8a599dcf288f7401d6` plus an uncommitted diff to
nine files: the eval-4 planner, judge and report; the eval-2/3 planner and confirmatory runner; the eval-6 D1 runner
and D2 judge; and the eval-5 planner and pairwise judge. That diff carries later fixes (tool
restriction through `--setting-sources project,local` and `--disallowedTools`, the eval-4 verdict lock, cap
handling). The file:line references in the change ledger (not published) point into that snapshot. The harness itself is
not published; this package ports its scorers and generators, and the `paper` ruleset keeps its scorers bit for
bit.

At the paper's close the lab harness stood at commit `aaa04e5`, which carries the campaign fixes (`eeb44dd`), the D3
pilot (`72d420d`) and the eval-5 oracle fix (`16cbe7f`). Seven of the eight files `eeb44dd` changed were already
byte-identical in the snapshot this package ported; the fixed eval-5 registry line is shipped
(`private/eval5/scenarios/solvara-market-access/registry_v0.1-fixed.jsonl`), and D3's four files are ported to
`dbwm/eval6/d3/`. The file:line references in the change ledger (not published) still point into the `87a3ffac` snapshot
unless a row says otherwise.
