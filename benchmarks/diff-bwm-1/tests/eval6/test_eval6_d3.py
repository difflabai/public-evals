"""Eval 6 D3 (dispersion pilot): the brief port, the scorer and the non-canonical runnable arm
(stub agent only; no LLM, no network, no Docker)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from dbwm.core.report import CellRecord
from dbwm.core.runner import RunOptions, run_eval
from dbwm.core.types import RULESET_PAPER, RULESET_V1, Cell, EvalDef, Isolation
from dbwm.eval6 import EVAL as EVAL6
from dbwm.eval6 import SCORER_SOURCES as E6_SCORER_SOURCES
from dbwm.eval6 import _paths
from dbwm.eval6.arms import D1_BUILTINS
from dbwm.eval6.d3 import EVAL, SCREENED_WORLDS, EvalD3
from dbwm.eval6.d3 import brief as B
from dbwm.eval6.d3 import report as R
from dbwm.eval6.d3 import score as S

from .conftest import PKG_ROOT, WORLDS

# sha256 of the paper (pilot) brief. The port was checked equal to the pilot's own
# d3_planner.build_d3_task_md on all 11 worlds when it was written; these pins keep the port
# from drifting.
PAPER_BRIEF_SHA = {"w1": "37a9e653ec667c71d00cca9942ca63f9efa952448a42e50df8bb3cc1c175feae",
                   "w7": "d0d13dc75bfa2788920ec647ff02a472ec7a0d9a21458a417083eaf1568c1734"}


def theta(w):
    return json.loads(_paths.answer_key(w, "theta.json").read_text())


def d3cell(w="w1", rep=0):
    return Cell(EVAL.eval_id, w, "self-supply", rep)


def _floats(obj):
    if isinstance(obj, float):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _floats(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _floats(v)


# -- contract ----------------------------------------------------------------------------------

def test_contract_and_regime():
    assert isinstance(EVAL, EvalDef) and EVAL.eval_id == "eval6-d3" and EVAL.eval_module == "dbwm.eval6.d3"
    assert set(EVAL.arms) == {"self-supply"}
    arm = EVAL.arms["self-supply"]
    assert arm.canonical is False and arm.min_isolation == Isolation.CONTAINER
    assert arm.tools.builtin == D1_BUILTINS and arm.tools.oracle_tools == () and arm.oracle_mode is None
    assert tuple(EVAL.deliverables) == ("plan.json", "claims.json") and EVAL.rulesets == (RULESET_PAPER, RULESET_V1)
    d = EVAL.default_regime()
    assert d["arms"] == ["self-supply"] and d["instances"] == list(SCREENED_WORLDS) and d["reps"] == 5
    assert d["max_turns"] == 60 and d["wall_clock_s"] == 2400 and d["usd_rail"] is None
    assert d["isolation"] == "container" and d["ruleset"] == "v1" and "model" not in d
    assert EVAL.instances() == list(WORLDS)


def test_not_in_eval6_scorer_sources():
    """D3 must not move eval 6 D1's engine hash."""
    assert not any("d3" in s for s in E6_SCORER_SOURCES)
    assert not any("d3" in p.parts for p in EVAL6.engine_sources())
    names = {p.name for p in EVAL.engine_sources()}
    assert {"score.py", "brief.py", "latent_engine.py", "score_d1.py", "claims.py"} <= names
    assert all(p.is_file() for p in EVAL.engine_sources())


def test_cells_default_to_the_screened_worlds():
    from dbwm.core.regime import build_regime
    reg = build_regime(EVAL, {"model": "m", "adapter": "stub", "auth_mode": "none"})
    cells = EVAL.cells(reg)
    assert len(cells) == 40 and {c.instance for c in cells} == set(SCREENED_WORLDS)
    assert {c.arm for c in cells} == {"self-supply"}
    with pytest.raises(ValueError):
        EVAL.cells(build_regime(EVAL, {"model": "m", "instances": ["w8"]}, validate=False))


# -- brief and staging -------------------------------------------------------------------------

