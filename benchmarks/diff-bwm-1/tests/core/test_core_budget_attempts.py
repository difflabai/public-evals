"""Turn backstop, wall-clock rail with process-group kill, attempts log selection, launch interleave."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

from dbwm.core import attempts as attempts_mod
from dbwm.core.budget import TurnBackstop, kill_process_group, run_supervised
from dbwm.core.runner import interleave
from dbwm.core.types import Cell, Outcome

EMIT_TURNS = r"""
import json, sys, time
for i in range(int(sys.argv[1])):
    for blk in ("thinking", "text"):
        print(json.dumps({"type": "assistant", "message": {"id": "m%d" % i, "content": [{"type": blk}]}}), flush=True)
    time.sleep(0.02)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": int(sys.argv[1])}), flush=True)
"""

GRANDCHILD = r"""
import json, subprocess, sys, time
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])  # inherits our stdout
open(sys.argv[1], "w").write(str(g.pid))
print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
time.sleep(120)
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie still answers kill(0); check its state
    try:
        import subprocess

        st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(st) and not st.startswith("Z")
    except OSError:
        return True


def test_backstop_counts_distinct_ids():
    b = TurnBackstop(2)
    for i in range(3):
        b.feed_line(json.dumps({"type": "assistant", "message": {"id": f"m{i}"}}))
        b.feed_line(json.dumps({"type": "assistant", "message": {"id": f"m{i}"}}))
    assert b.count == 3 and not b.tripped
    b.feed_line(json.dumps({"type": "assistant", "message": {"id": "m3"}}))
    assert b.tripped  # max_turns + 2


def test_turn_backstop_kills(tmp_path):
    sup = run_supervised([sys.executable, "-c", EMIT_TURNS, "50"], cwd=tmp_path, env=dict(os.environ),
                         stdin_data=b"", stdout_path=tmp_path / "out.jsonl", stderr_path=tmp_path / "err.txt",
                         wall_clock_s=60, max_turns=3)
    assert sup.killed_by == "turns"
    assert sup.summary.turns_counted >= 5 and sup.summary.result is None
    assert sup.duration_s < 30


def test_natural_finish_not_killed(tmp_path):
    sup = run_supervised([sys.executable, "-c", EMIT_TURNS, "2"], cwd=tmp_path, env=dict(os.environ),
                         stdin_data=b"hello", stdout_path=tmp_path / "out.jsonl", stderr_path=tmp_path / "err.txt",
                         wall_clock_s=60, max_turns=3)
    assert sup.killed_by is None and sup.returncode == 0 and sup.summary.result["num_turns"] == 2


def test_clock_rail_kills_grandchild_holding_stdout(tmp_path):
    pidfile = tmp_path / "gc.pid"
    t0 = time.monotonic()
    sup = run_supervised([sys.executable, "-c", GRANDCHILD, str(pidfile)], cwd=tmp_path, env=dict(os.environ),
                         stdin_data=None, stdout_path=tmp_path / "out.jsonl", stderr_path=tmp_path / "err.txt",
                         wall_clock_s=2, max_turns=None, grace_s=1.0)
    elapsed = time.monotonic() - t0
    assert sup.killed_by == "clock"
    assert elapsed < 20, elapsed
    gpid = int(pidfile.read_text())
    deadline = time.time() + 5
    while alive(gpid) and time.time() < deadline:
        time.sleep(0.1)
    assert not alive(gpid), "grandchild survived the process-group kill"
    assert sup.summary.init is not None


def test_leftover_group_torn_down_after_normal_exit(tmp_path):
    pidfile = tmp_path / "gc.pid"
    script = ("import subprocess, sys; g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
              f" open({str(pidfile)!r}, 'w').write(str(g.pid))")
    sup = run_supervised([sys.executable, "-c", script], cwd=tmp_path, env=dict(os.environ), stdin_data=None,
                         stdout_path=tmp_path / "o", stderr_path=tmp_path / "e", wall_clock_s=60, max_turns=None)
    assert sup.killed_by is None
    gpid = int(pidfile.read_text())
    deadline = time.time() + 5
    while alive(gpid) and time.time() < deadline:
        time.sleep(0.1)
    assert not alive(gpid)


