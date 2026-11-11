"""The deterministic stub agent, run directly into a materialized sandbox and scored."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from dbwm.core.types import RULESET_PAPER, RULESET_V1
from dbwm.eval6 import EVAL

from .conftest import PKG_ROOT, WORLDS, cell, materialize, no_oracle


@pytest.mark.parametrize("world", WORLDS)
@pytest.mark.parametrize("arm", ["unaided", "aided"])
def test_stub_runs_in_sandbox_and_scores(tmp_path, world, arm):
    c = cell(world, arm)
    sb = materialize(c, tmp_path / "sb")
    before = {p.relative_to(sb).as_posix() for p in sb.rglob("*")}
    EVAL.stub_agent(c, sb, no_oracle)
    after = {p.relative_to(sb).as_posix() for p in sb.rglob("*") if "__pycache__" not in p.parts}
    assert after - before == {"plan.json", "claims.json"}
    for rs in (RULESET_PAPER, RULESET_V1):
        r = EVAL.score(sb, c, rs)
        d = r.details
        assert r.deliverable_present and r.claims_parse_status == "ok" and d["no_plan"] is False
        assert d["budget_ok"] is True and d["raw_price_out_of_band"] == []
        assert d["true_profit"] > 0 and d["gap_to_mstar_pct"] is not None
        if rs == RULESET_V1:
            assert d["plan_fallback"] == [] and d["budget_projected"] is False


def test_stub_is_deterministic_and_rep_dependent(tmp_path):
    outs = []
    for i, rep in enumerate((0, 0, 1, 2)):
        c = cell("w2", "unaided", rep)
        sb = materialize(c, tmp_path / f"sb{i}")
        EVAL.stub_agent(c, sb, no_oracle)
        outs.append((sb / "plan.json").read_text() + (sb / "claims.json").read_text())
    assert outs[0] == outs[1]
    assert len(set(outs)) >= 2


def test_aided_stub_beats_unaided_stub_on_average(tmp_path):
    gains = []
    for world in ("w1", "w5", "w7"):
        tp = {}
        for arm in ("unaided", "aided"):
            c = cell(world, arm)
            sb = materialize(c, tmp_path / f"{world}-{arm}")
            EVAL.stub_agent(c, sb, no_oracle)
            tp[arm] = EVAL.score(sb, c, RULESET_PAPER).details["true_profit"]
        gains.append(tp["aided"] / tp["unaided"] - 1)
    assert sum(gains) / len(gains) > 0


SCRIPT = r"""
import json, sys, tempfile, shutil
from pathlib import Path
from dbwm.core.types import Cell
from dbwm.eval6 import EVAL
out = {}
for arm in ("unaided", "aided"):
    c = Cell("eval6", "w3", arm, 1)
    sb = Path(tempfile.mkdtemp(prefix="dbwm_e6hash_"))
    m = EVAL.stage(c)
    for f in m.files:
        p = sb / f.dst; p.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(f.src, p)
    (sb / "TASK.md").write_text(m.task_md)
    EVAL.stub_agent(c, sb, None)
    r = EVAL.score(sb, c, "v1")
    out[arm] = [(sb / "plan.json").read_text(), r.details["true_profit"], r.details["gap_to_mstar_pct"]]
    shutil.rmtree(sb)
print(json.dumps(out))
"""


def test_stub_and_scorer_independent_of_hash_seed():
    results = set()
    for seed in ("0", "1", "2", "3"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, "-c", SCRIPT], cwd=PKG_ROOT, env=env, capture_output=True,
                           text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-2000:]
        results.add(r.stdout)
    assert len(results) == 1
