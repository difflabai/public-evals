"""The EVAL object, arms, regime defaults, cells, the stub agent (direct), and the run report."""

from __future__ import annotations

import dataclasses
import hashlib
import json

import pytest

from dbwm.core.types import EvalDef, Isolation, Outcome, RegimeConfig, ToolPolicy
from dbwm.core.types import Cell
from dbwm.eval2 import ARMS as EVAL2_ARMS
from dbwm.eval3 import ARMS, DEFAULT_ARMS, EVAL, WORLD_IDS
from dbwm.eval3.selftest import run_stub_direct

CELLS = [Cell("eval3", w, a, 0) for w in WORLD_IDS for a in ARMS]


def test_eval_satisfies_the_protocol():
    assert isinstance(EVAL, EvalDef)
    assert EVAL.eval_id == "eval3" and EVAL.instances() == list(WORLD_IDS)
    assert tuple(EVAL.deliverables) == ("allocation.json", "claims.json", "model.json", "*.py")
    assert "reference.json" in EVAL.SCORER_ONLY_NAMES and "world.json" in EVAL.SCORER_ONLY_NAMES


def test_arm_tool_policies_are_exact():
    gf, gb, gc = ARMS["gen-figures"], ARMS["gen-both"], ARMS["gen-code"]
    assert gf.tools.builtin == frozenset({"read", "write"})
    assert [s.name for s in gf.tools.oracle_tools] == ["model_eval"]
    assert gf.oracle_mode == "eval" and gf.min_isolation == Isolation.HOST and gf.tools.transport == "mcp"
    assert gb.tools.builtin == frozenset({"read", "write"})
    assert [s.name for s in gb.tools.oracle_tools] == ["model_eval", "model_test"]
    assert gb.oracle_mode == "test" and gb.min_isolation == Isolation.HOST
    assert gc.tools == ToolPolicy(builtin=frozenset({"read", "write", "bash"}))
    assert gc.oracle_mode is None and gc.min_isolation == Isolation.CONTAINER and gc.tools.needs_bash
    for a in (gf, gb, gc):
        assert a.canonical and a.query_budget is None
    for name in ("ungrounded", "grounded-iterate"):
        assert ARMS[name] == dataclasses.replace(EVAL2_ARMS[name], canonical=False)
    assert set(ARMS) == set(DEFAULT_ARMS)


def test_regime_defaults_and_isolation_rules():
    from dbwm.core import regime as core_regime

    r = EVAL.default_regime()
    assert r["arms"] == list(DEFAULT_ARMS) and r["reps"] == 6 and r["isolation"] == "container"
    assert r["ruleset"] == "v1" and r["instances"] is None and "model" not in r
    reg = core_regime.build_regime(EVAL, {"model": "m", "adapter": "stub"}, validate=False)
    assert reg.arms == list(DEFAULT_ARMS) and reg.isolation == Isolation.CONTAINER
    core_regime.validate_regime(EVAL, reg, check_auth_env=False)
    host = core_regime.build_regime(EVAL, {"model": "m", "adapter": "stub", "isolation": "host"}, validate=False)
    with pytest.raises(core_regime.RegimeError, match="gen-code"):
        core_regime.validate_regime(EVAL, host, check_auth_env=False)
    ok = core_regime.build_regime(
        EVAL,
        {
            "model": "m",
            "adapter": "stub",
            "isolation": "host",
            "arms": "gen-figures,gen-both,ungrounded,grounded-iterate",
        },
        validate=False,
    )
    core_regime.validate_regime(EVAL, ok, check_auth_env=False)


def test_cells_cover_the_design_and_refuse_unknowns():
    reg = RegimeConfig(eval_id="eval3", model="m", arms=list(DEFAULT_ARMS), reps=6)
    cells = EVAL.cells(reg)
    assert len(cells) == 3 * 5 * 6 and len({c.cell_id for c in cells}) == 90
    with pytest.raises(ValueError):
        EVAL.cells(RegimeConfig(eval_id="eval3", model="m", arms=["raw-figures"]))
    with pytest.raises(ValueError):
        EVAL.cells(RegimeConfig(eval_id="eval3", model="m", arms=["gen-both"], instances=["cygnus-holdings"]))


def test_engine_sources_and_hash():
    srcs = EVAL.engine_sources()
    assert all(p.is_file() for p in srcs)
    names = {p.name for p in srcs}
    assert {"score.py", "model_dsl.py", "oracle.py", "task.py", "engine.py", "world.py", "claims.py"} <= names
    assert EVAL.engine_hash() == EVAL.engine_hash() and len(EVAL.engine_hash()) == 64


