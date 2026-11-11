# docker/: the agent image and CONTAINER isolation

`--isolation container` (required for every arm whose tools include bash) runs each attempt in containers built
from one image, `dbwm-agent:1.0.0`:

```
dbwm image build            # == docker build -t dbwm-agent:1.0.0 docker/
dbwm selftest --core-only   # reports the image as present (or SKIPPED with the reason)
```

The image is `python:3.12-slim` (Python 3.12.15) plus the Node.js 22 binary, `@anthropic-ai/claude-code` pinned to
**2.1.287**, and a fully pinned Python stack: the document stack for bash-enabled agents (numpy 2.2.6, pandas 2.2.3,
openpyxl 3.1.5, pyyaml 6.0.3, pdfplumber 0.11.4, python-docx 1.1.2) and `formulas` 1.3.4 + `schedula` 1.6.15, which
eval 4's `qa_check` oracle uses in the sidecar to recalculate agent-written workbooks (formulas brings scipy 1.18.1).
Both base images are pinned by digest in the Dockerfile. Nothing from the benchmark is baked in: no code, data or
answer keys. The image id is recorded in every run manifest (`container_image_id`), and `claude --version` is
recorded per attempt.

`requirements.in` lists the top-level packages; `requirements.txt` is the full lock the Dockerfile installs (every
transitive dependency pinned, `pip check` clean, wheels available for linux/arm64 and linux/amd64). After editing
`requirements.in`, regenerate the lock in the base image and rebuild:

```
make image-lock     # resolves requirements.in in python:3.12-slim, runs pip check, prints the pins
# paste the output under the header of docker/requirements.txt, then: dbwm image build
```

## Per-command limits for bash arms

When an eval states a per-command limit (`EVAL.command_timeout_s(cell)`, e.g. eval 3's gen-code: 120 s), the runner
sets `BASH_DEFAULT_TIMEOUT_MS` = `BASH_MAX_TIMEOUT_MS` = 1000 x the limit and `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`
in the agent environment (a background command would escape the limit), passes the same limit to openai_compat as
`bash_timeout_s`, and records it in `manifest.json` (`command_timeout_s`) and each verdict's details.

## What one CONTAINER attempt looks like

```
host (harness: runner, sandbox builder, scorer)
 |
 |-- docker network create --internal dbwm-net-<trial>      no route out
 |
 |-- sidecar  (dbwm-agent image)                            on the default bridge AND the internal network;
 |                                                          listens only on its internal-network address
 |     mounts: package  -> /dbwm      (read-only)            alias "dbwm-sidecar"
 |             sandbox  -> /work-ro   (read-only)
 |             attempt  -> /harness   (read-write: oracle_calls.jsonl, egress.jsonl, sealed.json)
 |     runs:   python -m dbwm.core.sidecar
 |               - oracle server  tcp://dbwm-sidecar:7700  (per-trial token, sealed mode)
 |               - egress proxy   http://dbwm-sidecar:3128 (CONNECT allowlist, default api.anthropic.com)
 |
 |-- agent    (dbwm-agent image, docker run --rm -i)         internal network ONLY
       mounts: sandbox   -> /work        (read-write, the cwd)
               tools dir -> /dbwm-tools  (read-only: MCP shim + mcp.json for no-bash arms)
       --read-only root fs, tmpfs /tmp and /home/agent, --user <your uid:gid>, --cap-drop ALL,
       no-new-privileges, --pids-limit 512, --memory 4g
       env: allowlist + HTTPS_PROXY=http://dbwm-sidecar:3128 (+ DBWM_ORACLE/DBWM_ORACLE_TOKEN for bash arms)
```

The agent never sees the package, the private data or the attempt directory. Its only peer is the sidecar; its only
way out is the proxy, which tunnels allowlisted `host:port` pairs and answers 403 to everything else (every request
is logged to `egress.jsonl` and denials appear in the audit). Add hosts with `--egress-allow host[:port],...` (they
become part of the hashed regime). For `--auth bedrock` and `--auth vertex` the documented endpoints are added
automatically (Bedrock: `bedrock-runtime.<AWS_REGION>.amazonaws.com`, `bedrock.<AWS_REGION>.amazonaws.com`; Vertex:
`<CLOUD_ML_REGION>-aiplatform.googleapis.com` and `oauth2.googleapis.com`; plus the host of
`ANTHROPIC_BEDROCK_BASE_URL`/`ANTHROPIC_VERTEX_BASE_URL`). That list is reasoned from the providers' documentation,
not verified: only `api_key` (against a local stand-in for the API) and the fake CLI were exercised. A 403 in
`egress.jsonl` names any host that is still missing; `oauth_token` gets only `api.anthropic.com`.

The sidecar is attached to the default bridge only for its own outbound connections (and port publishing). Its
proxy and oracle listen on its address on the trial's internal network and accept only clients from that subnet, so
other containers on the bridge cannot reach them (`sidecar-ready.json` records the listen plan). After the attempt the
oracle token is redacted from `sealed.json` and `tools/mcp.json`.

Teardown always runs: agent and sidecar removed, network removed, sidecar log saved to `attempt-<k>/sidecar.log`;
container and network names are in `attempt-<k>/container.json`. After a crashed harness,
`dbwm image clean` removes leftovers (everything labelled `dbwm.trial=1`).

## Authentication in the container

The subscription session (`--auth session`) lives in the host keychain and does not work in a container. Use
`--auth api_key` (`ANTHROPIC_API_KEY`) or `--auth oauth_token` (`CLAUDE_CODE_OAUTH_TOKEN`, from
`claude setup-token`). Credential values are passed through the docker client's environment, never on its command
line, and are redacted in `agent_invocation.json`.

## How this was verified

`tests/core/test_core_container_e2e.py` builds real trials in this image with a scripted test double of the Claude
Code CLI bind-mounted into the agent container at `/opt/fake/claude` and used as the CLI (`--claude-bin`): a bash
arm runs the CLI shim against the sidecar's
oracle; a no-bash arm calls the oracle over MCP; the proxy denies `example.com` (403, logged) and tunnels an
allowlisted host; a direct connection out fails; `/dbwm` is not visible; the root filesystem and the tools dir are
read-only; teardown leaves nothing behind. A live Claude Code run inside the container was NOT performed when this
package was built, because no API key or OAuth token was available; the live path was verified on the host
(`--isolation host`, and `host-unsafe` for a bash arm) with the same adapter.

The tests skip, with the reason, when Docker or the image is missing.

## Platform notes

- Docker Desktop (macOS/Windows) shares the default temp directories into the VM; if `DBWM_SANDBOX_ROOT` points
  elsewhere, make sure Docker can bind-mount it.
- Containers run as your uid:gid so the sandbox and attempt directories stay writable; a root harness runs them as
  65534:65534 instead.
- The oracle server and proxy speak TCP (not unix sockets), because Docker Desktop cannot share host unix sockets
  into containers.
