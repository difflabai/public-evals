"""EVAL contract: EvalDef protocol, arms, cells, regime defaults, dataset manifest."""

from __future__ import annotations

import json
import re

import pytest
from ._support import ARMS, HAVE_DATA, HAVE_PRIVATE, cell

from dbwm.core.types import EvalDef, Isolation, RegimeConfig
from dbwm.eval4 import EVAL, SCORER_ONLY_NAMES, dataset
from dbwm.eval4.arms import BUILTINS, QA_CHECK_TOOL


def test_protocol_and_identity():
    assert isinstance(EVAL, EvalDef)
    assert EVAL.eval_id == "eval4" and EVAL.instances() == ["w1_t2"]
    assert tuple(EVAL.deliverables) == ("market_model.xlsx", "claims.json")
    assert list(EVAL.arms) == list(ARMS)


@pytest.mark.parametrize("arm", ARMS)
def test_arm_policies(arm):
    a = EVAL.arms[arm]
    assert a.tools.builtin == BUILTINS == frozenset({"read", "write", "edit", "bash", "glob", "grep"})
    assert a.min_isolation == Isolation.CONTAINER and a.tools.needs_bash and a.canonical
    if arm in ("checklist-verdict", "checklist-executable"):
        assert a.tools.oracle_tools == (QA_CHECK_TOOL,) and a.tools.transport == "cli"
        assert a.oracle_mode == ("verdict" if arm == "checklist-verdict" else "figures")
    else:
        assert a.tools.oracle_tools == () and a.oracle_mode is None
    assert a.query_budget is None


def test_qa_check_tool_spec_is_neutral():
    t = QA_CHECK_TOOL
    text = " ".join([t.name, t.description, t.cli_name, t.cli_usage, json.dumps(t.input_schema)])
    for bad in ("figures", "verdict", "mode", "private", "rubric", "judge"):
        assert not re.search(rf"\b{bad}\b", text), bad
    assert "/" not in text.replace("./qa_check", "")
    assert t.cli_args == "none" and t.input_schema.get("properties") == {}


def test_cells_and_regime():
    reg = RegimeConfig.from_json({**EVAL.default_regime(), "model": "m"})
    cells = EVAL.cells(reg)
    assert len(cells) == 32 and len({c.cell_id for c in cells}) == 32
    assert reg.isolation == Isolation.CONTAINER and reg.ruleset == "v1" and reg.judge_model == "claude-sonnet-4-6"
    with pytest.raises(ValueError):
        EVAL.cells(RegimeConfig.from_json({**EVAL.default_regime(), "model": "m", "arms": ["legacy-verdict"]}))
    with pytest.raises(ValueError):
        EVAL.cells(RegimeConfig.from_json({**EVAL.default_regime(), "model": "m", "instances": ["w1_t3"]}))


def test_regime_loads_through_core():
    from dbwm.core import regime as core_regime

    reg = core_regime.build_regime(EVAL, {"model": "m", "adapter": "stub"})
    assert reg.eval_id == "eval4" and reg.max_turns == 80 and reg.usd_rail == 8.0 and reg.reps == 8
    assert reg.extra["judge_passes"] == 3 and reg.isolation == Isolation.CONTAINER
    with pytest.raises(core_regime.RegimeError, match="needs isolation container"):
        core_regime.build_regime(EVAL, {"model": "m", "adapter": "stub", "isolation": "host"})
    core_regime.build_regime(EVAL, {"model": "m", "adapter": "stub", "isolation": "host-unsafe"})


def test_scorer_only_names_cover_the_grader_files():
    names = {n.lower() for n in SCORER_ONLY_NAMES}
    for f in ("checks.jsonl", "grading_rubric_w1.json", "traceability_w1.json", "source_graph_w1.json",
              "judge_system.txt", "judge_user.txt", "submissions", "_qa_impl.py"):
        assert f in names
    # names an honest agent may legitimately give its own files are NOT listed
    assert "checker.py" not in names and "qa_check" not in names and "checklist.md" not in names


