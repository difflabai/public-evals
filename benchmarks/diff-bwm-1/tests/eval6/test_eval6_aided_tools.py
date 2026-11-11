"""The aided arm's staged tools vs the paper tool's outputs.

Eval 6 serves no harness oracle (EVAL.oracle_handlers is empty for every cell):
the aided arm's "tool" is its own simulator, staged in the sandbox. These are its
goldens: the staged model_sim.py + heldout_sim.py must give exactly what the
paper's model_sim.py + latent_engine.py printed (fixtures/model_sim_paper.json,
made by running the paper's script on two fixed plans) when run at the paper's
base_seed 101.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dbwm.eval6 import _paths

from .conftest import WORLDS, cell, import_staged, materialize


@pytest.fixture(scope="module")
def model_sim():
    return import_staged("model_sim.py", "_e6_model_sim_t")


@pytest.mark.parametrize("world", WORLDS)
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_staged_simulator_equals_paper_model_sim(model_sim, world, version):
    golden = json.loads((Path(__file__).parent / "fixtures" / "model_sim_paper.json").read_text())["worlds"][world]
    path = _paths.world_aided_v1(world) if version == "v1" else _paths.world_aided_v2(world)
    fm = json.loads(path.read_text())
    for name, rec in golden.items():
        out = json.dumps(model_sim.simulate(fm, rec["plan"], 200, 101), indent=2) + "\n"
        assert out == rec["stdout"], (world, version, name)


def test_staged_simulator_seed_is_not_a_scoring_seed(model_sim):
    seeds = json.loads(_paths.scoring_seeds().read_text())
    assert model_sim.BASE_SEED not in (seeds["paper"]["base_seed"], seeds["v1"]["base_seed"])


def test_model_sim_cli_runs_inside_a_sandbox(tmp_path):
    sb = materialize(cell("w4", "aided"), tmp_path / "sb")
    task = json.loads((sb / "public" / "task.json").read_text())
    plan = {"plan": {s: {"marketing": task["reference_marketing"][s], "price": 1.0} for s in task["segments"]}}
    (sb / "plan.json").write_text(json.dumps(plan))
    r = subprocess.run([sys.executable, "model_sim.py", "--plan", "plan.json", "--n-seeds", "20"],
                       cwd=sb, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert set(out) == {"expected_held_out_profit", "stderr", "per_segment_profit", "total_marketing_per_quarter",
                        "budget_cap", "over_budget"}
    assert out["over_budget"] is False and out["expected_held_out_profit"] > 0
    assert set(p.name for p in sb.iterdir()) - {"__pycache__"} == {
        "public", "TASK.md", "fitted_model.json", "model_sim.py", "heldout_sim.py", "plan.json"}  # wrote nothing else


def test_heldout_sim_matches_engine_on_true_world():
    """heldout_sim is a faithful subset of latent_engine's held-out dynamics."""
    from dbwm.eval6 import latent_engine as le
    hs = import_staged("heldout_sim.py", "_e6_heldout_sim_t2")
    theta = json.loads(_paths.answer_key("w9", "theta.json").read_text())
    mstar = json.loads(_paths.answer_key("w9", "mstar.json").read_text())
    ls = (mstar["latent_state"][0], mstar["latent_state"][1])
    a = le.held_out_profit(le.world_from_dict(theta), mstar["mstar_plan"], ls, mstar["cust_final"], 64, 5)
    b = hs.held_out_profit(hs.model_from_dict(theta), mstar["mstar_plan"], ls, mstar["cust_final"], 64, 5)
    assert a[0] == b[0] and a[1] == b[1] and list(a[2]) == list(b[2])
