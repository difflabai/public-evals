"""PYTHONHASHSEED independence of eval-5 scoring and of the ELO ladder's bootstrap (subprocesses with hash
seeds 0..7)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from .conftest import PKG_ROOT

STUB_PROBE = r"""
import json, sys, tempfile
from dbwm.core.types import Cell
from dbwm.eval5 import dataset as D, score as S, stubs
out = {}
for sid in D.SCENARIOS:
    with tempfile.TemporaryDirectory() as t:
        stubs.render(sid, "medium", t)
        r = S.score(t, Cell("eval5", sid, "baseline", 0), "v1")
        out[sid] = [r.success, r.details.get("item_pass"), r.details.get("checker_failed_ids")]
print(json.dumps(out, sort_keys=True))
"""


def test_stub_scores_identical_across_hash_seeds():
    outs = set()
    for seed in range(8):
        env = dict(os.environ, PYTHONHASHSEED=str(seed))
        r = subprocess.run([sys.executable, "-c", STUB_PROBE], cwd=str(PKG_ROOT), env=env,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stderr[-2000:]
        outs.add(r.stdout)
    assert len(outs) == 1


LADDER_PROBE = r"""
import json
from dbwm.eval5.elo import ladder as L
arms = ["baseline", "checklist-prose", "checklist-verdict", "checklist-executable"]
rows = []
for s, sid in enumerate(["alpha", "beta"]):
    for rep in range(2):
        for i, a in enumerate(arms):
            for j, b in enumerate(arms):
                if i != j:
                    k = (3 * i + 5 * j + 7 * s + rep) % 6
                    rows.append({"scenario": sid, "rep": rep, "first": a, "second": b,
                                 "winner": "tie" if k == 0 else (a if (k + i) % 3 else b)})
print(json.dumps({str(c): {k: list(v) for k, v in sorted(L.bootstrap_ci(rows, n_boot=200, cluster=c).items())}
                  for c in (None, "pair", "scenario")}, sort_keys=True))
"""


def test_ladder_bootstrap_identical_across_hash_seeds():
    """Synthetic comparisons (two scenarios, both orders, two reps, some ties); every cluster mode."""
    outs = set()
    for seed in range(8):
        env = dict(os.environ, PYTHONHASHSEED=str(seed))
        r = subprocess.run([sys.executable, "-c", LADDER_PROBE], cwd=str(PKG_ROOT), env=env,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stderr[-2000:]
        outs.add(r.stdout)
    assert len(outs) == 1
    assert {k: len(v) for k, v in json.loads(outs.pop()).items()} == {"None": 4, "pair": 4, "scenario": 4}
