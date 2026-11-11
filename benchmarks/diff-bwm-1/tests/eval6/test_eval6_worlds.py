"""World generation, the aided fitted model (v1 leaky / v2 seed-free) and the private-split salt."""

from __future__ import annotations

import json

import pytest

from dbwm.core.numcompare import text_numbers_close, values_close
from dbwm.eval6 import _paths
from dbwm.eval6 import latent_engine as le
from dbwm.eval6.gen import make_fitted_model as mfm
from dbwm.eval6.gen import make_latent_world as mlw

from .conftest import WORLDS, import_staged


@pytest.mark.parametrize("world", WORLDS)
def test_world_regenerates_byte_for_byte(world):
    w = mlw.draw_world(int(world[1:]))
    obs = le.simulate_history(w)
    assert mlw.history_csv_bytes(w, obs) == _paths.world_public(world, "history.csv").read_bytes()
    assert mlw.task_json_bytes(w) == _paths.world_public(world, "task.json").read_bytes()
    # theta / M* / the aided models carry transcendental-function results: byte-identical on the
    # macOS build machine, equal up to the last float bits on Linux (dbwm.core.numcompare).
    assert _close_bytes(mlw.theta_json_bytes(w), _paths.answer_key(world, "theta.json").read_bytes())
    mstar = json.loads(_paths.answer_key(world, "mstar.json").read_text())
    for k, v in mlw.mstar_dict(w, obs, with_optimum=False).items():
        if k != "n_seeds":
            assert values_close(mstar[k], json.loads(json.dumps(v, default=float))), k
    theta = json.loads(_paths.answer_key(world, "theta.json").read_text())
    assert _close_bytes(mfm.to_bytes(mfm.fitted_v1(theta, mstar)), _paths.world_aided_v1(world).read_bytes())
    assert _close_bytes(mfm.to_bytes(mfm.fitted_v2(theta, mstar)), _paths.world_aided_v2(world).read_bytes())


def _close_bytes(got: bytes, want: bytes) -> bool:
    return got == want or text_numbers_close(got.decode(), want.decode())[0]


@pytest.mark.parametrize("world", WORLDS)
def test_four_segments_hub_and_three_spokes(world):
    theta = json.loads(_paths.answer_key(world, "theta.json").read_text())
    assert list(theta["segments"]) == ["core", "smb", "ent", "growth"]
    cols = [j for j in range(4) if any(theta["coupling"][s][j] != 0.0 for s in range(4))]
    assert len(cols) == 1                                    # one hub
    hub = cols[0]
    assert sum(1 for s in range(4) if theta["coupling"][s][hub] != 0.0) == 3


def test_v2_is_v1_minus_history_only_fields():
    for world in WORLDS:
        v1 = json.loads(_paths.world_aided_v1(world).read_text())
        v2 = json.loads(_paths.world_aided_v2(world).read_text())
        assert "seed" in v1["fitted_world"] and "seed" not in v2["fitted_world"]
        assert mfm.strip_to_v2(v1) == v2
        for k in ("latent_nowcast", "cust_current", "budget", "held_out", "validation_report", "world_id"):
            assert v1[k] == v2[k]
        for sid, sv in v2["fitted_world"]["segments"].items():
            assert not set(mfm.V2_DROP_SEGMENT_KEYS) & set(sv)
            for k, x in sv.items():
                assert v1["fitted_world"]["segments"][sid][k] == x


def test_seed_replay_leak_v1_known_and_v2_closed():
    """E6-01: with the staged engine, the paper's fitted model replays the true latent end state."""
    v1 = json.loads(_paths.world_aided_v1("w1").read_text())
    mstar = json.loads(_paths.answer_key("w1", "mstar.json").read_text())
    replay = le.simulate_history(le.world_from_dict(v1["fitted_world"]))
    assert replay["latent_state"][0] == mstar["latent_state"][0] == 1.0691461795252533   # the KNOWN LEAK
    assert abs(v1["latent_nowcast"][0] - mstar["latent_state"][0]) > 0.01               # the disclosed nowcast
    v2 = json.loads(_paths.world_aided_v2("w1").read_text())
    with pytest.raises(TypeError, match="phi"):
        le.world_from_dict(v2["fitted_world"])                # no history parameters (or seed) to replay with
    hs = import_staged("heldout_sim.py", "_e6_heldout_sim_t")
    assert not hasattr(hs, "simulate_history")
    hs.model_from_dict(v2["fitted_world"])                    # the staged simulator does load v2


def test_salted_world_is_opaque_and_deterministic():
    pub = mlw.draw_world(3)
    a = mlw.draw_world(3, seed_salt="organizer-secret")
    b = mlw.draw_world(3, seed_salt="organizer-secret")
    c = mlw.draw_world(3, seed_salt="another-secret")
    assert pub.id == "w3" and pub.seed == 3
    assert a.id == b.id and a.id.startswith("p") and len(a.id) == 11 and "3" != a.id[1:]
    assert a.id != c.id and a.seed != 3 and a.seed == b.seed
    assert le.world_to_dict(a)["segments"] == le.world_to_dict(b)["segments"]
    assert le.world_to_dict(a)["segments"] != le.world_to_dict(pub)["segments"]
    oa, ob = le.simulate_history(a), le.simulate_history(b)
    assert mlw.history_csv_bytes(a, oa) == mlw.history_csv_bytes(b, ob)
    assert mlw.history_csv_bytes(a, oa) != mlw.history_csv_bytes(pub, le.simulate_history(pub))
    task = json.loads(mlw.task_json_bytes(a))
    assert task["world_id"] == a.id and "organizer-secret" not in mlw.task_json_bytes(a).decode()


def test_emit_split_layout(tmp_path):
    w = mlw.draw_world(5, seed_salt="s")
    mlw.emit_split(w, tmp_path / "data", tmp_path / "private", n_seeds=4)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == [
        f"data/{w.id}/public/history.csv", f"data/{w.id}/public/task.json",
        f"private/{w.id}/ANSWER_KEY/mstar.json", f"private/{w.id}/ANSWER_KEY/theta.json"]


def test_agent_visible_tree_leaves_out_answer_keys():
    root = _paths.data()
    files = [p.relative_to(root).as_posix() for p in sorted(root.rglob("*")) if p.is_file()
             and "__pycache__" not in p.parts]
    assert not any("ANSWER_KEY" in k or "AIDED_v1" in k for k in files)