def test_selftest_all_checks_pass_and_none_skipped():
    checks = EVAL.selftest()
    assert len(checks) >= 8
    assert all(c.ok for c in checks), [(c.name, c.detail) for c in checks if not c.ok]
    skipped = [c for c in checks if c.skipped]
    assert not skipped, [c.name for c in skipped]


def test_duplicate_corpus_starts_with_eval2s():
    from dbwm.eval2 import EVAL as EVAL2

    corpus = EVAL.duplicate_corpus(Cell("eval3", "lyra-partners", "gen-both", 0))
    e2 = EVAL2.duplicate_corpus(Cell("eval2", "lyra-partners", "ungrounded", 0))
    assert e2 and corpus[: len(e2)] == e2


EXPECT = {
    "gen-figures": (True, False),
    "gen-both": (True, False),
    "gen-code": (False, True),
    "grounded-iterate": (True, False),
    "ungrounded": (False, True),
}


@pytest.mark.parametrize("cell", CELLS, ids=[c.cell_id for c in CELLS])
def test_stub_agent_direct_every_arm_and_world(cell, tmp_path):
    """stub_agent into a temp dir, call_oracle wired straight to EVAL.oracle_handlers, then score it."""
    sr, calls = run_stub_direct(cell, "v1", tmp_path / "sb")
    succ, over = EXPECT[cell.arm]
    assert (sr.success, sr.overclaim) == (succ, over)
    assert sr.deliverable_present and sr.claims_parse_status == "ok"
    ops = {c["op"] for c in calls}
    allowed = set(EVAL.oracle_handlers(cell))
    assert ops <= allowed and all(c["ok"] for c in calls)
    if cell.arm in ("gen-figures", "gen-both", "grounded-iterate"):
        assert calls
    if cell.arm in ("gen-figures", "gen-both"):
        assert sr.details["declared_model"] and sr.details["declared_model_valid"]
        assert float(sr.details["model_plan_fidelity_pct_error"]) < 1e-30
        assert ops == ({"model_eval"} if cell.arm == "gen-figures" else {"model_eval", "model_test"})
    if cell.arm == "gen-code":
        assert (tmp_path / "sb" / "model.py").is_file() and not calls
        assert float(sr.details["self_model_fidelity_pct_error"]) > 10  # its churn-blind model is wrong
    paper = EVAL.score(tmp_path / "sb", cell, "paper")
    assert paper.success == succ


def _digest(d):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir()) if p.is_file()}


@pytest.mark.parametrize("arm", ["gen-figures", "gen-both", "gen-code"])
def test_stub_is_deterministic_and_never_a_duplicate(arm, tmp_path):
    from dbwm.core.audit import _deliverable_hashes, canonical_sha256, corpus_hashes

    cell = Cell("eval3", "vega-group", arm, 3)
    run_stub_direct(cell, "v1", tmp_path / "a")
    run_stub_direct(cell, "v1", tmp_path / "b")
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")
    corpus = corpus_hashes(EVAL.duplicate_corpus(cell))
    by_value = corpus_hashes(EVAL.duplicate_corpus(cell), EVAL.canonicalize_deliverable)
    for name in ("allocation.json", "model.json"):
        p = tmp_path / "a" / name
        if p.exists():
            assert canonical_sha256(p) not in corpus
            assert not _deliverable_hashes(p, EVAL.canonicalize_deliverable) & by_value


def test_canonicalize_deliverable_catches_a_retyped_careful_optimum(tmp_path):
    """The careful optimum (numeric strings) re-typed as JSON numbers still matches the corpus."""
    from dbwm.core.audit import _deliverable_hashes, canonical_sha256, corpus_hashes
    from dbwm.eval2 import load_reference

    cell = Cell("eval3", "lyra-partners", "gen-both", 0)
    alloc = load_reference("lyra-partners")["careful_optimum"]["allocation"]
    retyped = {"allocation": {lever: {proj: [float(x) for x in vals] for proj, vals in by.items()}
                              for lever, by in alloc.items()}}
    p = tmp_path / "allocation.json"
    p.write_text(json.dumps(retyped))
    assert canonical_sha256(p) not in corpus_hashes(EVAL.duplicate_corpus(cell))
    assert _deliverable_hashes(p, EVAL.canonicalize_deliverable) & corpus_hashes(
        EVAL.duplicate_corpus(cell), EVAL.canonicalize_deliverable)


