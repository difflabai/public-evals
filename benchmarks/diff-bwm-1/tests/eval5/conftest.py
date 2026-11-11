"""Shared helpers for the eval-5 tests (no LLM, no network, no Docker).

Every test calls the EVAL object or the eval-5 modules directly; runner-level
end-to-end and real-container leak tests belong to the core suite (wave 2).
"""

from __future__ import annotations

from pathlib import Path

from dbwm.core.types import Cell, OracleContext
from dbwm.eval5 import EVAL
from dbwm.eval5 import dataset as D
from dbwm.eval5.arms import ARM_ORDER, ARMS

PKG_ROOT = Path(__file__).resolve().parents[2]
ALL_CELLS = [(sid, arm) for sid in D.SCENARIOS for arm in ARM_ORDER]


def cell(sid: str, arm: str = "baseline", rep: int = 0) -> Cell:
    return Cell("eval5", sid, arm, rep)


def ctx_for(sid: str, arm: str, sandbox: Path, mode: str | None = None) -> OracleContext:
    a = ARMS[arm]
    return OracleContext(cell=cell(sid, arm), arm=a, mode=mode if mode is not None else a.oracle_mode,
                         sandbox=Path(sandbox), state={})


def direct_caller(sid: str, arm: str, sandbox: Path, log: list | None = None):
    """call_oracle wired straight to EVAL.oracle_handlers (no server), sealed mode from the arm."""
    handlers = EVAL.oracle_handlers(cell(sid, arm))
    ctx = ctx_for(sid, arm, sandbox)

    def call(op: str, payload: dict):
        if log is not None:
            log.append((op, dict(payload)))
        return handlers[op](payload, ctx)
    return call
