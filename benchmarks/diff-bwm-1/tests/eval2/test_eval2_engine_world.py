"""Engine, World and generator: the paper's self-checks, determinism, regeneration."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from decimal import Decimal, getcontext
from pathlib import Path

import pytest

from dbwm.core import paths
from dbwm.eval2 import WORLD_IDS, World, WorldSpecError, build_brief, load_reference, load_world, load_world_spec
from dbwm.eval2 import make_world_lib as mw
from dbwm.eval2.engine import PAPER_CONTEXT, evaluate
from dbwm.eval2.score import score_scenario
from dbwm.eval2.world import selftest_checks


def test_paper_selftest_checks_all_pass():
    checks = selftest_checks()
    assert [c[0] for c in checks] == [
        "world.selftest.additivity", "world.selftest.margin_identity", "world.selftest.pool_cap",
        "world.selftest.invariant", "world.selftest.broken_interface"]
    assert all(ok for _, ok, _ in checks), checks


def test_base_model_pinned():
    p = paths.private("eval2", "base_models", "sprout-crm.operating-model.json")
    assert hashlib.sha256(p.read_bytes()).hexdigest() == \
        "2f6ddcc4f729eab4b36d70173ea2001f5f7aa1a95199204c51e272528e907f95"


def test_engine_does_not_touch_global_decimal_context():
    before = getcontext().prec
    world = load_world("orion-holdings")
    world.evaluate({})
    assert getcontext().prec == before
    assert PAPER_CONTEXT.prec == 50


def test_worker_thread_gets_same_digits_as_main_thread():
    world = load_world("vega-group")
    sc = {"allocation": load_reference("vega-group")["careful_optimum"]["allocation"]}
    main = score_scenario(world, sc)
    got = []
    t = threading.Thread(target=lambda: got.append(score_scenario(world, sc)))
    t.start()
    t.join()
    assert got[0]["goal_multiple_actual"] == main["goal_multiple_actual"]
    assert got[0]["parent_trajectory"] == main["parent_trajectory"]
    # and the digits are the prec-50 ones the paper recorded
    assert main["goal_multiple_actual"] == load_reference("vega-group")["careful_optimum"]["multiple"]
    assert len(main["goal_multiple_actual"].replace(".", "")) == 50


def test_engine_rejects_bad_bindings():
    world = load_world("orion-holdings")
    model = world.models["alpha-cloud"]
    with pytest.raises(ValueError):
        evaluate(model, {"marketing_spend": ["1"] * 5})
    with pytest.raises(ValueError):
        evaluate(model, {"revenue": ["1"] * 6})


def test_world_invariants_raise_world_spec_error():
    spec = copy.deepcopy(load_world_spec("orion-holdings"))
    spec["pools"][0]["feeds_lever"] = "revenue"
    with pytest.raises(WorldSpecError):
        World(spec)
    spec = copy.deepcopy(load_world_spec("orion-holdings"))
    spec["units"][0]["base_model"] = "no-such-model"
    with pytest.raises(WorldSpecError):
        World(spec)


@pytest.mark.parametrize("wid", WORLD_IDS)
def test_public_brief_is_build_brief_of_private_world(wid):
    brief = json.loads(paths.data("eval2", "worlds", wid, "brief.json").read_text())
    assert brief == build_brief(load_world(wid))


def test_shipped_tree_has_no_agent_junk():
    # the snapshot's vega-group/candidate.json and world_view.json were written by agents (E23-D3)
    for root in (paths.data("eval2"), paths.private("eval2")):
        names = {p.name for p in root.rglob("*") if p.is_file()}
        assert "candidate.json" not in names and "world_view.json" not in names
    assert sorted(p.name for p in paths.data("eval2").rglob("*") if p.is_file()) == ["brief.json"] * 5


def test_regeneration_is_byte_identical(tmp_path):
    from dbwm.eval2.make_worlds import write_worlds

    verified = write_worlds(tmp_path, log=lambda *_: None)
    assert verified == list(WORLD_IDS)
    produced = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert len(produced) == 16
    for rel in produced:
        top, rest = rel.split("/", 1)
        shipped = (paths.data_root() if top == "data" else paths.private_root()) / rest
        assert (tmp_path / rel).read_bytes() == shipped.read_bytes(), rel


def test_v1_certificate_reproduces_and_holds():
    from dbwm.eval2.make_worlds import certify_v1

    cert = certify_v1(log=lambda *_: None)
    assert cert["ok"], {w: v["checks"] for w, v in cert["worlds"].items()}
    shipped = paths.private("eval2", "certificate-v1.json").read_text()
    assert json.dumps(cert, indent=2) + "\n" == shipped
    # E23-D5 documented: with layoffs the flat split passes in every world under the paper scorer
    for w, v in cert["worlds"].items():
        assert v["flat_split_v1_passing_pcts"] == []
        assert v["flat_split_paper_with_layoffs_passing_pcts"], w
    assert cert["worlds"]["orion-holdings"]["flat_split_paper_with_layoffs_passing_pcts"] == [96]
    assert cert["worlds"]["lyra-partners"]["flat_split_paper_with_layoffs_passing_pcts"] == [85, 86]


def test_derive_headcount_is_whole_and_non_negative():
    world = load_world("cygnus-holdings")
    cap = mw.flat_split_capital(world, 90)
    hc, _, _ = mw.derive_headcount(world, cap)
    for series in hc.values():
        assert all(Decimal(x) >= 0 and Decimal(x) == Decimal(x).to_integral_value() for x in series)
    exact = mw.derive_headcount_exact(world, cap)
    assert any(Decimal(x) < 0 for s in exact.values() for x in s) or exact == hc
