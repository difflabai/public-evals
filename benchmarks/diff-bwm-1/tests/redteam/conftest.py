"""Fixtures for the red-team tests (leak and isolation regression tests).

Sandboxes and runs go to pytest temp dirs (outside any repository). The fake
claude is copied from tests/core/fake_claude per test so script/log files never
collide with other suites running concurrently.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parents[1]
FAKE_CLAUDE = PKG_ROOT / "tests" / "core" / "fake_claude" / "claude"
EVAL_IDS = ("eval1", "eval2", "eval3", "eval4", "eval5", "eval6")


# Files a test run may legitimately create inside the package tree (bytecode, caches).
_PKG_TREE_IGNORE = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "egg-info")


def _pkg_tree_snapshot() -> set[str]:
    out = set()
    for dirpath, dirs, files in os.walk(PKG_ROOT):
        dirs[:] = [d for d in dirs if not any(x in d for x in _PKG_TREE_IGNORE)]
        rel = os.path.relpath(dirpath, PKG_ROOT)
        for fn in files:
            if not fn.endswith((".pyc", ".pyo")):
                out.add(os.path.normpath(os.path.join(rel, fn)))
        for d in dirs:
            out.add(os.path.normpath(os.path.join(rel, d)) + "/")
    return out


@pytest.fixture(scope="session", autouse=True)
def _no_writes_into_the_package_tree():
    """Fail the session if a red-team test left a new file or directory in the package
    tree (an earlier run let a traceback land in PKG/stderr.txt). Bytecode and tool
    caches are ignored. A concurrent writer in the same tree would also trip this;
    the message lists the paths so that case is easy to tell apart."""
    before = _pkg_tree_snapshot()
    yield
    new = sorted(_pkg_tree_snapshot() - before)
    if new:
        pytest.fail(f"red-team tests created files in the package tree: {new[:20]}", pytrace=False)


@pytest.fixture(autouse=True)
def _cwd_outside_the_package(tmp_path, monkeypatch):
    """Every red-team test runs with cwd in its own temp dir, so a relative-path write
    by any code under test (server, handler, adapter, fake CLI) lands there."""
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("DBWM_RUNS_DIR", str(tmp_path / "runs-default"))


def load_eval(eval_id: str):
    mod = importlib.import_module(f"dbwm.{eval_id}")
    return mod, mod.EVAL


@pytest.fixture
def iso_dirs(tmp_path, monkeypatch):
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
    """The toy eval used by the core runner tests (tests/core/toy_eval.py)."""
    import sys
    core = PKG_ROOT / "tests" / "core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    import toy_eval

    return toy_eval.ToyEval()
