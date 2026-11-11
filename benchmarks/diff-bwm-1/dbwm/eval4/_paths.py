"""Package-relative paths for eval 4.

Every location is derived from this file's position in the package (never from
the caller's cwd or an operator workspace layout -- the paper harness's
``HERE.parent.parent / "data" / "aa-briefcase-lite"`` assumed a private repo
layout; see docs/instrument-changes/eval4.md).

``dbwm.core.paths`` is the canonical resolver. It is used when present; when it
is not (eval builders were written concurrently with core), this module falls
back to the same rules: PKG root from ``__file__``, overridable with
``DBWM_DATA_DIR`` / ``DBWM_PRIVATE_DIR`` (each pointing at the directory that
CONTAINS the per-eval subdirectories).
"""

from __future__ import annotations

import os
from pathlib import Path

EVAL_ID = "eval4"
DATASET_DIRNAME = "aa-briefcase-lite"

PKG_ROOT = Path(__file__).resolve().parents[2]
EVAL_PKG_DIR = Path(__file__).resolve().parent

try:  # pragma: no cover - exercised only once core lands
    from dbwm.core import paths as _core_paths  # type: ignore
except Exception:  # noqa: BLE001 - core may be absent or mid-edit
    _core_paths = None


def _root(env: str, default: str) -> Path:
    v = os.environ.get(env)
    return Path(v).expanduser().resolve() if v else PKG_ROOT / default


def _core(fn: str, *parts: str) -> Path | None:
    f = getattr(_core_paths, fn, None) if _core_paths is not None else None
    if f is None:
        return None
    try:
        return Path(f(EVAL_ID, *parts))
    except Exception:  # noqa: BLE001 - fall back to local rules
        return None


def data(*parts: str) -> Path:
    """Agent-visible eval-4 data: ``<DATA>/eval4/...``."""
    p = _core("data", *parts)
    return p if p is not None else _root("DBWM_DATA_DIR", "data").joinpath(EVAL_ID, *parts)


def private(*parts: str) -> Path:
    """Scorer/oracle-only eval-4 data: ``<PRIVATE>/eval4/...``."""
    p = _core("private", *parts)
    return p if p is not None else _root("DBWM_PRIVATE_DIR", "private").joinpath(EVAL_ID, *parts)


def dataset_public(*parts: str) -> Path:
    """The agent-visible half of the fetched AA-Briefcase-Lite revision."""
    return data(DATASET_DIRNAME, *parts)


def dataset_private(*parts: str) -> Path:
    """The grader half (rubric, judge prompts, published submissions)."""
    return private(DATASET_DIRNAME, *parts)


def using_core_paths() -> bool:
    return _core_paths is not None and hasattr(_core_paths, "data")
