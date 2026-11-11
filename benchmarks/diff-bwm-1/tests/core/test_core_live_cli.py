"""Opt-in live check of the claude_code adapter and the MCP shim against the REAL Claude Code CLI.

Makes one short model call (haiku, --max-turns 4), so it never runs by default:

    DBWM_LIVE_SMOKE=1 python -m pytest tests/core/test_core_live_cli.py

Needs a logged-in CLI (session auth at host isolation). It asserts what the release depends on: the MCP tool
appears as ``mcp__dbwm__run_model`` in init.tools, the runtime assertions pass, the call reaches the per-trial
oracle server, and the attempt is scored. (This was run when the package was built, with Claude Code 2.1.287.)
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from dbwm.core import regime as regime_mod
from dbwm.core.runner import RunOptions, Runner
from dbwm.core.verdict import read_verdict

pytestmark = pytest.mark.skipif(
    os.environ.get("DBWM_LIVE_SMOKE") != "1" or not shutil.which("claude"),
    reason="live CLI check: set DBWM_LIVE_SMOKE=1 (one short haiku call) with a logged-in claude on PATH")


def test_live_mcp_arm_host(iso_dirs, toy):
    regime = regime_mod.build_regime(toy, {"model": "haiku", "adapter": "claude_code", "auth_mode": "session",
                                           "arms": ["mcp-oracle"], "instances": ["alpha"], "max_turns": 4,
                                           "wall_clock_s": 240, "max_attempts_per_cell": 1})
    res = Runner(toy, regime, RunOptions(run_id="live", runs_root=iso_dirs[1], install_signal_handlers=False)).run()
    adir = res.run_dir / "cells" / "alpha__mcp-oracle__r0" / "attempt-1"
    v = read_verdict(adir / "verdict.json")
    assert v["scored"], v["details"]
    assert v["details"]["assertion_failures"] == []
    init = json.loads((adir / "transcript.jsonl").read_text().splitlines()[0])
    assert "mcp__dbwm__run_model" in init["tools"]
    assert v["process"]["oracle_calls"] >= 1