def test_unknown_cell_rejected():
    with pytest.raises(ValueError):
        EVAL.stage(cell("legacy-executable"))
    with pytest.raises(ValueError):
        EVAL.prompt(cell("nope"))


def test_manifest_is_pinned_and_complete():
    m = dataset.manifest()
    assert re.fullmatch(r"[0-9a-f]{40}", m["revision"]) and m["revision"].startswith("4dec557b")
    assert m["repo"] == "ArtificialAnalysis/AA-Briefcase-Lite" and m["license"] == "apache-2.0"
    files = m["files"]
    assert len(files) == 166 and len({f["path"] for f in files}) == 166
    assert sum(f["split"] == "data" for f in files) == 150
    assert all(re.fullmatch(r"[0-9a-f]{64}", f["sha256"]) for f in files)
    priv = {f["path"] for f in files if f["split"] == "private"}
    assert "checks.jsonl" in priv and all(not p.startswith("source_files/") for p in priv)
    assert all(f["split"] == "data" for f in files if f["path"].startswith(("source_files/", "summary_docs/")))
    assert dataset.resolve_url("checks.jsonl").endswith(f"/resolve/{m['revision']}/checks.jsonl")
    assert len(dataset.sources()) == 147


def test_fetch_from_dir_verifies_hashes(tmp_path, monkeypatch):
    """A local copy with one corrupted file: the good file installs, the bad one is refused."""
    import hashlib

    good, bad = b"hello", b"tampered"
    fake = {"schema": "x", "repo": "r/r", "revision": "0" * 40, "files": [
        {"path": "summary_docs/a.md", "split": "data", "size": 5, "sha256": hashlib.sha256(good).hexdigest()},
        {"path": "checks.jsonl", "split": "private", "size": 5, "sha256": hashlib.sha256(good).hexdigest()}]}
    monkeypatch.setattr(dataset, "manifest", lambda: fake)
    monkeypatch.setenv("DBWM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DBWM_PRIVATE_DIR", str(tmp_path / "private"))
    src = tmp_path / "src"
    (src / "summary_docs").mkdir(parents=True)
    (src / "summary_docs" / "a.md").write_bytes(good)
    (src / "checks.jsonl").write_bytes(bad)
    st = dataset.fetch(from_dir=src, quiet=True)
    assert st["counts"]["copied"] == 1 and st["counts"]["failed"] == 1
    assert (tmp_path / "data" / "eval4" / "aa-briefcase-lite" / "summary_docs" / "a.md").read_bytes() == good
    assert not (tmp_path / "private" / "eval4" / "aa-briefcase-lite" / "checks.jsonl").exists()


@pytest.mark.skipif(not (HAVE_DATA and HAVE_PRIVATE), reason="SKIPPED-NO-DATASET: run `dbwm fetch eval4`")
def test_installed_dataset_verifies_and_rubric_facts():
    v = dataset.verify()
    assert v["complete"] and v["ok"] == 166
    rows = [json.loads(x) for x in dataset.read_private("checks.jsonl").splitlines() if x.strip()]
    assert len(rows) == 63
    w = [r for r in rows if r["task_id"] == "w1_t2"]
    assert len(w) == 14 and sum(r["scoring_type"] == "binary" for r in w) == 12
    assert [c["check_id"] for c in dataset.w1t2_checks()] == [f"W1-T2-A-0{i}" for i in range(1, 10)] + \
        [f"W1-T2-C-0{i}" for i in range(1, 4)]


def test_selftest_has_no_failures():
    res = EVAL.selftest()
    fails = [(c.name, c.detail) for c in res if not c.ok and not c.skipped]
    assert not fails, fails
    skipped = [c for c in res if c.skipped]
    assert all(c.detail.startswith("SKIPPED") for c in skipped)
