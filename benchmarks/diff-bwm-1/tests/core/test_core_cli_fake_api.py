"""Opt-in check of the REAL Claude Code CLI's tool/MCP behaviour, with NO model call.

    DBWM_CLI_FAKE_API=1 python -m pytest tests/core/test_core_cli_fake_api.py

The real ``claude`` on PATH runs through the claude_code adapter with a dummy
``ANTHROPIC_API_KEY`` and ``ANTHROPIC_BASE_URL`` pointing at a local stand-in for the
Messages API (``fake_api.py``), under macOS ``sandbox-exec`` with every non-loopback
connection denied, so nothing leaves the machine. It checks what the init assertions
depend on for api_key auth (review findings M1/M2, 2026-10-02):

- without ``--bare`` (the default) an MCP arm gets exactly Read, Write and the dbwm
  tool, the server connects and a tool call reaches the per-trial oracle server;
- ``--bare`` (``regime.extra["claude_bare"] = true``) keeps the MCP server but drops
  Write (CLAUDE_CODE_SIMPLE keeps only Bash/Edit/Read), so such an attempt fails the
  init assertion;
- a bash arm's per-command limit variables (``env.bash_timeout_env``, incl.
  CLAUDE_CODE_DISABLE_BACKGROUND_TASKS) leave init.tools unchanged.

Run it before adding a version to ``claude_code.VERIFIED_CLI_VERSIONS``. Verified on
2.1.287 on 2026-10-02.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from fake_api import FakeMessagesAPI

from dbwm.core import env as envmod
from dbwm.core import outcomes, sandbox
from dbwm.core.adapters.claude_code import ClaudeCodeAdapter
from dbwm.core.oracle.server import OracleServer
from dbwm.core.types import AgentRequest, Budget, Cell, Isolation, OracleContext, Outcome

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
TOKEN = "tok_fake_api_0123456789abcdef"

pytestmark = pytest.mark.skipif(
    os.environ.get("DBWM_CLI_FAKE_API") != "1" or not shutil.which("claude") or not os.path.exists(SANDBOX_EXEC)
    or sys.platform != "darwin",
    reason="real-CLI/fake-API check: set DBWM_CLI_FAKE_API=1 with claude on PATH (macOS: needs sandbox-exec "
           "to deny all non-loopback egress); no model call is made")


def _wrapper(tmp_path: Path) -> Path:
    prof = tmp_path / "loopback-only.sb"
    prof.write_text('(version 1)(allow default)(deny network-outbound)'
                    '(allow network-outbound (remote ip "localhost:*"))'
                    '(allow network-outbound (remote unix-socket))\n')
    w = tmp_path / "claude"
    w.write_text(f'#!/bin/sh\nexec {SANDBOX_EXEC} -f "{prof}" "{shutil.which("claude")}" "$@"\n')
    w.chmod(0o755)
    return w


def _run(toy, tmp_path, arm: str, api: FakeMessagesAPI, regime_extra=None, isolation=Isolation.HOST,
         extra_env=None):
    cell = Cell("toy", "alpha", arm, 0)
    spec = toy.arms[arm]
    h = sandbox.build_sandbox(toy.stage(cell), cell, spec.tools, spec.tools.oracle_tools)
    out = tmp_path / f"attempt-{arm}"
    out.mkdir()
    srv = None
    addr = None
    if spec.tools.oracle_tools:
        ctx = OracleContext(cell, spec, spec.oracle_mode, h.path, {})
        srv = OracleServer(toy.oracle_handlers(cell), ctx, TOKEN, out / "oracle_calls.jsonl", spec.query_budget,
                           specs=spec.tools.oracle_tools)
        addr = srv.start()
    src = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "nohome"), "ANTHROPIC_API_KEY": "dummy-not-a-key"}
    env = envmod.build_agent_env("api_key", isolation, harness_out=out, source=src, extra=extra_env)
    env["ANTHROPIC_BASE_URL"] = api.base_url
    req = AgentRequest(cell=cell, prompt=toy.prompt(cell), sandbox=h.path, tools=spec.tools,
                       budget=Budget(4, 120, None), model="haiku", effort=None, isolation=isolation,
                       harness_out=out, oracle_address=addr, oracle_token=TOKEN if addr else None,
                       auth_mode="api_key", env=env,
                       extra={"regime_extra": dict(regime_extra or {})})
    try:
        res = ClaudeCodeAdapter(claude_bin=str(_wrapper(tmp_path))).run(req)
        res.outcome = outcomes.classify(res)
    finally:
        if srv:
            srv.stop()
        sandbox.cleanup(h)
    init = next(json.loads(line) for line in (out / "transcript.jsonl").read_text().splitlines()
                if '"subtype":"init"' in line.replace(" ", ""))
    calls = (out / "oracle_calls.jsonl").read_text().splitlines() if (out / "oracle_calls.jsonl").exists() else []
    return res, init, calls, json.loads((out / "agent_invocation.json").read_text())


def test_api_key_mcp_arm_without_bare_is_exact(iso_dirs, toy, tmp_path):
    with FakeMessagesAPI(call_tool="mcp__dbwm__run_model", tool_input={"plan": {"x": 10}}) as api:
        res, init, calls, inv = _run(toy, tmp_path, "mcp-oracle", api)
    assert "--bare" not in inv["argv"]
    assert res.assertion_failures == [], res.assertion_failures
    assert res.outcome == Outcome.COMPLETED
    assert sorted(init["tools"]) == ["Read", "Write", "mcp__dbwm__run_model"]
    assert init["apiKeySource"] == "ANTHROPIC_API_KEY"
    assert [s["name"] for s in init["mcp_servers"] if s["status"] == "connected"] == ["dbwm"]
    assert len(calls) == 1  # the tool call reached the per-trial oracle server
    assert all(r["path"].startswith("/v1/messages") for r in api.requests if r["method"] == "POST")


def test_bare_keeps_mcp_but_drops_write(iso_dirs, toy, tmp_path):
    with FakeMessagesAPI() as api:
        res, init, _, inv = _run(toy, tmp_path, "mcp-oracle", api, regime_extra={"claude_bare": True})
    assert "--bare" in inv["argv"]
    assert "Write" not in init["tools"] and "Read" in init["tools"] and "mcp__dbwm__run_model" in init["tools"]
    assert [s["name"] for s in init["mcp_servers"] if s["status"] == "connected"] == ["dbwm"]
    assert any("init.tools mismatch" in f and "Write" in f for f in res.assertion_failures)
    assert res.outcome == Outcome.INFRA_INVALID_REGIME


def test_bash_timeout_env_keeps_init_tools(iso_dirs, toy, tmp_path):
    with FakeMessagesAPI() as api:
        res, init, _, inv = _run(toy, tmp_path, "bash-oracle", api, isolation=Isolation.HOST_UNSAFE,
                                 extra_env=envmod.bash_timeout_env(45))
    assert inv["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert sorted(init["tools"]) == ["Bash", "Read", "Write"]
    assert res.assertion_failures == [], res.assertion_failures
