"""The EVAL object, arms, regime defaults, the stub agent (direct), and the run report."""

from __future__ import annotations

import hashlib
import inspect
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from dbwm.core.types import (
    BUILTIN_TOOLS,
    EvalDef,
    Isolation,
    Outcome,
    RegimeConfig,
    Cell)
from dbwm.eval2 import ARMS, CANONICAL_ARMS, EVAL, WORLD_IDS, WORLD_QUERY_TOOL
from dbwm.eval2 import stub_agent as stub_mod
from dbwm.eval2.selftest import run_stub_direct


def test_eval_satisfies_protocol():
    assert isinstance(EVAL, EvalDef)
    assert EVAL.eval_id == "eval2"
    assert tuple(EVAL.deliverables) == ("allocation.json", "claims.json")
    assert EVAL.instances() == list(WORLD_IDS)
    assert "reference.json" in EVAL.SCORER_ONLY_NAMES and "world.json" in EVAL.SCORER_ONLY_NAMES
    assert len(EVAL.engine_hash()) == 64


def test_arms_exact_tool_policy_and_isolation():
    assert CANONICAL_ARMS == ("ungrounded", "grounded-readonly", "grounded-iterate", "grounded-boolean",
                              "raw-figures", "prose-feedback")
    modes = {"ungrounded": None, "grounded-readonly": "localizing", "grounded-iterate": "localizing",
             "grounded-boolean": "boolean", "raw-figures": "raw", "prose-feedback": "prose",
             "broken-interface": "broken"}
    for name, arm in ARMS.items():
        assert arm.tools.builtin == frozenset({"read", "write"}), name
        assert not arm.tools.needs_bash and arm.tools.transport == "mcp"
        assert arm.min_isolation == Isolation.HOST
        assert arm.oracle_mode == modes[name]
        assert arm.tools.oracle_tools == (() if name == "ungrounded" else (WORLD_QUERY_TOOL,))
        assert arm.canonical == (name != "broken-interface")
        assert arm.query_budget == (3 if name == "grounded-readonly" else None)


def test_default_regime_and_cells():
    d = EVAL.default_regime()
    assert d["eval_id"] == "eval2" and d["arms"] == list(CANONICAL_ARMS)
    assert d["isolation"] == "host" and d["ruleset"] == "v1" and d["reps"] == 8
    assert "model" not in d
    reg = RegimeConfig(eval_id="eval2", model="m", arms=d["arms"], reps=d["reps"])
    cells = EVAL.cells(reg)
    assert len(cells) == 240 and len(set(c.cell_id for c in cells)) == 240
    reg2 = RegimeConfig(eval_id="eval2", model="m", arms=["ungrounded"], instances=["vega-group"], reps=2)
    assert [c.cell_id for c in EVAL.cells(reg2)] == ["vega-group__ungrounded__r0", "vega-group__ungrounded__r1"]
    with pytest.raises(ValueError):
        EVAL.cells(RegimeConfig(eval_id="eval2", model="m", arms=["nope"]))
    with pytest.raises(ValueError):
        EVAL.cells(RegimeConfig(eval_id="eval2", model="m", arms=["ungrounded"], instances=["x"]))


def _run(cell: Cell, root: Path) -> tuple[int, dict, Path]:
    sb = root / cell.cell_id
    sb.mkdir(parents=True)
    for f in EVAL.stage(cell).files:
        shutil.copy(f.src, sb / f.dst)
    calls = run_stub_direct(cell, sb)
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sb.iterdir())}
    return calls, files, sb


