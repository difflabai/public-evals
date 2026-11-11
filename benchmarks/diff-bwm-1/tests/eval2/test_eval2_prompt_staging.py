"""Prompts (paper text + exactly P1-P4) and staging leak tests for every arm x instance."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from dbwm.core import paths
from dbwm.core.types import CANARY_GUID, CANARY_STRING, Cell
from dbwm.eval2 import ARMS, EVAL, SCORER_ONLY_NAMES, WORLD_IDS, load_brief, load_reference
from dbwm.eval2 import prompt as P

CELLS = [Cell("eval2", w, a, 0) for w in WORLD_IDS for a in ARMS]
ABS_PATH = re.compile(r"(^|[\s\"'=(])/(Users|home|private|var|tmp|etc|opt)/")
PAPER_PROMPT_SHA256 = json.loads(
    (Path(__file__).parent / "fixtures" / "paper_prompt_sha256.json").read_text())["sha256"]


@pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.cell_id)
def test_release_prompt_is_paper_prompt_plus_documented_changes(cell):
    brief = load_brief(cell.instance)
    rel = EVAL.prompt(cell)
    assert P.strip_release_changes(rel, cell.arm, brief["horizon"]) == P.paper_prompt(brief, cell.arm)
    assert P.ACTION_SPACE_LINES in rel  # P3
    if cell.arm == "ungrounded":
        assert "world_query" not in rel and "YOU HAVE NO MODEL TO RUN" in rel
    else:
        assert P.MCP_TOOL_LINE in rel  # P1 (no-bash arms use the MCP tool)
        assert "BWM_WORLD" not in rel and "--mode" not in rel and "python3" not in rel
    assert (P.READONLY_BUDGET_SUFFIX in rel) == (cell.arm == "grounded-readonly")  # P2
    levers = P.default_levers(cell.instance)  # P4
    for u in brief["units"]:
        assert P.levers_line(levers[u["id"]]) in rel
    assert P.LEVERS_REPLACE_SENTENCE in rel
    assert P.DEFAULT_LEVERS_PREFIX not in P.paper_prompt(brief, cell.arm)


@pytest.mark.parametrize("world", WORLD_IDS)
def test_p4_levers_are_the_ones_the_do_nothing_trajectory_runs_at(world):
    """The disclosed levers, sent as an explicit allocation, reproduce every unit's do-nothing revenue and cash
    exactly (so they are the defaults the oracle replaces), and every world uses constant levers."""
    from dbwm.eval2 import load_world

    w, brief = load_world(world), load_brief(world)
    lev = P.default_levers(world)
    alloc = {p["id"]: {u: lev[u][p["feeds_lever"]] for u in lev} for p in w.pools}
    out = w.evaluate(alloc)["units"]
    for u in brief["units"]:
        for d in ("revenue", "cash"):
            assert [str(q[d]) for q in out[u["id"]]] == u["base_trajectory"][d]
        assert all(len(set(v)) == 1 for v in lev[u["id"]].values())
    line = P.levers_line(lev[brief["units"][0]["id"]])
    assert line.startswith(P.DEFAULT_LEVERS_PREFIX) and "marketing_spend=" in line and "hires_per_quarter=1" in line


def test_p4_orion_levers():
    """orion-holdings: marketing $40K, $45K and $42K a quarter, one hire a quarter."""
    lev = P.default_levers("orion-holdings")
    assert [(v["marketing_spend"][0], v["hires_per_quarter"][0]) for v in lev.values()] == [
        ("40000", "1"), ("45000", "1"), ("42000", "1")]


def test_paper_prompt_pins_cover_every_world_and_arm():
    assert sorted(PAPER_PROMPT_SHA256) == sorted(f"{c.instance}|{c.arm}" for c in CELLS)


@pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.cell_id)
def test_paper_prompt_is_the_pinned_paper_text(cell):
    """paper_prompt is the paper harness's text byte for byte, with placeholders for its host paths."""
    text = P.paper_prompt(load_brief(cell.instance), cell.arm)
    assert hashlib.sha256(text.encode()).hexdigest() == PAPER_PROMPT_SHA256[f"{cell.instance}|{cell.arm}"]
    assert not ABS_PATH.search(text)
    if "world_query" in text:
        assert P.WORLD_JSON_PLACEHOLDER in text and P.WORLD_QUERY_PLACEHOLDER in text


def test_cli_transport_tool_line():
    brief = load_brief("orion-holdings")
    assert P.CLI_TOOL_LINE in P.build_prompt(brief, "grounded-iterate", transport="cli")


@pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.cell_id)
def test_staging_contains_no_answer_key_canary_or_scorer_file(cell):
    m = EVAL.stage(cell)
    assert [f.dst for f in m.files] == ["brief.json"]
    assert m.writable_dirs == []
    f = m.files[0]
    assert f.src == paths.data("eval2", "worlds", cell.instance, "brief.json")
    assert paths.private_root() not in f.src.resolve().parents
    staged = {"brief.json": f.src.read_text(), "TASK.md": m.task_md}
    values = EVAL.answer_key_values(cell)
    assert values, "every world has answer-key values to scan for"
    assert all(len(v) >= 8 for v in values)
    for name, text in staged.items():
        assert CANARY_GUID not in text and CANARY_STRING not in text, name
        for v in values:
            assert v not in text, (name, "answer-key value")
        for scorer_name in SCORER_ONLY_NAMES:
            if scorer_name in ("private", "grader", "answer_key", "ANSWER_KEY"):
                continue
            assert scorer_name not in text, (name, scorer_name)
    task = m.task_md
    assert task == EVAL.prompt(cell)
    assert not ABS_PATH.search(task)
    for tok in ("..", "private/", "ANSWER_KEY", "reference.json", "world.json"):
        assert tok not in task, tok


@pytest.mark.parametrize("wid", WORLD_IDS)
def test_answer_key_values_cover_the_reference_and_are_not_public(wid):
    cell = Cell("eval2", wid, "grounded-iterate", 0)
    values = set(EVAL.answer_key_values(cell))
    ref = load_reference(wid)
    assert ref["careful_optimum"]["multiple"] in values
    assert ref["hardness_floor"]["min_feasible_multiple"] in values
    assert ref["target_multiple"] not in values  # the target is public (in the prompt)
    public = load_brief(wid)
    assert not any(v in json.dumps(public) for v in values)


def test_stage_and_prompt_reject_unknown_cells():
    with pytest.raises(ValueError):
        EVAL.stage(Cell("eval2", "nowhere", "ungrounded", 0))
    with pytest.raises(ValueError):
        EVAL.prompt(Cell("eval2", "orion-holdings", "legacy-bash", 0))


@pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.cell_id)
def test_core_sandbox_precheck_is_clean(cell, tmp_path):
    """Build the real sandbox with dbwm.core.sandbox and run its pre-launch leak check."""
    from dbwm.core import sandbox as core_sandbox

    arm = ARMS[cell.arm]
    handle = core_sandbox.build_sandbox(EVAL.stage(cell), cell, arm.tools, arm.tools.oracle_tools,
                                        root=tmp_path)
    try:
        problems = core_sandbox.precheck(handle, answer_values=EVAL.answer_key_values(cell),
                                         prompt=EVAL.prompt(cell))
        assert problems == []
        names = sorted(p.name for p in handle.path.rglob("*") if p.is_file())
        assert names == ["TASK.md", "brief.json"]  # MCP arms: no shim in the sandbox
    finally:
        core_sandbox.cleanup(handle)
