"""dbwm.core.report: per-arm tables from verdicts."""

from __future__ import annotations

import json

import pytest

from analysis_fixtures.helpers import score, verdict, write_run_verdict
from dbwm.core import report as R
from dbwm.core.attempts import RunStore
from dbwm.core.types import Cell


def _arm(summary, arm, ruleset="v1"):
    sec = next(s for s in summary["sections"] if s["ruleset"] == ruleset)
    return next(a for a in sec["arms"] if a["arm"] == arm)


def _designed(instances=("w1", "w2"), arms=("g", "u"), reps=(0, 1)):
    return [Cell("evalX", i, a, r) for i in instances for a in arms for r in reps]


def test_two_denominators_overclaim_and_exclusions():
    vs = [
        verdict("w1", "g", 0, sc=score(True, claimed=True)),
        verdict("w1", "g", 1, sc=score(False, claimed=True)),  # overclaim
        verdict("w2", "g", 0, attempt=1, outcome="infra_api"),
        verdict("w2", "g", 0, attempt=2, sc=score(False, claimed=False)),  # retried, scored
        # w2 g r1: never attempted -> missing (fail under the designed denominator)
        verdict("w1", "u", 0, sc=score(False, claimed=True)),
        verdict("w1", "u", 1, outcome="crash"),  # unscored only
        verdict("w2", "u", 0, sc=score(True, claimed=True), audit={"flagged": True, "reasons": ["canary"]}),
        verdict("w2", "u", 1, outcome="clock_stop", sc=score(None, present=False, claimed=None, status="missing")),
    ]
    s = R.summarize(vs, designed_cells=_designed())
    g = _arm(s, "g")
    assert g["designed_cells"] == 4 and g["scored_cells"] == 3 and g["unscored_cells"] == 1
    assert g["success_of_scored"]["k"] == 1 and g["success_of_scored"]["n"] == 3
    assert g["success_of_designed"]["k"] == 1 and g["success_of_designed"]["n"] == 4
    assert g["overclaim_of_all"]["k"] == 1 and g["overclaim_of_all"]["n"] == 3
    assert g["overclaim_of_fails"]["k"] == 1 and g["overclaim_of_fails"]["n"] == 2
    assert g["unscored_attempts_by_outcome"] == {"infra_api": 1}
    assert g["exclusions"] == {"missing:no attempt": 1}
    u = _arm(s, "u")
    assert u["excluded_cells"] == 1 and u["exclusions"] == {"audit:canary": 1, "unscored:crash": 1}
    assert u["success_of_scored"]["n"] == 2 and u["success_of_scored"]["k"] == 0
    assert u["success_of_designed"]["n"] == 3  # 4 designed - 1 audit-excluded
    assert u["rails_hit"] == {"clock_stop": 1, "cost_stop": 0}
    lo, hi = g["success_of_scored"]["ci95"]
    assert 0 <= lo <= 1 / 3 <= hi <= 1


def test_first_scored_attempt_is_selected_never_a_later_one():
    vs = [verdict("w1", "g", 0, attempt=1, sc=score(False)), verdict("w1", "g", 0, attempt=2, sc=score(True))]
    cells = R.select_cells(vs)
    assert cells["w1__g__r0"].scored["attempt"] == 1


def test_rulesets_are_separate_sections():
    vs = [verdict("w1", "g", 0, sc=score(True, ruleset="paper")), verdict("w1", "g", 0, sc=score(False))]
    s = R.summarize(vs)
    assert [sec["ruleset"] for sec in s["sections"]] == ["paper", "v1"]
    assert _arm(s, "g", "paper")["success_of_scored"]["k"] == 1
    assert _arm(s, "g", "v1")["success_of_scored"]["k"] == 0


