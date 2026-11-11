"""Append-only attempt log, immutable per-attempt directories, scored-attempt selection.

Why: in the paper's harness ``run.log`` was append-only but ``verdict.json``
was overwritten in place, so a cleared and re-run cell left stale log rows
that nothing marked as superseded.
Planners reused one output directory across attempts, so a retry that produced
nothing was scored on the previous attempt's file. Two runner processes
could run the same cell.

Here:
    runs/<run_id>/attempts.jsonl            append-only; one JSON object per line
    runs/<run_id>/cells/<cell_id>/attempt-<k>/   one directory per attempt, never reused
Record types in attempts.jsonl:
    {"type": "attempt", "attempt_id", "run_id", "cell_id", "cell", "k", "started",
     "ended", "outcome", "scored", "regime_hash", "sandbox_sha256", ...}
    {"type": "invalidate", "attempt_id", "reason", "ts"}      excludes one attempt
    {"type": "reset", "cell_id", "reason", "ts"}              excludes every earlier
                                                               attempt of the cell
The scored attempt of a cell is, by rule, the first (lowest k) attempt whose
outcome is in SCORED_OUTCOMES and that is neither invalidated nor before a
reset. Nothing is inferred from which files happen to exist.
A per-cell ``fcntl`` lock stops two processes from running the same cell.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator

from dbwm.core.types import SCORED_OUTCOMES, Cell, Outcome

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows; locking degrades to a no-op
    fcntl = None  # type: ignore[assignment]

ATTEMPTS_FILE = "attempts.jsonl"
_SCORED_VALUES = {o.value for o in SCORED_OUTCOMES}


class CellBusy(RuntimeError):
    """Another process holds this cell's lock."""


def attempt_id(cell_id: str, k: int) -> str:
    return f"{cell_id}#a{k}"


class RunStore:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / ATTEMPTS_FILE

    # -- layout -------------------------------------------------------------------

    def cell_dir(self, cell: Cell | str) -> Path:
        cid = cell.cell_id if isinstance(cell, Cell) else cell
        return self.run_dir / "cells" / cid

    def attempt_dir(self, cell: Cell | str, k: int) -> Path:
        return self.cell_dir(cell) / f"attempt-{k}"

    def existing_attempt_numbers(self, cell: Cell | str) -> list[int]:
        d = self.cell_dir(cell)
        if not d.is_dir():
            return []
        out = []
        for p in d.iterdir():
            m = re.fullmatch(r"attempt-(\d+)", p.name)
            if m:
                out.append(int(m.group(1)))
        return sorted(out)

    def next_attempt_k(self, cell: Cell | str) -> int:
        """1 + the highest k seen in the log OR on disk (a crashed attempt leaves a dir)."""
        cid = cell.cell_id if isinstance(cell, Cell) else cell
        ks = [r.get("k", 0) for r in self.attempt_records(cid)]
        ks += self.existing_attempt_numbers(cid)
        return (max(ks) if ks else 0) + 1

    def new_attempt_dir(self, cell: Cell) -> tuple[int, Path]:
        """Create (exclusively) the next attempt directory."""
        while True:
            k = self.next_attempt_k(cell)
            d = self.attempt_dir(cell, k)
            d.parent.mkdir(parents=True, exist_ok=True)
            try:
                d.mkdir()
                return k, d
            except FileExistsError:
                continue

    # -- log ----------------------------------------------------------------------

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True, default=str) + "\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a torn final line from a crash is ignored, never "repaired"
        return out

    def attempt_records(self, cell_id: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.records() if r.get("type", "attempt") == "attempt"
                and (cell_id is None or r.get("cell_id") == cell_id)]

    def invalidate(self, attempt: str, reason: str) -> None:
        self.append({"type": "invalidate", "attempt_id": attempt, "reason": reason, "ts": time.time()})

    def reset(self, cell_id: str, reason: str) -> None:
        self.append({"type": "reset", "cell_id": cell_id, "reason": reason, "ts": time.time()})

    # -- selection ----------------------------------------------------------------------

    def effective_attempts(self, cell_id: str) -> list[dict[str, Any]]:
        """Attempts of a cell after resets and invalidations, in log order, each with
        ``invalidated_by`` set (None when the attempt counts)."""
        recs = self.records()
        out: list[dict[str, Any]] = []
        for r in recs:
            t = r.get("type", "attempt")
            if t == "attempt" and r.get("cell_id") == cell_id:
                out.append({**r, "invalidated_by": None})
            elif t == "reset" and r.get("cell_id") == cell_id:
                for a in out:
                    if a["invalidated_by"] is None:
                        a["invalidated_by"] = f"reset: {r.get('reason')}"
            elif t == "invalidate":
                for a in out:
                    if a.get("attempt_id") == r.get("attempt_id") and a["invalidated_by"] is None:
                        a["invalidated_by"] = f"invalidate: {r.get('reason')}"
        return out

    def scored_attempt(self, cell_id: str) -> dict[str, Any] | None:
        valid = [a for a in self.effective_attempts(cell_id) if a["invalidated_by"] is None]
        scored = [a for a in valid if a.get("outcome") in _SCORED_VALUES]
        if not scored:
            return None
        return min(scored, key=lambda a: a.get("k", 0))

    def attempts_used(self, cell_id: str) -> int:
        """Attempts that count toward max_attempts_per_cell (valid, i.e. not reset/invalidated)."""
        return sum(1 for a in self.effective_attempts(cell_id) if a["invalidated_by"] is None)

    def cell_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for r in self.attempt_records():
            seen.setdefault(r.get("cell_id"), None)
        return [c for c in seen if c]

    # -- locking -----------------------------------------------------------------------

    @contextlib.contextmanager
    def cell_lock(self, cell: Cell | str, blocking: bool = False) -> Iterator[None]:
        d = self.cell_dir(cell)
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(d / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                except BlockingIOError as e:
                    raise CellBusy(str(cell.cell_id if isinstance(cell, Cell) else cell)) from e
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)


def is_scored(outcome: Outcome | str | None) -> bool:
    if outcome is None:
        return False
    v = outcome.value if isinstance(outcome, Outcome) else str(outcome)
    return v in _SCORED_VALUES
