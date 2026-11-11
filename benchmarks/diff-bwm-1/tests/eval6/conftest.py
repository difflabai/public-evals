"""Shared helpers for the eval-6 tests (no LLM, no network, no Docker).

Most tests call the EVAL object and the eval-6 modules directly. One smoke test
drives the real runner with the stub adapter (tests/eval6/test_eval6_runner.py);
runner-level container e2e and real-sandbox escape tests belong to the core suite.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="eval 6 needs numpy (pip install 'diff-bwm-1[eval6]')")

from dbwm.core.types import Cell  # noqa: E402
from dbwm.eval6 import EVAL, _paths  # noqa: E402
from dbwm.eval6.arms import ARMS  # noqa: E402

PKG_ROOT = Path(__file__).resolve().parents[2]
WORLDS = ("w1", "w2", "w3", "w4", "w5", "w6", "w7", "w9", "w10", "w11", "w12")
ALL_CELLS = [(w, a) for w in WORLDS for a in ARMS]


def cell(world: str = "w1", arm: str = "unaided", rep: int = 0) -> Cell:
    return Cell("eval6", world, arm, rep)


def materialize(c: Cell, dest: Path) -> Path:
    """Copy exactly what the staging manifest lists (plus TASK.md) into dest."""
    m = EVAL.stage(c)
    dest.mkdir(parents=True, exist_ok=True)
    for f in m.files:
        p = dest / f.dst
        p.parent.mkdir(parents=True, exist_ok=True)
        if f.src is not None:
            shutil.copyfile(f.src, p)
        else:
            p.write_bytes(f.content)
    (dest / "TASK.md").write_text(m.task_md)
    return dest


def write_plan(d: Path, plan, claims=None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    if plan is not None:
        (d / "plan.json").write_text(plan if isinstance(plan, str) else json.dumps(plan))
    if claims is not None:
        (d / "claims.json").write_text(claims if isinstance(claims, str) else json.dumps(claims))
    return d


def no_oracle(op, payload):  # eval 6 serves no tools
    raise AssertionError(f"eval 6 has no oracle tools, but the stub called {op!r}")


@pytest.fixture(scope="session")
def answer_keys():
    from dbwm.eval6.score_d1 import load_answer_key
    return {w: load_answer_key(w) for w in WORLDS}


def import_staged(filename: str, modname: str):
    """Import an agent-visible tool from data/eval6/aided_tools without writing __pycache__ there."""
    import importlib.util
    import sys
    sys.path.insert(0, str(_paths.data("aided_tools")))
    dwb, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location(modname, _paths.aided_tool(filename))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
        sys.dont_write_bytecode = dwb
    return mod

