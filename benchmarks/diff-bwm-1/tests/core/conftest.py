"""Fixtures for the core runtime tests.

Everything runs in pytest temp dirs (outside any repository): sandboxes under
DBWM_SANDBOX_ROOT, runs under DBWM_RUNS_DIR. The fake claude is copied per test
so its script/log/counter files never collide.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parents[1]
FAKE_CLAUDE = HERE / "fake_claude" / "claude"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


@pytest.fixture
def iso_dirs(tmp_path, monkeypatch):
    """DBWM_SANDBOX_ROOT and DBWM_RUNS_DIR in a temp dir; returns (sandbox_root, runs_root)."""
    sb = tmp_path / "sandboxes"
    runs = tmp_path / "runs"
    sb.mkdir()
    runs.mkdir()
    monkeypatch.setenv("DBWM_SANDBOX_ROOT", str(sb))
    monkeypatch.setenv("DBWM_RUNS_DIR", str(runs))
    return sb.resolve(), runs.resolve()


class FakeClaude:
    def __init__(self, d: Path):
        self.dir = d
        self.bin = d / "claude"
        self.log = d / "fake_log.jsonl"
        self.script_path = d / "fake_script.json"

    def script(self, obj) -> "FakeClaude":
        self.script_path.write_text(json.dumps(obj), encoding="utf-8")
        return self

    def calls(self) -> list[dict]:
        if not self.log.is_file():
            return []
        return [json.loads(x) for x in self.log.read_text().splitlines() if x.strip()]


@pytest.fixture
def fake_claude(tmp_path) -> FakeClaude:
    d = tmp_path / "fakebin"
    d.mkdir()
    shutil.copy(FAKE_CLAUDE, d / "claude")
    os.chmod(d / "claude", os.stat(d / "claude").st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return FakeClaude(d)


@pytest.fixture
def toy():
    import toy_eval

    ev = toy_eval.ToyEval()
    return ev
