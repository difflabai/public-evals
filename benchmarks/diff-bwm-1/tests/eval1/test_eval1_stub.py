"""Eval-1 stub agent, run directly: TASK.md in a temp dir, call_oracle wired to the eval's handlers.

The stub is a pipeline test double (never evidence). These tests drive every arm on every
published instance without the runner (runner-level e2e is in the wave-2 suite), then score.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dbwm.core.types import RULESET_PAPER, RULESET_V1, Cell
from dbwm.eval1 import PUBLISHED_INSTANCES
from dbwm.eval1.arms import ARM_ORDER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PKG = Path(__file__).resolve().parents[2]


def _run_stub(ev, oracle_for, cell: Cell, root: Path) -> tuple[Path, list]:
    sb = root / cell.cell_id
    sb.mkdir(parents=True)
    (sb / "TASK.md").write_text(ev.stage(cell).task_md, encoding="utf-8")
    log: list = []
    ev.stub_agent(cell, sb, oracle_for(ev, cell, sb, log))
    return sb, log


def _summary(ev, oracle_for, root: Path) -> dict:
    out = {}
    for inst in PUBLISHED_INSTANCES:
        for arm in ARM_ORDER:
            cell = Cell("eval1", inst, arm, 0)
            sb, log = _run_stub(ev, oracle_for, cell, root)
            v1 = ev.score(sb, cell, RULESET_V1)
            pp = ev.score(sb, cell, RULESET_PAPER)
            out[cell.cell_id] = {
                "success": v1.success, "claimed": v1.claimed_success, "overclaim": v1.overclaim,
                "paper_success": pp.success, "paper_overclaim": pp.overclaim, "oracle_calls": len(log),
                "plan_sha256": hashlib.sha256((sb / "plan.json").read_bytes()).hexdigest(),
                "claims_sha256": hashlib.sha256((sb / "claims.json").read_bytes()).hexdigest(),
            }
    return out


@pytest.fixture(scope="module")
def stub_summary(ev, oracle_for, tmp_path_factory):
    return _summary(ev, oracle_for, tmp_path_factory.mktemp("stub"))


def test_stub_every_arm_every_instance(ev, stub_summary):
    assert len(stub_summary) == 15 * 6
    for cid, r in stub_summary.items():
        arm = cid.split("__")[2]
        assert r["success"] is not None and r["claimed"] is not None, cid
        budget = ev.arms[arm].query_budget
        if arm == "ungrounded":
            assert r["oracle_calls"] == 0
        else:
            assert r["oracle_calls"] >= 1
        if budget is not None:
            assert r["oracle_calls"] <= budget
        # v1 and paper agree for well-formed stub submissions
        assert r["success"] == r["paper_success"] and r["overclaim"] == r["paper_overclaim"], cid


def test_stub_arm_shape(stub_summary):
    """The stub encodes the paper's prediction (it is a test double): blind ungrounded plans
    overclaim; numeric-feedback arms do not."""
    by_arm: dict[str, list] = {}
    for cid, r in stub_summary.items():
        by_arm.setdefault(cid.split("__")[2], []).append(r)
    assert sum(r["overclaim"] for r in by_arm["ungrounded"]) >= 10
    for arm in ("grounded-readonly", "grounded-iterate", "raw-figures"):
        assert sum(r["overclaim"] for r in by_arm[arm]) == 0, arm
    assert sum(r["success"] for r in by_arm["grounded-iterate"]) >= 10


def test_stub_golden(stub_summary):
    """Byte-level regression: plans, claims and verdict fields equal the committed golden."""
    golden = json.loads((FIXTURES / "stub_golden.json").read_text(encoding="utf-8"))
    assert stub_summary == golden


def test_stub_deterministic_across_hash_seeds(tmp_path):
    """No ``hash()``-seeded randomness (the paper's demo.py had it, ledger E1-D9)."""
    code = (
        "import hashlib, json, sys, tempfile\n"
        "from pathlib import Path\n"
        "from dbwm.core.types import Cell\n"
        "from dbwm.eval1 import EVAL\n"
        "from dbwm.core.types import OracleContext\n"
        "h = hashlib.sha256()\n"
        "for arm in ('ungrounded', 'grounded-readonly', 'prose-feedback', 'broken-model'):\n"
        "    for rep in (0, 1, 2):\n"
        "        cell = Cell('eval1', 'atlas-advisory__hard', arm, rep)\n"
        "        with tempfile.TemporaryDirectory() as td:\n"
        "            sb = Path(td)\n"
        "            (sb / 'TASK.md').write_text(EVAL.prompt(cell))\n"
        "            a = EVAL.arms[arm]\n"
        "            ctx = OracleContext(cell=cell, arm=a, mode=a.oracle_mode, sandbox=sb, state={})\n"
        "            hs = EVAL.oracle_handlers(cell)\n"
        "            EVAL.stub_agent(cell, sb, lambda op, p: hs[op](p, ctx))\n"
        "            h.update((sb / 'plan.json').read_bytes() + (sb / 'claims.json').read_bytes())\n"
        "print(h.hexdigest())\n"
    )
    outs = set()
    for seed in ("0", "1", "7"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, "-c", code], cwd=str(PKG), env=env, capture_output=True,
                           text=True, timeout=300)
        assert r.returncode == 0, r.stderr
        outs.add(r.stdout.strip())
    assert len(outs) == 1


def test_stub_respects_budget_refusal(ev, tmp_path):
    """When the oracle refuses (budget), the stub commits what it has instead of crashing."""
    from dbwm.core.types import OracleContext

    cell = Cell("eval1", "northwind-analytics__hostile", "grounded-iterate", 0)
    (tmp_path / "TASK.md").write_text(ev.prompt(cell), encoding="utf-8")
    arm = ev.arms[cell.arm]
    ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=tmp_path, state={})
    calls = {"n": 0}

    def stingy(op, payload):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("query budget exhausted")
        return ev.oracle_handlers(cell)[op](payload, ctx)

    ev.stub_agent(cell, tmp_path, stingy)
    r = ev.score(tmp_path, cell, RULESET_V1)
    assert r.deliverable_present and r.claims_parse_status == "ok"