@pytest.mark.parametrize("wid", WORLD_IDS)
def test_stub_every_arm_end_to_end_direct(wid, tmp_path):
    for arm in ARMS:
        cell = Cell("eval2", wid, arm, 1)
        calls, files, sb = _run(cell, tmp_path / "a")
        assert {"allocation.json", "claims.json", "brief.json"} <= set(files)
        if ARMS[arm].oracle_mode is None:
            assert calls == 0
        if arm == "grounded-readonly":
            assert calls <= 3
        for rs in ("paper", "v1"):
            r = EVAL.score(sb, cell, rs)
            assert r.deliverable_present and r.success is not None and r.claims_parse_status == "ok"
            if arm == "ungrounded":
                assert r.claimed_success is True and r.success is False and r.overclaim is True
            if arm in ("grounded-iterate", "raw-figures", "grounded-boolean", "prose-feedback"):
                # an honest stub never claims what its channel did not show
                assert not r.overclaim, (cell.cell_id, rs)
        # deterministic: a second run writes identical files and makes the same calls
        calls2, files2, _ = _run(cell, tmp_path / "b")
        assert (calls2, files2) == (calls, files)


def test_stub_never_reads_private_data():
    src = inspect.getsource(stub_mod)
    for forbidden in ("load_reference", "load_world", "private", "reference.json", "world.json", "paths"):
        assert forbidden not in src.replace("never reads private data", ""), forbidden


def test_stub_plans_are_not_template_copies_of_the_answer_key(tmp_path):
    # the audit's duplicate gate must not flag the stub: its capital is whole dollars
    from dbwm.core.audit import _deliverable_hashes, canonical_sha256, corpus_hashes

    for wid in WORLD_IDS:
        cell = Cell("eval2", wid, "grounded-iterate", 0)
        _, _, sb = _run(cell, tmp_path / wid)
        corpus = corpus_hashes(EVAL.duplicate_corpus(cell))
        assert canonical_sha256(sb / "allocation.json") not in corpus
        # nor by value (the eval canonical form the audit adds via canonicalize_deliverable)
        hook = EVAL.canonicalize_deliverable
        assert not _deliverable_hashes(sb / "allocation.json", hook) & corpus_hashes(EVAL.duplicate_corpus(cell),
                                                                                      hook)


def test_duplicate_corpus_catches_a_copy_of_the_careful_optimum(tmp_path):
    from dbwm.core.audit import canonical_sha256, corpus_hashes
    from dbwm.eval2 import load_reference

    corpus = corpus_hashes(EVAL.duplicate_corpus(Cell("eval2", "orion-holdings", "raw-figures", 0)))
    p = tmp_path / "allocation.json"
    p.write_text(json.dumps({"allocation": load_reference("orion-holdings")["careful_optimum"]["allocation"]},
                            indent=4))
    assert canonical_sha256(p) in corpus


def _retype(obj, how):
    """Re-type every number (JSON number or numeric string) in an allocation: as a number
    ("num"), a plain string ("str"), or an exponent-spelled string ("exp")."""
    if isinstance(obj, dict):
        return {k: _retype(v, how) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_retype(v, how) for v in obj]
    if isinstance(obj, str):
        try:
            obj = float(obj) if any(c in obj for c in ".eE") else int(obj)
        except ValueError:
            return obj
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return {"num": obj, "str": str(obj), "exp": f"{Decimal(str(obj)):E}"}[how]
    return obj


@pytest.mark.parametrize("how", ["num", "str", "exp"])
def test_canonicalize_deliverable_catches_retyped_copies(tmp_path, how):
    """numbers re-typed as strings must still match the duplicate corpus."""
    from dbwm.core.audit import _deliverable_hashes, canonical_sha256, corpus_hashes
    from dbwm.eval2 import load_reference

    cell = Cell("eval2", "orion-holdings", "raw-figures", 0)
    hook = EVAL.canonicalize_deliverable
    corpus = corpus_hashes(EVAL.duplicate_corpus(cell), hook)
    co = load_reference("orion-holdings")["careful_optimum"]["allocation"]
    copies = [("careful", {"allocation": co}), ("careful-bare", co)]
    for name, obj in copies:
        d = tmp_path / name
        d.mkdir()
        p = d / "allocation.json"
        new = _retype(obj, how)
        p.write_text(json.dumps(new, indent=1))
        if how != "num" and new != obj:  # the bytes-level gate alone misses a string-typed copy ...
            assert canonical_sha256(p) not in corpus_hashes(EVAL.duplicate_corpus(cell)), (name, how)
        assert _deliverable_hashes(p, hook) & corpus, (name, how)  # ... the value form catches it
    # only allocation.json has a value form; an empty or non-allocation file has none
    other = tmp_path / "claims.json"
    other.write_text(json.dumps({"allocation": co}))
    assert EVAL.canonicalize_deliverable(other) is None
    empty = tmp_path / "empty" / "allocation.json"
    empty.parent.mkdir()
    empty.write_text('{"allocation": {}}')
    assert EVAL.canonicalize_deliverable(empty) is None


