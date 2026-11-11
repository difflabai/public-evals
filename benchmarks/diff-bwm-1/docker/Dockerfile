# diff-bwm-1 agent image (dbwm-agent:1.0.0).
#
# One image serves two roles per CONTAINER trial (dbwm/core/container.py):
#   agent    runs the agent CLI with cwd /work (the sandbox), read-only rootfs,
#            tmpfs /tmp and HOME, non-root, on an --internal network whose only
#            peer is the sidecar; it never sees the dbwm package.
#   sidecar  runs `python -m dbwm.core.sidecar` (oracle server + egress proxy)
#            with the package mounted read-only at /dbwm.
# Nothing from the benchmark (code, data, answer keys) is baked into the image.
#
# Build:  dbwm image build      (== docker build -t dbwm-agent:1.0.0 docker/)

# Base images pinned by digest (multi-arch index digests, resolved 2026-10-02:
# Python 3.12.15, Node v22.23.3). Override with --build-arg to rebase.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c

FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE}

ARG CLAUDE_CODE_VERSION=2.1.287

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    DISABLE_AUTOUPDATER=1 \
    LANG=C.UTF-8

# Node.js (for Claude Code) copied from the official Node image.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates procps \
 && rm -rf /var/lib/apt/lists/*

# Claude Code, pinned. The harness records `claude --version` per attempt.
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force \
 && claude --version

# Python packages from a full lock (requirements.txt, generated from
# requirements.in): the document stack available to bash-enabled agents (evals
# 3-6 read and write xlsx/docx/pdf/yaml/csv) and formulas/schedula, which eval
# 4's qa_check oracle needs in the sidecar to recalculate agent-written
# workbooks. Every transitive dependency is pinned, so rebuilds are identical.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --root-user-action=ignore -r /tmp/requirements.txt \
 && pip check \
 && rm /tmp/requirements.txt

# A non-root account for interactive debugging; trials run with --user <host uid:gid>
# and a tmpfs HOME at /home/agent.
RUN useradd --create-home --home-dir /home/agent --uid 10001 agent \
 && mkdir -p /work /dbwm-tools \
 && chmod 1777 /work /home/agent

WORKDIR /work
USER agent
CMD ["claude", "--version"]
