"""The claude_code adapter against the fake claude: argv/env/cwd per tool policy and isolation,
runtime init assertions, turn backstop, clock rail, outcome classification."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import pytest

from dbwm.core import env as envmod
from dbwm.core import outcomes, sandbox
from dbwm.core.adapters.claude_code import (
    BARE_BUILTINS,
    FORBIDDEN_FLAGS,
    VERIFIED_CLI_VERSIONS,
    ClaudeCodeAdapter,
    build_argv,
    cli_version_verified,
    use_bare,
)
from dbwm.core.oracle.server import OracleServer, read_calls_log
from dbwm.core.types import AgentRequest, Budget, Cell, Isolation, OracleContext, Outcome

TOKEN = "tok_" + secrets.token_hex(12)


class Trial:
    """Sandbox + (host) oracle server + request for one toy cell."""

    def __init__(self, toy, arm, tmp_path, isolation=Isolation.HOST, auth="none", max_turns=6, wall=60,
                 usd=None, effort=None, model="haiku", source_env=None):
        self.cell = Cell("toy", "alpha", arm, 0)
        self.arm = toy.arms[arm]
        self.h = sandbox.build_sandbox(toy.stage(self.cell), self.cell, self.arm.tools, self.arm.tools.oracle_tools)
        self.out = tmp_path / f"attempt-{arm}"
        self.out.mkdir()
        self.srv = None
        addr = None
        if self.arm.tools.oracle_tools:
            ctx = OracleContext(self.cell, self.arm, self.arm.oracle_mode, self.h.path, {})
            self.srv = OracleServer(toy.oracle_handlers(self.cell), ctx, TOKEN, self.out / "oracle_calls.jsonl",
                                    self.arm.query_budget, specs=self.arm.tools.oracle_tools)
            addr = self.srv.start()
        src = dict(os.environ) if source_env is None else source_env
        self.env = envmod.build_agent_env(auth, isolation, harness_out=self.out, source=src)
        self.req = AgentRequest(cell=self.cell, prompt=toy.prompt(self.cell), sandbox=self.h.path,
                                tools=self.arm.tools, budget=Budget(max_turns, wall, usd), model=model,
                                effort=effort, isolation=isolation, harness_out=self.out, oracle_address=addr,
                                oracle_token=TOKEN if addr else None, auth_mode=auth, env=self.env)

    def run(self, fake):
        try:
            res = ClaudeCodeAdapter(claude_bin=str(fake.bin)).run(self.req)
            res.outcome = outcomes.classify(res)
            self.answer = (self.h.path / "answer.json").read_text() if (self.h.path / "answer.json").exists() else None
            return res
        finally:
            if self.srv:
                self.srv.stop()
            sandbox.cleanup(self.h)


MCP_SCRIPT = {"actions": [
    {"op": "read", "path": "TASK.md"},
    {"op": "mcp", "tool": "run_model", "arguments": {"plan": {"x": 10}}},
    {"op": "write", "path": "answer.json", "json": {"y": "$last.y", "claimed_success": True}},
]}


def test_mcp_arm_host_argv_env_cwd(iso_dirs, toy, fake_claude, tmp_path):
    t = Trial(toy, "mcp-oracle", tmp_path)
    res = t.run(fake_claude.script(MCP_SCRIPT))
    call = fake_claude.calls()[0]
    argv = call["argv"]
    mcp_cfg = str(t.out / "mcp.json")
    assert argv == ["-p", "--model", "haiku", "--tools", "Read,Write,mcp__dbwm__run_model",
                    "--allowedTools", "Read,Write,mcp__dbwm__run_model", "--permission-prompts", "none",
                    "--restricted", "--strict-mcp-config", "--mcp-config", mcp_cfg, "--disable-slash-commands",
                    "--no-session-persistence", "--max-turns", "6", "--output-format", "stream-json", "--verbose"]
    for f in FORBIDDEN_FLAGS:
        assert f not in argv
    assert Path(call["cwd"]).resolve() == t.h.path.resolve()
    assert call["stdin"] == toy.prompt(t.cell)
    env = call["env"]
    assert "DBWM_ORACLE" not in env and "DBWM_ORACLE_TOKEN" not in env  # MCP arm: only the shim gets them
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TERM", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
               "DISABLE_AUTOUPDATER"}
    inv_env = json.loads((t.out / "agent_invocation.json").read_text())["env"]
    assert set(inv_env) <= allowed, set(inv_env) - allowed  # exactly what the harness passed
    # What the process saw may also hold variables its interpreter launcher adds
    # (macOS /usr/bin/python3 is an xcrun shim that sets SDKROOT, CPATH, ...).
    platform_added = {"PWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING", "LC_CTYPE", "SDKROOT", "CPATH",
                      "LIBRARY_PATH", "MANPATH"}
    assert set(env) <= allowed | platform_added, set(env) - allowed - platform_added
    # the MCP config and shim live outside the sandbox
    assert not mcp_cfg.startswith(str(t.h.path))
    cfg = json.loads(Path(mcp_cfg).read_text())
    assert cfg["mcpServers"]["dbwm"]["env"]["DBWM_ORACLE_TOKEN"] == TOKEN
    assert res.outcome == Outcome.COMPLETED, (res.assertion_failures, res.error_text)
    assert res.assertion_failures == []
    assert res.tools_available == ["Read", "Write", "mcp__dbwm__run_model"]
    assert [c.name for c in res.tool_calls] == ["Read", "mcp__dbwm__run_model", "Write"]
    assert json.loads(t.answer) == {"y": 73.1415, "claimed_success": True}
    assert [r["ok"] for r in read_calls_log(t.out / "oracle_calls.jsonl")] == [True]
    assert res.cli_version.startswith("2.1.287-fake")
    inv = json.loads((t.out / "agent_invocation.json").read_text())
    assert inv["expected_init"]["tools"] == ["Read", "Write", "mcp__dbwm__run_model"]


def test_no_tool_arm_has_no_mcp(iso_dirs, toy, fake_claude, tmp_path):
    t = Trial(toy, "no-tool", tmp_path)
    res = t.run(fake_claude.script({"actions": [{"op": "write", "path": "answer.json", "content": "{}"}]}))
    argv = fake_claude.calls()[0]["argv"]
    assert "--mcp-config" not in argv and argv[argv.index("--tools") + 1] == "Read,Write"
    assert res.outcome == Outcome.COMPLETED and res.assertion_failures == []


def test_restricted_read_outside_sandbox_is_refused(iso_dirs, toy, fake_claude, tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("secret")
    t = Trial(toy, "no-tool", tmp_path)
    res = t.run(fake_claude.script({"actions": [{"op": "read", "path": str(secret)}]}))
    assert res.outcome == Outcome.COMPLETED


def test_bash_arm_host_unsafe_gets_oracle_env_and_shim(iso_dirs, toy, fake_claude, tmp_path):
    t = Trial(toy, "bash-oracle", tmp_path, isolation=Isolation.HOST_UNSAFE)
    res = t.run(fake_claude.script({"actions": [
        {"op": "bash", "command": "./toy_model input.json", "stdout_only": True},
        {"op": "write", "path": "answer.json", "json": {"y": "$last.y", "claimed_success": True}}]}))
    call = fake_claude.calls()[0]
    assert call["argv"][call["argv"].index("--tools") + 1] == "Read,Write,Bash"
    assert "--mcp-config" not in call["argv"]
    assert call["env"]["DBWM_ORACLE"].startswith("tcp://127.0.0.1:") and call["env"]["DBWM_ORACLE_TOKEN"] == TOKEN
    assert res.outcome == Outcome.COMPLETED, res.assertion_failures
    assert json.loads(t.answer)["y"] == 73.1415


def test_api_key_auth_isolated_config_and_no_bare(iso_dirs, toy, fake_claude, tmp_path):
    """Review M1: --bare drops Write/Glob/Grep on 2.1.287, so it is not added by default."""
    src = {"PATH": os.environ["PATH"], "HOME": "/nonexistent-home", "ANTHROPIC_API_KEY": "dummy-api-key-for-tests"}
    t = Trial(toy, "no-tool", tmp_path, auth="api_key", source_env=src, effort="high", usd=1.5)
    t.run(fake_claude.script({}))
    call = fake_claude.calls()[0]
    argv = call["argv"]
    assert "--bare" not in argv and argv[-2:] == ["stream-json", "--verbose"]
    assert call["env"]["DISABLE_AUTOUPDATER"] == "1"
    assert argv[argv.index("--effort") + 1] == "high" and argv[argv.index("--max-budget-usd") + 1] == "1.5"
    assert call["env"]["ANTHROPIC_API_KEY"] == "dummy-api-key-for-tests"
    assert call["env"]["CLAUDE_CONFIG_DIR"] == str(t.out / "claude-config")
    assert call["env"]["HOME"] == str(t.out / "home")
    inv = json.loads((t.out / "agent_invocation.json").read_text())
    assert inv["env"]["ANTHROPIC_API_KEY"] == "<redacted>"
    assert "dummy-api-key-for-tests" not in (t.out / "agent_invocation.json").read_text()


@pytest.mark.parametrize("script,needle", [
    ({"init_overrides": {"tools": ["Read", "Write", "Bash", "mcp__dbwm__run_model"]}}, "init.tools mismatch"),
    ({"init_overrides": {"cwd": "/tmp/elsewhere"}}, "init.cwd"),
    ({"init_overrides": {"skills": ["leaked-operator-skill"]}}, "init.skills"),
    ({"init_overrides": {"model": "claude-opus-4-8"}}, "init.model"),
    ({"init_overrides": {"mcp_servers": [{"name": "dbwm", "status": "failed"}]}}, "not connected"),
    ({"served_model": "claude-sonnet-4-6"}, "pinned model"),
])
def test_init_assertion_failure_kills_and_is_invalid_regime(iso_dirs, toy, fake_claude, tmp_path, script, needle):
    t = Trial(toy, "mcp-oracle", tmp_path)
    script = {**script, "actions": [{"op": "sleep", "seconds": 30},
                                    {"op": "write", "path": "answer.json", "content": "{}"}]}
    res = t.run(fake_claude.script(script))
    assert res.outcome == Outcome.INFRA_INVALID_REGIME
    assert any(needle in f for f in res.assertion_failures), res.assertion_failures
    assert res.duration_s < 20  # killed on the init event, not after the sleep
    assert t.answer is None


def test_turn_backstop_through_adapter(iso_dirs, toy, fake_claude, tmp_path):
    t = Trial(toy, "no-tool", tmp_path, max_turns=2)
    res = t.run(fake_claude.script({"actions": [{"op": "turns", "n": 40, "interval": 0.02}]}))
    assert res.killed_by_harness == "turns" and res.outcome == Outcome.BUDGET_EXHAUSTED
    assert res.assistant_messages >= 4


def test_cli_max_turns_result_is_budget_exhausted(iso_dirs, toy, fake_claude, tmp_path):
    t = Trial(toy, "no-tool", tmp_path, max_turns=1)
    res = t.run(fake_claude.script({"actions": [{"op": "write", "path": "a.txt", "content": "1"},
                                                {"op": "write", "path": "b.txt", "content": "2"}]}))
    assert res.result_subtype == "error_max_turns" and res.outcome == Outcome.BUDGET_EXHAUSTED


def test_clock_rail_through_adapter(iso_dirs, toy, fake_claude, tmp_path):
    pidfile = tmp_path / "gc.pid"
    t = Trial(toy, "no-tool", tmp_path, wall=2)
    res = t.run(fake_claude.script({"actions": [{"op": "grandchild", "seconds": 120, "pidfile": str(pidfile)},
                                                {"op": "sleep", "seconds": 120}]}))
    assert res.killed_by_harness == "clock" and res.outcome == Outcome.CLOCK_STOP
    assert res.duration_s < 20
    from test_core_budget_attempts import alive

    gpid = int(pidfile.read_text())
    import time

    end = time.time() + 5
    while alive(gpid) and time.time() < end:
        time.sleep(0.1)
    assert not alive(gpid)


@pytest.mark.parametrize("script,want", [
    ({"actions": [{"op": "result", "is_error": True, "result": "Not logged in · Please run /login",
                   "num_turns": 1, "total_cost_usd": 0}]}, Outcome.INFRA_AUTH),
    ({"actions": [{"op": "rate_limit", "status": "rejected", "resetsAt": 4_000_000_000},
                  {"op": "result", "is_error": True, "result": "You've hit your limit · resets 5pm",
                   "num_turns": 1, "total_cost_usd": 0}]}, Outcome.INFRA_CAP),
    ({"actions": [{"op": "result", "is_error": True, "result": "API Error: 529 overloaded", "num_turns": 4,
                   "total_cost_usd": 0.3, "api_error_status": 529}]}, Outcome.INFRA_API),
    ({"actions": [{"op": "stderr", "text": "segfault"}, {"op": "exit", "code": 139}]}, Outcome.CRASH),
    ({"actions": [{"op": "text", "text": "I hit my usage limit of patience; done"}]}, Outcome.COMPLETED),
])
def test_outcomes_through_adapter(iso_dirs, toy, fake_claude, tmp_path, script, want):
    t = Trial(toy, "no-tool", tmp_path)
    res = t.run(fake_claude.script(script))
    assert res.outcome == want, (res.result_subtype, res.error_text, res.assertion_failures)


def test_missing_cli_is_crash(iso_dirs, toy, tmp_path):
    t = Trial(toy, "no-tool", tmp_path)
    try:
        res = ClaudeCodeAdapter(claude_bin=str(tmp_path / "no-such-claude")).run(t.req)
    finally:
        sandbox.cleanup(t.h)
    assert outcomes.classify(res) == Outcome.CRASH and "could not start" in res.error_text.lower() + \
        (t.out / "stderr.txt").read_text().lower()


def test_build_argv_never_contains_forbidden(toy):
    for arm in toy.arms.values():
        req = AgentRequest(cell=Cell("toy", "alpha", arm.name, 0), prompt="p", sandbox=Path("/x"), tools=arm.tools,
                           budget=Budget(3, 10), model="m", effort=None, isolation=Isolation.HOST,
                           harness_out=Path("/h"), oracle_address=None, oracle_token=None, auth_mode="session",
                           env={})
        argv = build_argv(req)
        assert not set(argv) & set(FORBIDDEN_FLAGS)
        assert argv[:2] == ["claude", "-p"] and "--restricted" in argv and "--strict-mcp-config" in argv


def _req(toy, arm="mcp-oracle", auth="api_key", regime_extra=None):
    a = toy.arms[arm]
    return AgentRequest(cell=Cell("toy", "alpha", arm, 0), prompt="p", sandbox=Path("/x"), tools=a.tools,
                        budget=Budget(3, 10), model="m", effort=None, isolation=Isolation.HOST,
                        harness_out=Path("/h"), oracle_address=None, oracle_token=None, auth_mode=auth,
                        env={}, extra={} if regime_extra is None else {"regime_extra": regime_extra})


@pytest.mark.parametrize("auth,extra,want", [
    ("api_key", None, False),                       # default: no --bare (it drops Write/Glob/Grep)
    ("api_key", {"claude_bare": False}, False),
    ("api_key", {"claude_bare": "yes"}, False),     # only a JSON true opts in
    ("api_key", {"claude_bare": True}, True),       # explicit opt-in
    ("session", {"claude_bare": True}, False),      # --bare never reads the keychain: api_key only
    ("oauth_token", {"claude_bare": True}, False),  # --bare ignores OAuth: api_key only
])
def test_bare_is_opt_in_and_api_key_only(toy, auth, extra, want):
    req = _req(toy, auth=auth, regime_extra=extra)
    assert use_bare(req) is want
    assert ("--bare" in build_argv(req, mcp_config="/h/mcp.json")) is want


def test_bare_builtins_document_the_tool_reduction(toy):
    """Every canonical no-bash arm needs Write, which --bare removes (verified on 2.1.287)."""
    from dbwm.core import stream_json

    tools = set(stream_json.claude_tool_names(toy.arms["mcp-oracle"].tools))
    assert "Write" in tools and not tools - {"mcp__dbwm__run_model"} <= BARE_BUILTINS


@pytest.mark.parametrize("text,want", [
    ("2.1.287 (Claude Code)", True), ("2.1.287", True), ("2.2.0 (Claude Code)", False),
    ("2.1.28 (Claude Code)", False), ("", None), (None, None)])
def test_cli_version_verified(text, want):
    assert "2.1.287" in VERIFIED_CLI_VERSIONS
    assert cli_version_verified(text) is want


def test_unverified_cli_version_is_recorded_in_invocation(iso_dirs, toy, fake_claude, tmp_path):
    t = Trial(toy, "no-tool", tmp_path)
    t.run(fake_claude.script({}))
    inv = json.loads((t.out / "agent_invocation.json").read_text())
    assert inv["cli_version_verified"] is cli_version_verified(inv["cli_version"])


def test_fake_init_event_carries_memory_paths(iso_dirs, toy, fake_claude, tmp_path):
    """The fake reports memory_paths like 2.1.287 (the adapter's assertion on it: test_core_v7_runtime.py).

    Shape: {"auto": "<CLAUDE_CONFIG_DIR>/projects/<cwd with every non-alphanumeric char -> '-'>/memory/"};
    "init_drop" removes it, for the "absent" case.
    """
    src = {"PATH": os.environ["PATH"], "HOME": "/nonexistent-home", "ANTHROPIC_API_KEY": "dummy-api-key-for-tests"}
    t = Trial(toy, "no-tool", tmp_path, auth="api_key", source_env=src)
    cwd = os.path.realpath(t.h.path)
    res = t.run(fake_claude.script({}))
    auto = res.raw_init_event["memory_paths"]["auto"]
    slug = "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in cwd)
    assert auto == os.path.join(str(t.out / "claude-config"), "projects", slug, "memory") + os.sep
    assert not Path(auto).exists()  # reported, never created
    (tmp_path / "second").mkdir()
    t2 = Trial(toy, "no-tool", tmp_path / "second", auth="api_key", source_env=src)
    res2 = t2.run(fake_claude.script({"init_drop": ["memory_paths"]}))
    assert "memory_paths" not in res2.raw_init_event
