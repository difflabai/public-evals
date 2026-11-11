"""Eval-1 arms, prompts and staging: exact tool policies, no leaks into what the agent sees."""

from __future__ import annotations

import re

import pytest

from dbwm.core.types import CANARY_GUID, CANARY_STRING, BUILTIN_TOOLS, Cell, Isolation, RegimeConfig
from dbwm.eval1 import PUBLISHED_INSTANCES, SCORER_ONLY_NAMES, all_instances
from dbwm.eval1.arms import ARM_ORDER, ORACLE_MODE, RUN_MODEL
from dbwm.eval1.brief import load_brief
from dbwm.eval1.prompt import build_prompt, build_prompt_paper

ABS_PATH = re.compile(r"(?:^|[\s'\"(=])/(?:Users|home|private|tmp|var|opt|etc|root)/", re.M)
ALL_INSTANCES = all_instances()


def test_instances():
    assert len(ALL_INSTANCES) == 30
    assert len(PUBLISHED_INSTANCES) == 15 and set(PUBLISHED_INSTANCES) <= set(ALL_INSTANCES)


def test_arms_exact_policies(ev):
    """Decisions 2/4: six no-bash arms at Isolation.HOST; the contrast is exactly the oracle tool."""
    assert tuple(ev.arms) == ARM_ORDER
    for name, arm in ev.arms.items():
        assert arm.name == name and arm.canonical is True
        assert arm.tools.builtin == frozenset({"read", "write"})
        assert set(arm.tools.builtin) <= set(BUILTIN_TOOLS) and not arm.tools.needs_bash
        assert arm.min_isolation == Isolation.HOST
        assert arm.oracle_mode == ORACLE_MODE[name]
        if name == "ungrounded":
            assert arm.tools.oracle_tools == () and arm.query_budget is None
        else:
            assert arm.tools.oracle_tools == (RUN_MODEL,) and arm.tools.transport == "mcp"
    assert {a: ev.arms[a].oracle_mode for a in ARM_ORDER} == {
        "ungrounded": None, "grounded-readonly": "numeric", "grounded-iterate": "numeric",
        "prose-feedback": "prose", "raw-figures": "raw", "broken-model": "broken"}
    assert ev.arms["grounded-readonly"].query_budget == 3
    assert all(ev.arms[a].query_budget is None for a in ARM_ORDER if a != "grounded-readonly")


def test_tool_spec_is_neutral():
    """No mode, backend, path or lesion wording in what the agent is told about the tool."""
    blob = (RUN_MODEL.description + str(RUN_MODEL.input_schema) + RUN_MODEL.cli_usage).lower()
    for word in ("mode", "backend", "broken", "lesion", "prose", "raw", "numeric", "bwm_query", "/"):
        if word == "/":
            assert not ABS_PATH.search(blob)
        else:
            assert not re.search(rf"\b{re.escape(word)}\b", blob), word
    assert RUN_MODEL.input_schema["required"] == ["plan"]


def test_default_regime_and_cells(ev):
    d = ev.default_regime()
    assert d["eval_id"] == "eval1" and d["arms"] == list(ARM_ORDER) and "model" not in d
    assert d["isolation"] == "host" and d["ruleset"] == "v1" and d["max_turns"] > 0
    reg = RegimeConfig(eval_id="eval1", model="m", arms=list(ARM_ORDER), reps=3)
    cells = ev.cells(reg)
    assert len(cells) == 15 * 6 * 3 and len({c.cell_id for c in cells}) == len(cells)
    reg2 = RegimeConfig(eval_id="eval1", model="m", arms=["ungrounded"], instances=["sensorgrid-iot__soft"])
    assert [c.cell_id for c in ev.cells(reg2)] == ["sensorgrid-iot__soft__ungrounded__r0"]
    with pytest.raises(ValueError):
        ev.cells(RegimeConfig(eval_id="eval1", model="m", arms=["legacy-bash"]))
    with pytest.raises(ValueError):
        ev.cells(RegimeConfig(eval_id="eval1", model="m", arms=["ungrounded"], instances=["x__soft"]))


def test_prompt_is_paper_text_except_tool_lines(ev):
    """only the tool-invocation paragraph differs from the paper's corrected prompt."""
    for inst in ("northwind-analytics__hostile", "atlas-advisory__soft"):
        brief = load_brief(inst)
        for arm in ARM_ORDER:
            mine = ev.prompt(Cell("eval1", inst, arm, 0))
            paper = build_prompt_paper(brief, arm, "TOOL")
            if arm == "ungrounded":  # : only the tool sentence differs
                from dbwm.eval1.prompt import UNGROUNDED_EXTRA, UNGROUNDED_EXTRA_V1

                assert mine == paper.replace(UNGROUNDED_EXTRA, UNGROUNDED_EXTRA_V1) and mine != paper
                assert "NO tools" not in mine and "file tools" in mine and "NO tool that runs code" in mine
                continue
            head = paper.split("You have an executable model")[0]
            assert mine.startswith(head)
            tail_paper = paper.split("TOOL\n", 1)[1]
            if arm == "grounded-readonly":  # : the enforced budget is stated
                tail_paper = tail_paper.replace(
                    "You may run a few what-ifs before committing.",
                    "You may run a few what-ifs before committing (at most 3 runs of the model).")
            assert mine.endswith(tail_paper)
            assert "run_model" in mine