def test_run_dir_layout_invalidations_and_rendering(tmp_path):
    run = tmp_path / "run"
    store = RunStore(run)
    cells = _designed(instances=("w1",), arms=("g",), reps=(0, 1))
    v1 = verdict("w1", "g", 0, attempt=1, sc=score(True))
    v2 = verdict("w1", "g", 0, attempt=2, sc=score(False))
    v3 = verdict("w1", "g", 1, attempt=1, sc=score(True))
    for v in (v1, v2, v3):
        write_run_verdict(run, v)
        store.append({"type": "attempt", "attempt_id": f"{v['cell']['cell_id']}#a{v['attempt']}",
                      "cell_id": v["cell"]["cell_id"], "k": v["attempt"], "outcome": "completed"})
    store.invalidate("w1__g__r0#a1", "operator: stale data")
    store.reset("w1__g__r1", "re-run after fix")
    (run / "manifest.json").write_text(json.dumps({
        "run_id": "r1", "regime": {"model": "haiku", "isolation": "host-unsafe", "ruleset": "v1"},
        "regime_hash": "a" * 64, "isolation": "host-unsafe", "conforming": False,
        "cells": [{"instance": c.instance, "arm": c.arm, "rep": c.rep, "cell_id": c.cell_id} for c in cells]}))
    # An agent-written "verdict.json" among the deliverables must never be read.
    forged = dict(v3, success=True)
    (run / "cells" / "w1__g__r0" / "attempt-2" / "deliverables").mkdir()
    (run / "cells" / "w1__g__r0" / "attempt-2" / "deliverables" / "verdict.json").write_text(json.dumps(forged))

    inval = R.load_invalidated(run)
    assert inval == {("w1__g__r0", 1): "invalidate: operator: stale data", ("w1__g__r1", 1): "reset: re-run after fix"}
    # Same rule as the runner's attempt store.
    for (cid, k) in inval:
        eff = {a["k"]: a["invalidated_by"] for a in store.effective_attempts(cid)}
        assert eff[k] is not None

    s = R.build_report(run)
    assert s["designed_cells_source"] == "manifest" and s["load_errors"] == []
    g = _arm(s, "g")
    assert g["scored_cells"] == 1 and g["success_of_scored"]["k"] == 0  # attempt 2 of r0
    assert g["attempts_invalidated"] == 2
    assert g["exclusions"] == {"missing:every attempt reset or invalidated": 1}
    assert s["conformance"]["conforming"] is False
    jp, mp = R.write_report(run)
    md = mp.read_text()
    assert "NON-CONFORMING" in md and "| g |" in md and "w1__g__r1" in md
    assert json.loads(jp.read_text())["schema"] == R.REPORT_SCHEMA


def test_load_verdicts_rejects_misfiled_duplicate_and_mislabelled(tmp_path):
    run = tmp_path / "run"
    v = verdict("w1", "g", 0, attempt=1)
    write_run_verdict(run, v)
    write_run_verdict(run, v, name="verdict.v1.json")  # duplicate of (cell, attempt, v1)
    write_run_verdict(run, v, name="verdict.paper.json")  # name says paper, content says v1
    misfiled = dict(v)
    misfiled["attempt"] = 2
    d = run / "cells" / "w1__g__r0" / "attempt-3"
    d.mkdir(parents=True)
    (d / "verdict.json").write_text(json.dumps(misfiled))
    (run / "cells" / "w1__g__r0" / "attempt-4").mkdir()
    (run / "cells" / "w1__g__r0" / "attempt-4" / "verdict.json").write_text("{bad")
    got, errors = R.load_verdicts(run)
    assert len(got) == 1
    joined = "\n".join(errors)
    assert "duplicate" in joined and "file name says ruleset 'paper'" in joined
    assert "not the directory" in joined and "not JSON" in joined


def test_tree_without_cells_dir(tmp_path):
    tree = tmp_path / "tree"
    (tree / "a").mkdir(parents=True)
    (tree / "a" / "verdict.json").write_text(json.dumps(verdict("w1", "g")))
    (tree / "a" / "deliverables").mkdir()
    (tree / "a" / "deliverables" / "verdict.json").write_text(json.dumps(verdict("w9", "g")))
    got, errors = R.load_verdicts(tree)
    assert [v["cell"]["instance"] for v in got] == ["w1"] and errors == []


def test_arm_contrast_denominators():
    vs = []
    for i in range(6):
        vs.append(verdict(f"w{i}", "g", 0, sc=score(i % 3 != 0)))
        vs.append(verdict(f"w{i}", "u", 0, sc=score(i % 3 == 0)) if i < 5 else verdict(f"w{i}", "u", 0,
                                                                                      outcome="crash"))
    designed = _designed(instances=tuple(f"w{i}" for i in range(6)), arms=("g", "u"), reps=(0,))
    c1 = R.arm_contrast(vs, "g", "u", ruleset="v1", designed_cells=designed, B=500, seed=1)
    c2 = R.arm_contrast(vs, "g", "u", ruleset="v1", designed_cells=designed, denominator="scored", B=500, seed=1)
    assert c1["n_b"] == 6 and c2["n_b"] == 5
    assert c1["diff"] == pytest.approx(4 / 6 - 2 / 6) and c2["diff"] == pytest.approx(4 / 6 - 2 / 5)
    assert c1 == R.arm_contrast(vs, "g", "u", ruleset="v1", designed_cells=designed, B=500, seed=1)
    pooled = R.arm_contrast(vs, ["g", "u"], "u", ruleset="v1", B=200, seed=2)
    assert pooled["n_a"] == 12  # the crashed u cell counts as a designed fail
    with pytest.raises(ValueError):
        R.arm_contrast(vs, "g", "u", ruleset="v1", denominator="all", seed=1)


