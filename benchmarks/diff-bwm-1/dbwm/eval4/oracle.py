"""Harness-side ``qa_check`` oracle for the two runnable arms.

The handler runs in the oracle server (host process at HOST level, the sidecar
container at CONTAINER level), never in the sandbox. It reads the agent's
``market_model.xlsx`` from the sandbox, runs the checker registry, and returns
exactly the text the paper's ``qa_check.py`` printed in that mode:

- ``verdict``: ``CHECKLIST RESULT: n/12 PASS`` and one PASS/FAIL line per item;
- ``figures``: the same plus each item's criteria note and measured values and
  a consistency line.

The mode is read from ``ctx.mode`` ONLY (sealed per arm by the server); the
payload is ignored entirely, so ``{"mode": "figures"}`` from a verdict-arm agent
changes nothing (the server also logs such keys as override attempts). The
handler takes no path from the agent: it always checks ``market_model.xlsx`` at
the sandbox root, opens it with O_NOFOLLOW (the CONTAINER sidecar mounts the
package read-only next to the sandbox, so a symlink to a published submission
would otherwise leak its figures), checks the open descriptor is a regular file,
caps the size, and runs the checker on a private copy outside the sandbox.
A workbook that refers to another workbook (external links, ``'[book]Sheet'!A1``
references, defined names, or an ``INDIRECT`` the recalculation refused) is
refused with an error: the checker never opens a second file, so the agent
cannot make the oracle read one (red-team finding RT-X).
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping

from dbwm.core.types import OracleContext

from . import checker

WORKBOOK = "market_model.xlsx"
MAX_WORKBOOK_BYTES = 50 * 1024 * 1024
MODES = ("verdict", "figures")


class QACheckError(ValueError):
    """A user-facing qa_check failure (the server returns ok:false with this text)."""


def _read_workbook_bytes(sandbox: Path) -> bytes:
    """The bytes of ``market_model.xlsx`` at the sandbox root, read safely.

    Opened with O_NOFOLLOW and checked with fstat on the open descriptor, so
    there is no window between "is it a regular file in the sandbox?" and the
    read in which the agent could swap in a symlink (the CONTAINER sidecar
    mounts the package read-only next to the sandbox; a symlink to a published
    submission must never be followed).
    """
    root = Path(sandbox).resolve()
    p = root / WORKBOOK
    # O_NONBLOCK: a FIFO named like the workbook must not stall the oracle thread
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(str(p), flags)
    except FileNotFoundError:
        raise QACheckError(f"{WORKBOOK} not found in the working directory; write your draft there first") from None
    except OSError:
        if p.is_symlink():
            raise QACheckError(f"{WORKBOOK} is a symlink; write the workbook itself into the "
                               "working directory") from None
        raise QACheckError(f"{WORKBOOK} could not be opened") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise QACheckError(f"{WORKBOOK} must be a regular file in the working directory")
        if st.st_size > MAX_WORKBOOK_BYTES:
            raise QACheckError(f"{WORKBOOK} is larger than {MAX_WORKBOOK_BYTES // (1024 * 1024)} MB")
        chunks, total = [], 0
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            total += len(b)
            if total > MAX_WORKBOOK_BYTES:
                raise QACheckError(f"{WORKBOOK} is larger than {MAX_WORKBOOK_BYTES // (1024 * 1024)} MB")
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def check_workbook_bytes(data: bytes, mode: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Run the checker on a private copy of ``data``; -> (printed text, result, recalc info).

    The copy lives in a harness temp dir (never the sandbox) and is deleted
    afterwards; the checker and ``formulas`` only ever see that copy.
    """
    if mode not in MODES:
        raise RuntimeError(f"qa_check: sealed mode must be one of {MODES}, got {mode!r}")
    tmp = Path(tempfile.mkdtemp(prefix="dbwm_eval4_qa_"))
    try:
        wb = tmp / WORKBOOK
        wb.write_bytes(data)
        try:
            res, corpus = checker.evaluate(wb)
        except Exception as e:  # noqa: BLE001 - unreadable workbook is the agent's to fix
            raise QACheckError(f"could not read {WORKBOOK} as an Excel workbook ({type(e).__name__})") from None
        info = checker.recalc_info(corpus)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if corpus.external_refs or info["recalc_status"] == checker.RECALC_REJECTED_EXTERNAL:
        raise QACheckError(f"{WORKBOOK} refers to another workbook (external workbook links are not "
                           "allowed); keep every input and calculation inside this workbook")
    text = checker.format_report(res, mode)
    if info["recalc_status"] == checker.RECALC_UNAVAILABLE or (
            info["recalc_status"] == checker.RECALC_FAILED and checker.recalc_unusable_reason() is not None):
        # The paper's tool always had a working `formulas`; without one (absent, or
        # present but broken, e.g. scipy cannot load), formula outputs are invisible
        # to the checker. Say so rather than silently diverging (review M4). A
        # failure caused by the workbook's own invalid formulas in a working
        # environment prints nothing extra, exactly like the paper's tool.
        text += ("  note: formula cells without cached values could not be recalculated "
                 "in this environment; computed figures were not checked.\n")
    return text, res, info


def qa_check(payload: Mapping[str, Any], ctx: OracleContext) -> str:
    """OracleHandler for op ``qa_check``. Returns the paper tool's printed text.

    ``payload`` is ignored entirely (no path, no mode, no flags are taken from
    the agent); the mode is ``ctx.mode``, sealed per arm by the server.
    """
    mode = ctx.mode
    if mode not in MODES:
        # A misconfigured server, never agent input: refuse rather than guess.
        raise RuntimeError(f"qa_check: sealed mode must be one of {MODES}, got {mode!r}")
    data = _read_workbook_bytes(ctx.sandbox)
    text, res, info = check_workbook_bytes(data, mode)
    st = ctx.state.setdefault("qa_check", {"calls": 0, "history": []})
    st["calls"] += 1
    st["history"].append({"passed": res["summary"]["passed"], "total": res["summary"]["total"],
                          "recalc_status": info["recalc_status"]})
    return text


def handlers_for(oracle_mode: str | None) -> dict:
    """Ops served for an arm: qa_check for the two runnable arms, nothing else."""
    if oracle_mode in MODES:
        return {"qa_check": qa_check}
    return {}
