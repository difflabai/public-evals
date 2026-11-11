"""Locations of eval-6 files, via dbwm.core.paths.

Why a wrapper: the paper harness located worlds with ``HERE / "worlds"`` and the
D2 judge defaulted its offline report to a path three directories above the
script (d2_judge.py:250-252), so nothing worked outside the original checkout.
Every eval-6 module resolves files through these helpers instead. They defer to
``dbwm.core.paths`` (env overrides DBWM_DATA_DIR / DBWM_PRIVATE_DIR re-read
on every call). If that module cannot be imported
(a partial checkout), a minimal fallback resolves the package root from this
file and honours the same environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path

EVAL_ID = "eval6"

try:  # pragma: no cover - exercised implicitly whenever core is present
    from dbwm.core import paths as _core
except Exception:  # noqa: BLE001
    _core = None

_PKG_ROOT_FALLBACK = Path(__file__).resolve().parents[2]


def _fallback_root(kind: str) -> Path:
    env = {"data": "DBWM_DATA_DIR", "private": "DBWM_PRIVATE_DIR"}[kind]
    override = os.environ.get(env)
    if override:
        return Path(override).expanduser().resolve()
    return _PKG_ROOT_FALLBACK / kind


def data(*parts: str) -> Path:
    """data/eval6/... (agent-visible files only)."""
    if _core is not None:
        return _core.data(EVAL_ID, *parts)
    return _fallback_root("data").joinpath(EVAL_ID, *parts)


def private(*parts: str) -> Path:
    """private/eval6/... (scorer-only: answer keys, AIDED_v1, certify scan, seeds)."""
    if _core is not None:
        return _core.private(EVAL_ID, *parts)
    return _fallback_root("private").joinpath(EVAL_ID, *parts)


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def world_public(world_id: str, *parts: str) -> Path:
    return data("worlds", world_id, "public", *parts)


def world_aided_v2(world_id: str) -> Path:
    return data("worlds", world_id, "AIDED_v2", "fitted_model.json")


def world_aided_v1(world_id: str) -> Path:
    """The paper's leaky fitted model (carries the world seed): the scorer-side source of AIDED_v2
    (gen/make_fitted_model.py strip_to_v2). Never staged."""
    return private("worlds", world_id, "AIDED_v1", "fitted_model.json")


def answer_key(world_id: str, name: str) -> Path:
    return private("worlds", world_id, "ANSWER_KEY", name)


def aided_tool(name: str) -> Path:
    return data("aided_tools", name)


def certify_scan() -> Path:
    return private("certify_scan.json")


def scoring_seeds() -> Path:
    return private("scoring_seeds.json")
