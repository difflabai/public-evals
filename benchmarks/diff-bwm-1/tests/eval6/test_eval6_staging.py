"""Staging manifests, briefs and leak checks for every arm x instance."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dbwm.core import sandbox as core_sandbox
from dbwm.core.types import CANARY_GUID, CANARY_STRING, Isolation, RegimeConfig
from dbwm.eval6 import EVAL, SCORER_ONLY_NAMES, _paths
from dbwm.eval6 import prompt as P
from dbwm.eval6.arms import ARMS

from .conftest import ALL_CELLS, WORLDS, cell

EXPECTED = {
    "unaided": {"public/history.csv", "public/task.json"},
    "aided": {"public/history.csv", "public/task.json", "fitted_model.json", "model_sim.py", "heldout_sim.py"},
}


def test_eval_satisfies_the_protocol():
    from dbwm.core.types import EvalDef
    assert isinstance(EVAL, EvalDef) and EVAL.eval_id == "eval6"
    assert tuple(EVAL.deliverables) == ("plan.json", "claims.json")
    src = {p.name for p in EVAL.engine_sources()}
    assert {"latent_engine.py", "score_d1.py", "claims.py"} <= src


def test_arms_are_bash_container_arms_without_oracle_tools():
    assert set(ARMS) == {"unaided", "aided"}
    for a in ARMS.values():
        assert a.tools.builtin == frozenset({"read", "write", "edit", "bash", "glob", "grep"})
        assert a.tools.oracle_tools == () and a.oracle_mode is None and a.query_budget is None
        assert a.min_isolation == Isolation.CONTAINER and a.canonical


def test_instances_and_cells():
    assert EVAL.instances() == list(WORLDS)
    reg = RegimeConfig(eval_id="eval6", model="m", arms=["unaided", "aided"], reps=3)
    cells = EVAL.cells(reg)
    assert len(cells) == 66 and len({c.cell_id for c in cells}) == 66
    with pytest.raises(ValueError):
        EVAL.cells(RegimeConfig(eval_id="eval6", model="m", arms=["unaided"], instances=["w8"]))
    with pytest.raises(ValueError):
        EVAL.cells(RegimeConfig(eval_id="eval6", model="m", arms=["oracle"]))


def test_default_regime():
    d = EVAL.default_regime()
    assert d["eval_id"] == "eval6" and d["arms"] == ["unaided", "aided"] and d["max_turns"] == 60
    assert d["isolation"] == "container" and d["ruleset"] == "v1" and "model" not in d


@pytest.mark.parametrize("world,arm", ALL_CELLS)
def test_manifest_lists_only_agent_visible_files(world, arm):
    m = EVAL.stage(cell(world, arm))
    dsts = {f.dst for f in m.files}
    assert dsts == EXPECTED[arm]
    assert m.writable_dirs == []
    for f in m.files:
        assert f.src is not None and f.src.is_file()
        assert _paths.data() in f.src.resolve().parents, f"{f.dst} staged from outside data/eval6"
        assert not any(part in SCORER_ONLY_NAMES for part in Path(f.dst).parts)
    assert m.task_md == EVAL.prompt(cell(world, arm))


@pytest.mark.parametrize("world,arm", ALL_CELLS)
def test_staged_texts_carry_no_answer_key_canary_or_paths(world, arm):
    c = cell(world, arm)
    m = EVAL.stage(c)
    vals = EVAL.answer_key_values(c)
    texts = {f.dst: f.src.read_text() for f in m.files}
    texts["TASK.md"] = m.task_md
    for name, t in texts.items():
        assert CANARY_GUID not in t and CANARY_STRING not in t, name
        hits = [v for v in vals if v in t]
        assert not hits, f"{name} contains answer-key values {hits[:3]}"
    prompt = EVAL.prompt(c)
    assert not re.search(r"/(?:Users|home|tmp|var)/", prompt)
    for bad in ("..", "private/", "ANSWER_KEY", "reference.json", "theta.json", "mstar", "latent_engine", "seed"):
        assert bad not in prompt, bad
    if arm == "aided":
        fm = json.loads(texts["fitted_model.json"])
        assert "seed" not in fm["fitted_world"]
        for sv in fm["fitted_world"]["segments"].values():
            assert not {"phi", "psi", "cust0"} & set(sv)
        assert "simulate_history" not in texts["heldout_sim.py"] and "import latent_engine" not in texts["model_sim.py"]


@pytest.mark.parametrize("world,arm", [("w1", "unaided"), ("w7", "aided"), ("w12", "aided")])
def test_core_sandbox_builder_precheck_is_clean(tmp_path, monkeypatch, world, arm):
    monkeypatch.setenv("DBWM_SANDBOX_ROOT", str(tmp_path))
    c = cell(world, arm)
    h = core_sandbox.build_sandbox(EVAL.stage(c), c, ARMS[arm].tools, ARMS[arm].tools.oracle_tools)
    try:
        problems = core_sandbox.precheck(h, answer_values=EVAL.answer_key_values(c), prompt=EVAL.prompt(c),
                                         disclosed_values=EVAL.disclosed_values_for(c))
        assert problems == []
        names = {p.relative_to(h.path).as_posix() for p in h.path.rglob("*") if p.is_file()}
        assert names == EXPECTED[arm] | {"TASK.md"}
    finally:
        core_sandbox.cleanup(h)


def test_precheck_catches_a_planted_answer_key_value(tmp_path, monkeypatch):
    monkeypatch.setenv("DBWM_SANDBOX_ROOT", str(tmp_path))
    c = cell("w1", "unaided")
    h = core_sandbox.build_sandbox(EVAL.stage(c), c, ARMS["unaided"].tools)
    try:
        theta = json.loads(_paths.answer_key("w1", "theta.json").read_text())
        hist = h.path / "public" / "history.csv"
        hist.chmod(0o644)
        with hist.open("a") as f:
            f.write(repr(theta["segments"]["core"]["elast"]))
        problems = core_sandbox.precheck(h, answer_values=EVAL.answer_key_values(c), prompt=EVAL.prompt(c))
        assert any("answer-key value" in p and "public/history.csv" in p for p in problems), problems
    finally:
        core_sandbox.cleanup(h)


def test_oracle_handlers_are_empty():
    for w, a in ALL_CELLS:
        assert EVAL.oracle_handlers(cell(w, a)) == {}


def test_bad_cell_rejected():
    with pytest.raises(ValueError):
        EVAL.stage(cell("w8", "unaided"))
    with pytest.raises(ValueError):
        EVAL.prompt(cell("w1", "oracle"))


# -- briefs ------------------------------------------------------------------------------------

def test_brief_is_the_paper_text_and_states_the_band():
    task = json.loads(_paths.world_public("w3", "task.json").read_text())
    un, ai = P.brief(task, "unaided", version="paper"), P.brief(task, "aided", version="paper")
    assert un.startswith("# Self-supply held-out planning task — world w3")
    assert ai.startswith("# Aided held-out planning task — world w3")
    for t in (un, ai):
        assert "[0.7, 1.3]" in t and "plan.json" in t and "claims.json" in t
    assert "python3 model_sim.py --plan plan.json" in ai and "model_sim" not in un
    assert re.search(r"<= 2\d{5}\.\d+ usd per quarter", un)
    assert "total gross profit" in un and "Profit accounting" not in un        # the paper brief: undisclosed
    with pytest.raises(KeyError):
        P.brief(task, "reference", version="paper")
    with pytest.raises(ValueError):
        P.brief(task, "unaided")                                               # v1 needs the accounting
    with pytest.raises(ValueError):
        P.brief(task, "unaided", version="v2")


@pytest.mark.parametrize("world", WORLDS)
def test_v1_brief_pins_the_accounting_and_the_marketing_bound(world):
    """v7 entry 21 / G.3: the paper brief disclosed neither gm nor fixed costs nor a marketing bound."""
    from dbwm.eval6.score_d1 import history_marketing_max
    task = json.loads(_paths.world_public(world, "task.json").read_text())
    theta = json.loads(_paths.answer_key(world, "theta.json").read_text())
    acc = P.world_accounting(world)
    hm = history_marketing_max(world)
    for arm in ("unaided", "aided"):
        text = EVAL.prompt(cell(world, arm))
        assert text == P.brief(task, arm, acc) == P.brief_for_world(world, arm)
        paper = P.brief(task, arm, version="paper")
        assert len(text) > len(paper)
        assert "profit = revenue * gm - marketing - fixed" in text
        for s in task["segments"]:
            assert json.dumps(round(theta["segments"][s]["gm"], 4)) in text
            assert json.dumps(round(theta["segments"][s]["fixed"], 2)) in text
            assert f"- {s}: <= {hm[s]:.2f} usd/quarter" in text
            assert repr(theta["segments"][s]["gm"]) not in text or repr(theta["segments"][s]["gm"]) == \
                repr(round(theta["segments"][s]["gm"], 4))
        if arm == "unaided":
            assert "total gross profit" not in text and "total profit = revenue x gm" in text
    disclosed = EVAL.disclosed_values_for(cell(world, "unaided"))
    assert disclosed == P.disclosed_values(acc)
    vals = EVAL.answer_key_values(cell(world, "unaided"))
    assert not set(disclosed) & set(vals)