@pytest.mark.parametrize("world", WORLDS)
def test_staging_is_the_unaided_manifest_plus_the_d3_brief(world):
    m = EVAL.stage(d3cell(world))
    u = EVAL6.stage(Cell("eval6", world, "unaided", 0))
    assert [(f.dst, f.src) for f in m.files] == [(f.dst, f.src) for f in u.files]
    assert {f.dst for f in m.files} == {"public/history.csv", "public/task.json"}
    assert m.task_md == EVAL.prompt(d3cell(world)) == B.task_md(theta(world), B.BRIEF_V1)
    assert m.writable_dirs == u.writable_dirs


@pytest.mark.parametrize("world", WORLDS)
def test_brief_discloses_rounded_values_and_no_full_precision_theta_value(world):
    th = theta(world)
    text = EVAL.prompt(d3cell(world))
    for v in _floats(th):
        if repr(v) != repr(round(v, 4)) and len(repr(v)) >= 7:
            assert repr(v) not in text, (world, v)
    for v in EVAL.answer_key_values(d3cell(world)):
        assert v not in text
    disclosed = EVAL.disclosed_values_for(d3cell(world))
    probe, partner = B.detect_probe_pair(th)
    assert probe in disclosed and partner in disclosed
    for s in th["segment_order"]:
        assert json.dumps(round(th["segments"][s]["gm"], 4)) in disclosed
        assert json.dumps(round(th["segments"][s]["gm"], 4)) in text
        assert json.dumps(round(th["segments"][s]["fixed"], 2)) in text
    assert "ANSWER_KEY" not in text and "private/" not in text


def test_paper_brief_pinned_and_v1_changes_two_passages():
    for w, sha in PAPER_BRIEF_SHA.items():
        assert hashlib.sha256(B.task_md(theta(w), B.BRIEF_PAPER).encode()).hexdigest() == sha
    th = theta("w1")
    paper, v1 = B.task_md(th, B.BRIEF_PAPER), B.task_md(th, B.BRIEF_V1)
    probe, partner = B.detect_probe_pair(th)
    assert f"from {partner} into {probe}'s latent demand" in paper
    assert f"{partner}'s marketing multiplies {probe}'s\nmarketing-to-signups response" in v1
    assert "into core's latent demand" not in v1 and "capped at its maximum in history.csv" in v1
    assert paper.replace(B.PAPER_COUPLING_SENTENCE.format(probe=probe, partner=partner),
                         B.V1_COUPLING_SENTENCE.format(probe=probe, partner=partner)
                         ).replace(B.PAPER_PLAN_ITEM, B.V1_PLAN_ITEM) == v1
    with pytest.raises(ValueError):
        B.task_md(th, "v2")


# -- references ---------------------------------------------------------------------------------

def test_paper_and_v1_references_differ_by_the_e7_defect():
    """The E.7 defect: revenue and the spillover differ between the two reference sets."""
    p, v = S.references("w1", ruleset=RULESET_PAPER), S.references("w1", ruleset=RULESET_V1)
    assert p["pinned_plan_total_profit_usd"] == v["pinned_plan_total_profit_usd"]
    assert p["pinned_plan_total_revenue_usd"] != v["pinned_plan_total_revenue_usd"]
    assert p["cross_segment_spillover_pct"] != v["cross_segment_spillover_pct"]
    with pytest.raises(ValueError):
        S.references("w1", ruleset="as-run")


# -- gate labelling -------------------------------------------------------------------------------

def _runs(n, base):
    return [{"figures": {k: v * (1 + 0.001 * i) for k, v in base.items()}} for i in range(n)]