def _synthetic_run(tmp_path, arms, reps=1):
    from dbwm.core.verdict import build_verdict, write_verdict

    reg = RegimeConfig(
        eval_id="eval3", model="dbwm-stub", arms=list(arms), reps=reps, adapter="stub", isolation=Isolation.CONTAINER
    )
    cells = EVAL.cells(reg)
    run = tmp_path / "run"
    for cell in cells:
        adir = run / "cells" / cell.cell_id / "attempt-1"
        adir.mkdir(parents=True)
        sb = tmp_path / "sb" / cell.cell_id
        _sr, calls = run_stub_direct(cell, "v1", sb)
        for rs in ("v1", "paper"):
            v = build_verdict(
                cell=cell,
                attempt=1,
                outcome=Outcome.COMPLETED,
                score=EVAL.score(sb, cell, rs),
                regime_hash=reg.regime_hash(),
                engine_hash=EVAL.engine_hash(),
                model_requested="dbwm-stub",
                models_served=["dbwm-stub"],
                oracle_calls=len(calls),
            )
            write_verdict(adir / ("verdict.json" if rs == "v1" else "verdict.paper.json"), v)
    (run / "manifest.json").write_text(json.dumps({"cells": [c.cell_id for c in cells]}))
    return run


def test_report_over_a_synthetic_run_dir(tmp_path):
    run = _synthetic_run(tmp_path, DEFAULT_ARMS)
    rep = EVAL.report(run)
    assert rep["schema"] == "dbwm-eval3-report/1"
    secs = {s["ruleset"]: s for s in rep["eval3"]["sections"]}
    assert set(secs) == {"paper", "v1"}
    v1 = secs["v1"]
    assert len(v1["contrasts"]) == 14  # 7 contrasts x 2 denominators
    by = {(c["label"].split(" (")[0], c["denominator"]): c for c in v1["contrasts"]}
    assert all(c["status"] == "ok" and c["B"] == 2000 and c["seed"] == 20260701 for c in v1["contrasts"])
    assert by[("generative - ungrounded", "designed")]["diff"] == pytest.approx(2 / 3)
    # v7: the registered hypotheses come first (H1 per arm, H2 = grounded-iterate - gen-both), each with exact
    # tests beside the bootstrap; the exact tests count the same cells the bootstrap resamples
    labels = [c["label"] for c in v1["contrasts"][::2]]
    assert labels[:3] == ["gen-figures - ungrounded (H1, registered)", "gen-both - ungrounded (H1, registered)",
                          "grounded-iterate - gen-both (H2, registered)"]
    assert [c["registered"] for c in v1["contrasts"][::2]] == [True, True, True, False, False, False, False]
    for c in v1["contrasts"]:
        assert c["fisher_two_sided"] is not None and 0 <= c["fisher_two_sided"] <= 1
        assert c["k_a"] / c["n_a"] - c["k_b"] / c["n_b"] == pytest.approx(c["diff"])
        lo, hi = c["newcombe95"]
        assert lo <= c["diff"] <= hi
    assert by[("gen-code - gen-both", "scored")]["diff"] == pytest.approx(-1.0)
    assert by[("gen-both - gen-figures", "designed")]["diff"] == pytest.approx(0.0)
    assert v1["per_arm"]["gen-code"]["overclaim_of_all"] == {"k": 3, "n": 3}
    assert v1["per_arm"]["gen-code"]["overclaim_of_fails"] == {"k": 3, "n": 3}
    assert v1["per_arm"]["gen-figures"]["overclaim_of_fails"] == {"k": 0, "n": 0}
    assert v1["generative_pooled"]["overclaim_of_all"] == {"k": 3, "n": 9}
    assert v1["generative_pooled"]["overclaim_of_fails"] == {"k": 3, "n": 3}
    assert v1["per_arm"]["gen-both"]["oracle_calls_median"] == 2
    assert v1["per_arm"]["gen-figures"]["fidelity"]["declared_model"] == {"k": 3, "n": 3}
    md = rep["markdown"]
    assert "overclaim of fails" in md and "tool-form effect" in md and "Ruleset `v1`" in md
    assert "| Fisher p | Newcombe 95% | world-clustered 95% |" in md and "(H2, registered)" in md
    assert json.dumps(EVAL.report(run), sort_keys=True, default=str) == json.dumps(rep, sort_keys=True, default=str)


def test_report_marks_contrasts_whose_arms_were_not_run(tmp_path):
    run = _synthetic_run(tmp_path, ("gen-figures", "gen-both"))
    secs = {s["ruleset"]: s for s in EVAL.report(run)["eval3"]["sections"]}
    st = {(c["label"].split(" (")[0], c["denominator"]): c["status"] for c in secs["v1"]["contrasts"]}
    assert st[("gen-both - gen-figures", "designed")] == "ok"
    assert st[("gen-code - gen-both", "designed")].startswith("not in this run")
    assert st[("generative - ungrounded", "scored")].startswith("not in this run")
    assert st[("grounded-iterate - generative (grounding premium)".split(" (")[0], "designed")].startswith("not in")
    assert st[("grounded-iterate - gen-both", "designed")].startswith("not in this run")
    assert st[("gen-figures - ungrounded", "scored")].startswith("not in this run")
