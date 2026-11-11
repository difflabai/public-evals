"""Turn budget backstop, wall-clock rail and process-group teardown.

Why: the paper's budgets were dollars (``--max-budget-usd 2/3/4/8``) plus a
clock, and how hard that proxy for "equal effort" binds depends on the model
that runs, so the instrument changes with the model (ledger A14; paper v7
Appendix A entry 14). v7's lesson: pin the iteration budget, let cost float
and record it as an outcome. The primary budget is now
agent turns: ``--max-turns K`` for Claude Code (present but hidden in 2.1.287)
and, as an adapter-agnostic backstop, a harness counter that kills the agent's
process group once it has produced K+2 distinct assistant messages. Wall clock
and USD are rails: a trial that hits one is scored, never retried; its cost is
in the verdict either way.

The kill is SIGTERM to the whole process group, then SIGKILL after a grace
period, so grandchildren holding the agent's stdout die too (the paper's
``subprocess.run(..., timeout=)`` could block on a grandchild holding the
pipe; ledger G02; the group kill itself is ported from run_confirmatory.py:296-310).
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Any

from dbwm.core.stream_json import StreamSummary

BACKSTOP_SLACK = 2  # kill at max_turns + 2 distinct assistant messages
DEFAULT_GRACE_S = 5.0


class TurnBackstop:
    """Counts distinct assistant message ids; ``tripped`` once the count reaches max_turns+2."""

    def __init__(self, max_turns: int, slack: int = BACKSTOP_SLACK):
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        self.max_turns = max_turns
        self.limit = max_turns + slack
        self.summary = StreamSummary()

    def feed_line(self, line: str, ts: float | None = None) -> Any:
        return self.summary.feed_line(line, ts)

    @property
    def count(self) -> int:
        return self.summary.turns_counted

    @property
    def tripped(self) -> bool:
        return self.count >= self.limit


class Deadline:
    """Monotonic wall-clock rail."""

    def __init__(self, seconds: float):
        self.seconds = float(seconds)
        self.start = time.monotonic()
        self.at = self.start + self.seconds

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.at

    @property
    def remaining(self) -> float:
        return max(0.0, self.at - time.monotonic())

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def kill_process_group(proc: subprocess.Popen | None, pgid: int | None = None,
                       grace_s: float = DEFAULT_GRACE_S) -> None:
    """SIGTERM the process group, wait up to ``grace_s``, then SIGKILL; reap the leader.

    ``pgid`` defaults to ``proc.pid`` (processes started with
    start_new_session=True lead their own group). Safe to call when the leader
    or the whole group has already exited.
    """
    if pgid is None:
        if proc is None:
            return
        pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if proc is not None:
            try:
                proc.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        else:
            time.sleep(0.1)
        if not _group_alive(pgid):
            break
    if _group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if proc is not None:
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            pass
    # Grandchildren re-parented away still belong to the group; give the
    # kernel a moment to deliver SIGKILL before the caller inspects files.
    end = time.monotonic() + 2.0
    while _group_alive(pgid) and time.monotonic() < end:
        time.sleep(0.05)


class Supervised:
    """What run_supervised observed."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed_by: str | None = None
        self.duration_s: float = 0.0
        self.timestamps: list[float] = []
        self.summary: StreamSummary = StreamSummary()
        self.pid: int | None = None
        self.start_error: str | None = None


def run_supervised(argv: list[str], *, cwd: str | os.PathLike | None, env: dict[str, str],
                   stdin_data: bytes | None, stdout_path: os.PathLike, stderr_path: os.PathLike,
                   wall_clock_s: float, max_turns: int | None,
                   on_event: Any = None, on_kill: Any = None, abort_event: Any = None,
                   poll_s: float = 0.1, grace_s: float = DEFAULT_GRACE_S) -> Supervised:
    """Run an agent process under the turn backstop and the wall-clock rail.

    stdout goes straight to ``stdout_path`` (a file, not a pipe: a runner that
    stops must not SIGPIPE its children; ledger G05) and is tailed line by
    line. ``on_event(ev)`` sees each decoded JSON event and may return a kill
    reason string (the claude_code adapter returns "invalid_regime" when the
    init event fails the runtime assertions). ``on_kill()`` runs before the
    process-group kill (the container path uses it to ``docker kill`` the
    agent container). ``abort_event`` (a threading.Event) set by the runner
    on a second operator interrupt kills the agent with reason "interrupt"
    (classified as a retryable CRASH, never as the agent's failure). After the
    leader exits, any processes left in its group are torn down too.
    """
    sup = Supervised()
    backstop = TurnBackstop(max_turns) if max_turns else None
    deadline = Deadline(wall_clock_s)
    out_fh = open(stdout_path, "wb")
    err_fh = open(stderr_path, "wb")
    try:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env,
                                    stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                                    stdout=out_fh, stderr=err_fh, start_new_session=True)
        except OSError as e:
            sup.start_error = f"{type(e).__name__}: {e}"
            err_fh.write(f"[dbwm] could not start agent: {sup.start_error}\n".encode())
            return sup
        sup.pid = proc.pid
        if stdin_data is not None:
            def _feed() -> None:
                try:
                    proc.stdin.write(stdin_data)  # type: ignore[union-attr]
                    proc.stdin.close()  # type: ignore[union-attr]
                except (BrokenPipeError, OSError, ValueError):
                    pass

            threading.Thread(target=_feed, name="dbwm-stdin", daemon=True).start()
        reader = open(stdout_path, "rb")
        pending = b""

        def _drain() -> str | None:
            nonlocal pending
            chunk = reader.read()
            if not chunk:
                return None
            pending += chunk
            *lines, pending = pending.split(b"\n")
            first_reason: str | None = None
            for raw in lines:
                ts = time.time()
                sup.timestamps.append(ts)
                line = raw.decode("utf-8", "replace")
                ev = sup.summary.feed_line(line, ts)
                if backstop is not None:
                    backstop.summary = sup.summary
                if ev is not None and on_event is not None and first_reason is None:
                    reason = on_event(ev)
                    if reason:
                        first_reason = str(reason)
                if first_reason is None and backstop is not None and backstop.tripped:
                    first_reason = "turns"
            return first_reason

        try:
            while True:
                reason = _drain()
                if reason is None and deadline.expired:
                    reason = "clock"
                if reason is None and abort_event is not None and abort_event.is_set():
                    reason = "interrupt"
                if reason is not None:
                    sup.killed_by = reason
                    if on_kill is not None:
                        try:
                            on_kill()
                        except Exception:  # noqa: BLE001 - teardown must continue
                            pass
                    kill_process_group(proc, grace_s=grace_s)
                    break
                if proc.poll() is not None:
                    break
                time.sleep(poll_s)
            # Final drain of whatever was written before exit/kill. Kill reasons
            # found here are moot (the process is gone); the adapter re-checks
            # the complete transcript afterwards.
            _drain()
            if pending.strip():
                sup.timestamps.append(time.time())
                sup.summary.feed_line(pending.decode("utf-8", "replace"), time.time())
                pending = b""
        finally:
            reader.close()
            # Leader gone (or killed): tear down anything left in its group.
            kill_process_group(proc, grace_s=1.0)
            sup.returncode = proc.returncode
    finally:
        sup.duration_s = round(deadline.elapsed, 3)
        out_fh.close()
        err_fh.close()
    return sup