def test_world_clustered_interval():
    """A fixed per-world agreement dict (6 figures x 8 worlds): the interval is a pair of attainable
    rates, the same for any dict order and on every call (a fresh generator per readout)."""
    per_world = {"w1": 5, "w2": 0, "w3": 3, "w4": 6, "w5": 2, "w6": 1, "w9": 4, "w10": 3}
    assert S.world_clustered_ci(per_world, 6) == [14 / 48, 34 / 48]
    assert S.world_clustered_ci(dict(reversed(list(per_world.items()))), 6) == [14 / 48, 34 / 48]
    assert S.world_clustered_ci(per_world, 6, B=200) == [11 / 48, 34 / 48]
    assert S.world_clustered_ci(per_world, 6, B=200, seed=1) == [14 / 48, 34 / 48]
    assert S.world_clustered_ci({}, 6) == [None, None] and S.world_clustered_ci(per_world, 0) == [None, None]
    p, wlo, whi = S.wilson(13, 30)
    assert p == 13 / 30 and (round(wlo, 4), round(whi, 4)) == (0.2738, 0.6080)


def test_gate_refuses_agree5_below_five_reports():
    ref1 = S.references("w1")
    g5 = S.gate({"w1": _runs(5, ref1)}, {"w1": ref1}, ci=False)
    assert g5["label"] == "agree-5" and g5["per_world"]["w1"]["label"] == "agree-5"
    g3 = S.gate({"w1": _runs(3, ref1), "w2": _runs(5, S.references("w2"))},
                {"w1": ref1, "w2": S.references("w2")}, ci=False)
    assert g3["label"].startswith("agree-k") and "w1" in g3["label"] and "w2" not in g3["label"]
    assert g3["per_world"]["w1"]["label"] == "agree-3" and g3["per_world"]["w2"]["label"] == "agree-5"
    one = S.gate({"w1": _runs(1, ref1)}, {"w1": ref1}, ci=False)
    assert one["cells"] == 0 and all(f is None for f in one["per_world"]["w1"]["figures"].values())


def test_report_counts_rails_and_missing_figures_as_missing_never_zero():
    figs = S.references("w1")
    none = {k: None for k in figs}

    def rec(rep, outcome, figures, status="scored"):
        r = CellRecord(f"w1__self-supply__r{rep}", "w1", "self-supply", rep)
        r.status = status
        r.scored = {"outcome": outcome, "details": {"figures": figures, "references": figs}}
        return r
    cells = {r.cell_id: r for r in (rec(0, "completed", figs), rec(1, "cost_stop", none),
                                    rec(2, "clock_stop", figs), rec(3, "completed", none))}
    runs, refs, attrition = R._sessions_by_world(cells)
    assert len(runs["w1"]) == 2 and refs["w1"] == figs
    assert attrition["w1"] == Counter({"designed": 4, "reported": 2, "no_figures": 2, "rail:cost_stop": 1,
                                       "rail:clock_stop": 1})


# -- per-attempt score ------------------------------------------------------------------------------

