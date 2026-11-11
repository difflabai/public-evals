# Known limitations

What diff-bwm-1 1.0.0 cannot do or does not yet know, across the suite and per eval. Instrument defects that the
package fixes are not repeated here.

## Across the suite

- **New runs are a new series.** Fixed sandboxes, exact tool lists, sealed oracles, turn budgets and the `v1`
  scorers make every run of this package a different instrument from the paper's. Do not pool runs of this package
  with the paper's runs.
- **Only smoke-sized live runs have been made with this package.** Ten short sessions at `host` isolation with
  session auth (evals 1, 2 and 3; `--max-turns` 8 to 20; five through `dbwm run` on 2026-10-03, five through the
  red team's tests on 2026-10-02) passed every init assertion, called the oracle as `mcp__dbwm__<op>`, were scored,
  audited and reported, and had every directed out-of-sandbox read refused. Nothing larger has been run, and none of
  the sessions is a result. Every regime default is provisional pending
  the calibration pilot in [regime.md](regime.md).
- **No live model has run in a container.** No API key or OAuth token was available on the build machine, and
  subscription login does not work inside a container. The container path was verified with a scripted stand-in for
  the CLI in the real image (core, integration and red-team tests) and the API relay with the real Claude Code
  2.1.287 against a local stand-in for the Messages API; the relay has not been exercised against the real API, and
  `oauth_token` through it is reasoned only. The first live container run should be a one-cell-per-arm smoke test.
- **Auth modes.** `api_key` was checked only with the real CLI against a local stand-in for the API (which is also
  how the harness learned not to pass `--bare`, CR-29); `oauth_token`, `bedrock` and `vertex` were never exercised.
  For Bedrock and Vertex the container proxy pre-populates the providers' documented endpoints from the auth mode
  (reasoned from their documentation, not verified; a 403 in `egress.jsonl` names a missing host, `--egress-allow`
  adds it); `oauth_token` gets `api.anthropic.com` only, likewise unverified (CR-34). The relay keeps the credential
  out of the agent container for the `claude_code` adapter only: a `command` agent at `container` with `api_key`
  receives the real key, and an agent can still send pinned-model requests with its placeholder (logged and
  tallied; the USD rail reads the CLI's own cost).
- **Not a leaderboard.** The public worlds are a contaminated development split; answer keys ship or regenerate
  ([threat-model.md](threat-model.md#contamination)). No private held-out split is hosted yet.
- **Dependence on Claude Code behaviour.** `host` isolation rests on `--restricted`; the turn budget on the hidden
  `--max-turns`; the per-command bash limit on two environment variables read by the CLI. All were verified on
  version 2.1.287 only. The harness compares `claude --version` with its verified list: another version runs, with
  a warning and `cli_version_verified: false` in the manifest, every verdict and `report.md`, and
  `DISABLE_AUTOUPDATER=1` keeps the agent's CLI from changing mid-batch (CR-30); the image pins 2.1.287. A new
  version must be re-verified before its runs count as conforming evidence of isolation.
- **Third-party agents.** A `command` agent at `host` isolation is not confined by the harness and such a run is
  recorded as NON-CONFORMING (SF-01); run third-party agents at `container`. `openai_compat` (agent loop and the D2
  judge) has been tested only against a local fake endpoint; its settings are set with `--openai-base-url`,
  `--openai-key-env` and `--regime-extra` (SF-02, SF-04).
- **The duplicate gate has a small corpus.** Evals 1 to 4 compare a submission only with the private careful plans
  (and, for eval 4, the fetched dataset submissions).
- **Determinism across platforms.** Eval 6 relies on numpy random streams that are stable across the numpy 2.x
  versions tested but not by contract; eval 4's recalculation depends on the `formulas` version; Python 3.12 changed
  float `sum()`, so scorers that must match the paper's outputs use left-to-right sums. On Linux (glibc libm,
  OpenBLAS) a few eval-5 and eval-6 floating-point outputs differ from macOS in the last bits (largest relative
  difference seen 2.3e-14; checked in `python:3.12-slim` and `3.10-slim`), so compare such outputs within a small
  relative tolerance rather than byte for byte.

## Eval 1

- **Unbounded levers**: a final-quarter price spike reaches any goal multiple, so "hostile" is not hostile to it;
  reported as diagnostics only (E1-L01).
- **Lesion design**: the broken mode only inflates, by factors fixed by key order, and the prompt says to use the
  output as-is (E1-L02).
- **Readonly vs iterate** differ by one prompt sentence and, in `v1`, a 3-call budget (E1-L03).
- **The engine is normative**: the calibration self-test is a regression check (E1-G03).
- The `v1` ungrounded prompt replaces the paper's false line "You have NO tools" with an accurate one (no model or
  calculator service, no tool that runs code, file tools only; E1-P03).
- Four of the five default companies' notes name their efficient levers, so the unaided arm's success depends on
  those notes.

## Eval 2

- Where an arm is 0/k the world-clustered bootstrap degenerates, so exact tests and Newcombe intervals are printed
  beside it (E2-34).
- The `paper` scorer allows layoffs and plans that leave a unit out of a pool; `v1` forbids both (E2-06, E2-07).
- The careful optimum M* is a feasibility witness from a 729-template search, not an optimum (E2-28).
- Three to five worlds give wide clustered intervals; one-world blocks are degenerate.
- The localizing channel keeps the paper's prescriptive strategy hint (E2-16); the optional broken-interface arm is
  not in the default regime and runs only on request (E2-17).

## Eval 3

- `gen-code` needs Docker.
- With three worlds the world-clustered bootstrap has only ten distinct cluster resamples, so the exact tests are
  primary.

## Eval 4

- One task (`w1_t2`) from one third-party dataset; the baseline is not the dataset's leaderboard condition (E4P-40).
- `CHECKLIST.md` states the numeric anchors that resolve the planted contradictions, so prose-vs-baseline measures
  being told much of the answer key (E4P-38).
- The checker under-specifies the rubric (E4P-39); the judge is one model with three samples, not the dataset's
  multi-model protocol.
- Formula recalculation needs the optional EUPL `formulas` package where the check runs (the harness for scoring,
  the sidecar image for `qa_check` in container runs; `docker/requirements.txt` pins it). "Installed" is
  not enough: `formulas` imports even when its scipy cannot load and then every function fails, so `dbwm selftest
  --eval 4` runs a two-cell recalculation smoke and reports such an install as `INSTALLED BUT UNUSABLE: <cause>`.
  Under `v1` a broken environment makes workbooks with uncached formulas unscorable (reason recorded), while a
  workbook whose own invalid formulas make it unrecalculable (prose after `=`) is scored as the paper did, with
  `details.recalc_note` (E4P-44). A workbook that refers to another workbook is refused by `qa_check` and fails
  under `v1` (E4P-45).
- The dataset is fetched, not redistributed; if its host removes it, eval 4 cannot be rebuilt without a mirror.

## Eval 5

- The scenarios and registries are LLM-authored; briefs telegraph some traps (E5-45).
- `CHECKLIST.md` is answer-bearing (an in-band value for every banded check; meridian's planted gaps listed), so
  checklist-vs-baseline is an answer-disclosure contrast; no arm isolates "knowing the criteria" from "knowing the
  answers" (E5-44).
- Registry v1.0 (`v1`) and the `paper` registry grade some submissions differently, and their SOLVARA-A-13 repairs
  differ (E5-47, E5-49). New `v1` runs put each check's pass condition into `CHECKLIST.md` (E5-48); runs with and
  without it are different series.
- The oracle grades the structured artifact more than the prose; manual items are not scored.
- The optional ELO pass rests on one LLM judge whose picks can change with presentation order; `dbwm eval5-elo`
  judges both orders and labels a ladder POSITION-UNSTABLE below its both-order agreement gate. Eval 5 has no
  registry-independent quality measure.

## Eval 6

- M* is a grid optimum found on the scoring paths; a plan can beat it, so gap to M* is a lower bound and uplift is
  the headline (E6-15).
- The D2 ledger and no-ledger signals use the true structure, so the ledger is close to the oracle; D2's 16 states
  include the decertified seed 8 (E6-13).
- D2 is instrument-limited: every judge is handed the true engine's projection of each candidate and told it is
  crude (prompt `v1`; `v2` describes it accurately), and the ledger and no-ledger signals run the same true engine,
  so D2 measures re-ranking against a handed projection, not access.
- OpenAI-compatible D2 judges run through `dbwm eval6-d2 judge run --judge openai_compat` (SF-04), tested against a
  local fake endpoint only.
- `v1` bounds marketing at the history's per-segment maximum and leaves success undefined on w7, where the target
  exceeds the bounded optimum (E6-25, E6-31); the `paper` scorer leaves marketing unbounded.
- D3's runnable arm `dbwm.eval6.d3` is non-canonical and has never run live; its regime is provisional (D3-01 to
  D3-06).
- On the public split, a model that knows the generator could still derive the market-factor path from the world
  id; the salted private split closes this.
