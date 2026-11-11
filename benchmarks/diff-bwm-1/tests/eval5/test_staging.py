"""Staging leak tests for every arm x instance, arm information parity, prompt hygiene."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbwm.core import paths as core_paths
from dbwm.core import sandbox as core_sandbox
from dbwm.core.types import CANARY_GUID, Isolation
from dbwm.eval5 import EVAL, SCORER_ONLY_NAMES
from dbwm.eval5 import dataset as D
from dbwm.eval5 import engine as E
from dbwm.eval5.arms import ARMS, ARMS_WITH_CHECKLIST, ARMS_WITH_TOOL, QA_CHECK_TOOL

from .conftest import ALL_CELLS, cell

# Strings that name grading material or the paper's leaky sandbox files.
LEAK_TOKENS = ("checks.jsonl", "answer_key", "blueprint.json", "anchors.json", "contradictions.json",
               "score_1_criteria", "score_0_criteria", "registry_v", "_qa_registry", "_qa_impl", "probes.json",
               "as-run-pins")


def _blobs(m):
    for f in m.files:
        yield f.dst, (f.src.read_bytes() if f.src is not None else f.content)
    yield "TASK.md", m.task_md.encode()


@pytest.mark.parametrize("sid,arm", ALL_CELLS)
def test_manifest_holds_agent_visible_files_only(sid, arm):
    m = EVAL.stage(cell(sid, arm))
    data_root = D.data().resolve()
    for f in m.files:
        assert f.src is not None and data_root in f.src.resolve().parents, f.src
        assert not f.dst.startswith("/") and ".." not in Path(f.dst).parts
        assert f.dst.split("/")[0] in ("shared", "engagement") or f.dst == "CHECKLIST.md"
        assert Path(f.dst).name not in SCORER_ONLY_NAMES
        assert f.mode == 0o444 and not f.executable
    assert m.writable_dirs == []


@pytest.mark.parametrize("sid,arm", ALL_CELLS)
def test_staged_content_has_no_answer_key_values_canary_or_leak_tokens(sid, arm):
    m = EVAL.stage(cell(sid, arm))
    vals = EVAL.answer_key_values(cell(sid, arm))
    for dst, b in _blobs(m):
        t = b.decode("utf-8", errors="replace")
        assert CANARY_GUID not in t, dst
        for v in vals:
            assert v not in t, (dst, v[:60])
        for tok in LEAK_TOKENS:
            assert tok not in t, (dst, tok)


@pytest.mark.parametrize("sid,arm", ALL_CELLS)
def test_task_md_and_prompt_pass_core_text_checks(sid, arm):
    m = EVAL.stage(cell(sid, arm))
    assert core_sandbox.check_text(m.task_md, "TASK.md") == []
    p = EVAL.prompt(cell(sid, arm))
    assert core_sandbox.check_text(p, "prompt") == []
    assert str(core_paths.PKG_ROOT) not in m.task_md + p
    assert "uv run" not in m.task_md and "pip install" not in m.task_md


@pytest.fixture
def pass_conditions_off():
    """EVAL as the runner leaves it after cells() for a regime with the pass-condition opt-out."""
    from dbwm.core.types import RegimeConfig
    EVAL.cells(RegimeConfig(eval_id="eval5", model="m", arms=list(ARMS),
                            extra={"eval5": {"checklist_pass_conditions": False}}))
    assert EVAL.pass_conditions is False
    yield
    EVAL.cells(RegimeConfig(eval_id="eval5", model="m", arms=list(ARMS)))
    assert EVAL.pass_conditions is True


def _checklists(sid):
    out = {}
    for arm in ARMS:
        m = EVAL.stage(cell(sid, arm))
        out[arm] = {f.dst: (f.src.read_bytes() if f.src is not None else f.content) for f in m.files}
    return out


@pytest.mark.parametrize("on", [True, False])
def test_information_parity_across_arms(on, request):
    if not on:
        request.getfixturevalue("pass_conditions_off")
    for sid in D.SCENARIOS:
        files = _checklists(sid)
        assert "CHECKLIST.md" not in files["baseline"]
        checklists = {files[a]["CHECKLIST.md"] for a in ARMS_WITH_CHECKLIST}
        assert len(checklists) == 1  # byte-identical in prose, verdict and executable
        text = checklists.pop().decode()
        reg = D.registry(sid, "v1")
        assert text == E.render_checklist_v1(reg, pass_conditions=on)
        assert ("PASSES WHEN:" in text) is on
        base = {k: v for k, v in files["baseline"].items()}
        for a in ARMS_WITH_CHECKLIST:
            assert {k: v for k, v in files[a].items() if k != "CHECKLIST.md"} == base
        # the tool arms differ from prose only by the harness-served qa_check tool
        assert ARMS["checklist-prose"].tools.oracle_tools == ()
        for a in ARMS_WITH_TOOL:
            assert ARMS[a].tools.oracle_tools == (QA_CHECK_TOOL,)
            assert ARMS[a].tools.builtin == ARMS["checklist-prose"].tools.builtin
        assert ARMS["checklist-verdict"].oracle_mode == "verdict"
        assert ARMS["checklist-executable"].oracle_mode == "figures"


def test_arms_need_container_isolation():
    for a in ARMS.values():
        assert a.tools.needs_bash and a.min_isolation == Isolation.CONTAINER


def test_tool_spec_is_neutral():
    blob = (QA_CHECK_TOOL.description + QA_CHECK_TOOL.cli_usage + QA_CHECK_TOOL.cli_name).lower()
    for w in ("mode", "verdict", "figures", "registry", "private", "/"):
        assert w not in blob.replace("./qa_check", ""), w
    assert QA_CHECK_TOOL.cli_args == "none"


@pytest.mark.parametrize("sid,arm", [(s, a) for s in D.SCENARIOS for a in ("baseline", "checklist-executable")])
def test_real_sandbox_build_and_precheck(tmp_path, monkeypatch, sid, arm):
    """Build the actual sandbox with the core builder and run its pre-launch checks."""
    monkeypatch.setenv("DBWM_SANDBOX_ROOT", str(tmp_path))
    c = cell(sid, arm)
    a = ARMS[arm]
    h = core_sandbox.build_sandbox(EVAL.stage(c), c, a.tools, a.tools.oracle_tools)
    try:
        probs = core_sandbox.precheck(h, answer_values=EVAL.answer_key_values(c), prompt=EVAL.prompt(c))
        assert probs == []
        names = {p.name for p in Path(h.path).rglob("*")}
        assert not names & set(SCORER_ONLY_NAMES)
        assert ("qa_check" in names) == (arm in ARMS_WITH_TOOL)
    finally:
        core_sandbox.cleanup(h)


def test_cells_cover_design():
    from dbwm.core.types import RegimeConfig
    r = RegimeConfig(eval_id="eval5", model="m", arms=list(ARMS), reps=4)
    cells = EVAL.cells(r)
    assert len(cells) == 64 and len({c.cell_id for c in cells}) == 64
    with pytest.raises(ValueError):
        EVAL.cells(RegimeConfig(eval_id="eval5", model="m", arms=["legacy-executable"]))


def test_deliverables_for_is_per_scenario():
    """the collector declares only this scenario's names (core runner hook)."""
    from dbwm.core.runner import deliverables_for
    from dbwm.core.types import Cell

    seen = set()
    for s in D.manifest()["scenarios"]:
        got = deliverables_for(EVAL, Cell("eval5", s["id"], "baseline", 0))
        assert got == s["deliverables"] and got[-1] == "claims.json"
        assert s["prose_deliverable"] in got and s["structured_deliverable"] in got
        seen |= set(got)
    assert seen == set(EVAL.deliverables)          # the eval-wide union is unchanged
    with pytest.raises(ValueError):
        EVAL.deliverables_for(Cell("eval5", "nope", "baseline", 0))