def test_abort_event_interrupts(tmp_path):
    ev = threading.Event()
    threading.Timer(0.5, ev.set).start()
    sup = run_supervised([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, env=dict(os.environ),
                         stdin_data=None, stdout_path=tmp_path / "o", stderr_path=tmp_path / "e",
                         wall_clock_s=60, max_turns=None, abort_event=ev, grace_s=1.0)
    assert sup.killed_by == "interrupt"


def test_start_error_recorded(tmp_path):
    sup = run_supervised(["/nonexistent/agent"], cwd=tmp_path, env={}, stdin_data=None,
                         stdout_path=tmp_path / "o", stderr_path=tmp_path / "e", wall_clock_s=5, max_turns=None)
    assert sup.start_error and "could not start agent" in (tmp_path / "e").read_text()


def test_kill_process_group_tolerates_dead_group():
    kill_process_group(None, pgid=999_999_9, grace_s=0.1)


# -- attempts -----------------------------------------------------------------------------


def rec(cell_id, k, outcome):
    return {"type": "attempt", "attempt_id": attempts_mod.attempt_id(cell_id, k), "cell_id": cell_id, "k": k,
            "outcome": outcome}


def test_scored_attempt_selection_with_resets(tmp_path):
    st = attempts_mod.RunStore(tmp_path)
    st.append(rec("c1", 1, Outcome.INFRA_API.value))
    st.append(rec("c1", 2, Outcome.COMPLETED.value))
    st.append(rec("c1", 3, Outcome.COMPLETED.value))
    assert st.scored_attempt("c1")["k"] == 2  # first scored, later ones never consulted
    st.invalidate(attempts_mod.attempt_id("c1", 2), "audit: copied deliverable")
    assert st.scored_attempt("c1")["k"] == 3
    st.reset("c1", "re-run after harness fix")
    assert st.scored_attempt("c1") is None and st.attempts_used("c1") == 0
    st.append(rec("c1", 4, Outcome.CLOCK_STOP.value))
    assert st.scored_attempt("c1")["k"] == 4  # a rail is scored
    st.append(rec("c2", 1, Outcome.CRASH.value))
    assert st.scored_attempt("c2") is None and st.attempts_used("c2") == 1
    assert st.cell_ids() == ["c1", "c2"]


def test_torn_log_line_ignored_and_k_monotonic(tmp_path):
    st = attempts_mod.RunStore(tmp_path)
    st.append(rec("c1", 1, "completed"))
    with open(st.path, "a") as fh:
        fh.write('{"type": "attempt", "cell_id": "c1", "k": 2, "outc')  # crash mid-write
    assert len(st.records()) == 1
    cell = Cell("toy", "a", "b", 0)
    (tmp_path / "cells" / cell.cell_id / "attempt-7").mkdir(parents=True)  # crashed attempt dir on disk
    k, d = st.new_attempt_dir(cell)
    assert k == 8 and d.is_dir()


def test_cell_lock_excludes_second_holder(tmp_path):
    st = attempts_mod.RunStore(tmp_path)
    cell = Cell("toy", "a", "b", 0)
    with st.cell_lock(cell):
        other = attempts_mod.RunStore(tmp_path)
        with pytest.raises(attempts_mod.CellBusy):
            with other.cell_lock(cell):
                pass
    with st.cell_lock(cell):
        pass


# -- interleave ---------------------------------------------------------------------------


def grid(n_inst=5, arms=("a", "b", "c", "d"), reps=3):
    return [Cell("e", f"i{i}", a, r) for r in range(reps) for i in range(n_inst) for a in arms]


def test_interleave_is_a_permutation_and_deterministic():
    cells = grid()
    o1 = interleave(cells, seed=7)
    assert sorted(c.cell_id for c in o1) == sorted(c.cell_id for c in cells)
    assert o1 == interleave(list(reversed(cells)), seed=7)  # input order irrelevant
    assert o1 != interleave(cells, seed=8)
    reps = [c.rep for c in o1]
    assert reps == sorted(reps)  # rep blocks in order


def test_interleave_prefix_covers_all_arms():
    arms = ("a", "b", "c", "d")
    for seed in range(20):
        order = interleave(grid(arms=arms), seed=seed)
        block = [c for c in order if c.rep == 0]
        for n in range(1, len(block) + 1):
            counts = Counter(c.arm for c in block[:n])
            vals = [counts.get(a, 0) for a in arms]
            assert max(vals) - min(vals) <= 1
            if n >= len(arms):
                assert all(vals), (seed, n, counts)


def test_interleave_independent_of_hash_seed():
    import subprocess

    code = ("import sys; sys.path.insert(0, %r); from dbwm.core.runner import interleave; "
            "from dbwm.core.types import Cell; "
            "print([c.cell_id for c in interleave([Cell('e', 'i%%d' %% i, a, 0) for i in range(4) "
            "for a in 'xyz'], 3)])") % str(Path(__file__).resolve().parents[2])
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": str(h)}).stdout for h in (0, 1, 2, 3)}
    assert len(outs) == 1 and outs.pop().startswith("[")
