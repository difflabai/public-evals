# Runbook

How to run diff-bwm-1 for real: authentication, Docker, keeping long batches alive, resuming, what is retried and
what is scored, usage caps, reading a run, extending the package, and making a private split. For what each regime
value means and the calibration pilot, see [regime.md](regime.md); for what isolation guarantees, see
[threat-model.md](threat-model.md).

## Before you start

- Python 3.10 or later and a checkout of the package: `python -m pip install -e '.[all,dev]'` (the `all` extra
  includes the EUPL-licensed `formulas` that eval 4 needs; install `eval4`, `eval5`, `eval6` individually to avoid
  it). A non-editable `pip install .` copies only `dbwm/`, and the CLI then refuses to start with a message naming
  `DBWM_DATA_DIR` and `DBWM_PRIVATE_DIR`.
- `dbwm fetch eval4` before running eval 4 (needs network access to huggingface.co; checks every
  file's sha256; `--verify-only` re-checks an installed copy, `--from-dir DIR` installs from a local copy).
- Claude Code (`claude`) on `PATH` for `host` runs. The harness relies on flags verified on version 2.1.287
  ([threat-model.md](threat-model.md#isolation-levels-and-what-each-guarantees)) and compares `claude --version`
  with that list: another version runs, but the runner warns once and the manifest, every verdict
  (`details.cli_version_verified`) and `report.md` say `cli_version_verified: false`. The agent environment sets
  `DISABLE_AUTOUPDATER=1` so the version cannot change under a batch.
- Docker for `container` runs, and `dbwm image build`.
- `dbwm selftest` (no LLM, no network) should report no failures; a SKIPPED check (eval 4's, before `dbwm fetch
  eval4`) is not a pass. `dbwm selftest --eval 4` also runs a formula
  recalculation smoke and reports an installed but broken `formulas` (e.g. scipy cannot load) as
  `INSTALLED BUT UNUSABLE` with the cause. `dbwm sandbox-preview --eval N --instance I --arm A` shows exactly what an
  agent would see.
- `dbwm run ... --dry-run` prints the plan without launching or writing anything: cells per arm, the first launches,
  the regime hash, isolation and conformance, the rails and the dollar exposure (cells x USD rail, and with every
  retry). Use it before every batch.

## Authentication

| `--auth` | credential | where it works | trade-offs |
|---|---|---|---|
| `session` (default) | the logged-in Claude Code subscription in the host keychain; the harness passes `HOME` and `USER` | `host` only | matches how the paper ran; subject to subscription usage caps; partial isolation of the config directory. Verified live (ten short sessions) |
| `api_key` | `ANTHROPIC_API_KEY`; isolated `HOME` and `CLAUDE_CONFIG_DIR`; no `--bare` (on 2.1.287 it drops Write/Glob/Grep, CR-29; `--claude-bare` opts in) | `host` and `container` | metered cost, no usage walls, reproducible; recommended for reported runs. Checked only with the real CLI against a local stand-in for the API (no live key) |
| `oauth_token` | `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`; isolated `HOME` and `CLAUDE_CONFIG_DIR` | `host` and `container` | subscription billing in a container; usage caps apply. Never exercised |
| `bedrock`, `vertex` | the providers' documented variables | both | the container proxy adds the providers' documented endpoints by itself (CR-34; reasoned from their documentation, not verified); `--egress-allow host[:port]` adds any that are missing (a 403 in `egress.jsonl` names it). Never exercised |
| `none` | nothing | both | for `stub`, `replay` and `command` agents that need no credentials |

The auth mode is part of the hashed regime. Credentials never appear in argv or on disk (`agent_invocation.json`
redacts them). At `container` with `api_key` or `oauth_token` and the `claude_code` adapter, the agent container
holds only a per-trial placeholder credential and `ANTHROPIC_BASE_URL=http://dbwm-sidecar:8089`: the sidecar's API
relay (CR-35) holds the real credential (passed through the docker client's environment, never argv, `sealed.json`
or `runs/`), forwards only `POST /v1/messages` and `/v1/messages/count_tokens` for the pinned model
(`regime.extra.api_relay_models` adds models), tallies usage per request in `api_relay.jsonl`, and
`api.anthropic.com` leaves the proxy allowlist. `regime.extra.api_relay=false` restores the old wiring and marks the
run NON-CONFORMING. After every attempt the credential values are redacted from the whole attempt directory; if the
agent printed or wrote one, `credential_redactions.json` is written and the audit flags `credential_exposed`
(the cell is excluded). Residuals: the agent can still send pinned-model requests with the placeholder (logged and
tallied, but the USD rail reads the CLI's own `total_cost_usd`); the relay applies to `claude_code` only, so a
`command` agent at `container` receives the real key. The relay was verified with the real CLI 2.1.287 against a
local stand-in for the API and with the fake CLI in the real image, not against the real API; `oauth_token` through
it is reasoned only. The first live container run is a smoke test (one cell per arm) before anything else.

## Docker

```
dbwm image build           # docker build -t dbwm-agent:1.0.0 docker/
dbwm selftest --core-only  # reports whether the image is present
dbwm image clean           # remove leftover trial containers and networks after a crashed harness
```

The image pins Claude Code 2.1.287 and a document stack for bash agents; nothing from the benchmark is baked in.
Each container attempt creates an internal network, a sidecar (oracle server, egress proxy and API relay) and an
agent container, and always tears them down; names are in `attempt-<k>/container.json` and the sidecar log in
`attempt-<k>/sidecar.log`. The sidecar's proxy and oracle listen only on the trial network's address and accept
only that subnet (CR-31). On Docker Desktop, keep `DBWM_SANDBOX_ROOT` (default: the system temp directory) on a
path Docker can bind-mount; the agent container can read the host source paths of its mounts from
`/proc/self/mountinfo`, so point `DBWM_SANDBOX_ROOT` at a neutral directory (the tools dir is already staged there,
not under `runs/`; CR-37). Details: [docker/README.md](../docker/README.md).

## Keeping a batch alive

- **Run the harness under a supervisor** (`nohup`, `tmux`, `screen`, `systemd`, `launchd`), never as a child of an
  interactive agent session: some agent tools and IDEs kill idle process trees after about 30 minutes, which
  interrupts every in-flight attempt.
- **Interrupts are safe.** The first SIGINT or SIGTERM stops new launches, lets in-flight attempts finish and
  record, and marks the manifest `interrupted`. A second signal kills in-flight agents; those attempts become
  retryable crashes.
- **Resume** by re-running the same command with the same `--run-id`. Cells that already have a scored attempt are
  skipped; the run is refused if the regime hash changed.
- **Concurrency.** `--concurrency C` runs C attempts at once. Launch order is a seeded interleave (`--order-seed`),
  rep by rep, so a run that stops early still covers every arm of every instance it reached. On a subscription,
  keep C low (1 or 2) to stay under the usage cap.

## Retries, rails and usage caps

| outcome | meaning | scored? | retried? |
|---|---|---|---|
| `completed` | the agent finished | yes | never |
| `budget_exhausted` | hit `--max-turns` (CLI `error_max_turns` or the harness backstop) | yes | never |
| `clock_stop`, `cost_stop` | hit the wall-clock or USD rail | yes (no deliverable = fail) | never |
| `infra_api` | API error (429, 5xx, 529 overloaded) | no | up to `--max-attempts` (default 3) |
| `infra_invalid_regime` | a pre-launch check or an init-event assertion failed (tools, cwd, model, skills, MCP servers, an auto-memory path outside the attempt's own config or sandbox project, CR-41) | no | yes; 3 in a row halt the run |
| `crash` | the agent process failed or was killed by a second interrupt | no | yes |
| `infra_auth` | not logged in, 401/403 | no | no: the run halts; fix the login and resume |
| `infra_cap` | subscription usage cap | no | no: `--on-cap halt` (default) stops the run; `--on-cap wait` pauses launches until the reset time the CLI reports, then continues |

Outcomes come from structured stream fields (result subtype, `is_error`, API status, rate-limit events, the
harness's own kill reason), never from the agent's text: a completed trial whose final message mentions a "usage
limit" is still `completed`. Every attempt is kept: `runs/<run_id>/attempts.jsonl` is append-only and each attempt
has its own `cells/<cell_id>/attempt-<k>/`. The scored attempt of a cell is its first scored attempt that has not
been invalidated or reset.

**Run-level dollar rail.** `--max-total-usd X` stops launching new attempts once the sum of the run's attempt costs
(`attempts.jsonl`) reaches X; in-flight attempts finish and are scored. The manifest records `max_total_usd` and
`spent_usd`. The limit is a run option, not part of the regime hash, so resuming with the same `--run-id` and a
higher limit continues the run. `--dry-run` prints the exposure bounds (`usd_bound_scored` = cells x per-attempt rail;
`usd_bound_all_attempts` = with every retry) so the limit can be chosen before launch (CR-33).

## Never

- Never pass `--resume`, `--continue`, `--fallback-model` or `--dangerously-skip-permissions` to the agent (the
  harness refuses to build such a command line; do not wrap `claude` in a script that adds them).
- Never wrap the harness in `env -i` or a hand-built environment: it builds the agent's environment itself from an
  allowlist, and session auth needs `HOME` and `USER`.
- Never put `DBWM_SANDBOX_ROOT` inside a git checkout or below a `CLAUDE.md` (the run is refused).
- Never report a `host-unsafe` run or pool runs of this package with the paper's runs.

## Reading a run

```
runs/<run_id>/
  manifest.json            regime and hash, engine hash, harness/Python/platform/CLI versions and
                           cli_version_verified, isolation and conformance (with reasons), image id, egress
                           allowlist, max_total_usd and spent_usd, designed cells, launch order, status
  attempts.jsonl           every start, attempt, invalidate and reset record
  cells/<cell_id>/attempt-<k>/
    prompt.txt, sandbox.json          what the agent was given (listing, manifest hash, and any values the brief
                                      discloses on purpose, `disclosed_values`)
    agent_invocation.json             argv and environment (credentials redacted; claude_code and command adapters)
    transcript.jsonl, stderr.txt      the agent's stream, verbatim (credential values redacted afterwards)
    agent_result.json                 parsed init/result fields (permission mode, memory paths and their check),
                                      turns, cost, models served
    deliverables/, deliverables.json  collected files (missing ones recorded)
    oracle_calls.jsonl                every oracle request (hashes, override attempts incl. nested key paths, calls remaining)
    tools/                            the MCP shim and mcp.json the agent was given (token redacted afterwards)
    egress.jsonl, sidecar.log, sidecar-ready.json, sealed.json, container.json   container runs only
    api_relay.jsonl                   container runs with api_key/oauth_token: every request through the relay
    credential_redactions.json        only if a credential value had to be redacted (audit flag credential_exposed)
    verdict.json                      dbwm.verdict/1 (ruleset, outcome, success, claims, overclaim, process;
                                      `details.agent_init` for claude_code: permission mode and memory status)
    verdict.<ruleset>.json            written by `dbwm score --ruleset` for the other ruleset
    audit.json                        flags and notes
  report.json, report.md
```

```
dbwm score  --run runs/<id> --ruleset paper     # writes verdict.paper.json beside verdict.json
dbwm score  --run runs/<id> --ruleset as-run    # eval 5 only: registry v0.1 as the paper's sessions were scored
dbwm audit  --run runs/<id>                      # re-audit every attempt
dbwm report --run runs/<id>                      # both denominators, Wilson CIs, exclusions, rails, regime
```

`dbwm score` and `dbwm run --ruleset` refuse a ruleset the run's eval does not define, with a message naming the
eval's rulesets (every eval defines `paper` and `v1`; eval 5 also `as-run`).

Judged evals have a second step that calls an LLM: `dbwm eval4-judge --run DIR` (eval 4's primary measure),
`dbwm eval5-elo --batch DIR --out DIR` (eval 5's optional ELO pass) and `dbwm eval6-d2 judge run --model M ...`
(eval 6 D2, where the judge model is the treatment). The D2 judge also runs against any OpenAI-compatible endpoint:
`dbwm eval6-d2 judge run --judge openai_compat --base-url URL --api-key-env VAR [--pricing IN,CACHED_IN,OUT]
--model M ...` (for example base URL `https://api.fireworks.ai/inference/v1`, model
`accounts/fireworks/models/glm-5p1`); tested against a local fake endpoint only (SF-04). The D2 offline signals
regenerate with `dbwm eval6-d2 signals run --out FILE` (with a `lambda = 0` block beside the paper's `lambda = 1`
signals; `--paper-format` writes the paper's report shape only).

**Eval 5's checklist pass conditions.** New runs stage a `CHECKLIST.md` that states each check's pass condition,
byte-identical in the three checklist arms (E5-48). To run without them (the as-run checklist layout of registry
v1.0), pass a regime-extra file; it is recorded in the regime and its hash, so the two are different series:

```
echo '{"eval5": {"checklist_pass_conditions": false}}' > no-pass-conditions.json
dbwm run --eval 5 --model claude-sonnet-4-6 --auth api_key --regime-extra no-pass-conditions.json
```

**Eval 6 D3 (non-canonical).** The dispersion pilot's self-supply arm runs as its own eval module, never pooled with
D1 and never run live. No per-attempt USD rail is set on D3, so cap the run instead; the gate needs five reports
per world and the report labels a world with fewer agree-k:

```
dbwm run --eval dbwm.eval6.d3 --model <model> --auth api_key --max-total-usd <cap>
dbwm report --run runs/<run-id>                  # the gate (agree-5 per world and figure), n per world
```

## Testing the package

```
pytest                                   # the whole suite (container tests skip without Docker + image)
pytest tests/integration                 # the CLI end to end with the stub (every canonical arm of every eval), ~30 s
pytest -m "not slow"                     # skip the container end-to-end runs
pytest -m "not docker and not live"      # no Docker, no model calls (markers registered in pyproject.toml)
```

Opt-in tests that need more than the checkout: `DBWM_CLI_FAKE_API=1` runs the real `claude` against a local
stand-in for the Messages API (no model call); `DBWM_REDTEAM_LIVE=1` runs the red team's four short live sessions.
Before a release, run a fresh-clone check: export `git ls-files`, `pip install -e '.[all,dev]'` in
`python:3.12-slim` and `python:3.10-slim`, then `dbwm selftest` and `pytest -m "not docker and not live"` with the
network off.

## Adding an agent adapter

The quickest route is `--adapter command` (contract in the README and `dbwm/core/adapters/command.py`). To add a
built-in adapter:

1. Implement the `AgentAdapter` protocol (`dbwm/core/adapters/base.py`): a `name` and
   `run(req: AgentRequest) -> AgentResult`. Respect `req.tools` (builtins plus oracle specs), `req.max_turns`, the
   wall-clock rail (`dbwm.core.budget.run_supervised` kills the whole process group) and `req.isolation`; never
   give bash outside `container` or `host-unsafe`.
2. Write the transcript in the normalized stream-json subset (`dbwm.core.stream_json` writers: an init event with the
   tool list and model, one assistant event per turn with its tool_use blocks, tool results, a result event), so
   outcome classification and the audit work unchanged.
3. Register it in `dbwm/core/adapters/registry.py` and add it to the `--adapter` choices in `dbwm/cli.py`.
4. Test it with the stub and replay paths and against a local fake endpoint (see
   `tests/core/test_analysis_openai_compat.py` for the pattern); no network in tests.

`openai_compat` is configured through `regime.extra["openai_compat"]` (`base_url`, `api_key_env`, `max_tokens`,
`pricing`, `extra_body`, `request_timeout_s`, `retries`, `bash_timeout_s`): `dbwm run --adapter openai_compat
--openai-base-url URL --openai-key-env VAR` sets the first two, and `--regime-extra FILE.json` merges the rest
(`{"openai_compat": {"pricing": {...}, ...}}`; credential-like keys are refused; SF-02). Example in the README,
"Plugging in another agent".

## Adding an eval

1. Create `dbwm/evalK/` exposing `EVAL` (the `EvalDef` protocol in `dbwm/core/types.py`: arms with exact tool
   policies and `min_isolation`, `instances`, `cells`, `stage`, `prompt`, `oracle_handlers`, `score` with both
   rulesets, `answer_key_values`, `stub_agent`, `report`, `selftest`) and `SCORER_ONLY_NAMES`, plus
   `regime.json` with no model default.
2. Put agent-visible files in `data/evalK/` and everything else in `private/evalK/` with `CANARY.txt`
   (`dbwm canary --write`). Staging manifests list agent-visible files only.
3. List as answer-key values only strings an honest agent could not produce (hidden parameters, private ids), never a
   reachable target, or correct runs will be flagged.
4. Add tests (`tests/evalK/`: golden scorer cases, sandbox leak checks for every arm and instance, a stub run through
   the runner), a card in `docs/evals/`, and rows in `docs/instrument-changes/`.
5. `dbwm run --eval` also accepts a module path exposing `EVAL`, so an eval can live outside the package.

## Regenerating worlds and data

| eval | command | output |
|---|---|---|
| 1 | `python -m dbwm.eval1.gen.generate_matrix --out DIR` | the 10 companies, tiers and calibration report, byte-identical to the shipped files |
| 2 | `python -m dbwm.eval2.make_worlds --out DIR`; `--certify-v1 [--json FILE]` | briefs, world specs, answer keys; the v1 hardness certificate |
| 3 | eval 2's worlds; `data/eval3/worlds/*/task.json` is derived from them | |
| 4 | `dbwm fetch eval4` | the pinned dataset subset |
| 5 | `dbwm eval5-validate [--ruleset v1\|paper\|as-run]` | lints and the golden-probe suite for the registries (`v1` must grade every probe correctly; the `paper` and `as-run` misgrades are pinned) |
| 6 | `dbwm eval6-worlds make --seed N ...`, `fitted`, `certify` | worlds, the aided fitted model, the step-0 certificate |

## Making a private split

The shipped worlds are a public development split: their answer keys ship or regenerate
([threat-model.md](threat-model.md#contamination)). A held-out evaluation needs worlds nobody outside the organizer
has seen.

1. **Generate** new instances privately:
   - eval 6: `dbwm eval6-worlds make --seed <n> --seed-salt <secret> --data-out <D> --private-out <P>` for each
     seed; the salt drives parameters and the history seed and the world id is opaque. Certify with
     `dbwm eval6-worlds certify --seed-salt <secret> ...` and keep only passing worlds.
   - eval 2 and eval 3: the worlds are hand-set economic configurations, not random draws, so there is nothing to
     salt; write private configurations in the shape of the generator's `CONFIGS` (a JSON list) and run
     `python -m dbwm.eval2.make_worlds --configs private.json --out DIR` (the base model is copied, so
     `DIR/private` is self-contained), then certify them under v1 with
     `DBWM_DATA_DIR=DIR/data DBWM_PRIVATE_DIR=DIR/private python -m dbwm.eval2.make_worlds --configs private.json
     --certify-v1`. Reusing the five public world ids as labels lets `dbwm run --eval 2` run the split with the same
     two variables and no code change (E2-31; `tests/eval2/test_eval2_private_configs.py`). Derive eval 3's
     `task.json` from the new worlds the same way.
   - eval 1: the companies are hand-authored models with no random draw; replace the company definitions in a
     private copy of `dbwm/eval1/gen/generate_matrix.py` (its calibration asserts the tier semantics).
   - eval 5: author new scenarios and registries, then `dbwm eval5-validate` until it reports no errors and every
     probe is graded correctly.
   - eval 4 has no private split in this package.
2. **Isolate**: point `DBWM_DATA_DIR` and `DBWM_PRIVATE_DIR` at the private trees, write a new canary GUID into each
   private directory, and keep the salts, answer keys and scoring seeds (eval 6: `DBWM_EVAL6_V1_SCORING_SEED`)
   off any machine submitters can read.
3. **Score centrally**: submitters send an adapter (or a `command` agent); the organizer runs it at `container`
   isolation and publishes verdicts, the regime and the run manifests, not the worlds.

## Before you publish results

- `dbwm hygiene` must report no hits (operator paths, private names, keys, oversized files).
- `dbwm audit --run DIR` must be clean, or every flagged cell must be excluded and reported by reason.
- The manifest must say `conforming: true` (no `host-unsafe`, no `command` agent at `host`, no `api_relay=false`)
  and `cli_version_verified: true`; `report.md` prints a banner otherwise.
- Report both denominators, the regime (including the pinned K from [regime.md](regime.md)), the Claude Code
  version, the isolation level and the ruleset.
