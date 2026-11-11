"""Fixtures for the integration tests: the real CLI (``python -m dbwm``) in subprocesses.

Every run directory and sandbox goes to a pytest temp dir (DBWM_RUNS_DIR,
DBWM_SANDBOX_ROOT), every subprocess runs with cwd in a temp dir, and a session
guard fails the run if any new file or directory appears in the package tree.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parents[1]
FAKE_CLAUDE = PKG_ROOT / "tests" / "core" / "fake_claude" / "claude"
_IGNORE = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "egg-info")


def _tree() -> set[str]:
    out = set()
    for dirpath, dirs, files in os.walk(PKG_ROOT):
        dirs[:] = [d for d in dirs if not any(x in d for x in _IGNORE)]
        rel = os.path.relpath(dirpath, PKG_ROOT)
        out.update(os.path.normpath(os.path.join(rel, f)) for f in files if not f.endswith((".pyc", ".pyo")))
    return out


@pytest.fixture(scope="session", autouse=True)
def _no_writes_into_the_package_tree():
    before = _tree()
    yield
    new = sorted(_tree() - before)
    if new:
        pytest.fail(f"integration tests created files in the package tree: {new[:20]}", pytrace=False)


class Cli:
    """Runs ``python -m dbwm`` with isolated runs/sandbox roots."""

    def __init__(self, base: Path):
        self.base = base
        self.runs = base / "runs"
        self.sandboxes = base / "sandboxes"
        self.cwd = base / "cwd"
        for d in (self.runs, self.sandboxes, self.cwd):
            d.mkdir(parents=True, exist_ok=True)

    def env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("DBWM_")}
        # Test THIS checkout even when a (non-editable) copy of dbwm is installed in site-packages.
        pp = [str(PKG_ROOT)] + [x for x in env.get("PYTHONPATH", "").split(os.pathsep) if x]
        env.update({"DBWM_RUNS_DIR": str(self.runs), "DBWM_SANDBOX_ROOT": str(self.sandboxes),
                    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.pathsep.join(pp)})
        env.update(extra or {})
        return env

    def __call__(self, *args: str, check: bool = True, timeout: float = 900,
                 env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, "-m", "dbwm", *args], cwd=self.cwd, env=self.env(env),
                           capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise AssertionError(f"dbwm {' '.join(args)} -> rc {r.returncode}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
        return r

    def run_dir(self, run_id: str) -> Path:
        return self.runs / run_id


def last_json(text: str) -> Any:
    """The last top-level JSON object printed (``dbwm run`` prints log lines, then a summary)."""
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i] == "{":
            return json.loads("\n".join(lines[i:]))
        if lines[i].startswith("{") and lines[i].rstrip().endswith("}"):
            try:
                return json.loads(lines[i])
            except ValueError:
                pass
    raise AssertionError(f"no JSON object in output: {text[-1500:]}")


def verdicts(run_dir: Path, name: str = "verdict.json") -> dict[str, dict]:
    """cell_id -> the verdict of the cell's LAST attempt."""
    out: dict[str, dict] = {}
    for cell in sorted((run_dir / "cells").iterdir()):
        attempts = sorted(cell.glob("attempt-*"), key=lambda p: int(p.name.split("-")[1]))
        if attempts and (attempts[-1] / name).exists():
            out[cell.name] = json.loads((attempts[-1] / name).read_text())
    return out


def attempts_log(run_dir: Path, kind: str | None = "attempt") -> list[dict]:
    """Records of attempts.jsonl (default: the finished-attempt records only)."""
    recs = [json.loads(x) for x in (run_dir / "attempts.jsonl").read_text().splitlines() if x.strip()]
    return [r for r in recs if kind is None or r.get("type") == kind]


def fake_claude_dir(base: Path, script: Mapping[str, Any]) -> Path:
    """A private copy of the fake claude with its script beside it (it reads fake_script.json there)."""
    d = base / "fakebin"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(FAKE_CLAUDE, d / "claude")
    os.chmod(d / "claude", os.stat(d / "claude").st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (d / "fake_script.json").write_text(json.dumps(script), encoding="utf-8")
    return d


def arm_table(report_md: str, eval_id: str, ruleset: str = "v1") -> dict[str, list[str]]:
    """arm -> cells of the generic per-arm success table under '## Ruleset `<r>` (<eval>)'."""
    head = f"## Ruleset `{ruleset}` ({eval_id})"
    assert head in report_md, f"missing section {head!r}"
    section = report_md.split(head, 1)[1]
    rows: dict[str, list[str]] = {}
    started = False
    for line in section.splitlines():
        if line.startswith("| arm | designed |"):
            started = True
            continue
        if started:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells and not set(cells[0]) <= {"-"}:
                rows[cells[0]] = cells[1:]
    return rows


@pytest.fixture(scope="module")
def cli(tmp_path_factory) -> Cli:
    return Cli(tmp_path_factory.mktemp("dbwm_cli"))


def isolation_args(isolation: str) -> Sequence[str]:
    return ["--isolation", isolation]