def test_score_attempt_strict_numbers_and_missing(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    figs = {n: 1.0 for n in B.FIGURE_NAMES}
    figs["pinned_plan_total_profit_usd"] = "5M"                       # a unit: not a strict number
    del figs["cross_segment_spillover_pct"]
    figs.update({"confidence": {"pinned_plan_total_revenue_usd": 0.4}, "first_action": "look", "rationale": "x"})
    (d / "claims.json").write_text(json.dumps(figs))
    r = EVAL.score(d, d3cell("w1"), RULESET_V1)
    det = r.details
    assert r.success is None and r.overclaim is None and r.deliverable_present
    assert set(det["missing_figures"]) == {"pinned_plan_total_profit_usd", "cross_segment_spillover_pct"}
    assert det["figures"]["pinned_plan_total_profit_usd"] is None and det["complete_report"] is False
    assert det["first_action"] == "look" and det["confidence"] == {"pinned_plan_total_revenue_usd": 0.4}
    assert det["plan"]["no_plan"] is True and det["reference_kind"].startswith("engine-consistent")
    with pytest.raises(ValueError):
        EVAL.score(d, d3cell("w1"), "as-run")


# -- the runnable arm, stub agent ---------------------------------------------------------------------

class _SilentRep(EvalD3):
    """rep 4 writes nothing (a session that never reported)."""

    def stub_agent(self, cell, sandbox, call_oracle):
        if cell.rep == 4:
            (Path(sandbox) / "notes.txt").write_text("ran out\n")
            return
        super().stub_agent(cell, sandbox, call_oracle)


def test_stub_run_scores_and_gate_reports_n_per_world(tmp_path, monkeypatch):
    monkeypatch.setenv("DBWM_SANDBOX_ROOT", str(tmp_path / "sb"))
    (tmp_path / "sb").mkdir()
    ev = _SilentRep()
    res = run_eval(ev, {"adapter": "stub", "instances": ["w1", "w2"], "reps": 5, "model": "dbwm-stub",
                        "auth_mode": "none"},
                   RunOptions(run_id="d3", runs_root=tmp_path / "runs", install_signal_handlers=False,
                              preflight=False, log=lambda m: None))
    assert res.cells_scored == 10
    run = tmp_path / "runs" / "d3"
    vs = [json.loads(p.read_text()) for p in sorted(run.glob("cells/*/attempt-*/verdict.json"))]
    assert len(vs) == 10 and all(v["scored"] and v["eval"] == "eval6-d3" for v in vs)
    for v in vs:
        assert v["audit"]["flagged"] is False
        complete = v["cell"]["rep"] != 4
        assert v["details"]["complete_report"] is complete
        # D3 defines no per-attempt success; the core coerces None to False only when no deliverable exists
        assert v["success"] is (None if complete else False)
    rep = ev.report(run)
    sec = rep["eval6_d3"][0]
    assert sec["ruleset"] == "v1" and sec["attrition"]["w1"]["reported"] == 4
    assert sec["attrition"]["w1"]["no_figures"] == 1
    assert sec["per_world"]["w1"]["n_reports"] == 4 and sec["per_world"]["w1"]["label"] == "agree-4"
    assert sec["gate"]["label"].startswith("agree-k") and sec["gate"]["cells"] == 12
    md = (run / "eval6_d3_report.md").read_text()
    assert "agree-k" in md and "| w1 | 4 | agree-4 |" in md and "pilot, gate only" in md
    assert not list((tmp_path / "sb").iterdir())


def test_cli_stub_run_and_report(tmp_path):
    env = dict(os.environ, DBWM_SANDBOX_ROOT=str(tmp_path / "sb"))
    (tmp_path / "sb").mkdir()
    runs = tmp_path / "runs"
    r = subprocess.run([sys.executable, "-m", "dbwm", "run", "--eval", "dbwm.eval6.d3", "--adapter", "stub",
                        "--instances", "w1", "--reps", "5", "--run-id", "stub-d3", "--runs-dir", str(runs)],
                       cwd=str(PKG_ROOT), env=env, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr[-3000:]
    rep = subprocess.run([sys.executable, "-m", "dbwm", "report", "--run", str(runs / "stub-d3")],
                         cwd=str(PKG_ROOT), env=env, capture_output=True, text=True, timeout=300)
    assert rep.returncode == 0, rep.stderr[-2000:]
    assert "# Eval 6 D3 dispersion gate report" in rep.stdout and "**Gate (agree-5):**" in rep.stdout
    assert "| w1 | 5 | agree-5 |" in rep.stdout
    sc = subprocess.run([sys.executable, "-m", "dbwm", "score", "--run", str(runs / "stub-d3"), "--ruleset", "paper"],
                        cwd=str(PKG_ROOT), env=env, capture_output=True, text=True, timeout=300)
    assert sc.returncode == 0, sc.stderr[-2000:]
    v = json.loads(next((runs / "stub-d3").glob("cells/*/attempt-1/verdict.paper.json")).read_text())
    assert v["details"]["reference_kind"].startswith("pilot")
    bad = subprocess.run([sys.executable, "-m", "dbwm", "score", "--run", str(runs / "stub-d3"), "--ruleset",
                          "as-run"], cwd=str(PKG_ROOT), env=env, capture_output=True, text=True, timeout=300)
    assert bad.returncode == 2 and "does not define ruleset 'as-run'" in bad.stderr


def test_selftest():
    cs = EVAL.selftest()
    assert cs and all(c.ok for c in cs), [(c.name, c.detail) for c in cs if not c.ok]
    skipped = [c for c in cs if c.skipped]
    assert not skipped
