"""Shared fixtures for the eval-1 tests (no LLM, no network, no runner)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def ev():
    from dbwm.eval1 import EVAL

    return EVAL


@pytest.fixture(scope="session")
def goldens() -> dict:
    return json.loads((FIXTURES / "paper_goldens.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def golden_inputs() -> dict:
    return json.loads((FIXTURES / "paper_goldens_inputs.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def calibration() -> dict:
    from dbwm.eval1.engine import load_calibration

    return {e["model_id"]: e for e in load_calibration()}


def write_submission(d: Path, plan: Any = None, claims: Any = None, *, raw_plan: str | None = None,
                     raw_claims: str | None = None) -> Path:
    """Write plan.json / claims.json into ``d`` (None = file absent)."""
    d.mkdir(parents=True, exist_ok=True)
    if raw_plan is not None:
        (d / "plan.json").write_text(raw_plan, encoding="utf-8")
    elif plan is not None:
        (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    if raw_claims is not None:
        (d / "claims.json").write_text(raw_claims, encoding="utf-8")
    elif claims is not None:
        (d / "claims.json").write_text(json.dumps(claims), encoding="utf-8")
    return d


@pytest.fixture
def submit(tmp_path):
    counter = {"n": 0}

    def _submit(plan=None, claims=None, **kw) -> Path:
        counter["n"] += 1
        return write_submission(tmp_path / f"sub{counter['n']}", plan, claims, **kw)

    return _submit


def direct_oracle(ev, cell, sandbox: Path, log: list | None = None):
    """call_oracle wired straight to the eval's handlers, with the arm's sealed mode and budget.

    Mirrors what the core oracle server guarantees (mode from the arm only,
    override keys stripped, budget enforced) without starting a server.
    """
    from dbwm.core.types import OracleContext

    arm = ev.arms[cell.arm]
    handlers = ev.oracle_handlers(cell)
    ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=Path(sandbox), state={})
    state = {"calls": 0}

    def call(op: str, payload: dict):
        if op not in handlers:
            raise RuntimeError(f"no oracle op {op!r} for arm {cell.arm}")
        if arm.query_budget is not None and state["calls"] >= arm.query_budget:
            raise RuntimeError("query budget exhausted")
        state["calls"] += 1
        clean = {k: v for k, v in payload.items() if k not in ("mode", "channel", "backend", "world", "path")}
        res = handlers[op](clean, ctx)
        if log is not None:
            log.append((op, payload, res))
        return res

    call.state = state  # type: ignore[attr-defined]
    return call


@pytest.fixture(scope="session")
def oracle_for():
    """``oracle_for(ev, cell, sandbox, log=None)`` -> call_oracle (see direct_oracle)."""
    return direct_oracle