# ---------------------------------------------------------------------------
# pass-condition disclosure 
# ---------------------------------------------------------------------------


def test_regime_extra_opt_out_is_validated():
    from dbwm.core.types import RegimeConfig
    from dbwm.eval5 import checklist_pass_conditions

    def r(extra):
        return RegimeConfig(eval_id="eval5", model="m", arms=list(ARMS), extra=extra)
    assert checklist_pass_conditions(None) is True
    assert checklist_pass_conditions(r({})) is True
    assert checklist_pass_conditions(r({"eval5": {}})) is True
    assert checklist_pass_conditions(r({"eval5": {"checklist_pass_conditions": False}})) is False
    from dbwm.core.regime import RegimeError
    for bad in ({"eval5": {"checklist_pass_conditions": "no"}}, {"eval5": {"pass_conditions": False}},
                {"eval5": False}):
        with pytest.raises(RegimeError):
            checklist_pass_conditions(r(bad))
    # `dbwm run` reports a malformed setting as a usage error (exit 2), not a traceback
    import json
    import os
    import subprocess
    import sys
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "bad.json"
        bad.write_text(json.dumps({"eval5": {"checklist_pass_conditions": "no"}}))
        env = dict(os.environ, DBWM_RUNS_DIR=str(Path(td) / "runs"), DBWM_SANDBOX_ROOT=td)
        pr = subprocess.run([sys.executable, "-m", "dbwm", "run", "--eval", "5", "--adapter", "stub", "--reps", "1",
                             "--model", "dbwm-stub", "--regime-extra", str(bad), "--dry-run"],
                            cwd=str(core_paths.PKG_ROOT), env=env, capture_output=True, text=True, timeout=300)
        assert pr.returncode == 2 and "checklist_pass_conditions must be true or false" in pr.stderr, pr.stderr[-800:]
        assert "Traceback" not in pr.stderr
    # the setting is part of the regime, so of the regime hash
    assert r({"eval5": {"checklist_pass_conditions": False}}).regime_hash() != r({}).regime_hash()


