"""Eval-1 locations, via :mod:`dbwm.core.paths` (package-relative, ``DBWM_*_DIR`` overridable).

Resolved at call time, so tests and the sidecar can point the environment
overrides elsewhere. Never relative to the current working directory or to a
repository layout (the paper's ``Path(__file__).parents[3] / "examples"``,
oracle.py:55-65).
"""

from __future__ import annotations

from pathlib import Path

from dbwm.core import paths as core_paths

EVAL_ID = "eval1"
PACKAGE_DIR = Path(__file__).resolve().parent


def data(*parts: str) -> Path:
    """Agent-visible eval-1 data (the task briefs the prompts are rendered from)."""
    return core_paths.data(EVAL_ID, *parts)


def private(*parts: str) -> Path:
    """Scorer/oracle-only eval-1 data (worlds, calibration answer key)."""
    return core_paths.private(EVAL_ID, *parts)
