"""RT-E: does the REAL Claude Code CLI hand the auth credential to the agent's Bash tool?

The container agent gets the auth variable (``ANTHROPIC_API_KEY`` /
``CLAUDE_CODE_OAUTH_TOKEN``) in its environment because the CLI needs it, and
the egress proxy allows ``api.anthropic.com:443``. If the CLI's Bash tool
inherits that variable, a bash-arm agent (eval 3 gen-code, 4, 5, 6) can print
the credential into ``transcript.jsonl`` and call the API itself through the
allowed tunnel: other models (bypassing the model pin and the served-model
audit, which reads only the CLI's own ``modelUsage``) and spend outside the USD
rail (which reads only the CLI's own ``total_cost_usd``).

This test runs the real CLI (no model call) against ``tests/core/fake_api.py``
with a dummy key, under macOS ``sandbox-exec`` denying all non-loopback egress,
and has the fake "model" call Bash once with a command that reports whether the
variable is visible (its length only; the dummy value is never printed).
Opt-in like tests/core/test_core_cli_fake_api.py: ``DBWM_CLI_FAKE_API=1``.

FIXED 2026-10-02 (integration): at CONTAINER isolation the claude_code agent now
gets only a per-trial placeholder plus ``ANTHROPIC_BASE_URL`` pointing at the
sidecar's API relay (dbwm.core.api_relay), which swaps in the real credential,
forwards only pinned-model /v1/messages calls and logs usage. The CLI still hands
its environment to Bash (that is CLI behaviour), so this test now runs the real
CLI through the relay exactly as the runner wires it (relay_agent_env) and
asserts that Bash sees only the placeholder while the upstream receives the real
credential.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from dbwm.core import api_relay
from dbwm.core import env as envmod
from dbwm.core import sandbox
from dbwm.core.adapters.claude_code import ClaudeCodeAdapter
from dbwm.core.types import AgentRequest, Budget, Cell, Isolation

CORE_TESTS = Path(__file__).resolve().parents[1] / "core"
if str(CORE_TESTS) not in sys.path:
    sys.path.insert(0, str(CORE_TESTS))

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
DUMMY = "dummy-not-a-key-0123456789"

pytestmark = pytest.mark.skipif(
    os.environ.get("DBWM_CLI_FAKE_API") != "1" or not shutil.which("claude") or not os.path.exists(SANDBOX_EXEC)
    or sys.platform != "darwin",
    reason="real-CLI/fake-API check: set DBWM_CLI_FAKE_API=1 with claude on PATH (macOS sandbox-exec); "
           "no model call is made")


def _wrapper(tmp_path: Path) -> Path:
    prof = tmp_path / "loopback-only.sb"
    prof.write_text('(version 1)(allow default)(deny network-outbound)'
                    '(allow network-outbound (remote ip "localhost:*"))'
                    '(allow network-outbound (remote unix-socket))\n')
    w = tmp_path / "claude"
    w.write_text(f'#!/bin/sh\nexec {SANDBOX_EXEC} -f "{prof}" "{shutil.which("claude")}" "$@"\n')
    w.chmod(0o755)
    return w


PLACEHOLDER = "dbwm-relay-placeholder-for-the-cli-test"


def test_bash_tool_sees_only_the_relay_placeholder(iso_dirs, toy, tmp_path):
    from fake_api import FakeMessagesAPI

    probe = 'echo "KEYLEN=${#ANTHROPIC_API_KEY} OAUTHLEN=${#CLAUDE_CODE_OAUTH_TOKEN}"'
    cell = Cell("toy", "alpha", "bash-oracle", 0)
    spec = toy.arms["bash-oracle"]
    h = sandbox.build_sandbox(toy.stage(cell), cell, spec.tools, ())
    out = tmp_path / "attempt"
    out.mkdir()
    src = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "nohome"), "ANTHROPIC_API_KEY": DUMMY}
    env = envmod.build_agent_env("api_key", Isolation.HOST_UNSAFE, harness_out=out, source=src)
    try:
        with FakeMessagesAPI(call_tool="Bash", tool_input={"command": probe, "description": "probe"}) as api:
            relay = api_relay.ApiRelay(credential=DUMMY, placeholder=PLACEHOLDER, pin="haiku",
                                       upstream=api.base_url, log_path=out / "api_relay.jsonl",
                                       host="127.0.0.1", port=0)
            relay.start()
            try:
                env = envmod.relay_agent_env(env, "ANTHROPIC_API_KEY", PLACEHOLDER,
                                             f"http://127.0.0.1:{relay.port}")
                req = AgentRequest(cell=cell, prompt="probe", sandbox=h.path, tools=spec.tools,
                                   budget=Budget(4, 120, None), model="haiku", effort=None,
                                   isolation=Isolation.HOST_UNSAFE, harness_out=out, oracle_address=None,
                                   oracle_token=None, auth_mode="api_key", env=env, extra={})
                ClaudeCodeAdapter(claude_bin=str(_wrapper(tmp_path))).run(req)
            finally:
                relay.stop()
            upstream = list(api.requests)
    finally:
        sandbox.cleanup(h)
    text = (out / "transcript.jsonl").read_text()
    assert DUMMY not in text
    results = []
    for line in text.splitlines():
        ev = json.loads(line)
        if ev.get("type") == "user":
            for b in ev["message"].get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    results.append(c if isinstance(c, str) else json.dumps(c))
    probe_out = next((r for r in results if "KEYLEN=" in r), None)
    assert probe_out is not None, f"Bash probe did not run: {results[:3]}"
    (tmp_path / "probe_out.txt").write_text(probe_out)
    assert f"KEYLEN={len(DUMMY)}" not in probe_out, f"Bash tool sees the API key: {probe_out!r}"
    assert f"KEYLEN={len(PLACEHOLDER)}" in probe_out, probe_out
    # every request the CLI made went through the relay and carried the REAL credential upstream
    posts = [r for r in upstream if r["method"] == "POST"]
    assert posts and all(r["auth_sha256"] == FakeMessagesAPI.auth_digest(DUMMY) for r in posts)
    log = (out / "api_relay.jsonl").read_text()
    assert DUMMY not in log and PLACEHOLDER not in log
    recs = [json.loads(x) for x in log.splitlines()]
    (tmp_path / "relay_records.json").write_text(json.dumps(recs, indent=1))
    assert any(r.get("allowed") for r in recs)
    # PR #6 review: the relay refuses server-side tools and mcp_servers/container; the real CLI's own
    # requests (WebSearch/WebFetch excluded by --tools) must never trip that filter.
    tripped = [r for r in recs if not r.get("allowed") and any(
        k in str(r.get("reason")) for k in ("server-side tool", "mcp_servers", "'container'", "client tools"))]
    assert not tripped, tripped
