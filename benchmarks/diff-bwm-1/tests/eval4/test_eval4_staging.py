"""What the agent sees, per arm: staging manifests, TASK.md, the prompt.

Leak tests for every arm x instance: the staged files carry no answer-key value,
no canary, no scorer-only name and no checker source; TASK.md and the prompt
pass the sandbox pre-check's text rules. A materialized copy of each manifest
is scanned with the core scanners (zip members of .docx/.xlsx included), and
the real core sandbox builder + pre-check is run once per arm.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from ._support import ARMS, cell, needs_data

from dbwm.core import canary
from dbwm.core.sandbox import check_text
from dbwm.core.types import CANARY_GUID, CANARY_STRING
from dbwm.eval4 import EVAL, SCORER_ONLY_NAMES, checker
from dbwm.eval4.prompt import PROMPT, build_task_md
from dbwm.eval4.selftest import CHECKER_SOURCE_FINGERPRINTS, staging_problems

INSTANCES = EVAL.instances()


def _materialize(man, dest: Path) -> None:
    for f in man.files:
        p = dest / f.dst
        p.parent.mkdir(parents=True, exist_ok=True)
        if f.src is not None:
            shutil.copyfile(f.src, p)
        else:
            p.write_bytes(f.content)
    (dest / "TASK.md").write_text(man.task_md)


@needs_data
@pytest.mark.parametrize("instance", INSTANCES)
@pytest.mark.parametrize("arm", ARMS)
def test_staging_leak_free(arm, instance, tmp_path):
    c = cell(arm)
    assert c.instance == instance
    assert staging_problems(EVAL, c) == []
    man = EVAL.stage(c)
    _materialize(man, tmp_path / "sb")
    values = EVAL.answer_key_values(c)
    assert canary.scan_for_values(tmp_path / "sb", values) == []
    assert canary.scan_for_guid(tmp_path / "sb") == []
    names = {n.lower() for n in SCORER_ONLY_NAMES}
    for p in (tmp_path / "sb").rglob("*"):
        assert p.name.lower() not in names, p
        if p.is_file():
            data = p.read_bytes()
            assert CANARY_STRING.encode() not in data
            for fp in CHECKER_SOURCE_FINGERPRINTS:
                assert fp.encode() not in data, (fp, p)
    # the checker source itself, or any line of it, is not staged
    src = (Path(checker.__file__)).read_bytes()
    for p in (tmp_path / "sb").rglob("*.py"):
        assert p.read_bytes() != src
    assert not list((tmp_path / "sb").rglob("*.py"))


@needs_data
@pytest.mark.parametrize("arm", ARMS)
def test_manifest_shape(arm):
    man = EVAL.stage(cell(arm))
    dsts = sorted(f.dst for f in man.files)
    srcs = [d for d in dsts if d.startswith(("shared/", "week/"))]
    assert len(srcs) == 147
    assert sum(d.startswith("shared/") for d in srcs) == 130 and sum(d.startswith("week/") for d in srcs) == 17
    extra = sorted(set(dsts) - set(srcs))
    assert extra == (["CHECKLIST.md"] if arm != "baseline" else [])
    assert man.writable_dirs == []
    assert all(f.mode == 0o444 and not f.executable for f in man.files)


@needs_data
def test_same_checklist_for_all_checklist_arms():
    blobs = set()
    for arm in ARMS[1:]:
        ck = [f for f in EVAL.stage(cell(arm)).files if f.dst == "CHECKLIST.md"]
        blobs.add(ck[0].content)
    assert blobs == {checker.checklist_md_bytes()}


@needs_data
@pytest.mark.parametrize("arm", ARMS)
def test_task_md_and_prompt_pass_precheck_text_rules(arm):
    t = build_task_md(arm, "v1")
    assert check_text(t, "TASK.md") == []
    assert check_text(PROMPT, "prompt") == []
    assert EVAL.prompt(cell(arm)) == PROMPT
    for bad in ("qa_check.py", "_qa_impl", "python3 qa_check", "aa-briefcase-lite", "checks.jsonl", "private/"):
        assert bad not in t
    if arm in ("checklist-verdict", "checklist-executable"):
        assert "./qa_check" in t
    else:
        assert "qa_check" not in t.replace("\"qa_runs\"", "")
    assert CANARY_GUID not in t


@needs_data
def test_v1_task_md_differs_from_paper_only_in_tool_lines():
    for arm in ARMS:
        v1, paper = build_task_md(arm, "v1"), build_task_md(arm, "paper")
        if arm in ("baseline", "checklist-prose"):
            assert v1 == paper
        else:
            import difflib
            changed = [ln for ln in difflib.unified_diff(paper.splitlines(), v1.splitlines(), lineterm="", n=0)
                       if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
            assert changed and all("qa_check" in ln or "Run it against" in ln for ln in changed), changed


@needs_data
@pytest.mark.parametrize("arm", ARMS)
def test_core_sandbox_builder_precheck_passes(arm, tmp_path, monkeypatch):
    """Integration with the core sandbox builder: the real pre-check is clean."""
    from dbwm.core import sandbox as sbx

    monkeypatch.setenv("DBWM_SANDBOX_ROOT", str(tmp_path))
    c = cell(arm)
    a = EVAL.arms[arm]
    man = EVAL.stage(c)
    h = sbx.build_sandbox(man, c, a.tools, a.tools.oracle_tools)
    try:
        assert sbx.precheck(h, answer_values=EVAL.answer_key_values(c), prompt=EVAL.prompt(c)) == []
        listing = set(h.listing)
        if arm in ("checklist-verdict", "checklist-executable"):
            assert "qa_check" in listing
            shim = (h.path / "qa_check").read_text()
            for bad in ("figures", "verdict", "private", "eval4", "RECOMMENDATION_TOKENS"):
                assert bad not in shim
        else:
            assert "qa_check" not in listing
    finally:
        sbx.remove_tree(h.path)


def test_answer_key_values_are_private_identifiers():
    vals = EVAL.answer_key_values(cell("baseline"))
    assert "W1-T2-A-01" in vals and "W1-T2-C-03" in vals and "_qa_impl" in vals
    assert all(len(v) >= 6 for v in vals)
    # never an achievable target (numbers an honest agent could compute)
    assert not any(any(ch.isdigit() for ch in v) and v.replace(".", "").isdigit() for v in vals)