@pytest.mark.parametrize("sid", D.SCENARIOS)
def test_disclosed_values_are_the_pass_conditions_of_checklist_arms(sid):
    pcs = E.pass_conditions(D.registry(sid, "v1"))
    assert EVAL.disclosed_values_for(cell(sid, "baseline")) == []
    for arm in ARMS_WITH_CHECKLIST:
        got = EVAL.disclosed_values_for(cell(sid, arm))
        assert got == list(dict.fromkeys(pcs)) and got
        text = EVAL.stage(cell(sid, arm))
        blob = next(f.src.read_text() for f in text.files if f.dst == "CHECKLIST.md")
        assert all(v in blob for v in got)
    # the baseline arm keeps every answer-key value; checklist arms drop only values shown in PASSES WHEN
    full = EVAL.answer_key_values(cell(sid, "baseline"))
    for arm in ARMS_WITH_CHECKLIST:
        part = EVAL.answer_key_values(cell(sid, arm))
        dropped = set(full) - set(part)
        assert set(part) <= set(full) and dropped
        assert all(any(v in pc for pc in pcs) for v in dropped)


def test_nothing_disclosed_when_pass_conditions_are_off(pass_conditions_off):
    for sid in D.SCENARIOS:
        for arm in ARMS:
            assert EVAL.disclosed_values_for(cell(sid, arm)) == []
            assert EVAL.answer_key_values(cell(sid, arm)) == EVAL.answer_key_values(cell(sid, "baseline"))


@pytest.mark.parametrize("sid", D.SCENARIOS)
def test_pass_conditions_would_leak_without_the_disclosure(sid):
    """The PASSES WHEN texts are registry score_1 texts: the scan exempts them only as disclosed values."""
    from dbwm.core import canary as core_canary
    from dbwm.eval5.score import answer_key_values
    c = cell(sid, "checklist-prose")
    blob = next(f.src.read_text() for f in EVAL.stage(c).files if f.dst == "CHECKLIST.md")
    vals = answer_key_values(sid)
    assert core_canary.scan_text_for_values(blob, vals)  # undisclosed: these would be leaks
    assert not core_canary.scan_text_for_values(blob, vals, disclosed=EVAL.disclosed_values_for(c))


@pytest.mark.parametrize("sid", D.SCENARIOS)
def test_real_sandbox_precheck_with_pass_conditions(tmp_path, monkeypatch, sid):
    """The core pre-check over a checklist-prose sandbox with the full answer-key list and the disclosure."""
    from dbwm.core.types import disclosed_values_for
    from dbwm.eval5.score import answer_key_values
    monkeypatch.setenv("DBWM_SANDBOX_ROOT", str(tmp_path))
    c = cell(sid, "checklist-prose")
    a = ARMS["checklist-prose"]
    h = core_sandbox.build_sandbox(EVAL.stage(c), c, a.tools, a.tools.oracle_tools)
    try:
        listed = (Path(h.path) / "CHECKLIST.md").read_text()
        assert listed.count("PASSES WHEN:") == len(E.pass_conditions(D.registry(sid, "v1")))
        disclosed = disclosed_values_for(EVAL, c)
        assert core_sandbox.precheck(h, answer_values=answer_key_values(sid), prompt=EVAL.prompt(c),
                                     disclosed_values=disclosed) == []
        assert core_sandbox.precheck(h, answer_values=answer_key_values(sid), prompt=EVAL.prompt(c))
    finally:
        core_sandbox.cleanup(h)