def test_designed_cells_parsing():
    assert R._designed_from(["w__1__g__r3"]) == [("w__1__g__r3", "w__1", "g", 3)]
    with pytest.raises(ValueError):
        R._designed_from(["nope"])
    with pytest.raises(TypeError):
        R._designed_from([3])


def test_empty_report_renders():
    s = R.summarize([])
    assert "No verdicts found." in R.render_markdown(s)


def test_eval_report_markdown_shapes(monkeypatch):
    """Every shape the six evals return renders into report.md (or visibly does not)."""
    import sys
    import types

    assert R.eval_report_markdown(None) is None
    assert R.eval_report_markdown("# plain") == "# plain"
    assert R.eval_report_markdown({"markdown": "## e3", "x": 1}) == "## e3"            # evals 3, 4
    assert "skipped: nothing" in R.eval_report_markdown({"skipped": "nothing"})
    assert "error: boom" in R.eval_report_markdown({"error": "boom"})
    e1 = {"rulesets": {"v1": {"summary_md": "S-V1\n"}, "paper": {"note": "no scored cells"}}}  # eval 1
    md = R.eval_report_markdown(e1)
    assert md.index("`paper`") < md.index("`v1`") and "S-V1" in md and "no scored cells" in md
    assert R.eval_report_markdown({"numbers": [1, 2]}) is None

    # evals 2, 5, 6: <eval module>.report.render_markdown(rep)
    pkg = types.ModuleType("dbwm_fake_eval")
    pkg.__path__ = []
    sub = types.ModuleType("dbwm_fake_eval.report")
    sub.render_markdown = lambda rep: f"## fake {rep['k']}"
    monkeypatch.setitem(sys.modules, "dbwm_fake_eval", pkg)
    monkeypatch.setitem(sys.modules, "dbwm_fake_eval.report", sub)
    ev = types.SimpleNamespace(eval_id="fake", eval_module="dbwm_fake_eval")
    assert R.eval_report_markdown({"k": 7}, ev) == "## fake 7"
    sub.render_markdown = lambda rep: 1 / 0
    assert "ZeroDivisionError" in R.eval_report_markdown({"k": 7}, ev)
    # an explicit hook on the EVAL object wins over the module renderer
    ev.report_markdown = lambda rep: "## hook"
    assert R.eval_report_markdown({"k": 7}, ev) == "## hook"


@pytest.mark.parametrize("eval_id", ["eval1", "eval2", "eval3", "eval4", "eval5", "eval6"])
def test_real_eval_report_shapes_render(eval_id):
    """Each shipped eval's report module is reachable by the renderer (shape check, no run needed)."""
    import importlib

    ev = importlib.import_module(f"dbwm.{eval_id}").EVAL
    mod = importlib.import_module(f"dbwm.{eval_id}.report")
    has_renderer = callable(getattr(mod, "render_markdown", None)) or callable(getattr(ev, "report_markdown", None))
    # evals without a module renderer must put markdown into their report dict (eval 1: per-ruleset summary_md)
    assert has_renderer or eval_id == "eval1"


@pytest.mark.parametrize("version,verified,banner", [
    ("2.1.287 (Claude Code)", True, False), ("9.9.9 (Claude Code)", False, True), ("stub", None, False), (None, None, None)])
def test_report_prints_cli_version_verification(tmp_path, version, verified, banner):
    """Review M2: the report says whether the Claude Code version was a verified one."""
    run = tmp_path / "run"
    write_run_verdict(run, verdict("w1", "g", 0, attempt=1, sc=score(True)))
    m = {"run_id": "r1", "regime": {"model": "haiku", "isolation": "host"}, "isolation": "host",
         "conforming": True}
    if version is not None:
        m.update(cli_version=version, cli_version_verified=verified)
    (run / "manifest.json").write_text(json.dumps(m))
    md = R.render_markdown(R.build_report(run))
    if version is None:
        assert "Claude Code:" not in md
    else:
        shown = "n/a (not a Claude Code run)" if verified is None else verified
        assert f"- Claude Code: `{version}`; verified version: `{shown}`" in md
        assert ("Unverified CLI version" in md) is banner