def test_report_over_a_synthetic_run_dir(tmp_path):
    from dbwm.core.verdict import build_verdict, write_verdict

    reg = RegimeConfig(eval_id="eval2", model="dbwm-stub", arms=list(CANONICAL_ARMS),
                       instances=["orion-holdings", "vega-group"], reps=1, adapter="stub",
                       isolation=Isolation.HOST)
    cells = EVAL.cells(reg)
    run = tmp_path / "run"
    for cell in cells:
        adir = run / "cells" / cell.cell_id / "attempt-1"
        sb = tmp_path / "sb" / cell.cell_id
        sb.mkdir(parents=True)
        for f in EVAL.stage(cell).files:
            shutil.copy(f.src, sb / f.dst)
        n = run_stub_direct(cell, sb)
        for rs in ("v1", "paper"):
            score = EVAL.score(sb, cell, rs)
            v = build_verdict(cell=cell, attempt=1, outcome=Outcome.COMPLETED, score=score,
                              regime_hash=reg.regime_hash(), engine_hash=EVAL.engine_hash(),
                              model_requested="dbwm-stub", models_served=["dbwm-stub"], oracle_calls=n)
            name = "verdict.json" if rs == "v1" else "verdict.paper.json"
            adir.mkdir(parents=True, exist_ok=True)
            write_verdict(adir / name, v)
    (run / "manifest.json").write_text(json.dumps({"cells": [c.cell_id for c in cells]}))
    rep = EVAL.report(run)
    assert rep["schema"] == "dbwm-eval2-report/1"
    secs = {s["ruleset"]: s for s in rep["eval2"]["sections"]}
    assert set(secs) == {"paper", "v1"}
    v1 = secs["v1"]
    assert set(v1["achievement_by_world"]) == {"orion-holdings", "vega-group"}
    labels = {(c["label"], c["denominator"]) for c in v1["contrasts"]}
    assert len(labels) == 6
    for c in v1["contrasts"]:
        assert c["n_a"] > 0 and c["n_b"] > 0 and c["B"] == 2000 and c["seed"] == 20260911
        # v7 Appendix C: exact tests on the same cells the bootstrap resamples
        assert c["k_a"] / c["n_a"] - c["k_b"] / c["n_b"] == pytest.approx(c["diff"])
        assert 0 <= c["fisher_two_sided"] <= 1 and c["newcombe95"][0] <= c["diff"] <= c["newcombe95"][1]
    assert v1["overclaim_by_arm"]["ungrounded"]["overclaim_all"] == "2/2"
    rep2 = EVAL.report(run)
    assert json.dumps(rep2, sort_keys=True, default=str) == json.dumps(rep, sort_keys=True, default=str)
    from dbwm.eval2.report import render_markdown

    md = render_markdown(rep)
    assert "any-grounded" in md and "Ruleset `v1`" in md and "| Fisher p | Newcombe 95% |" in md


def test_selftest_all_ok_no_skips():
    res = EVAL.selftest()
    assert res and all(r.ok for r in res), [r for r in res if not r.ok]
    skipped = [r for r in res if r.skipped]
    assert not skipped, skipped


def test_engine_sources_are_the_scorer_and_match_the_runner_hash():
    names = sorted(p.name for p in EVAL.engine_sources())
    assert names == ["claims.py", "engine.py", "score.py", "world.py", "world_query.py"]
    from dbwm.core.runner import compute_engine_hash

    assert compute_engine_hash(EVAL) == EVAL.engine_hash()