def test_prompt_states_plan_own_baseline_convention(ev):
    """A01 regression: the prompt states the goal against the plan's OWN period 0, which is what
    the scorer computes (goal_multiple uses the plan's trajectory)."""
    from decimal import Decimal

    from dbwm.eval1.engine import evaluate, goal_multiple, load_model_and_tiers

    text = ev.prompt(Cell("eval1", "northwind-analytics__hostile", "ungrounded", 0))
    assert "OF YOUR OWN PLAN'S trajectory" in text and "NOT off the do-nothing starting figures" in text
    model, tiers_doc = load_model_and_tiers("northwind-analytics")
    plan = {"acv": ["18000"] * 6}  # raises period-0 revenue too
    periods = evaluate(model, plan)
    own = goal_multiple(periods, tiers_doc["goal"])
    displayed = periods[5]["revenue"] / Decimal(load_brief("northwind-analytics__hostile")
                                                ["base_trajectory"]["revenue"][0])
    assert own != displayed  # the convention matters (non-vacuous)


@pytest.mark.parametrize("inst", ALL_INSTANCES)
def test_staging_leaks_every_arm(ev, inst):
    """For every arm x instance: TASK.md only; no answer-key value, canary, scorer-only name or
    absolute path in anything staged; the prompt is the TASK.md."""
    for arm in ARM_ORDER:
        cell = Cell("eval1", inst, arm, 0)
        m = ev.stage(cell)
        assert m.files == [] and m.writable_dirs == []
        task = m.task_md
        assert task == ev.prompt(cell)
        values = ev.answer_key_values(cell)
        assert values, "answer_key_values must not be empty (else this test is vacuous)"
        for v in values:
            assert v not in task, (cell.cell_id, "answer-key value")
        assert CANARY_GUID not in task and CANARY_STRING not in task
        for name in SCORER_ONLY_NAMES:
            assert name not in task, (cell.cell_id, name)
        assert not ABS_PATH.search(task) and ".." not in task.replace("...", "")
        for bad in ("bwm_query", "--mode", "--backend", "BWM_", "reference.json", "ANSWER_KEY",
                    "lesion", "candidate.json", "oracle"):
            assert bad not in task, (cell.cell_id, bad)
        if arm == "ungrounded":
            assert "run_model" not in task


def test_answer_key_values_are_private(ev, calibration):
    from dbwm.eval1 import _paths

    for inst in ALL_INSTANCES:
        cell = Cell("eval1", inst, "ungrounded", 0)
        values = ev.answer_key_values(cell)
        company = inst.rsplit("__", 1)[0]
        private_text = (_paths.private("calibration", "report.json").read_text(encoding="utf-8")
                        + _paths.private("worlds", company, "eval-tiers.json").read_text(encoding="utf-8"))
        public_text = "".join(p.read_text(encoding="utf-8") for p in _paths.data("tasks").glob(f"{inst}.*"))
        for v in values:
            assert len(v) >= 8 and v in private_text and v not in public_text
        assert f"local:{company}" in values


def test_real_sandbox_precheck_published_cells(ev):
    """Build the actual sandboxes (core builder) and run the core pre-check for the 15 published
    instances x 6 arms. (Full real-sandbox/runner leak tests are wave 2.)"""
    sandbox = pytest.importorskip("dbwm.core.sandbox")
    for inst in PUBLISHED_INSTANCES:
        for arm_name, arm in ev.arms.items():
            cell = Cell("eval1", inst, arm_name, 0)
            h = sandbox.build_sandbox(ev.stage(cell), cell, arm.tools, arm.tools.oracle_tools)
            try:
                problems = sandbox.precheck(h, answer_values=ev.answer_key_values(cell), prompt=ev.prompt(cell))
                assert problems == [], (cell.cell_id, problems)
                assert sorted(p.name for p in h.path.iterdir()) == ["TASK.md"]
            finally:
                sandbox.cleanup(h, keep=False)


def test_build_prompt_rejects_unknowns():
    brief = load_brief("sprout-crm__soft")
    with pytest.raises(ValueError):
        build_prompt(brief, "legacy-bash", transport="mcp")
    with pytest.raises(ValueError):
        build_prompt(brief, "grounded-iterate")  # tool arm without a transport
    cli = build_prompt(brief, "grounded-iterate", transport="cli")
    assert "./run_model candidate.json" in cli
