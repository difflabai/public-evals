"""Prompts (paper text + documented changes only) and staging leak tests for every arm x instance."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from dbwm.core import paths
from dbwm.core.types import CANARY_GUID, CANARY_STRING, Cell
from dbwm.eval2 import build_prompt as eval2_build_prompt
from dbwm.eval2 import load_brief
from dbwm.eval2.prompt import LEVERS_REPLACE_SENTENCE, default_levers, levers_line
from dbwm.eval3 import ARMS, EVAL, WORLD_IDS
from dbwm.eval3.prompt import (
    COMMAND_TIMEOUT_S,
    MCP_EVAL_LINE,
    MCP_TEST_LINE,
    build_prompt,
    paper_prompt,
    strip_release_changes,
)

CELLS = [Cell("eval3", w, a, 0) for w in WORLD_IDS for a in ARMS]
HOST_PATH = re.compile(r"/(?:Users|home|private|var/folders)/")
ACTION_SPACE = "eng_headcount values are net hires and must be whole numbers >= 0 (no layoffs)."
PAPER_PROMPT_SHA256 = json.loads(
    (Path(__file__).parent / "fixtures" / "paper_prompt_sha256.json").read_text())["sha256"]


def test_paper_prompt_pins_cover_every_world_and_arm():
    assert sorted(PAPER_PROMPT_SHA256) == sorted(f"{c.instance}|{c.arm}" for c in CELLS)


@pytest.mark.parametrize("cell", CELLS, ids=lambda c: c.cell_id)
def test_paper_prompt_is_the_pinned_paper_text(cell):
    """paper_prompt is the paper harness's text byte for byte; host paths are placeholders."""
    text = paper_prompt(load_brief(cell.instance), cell.arm)
    assert hashlib.sha256(text.encode()).hexdigest() == PAPER_PROMPT_SHA256[f"{cell.instance}|{cell.arm}"]
    assert not HOST_PATH.search(text)


@pytest.mark.parametrize("world", WORLD_IDS)
@pytest.mark.parametrize("arm", ["gen-figures", "gen-both", "gen-code"])
def test_release_prompt_is_paper_plus_documented_changes(world, arm):
    b = load_brief(world)
    rel = build_prompt(b, arm)
    assert rel != paper_prompt(b, arm)
    assert strip_release_changes(rel, arm, b) == paper_prompt(b, arm)
    assert ACTION_SPACE in rel  # P3, from eval 2's own release prompt
    assert LEVERS_REPLACE_SENTENCE in rel  # P4, likewise: the do-nothing levers the oracle replaces
    for u, lev in default_levers(world).items():
        assert levers_line(lev) in rel
    assert "model_tool.py" not in rel and "--mode" not in rel and "<SANDBOX>" not in rel
    if arm == "gen-figures":
        assert MCP_EVAL_LINE in rel and "model_test" not in rel and "task.json" not in rel
    elif arm == "gen-both":
        assert MCP_TEST_LINE in rel
    else:
        assert f"each command\nis stopped after {COMMAND_TIMEOUT_S} seconds." in rel
        assert "time-limited per" not in rel and "model_eval" not in rel and "model_test" not in rel


@pytest.mark.parametrize("arm", ["ungrounded", "grounded-iterate"])
def test_baseline_prompts_are_eval2s(arm):
    for w in WORLD_IDS:
        assert build_prompt(load_brief(w), arm) == eval2_build_prompt(load_brief(w), arm)


def test_gen_code_timeout_is_exposed_to_the_harness():
    assert EVAL.command_timeout_s(Cell("eval3", "orion-holdings", "gen-code", 0)) == COMMAND_TIMEOUT_S
    assert EVAL.default_regime()["extra"]["bash_command_timeout_s"] == COMMAND_TIMEOUT_S
    for a in ARMS:
        if a != "gen-code":
            assert EVAL.command_timeout_s(Cell("eval3", "orion-holdings", a, 0)) is None


def _staged_texts(cell):
    man = EVAL.stage(cell)
    texts = {f.dst: (f.src.read_text(encoding="utf-8") if f.src else f.content.decode("utf-8")) for f in man.files}
    texts["TASK.md"] = man.task_md
    return man, texts


@pytest.mark.parametrize("cell", CELLS, ids=[c.cell_id for c in CELLS])
def test_staging_has_only_agent_visible_files(cell):
    man, texts = _staged_texts(cell)
    want = {"brief.json", "task.json"} if cell.arm in ("gen-both", "gen-code") else {"brief.json"}
    assert {f.dst for f in man.files} == want
    assert man.writable_dirs == []
    for f in man.files:
        assert f.src is not None and paths.data_root() in f.src.resolve().parents
        assert f.mode == 0o444 and not f.executable
    assert man.task_md == EVAL.prompt(cell)


@pytest.mark.parametrize("cell", CELLS, ids=[c.cell_id for c in CELLS])
def test_staging_leaks_nothing(cell):
    _man, texts = _staged_texts(cell)
    values = EVAL.answer_key_values(cell)
    assert values, "answer-key values must exist to scan for"
    names = [n for n in EVAL.SCORER_ONLY_NAMES if n not in ("private", "grader")]
    for name, text in texts.items():
        assert CANARY_GUID not in text and CANARY_STRING not in text, name
        assert not [v for v in values if v in text], name
        assert not [n for n in names if n in text], name
        assert not HOST_PATH.search(text), name
        assert str(paths.PKG_ROOT) not in text and "../" not in text, name


@pytest.mark.parametrize("cell", CELLS, ids=[c.cell_id for c in CELLS])
def test_core_sandbox_build_and_precheck_are_clean(cell):
    from dbwm.core import sandbox

    arm = EVAL.arms[cell.arm]
    handle = sandbox.build_sandbox(EVAL.stage(cell), cell, arm.tools, arm.tools.oracle_tools)
    try:
        problems = sandbox.precheck(handle, answer_values=EVAL.answer_key_values(cell), prompt=EVAL.prompt(cell))
        assert problems == []
        listing = sorted(p.name for p in handle.path.iterdir())
        assert "reference.json" not in listing and "world.json" not in listing
        assert not any(n.endswith(".py") for n in listing)  # no tool source, no engine (decision 3)
        if cell.arm == "gen-figures":
            assert "task.json" not in listing
    finally:
        sandbox.cleanup(handle)


def test_answer_key_values_are_eval2s_minus_public_text():
    from dbwm.eval2 import EVAL as EVAL2

    for w in WORLD_IDS:
        cell = Cell("eval3", w, "gen-both", 0)
        mine = EVAL.answer_key_values(cell)
        theirs = EVAL2.answer_key_values(Cell("eval2", w, "ungrounded", 0))
        assert set(mine) <= set(theirs) and len(mine) >= 5
        public = json.dumps(load_brief(w)) + EVAL.prompt(cell)
        assert not [v for v in mine if v in public]
