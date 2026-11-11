"""Core contract additions for paper v7 (lane C-0): the "as-run" ruleset and per-eval ``rulesets``,
``ArmSpec.disclosed_values`` in the sandbox pre-check and the audit."""

from __future__ import annotations

import dataclasses
import json

import pytest

from analysis_fixtures.helpers import ToyEval as AuditToyEval
from analysis_fixtures.helpers import Transcript
from dbwm.core import audit as A
from dbwm.core import canary, sandbox
from dbwm.core import regime as regime_mod
from dbwm.core.types import (
    DEFAULT_EVAL_RULESETS,
    RULESET_AS_RUN,
    RULESET_PAPER,
    RULESET_V1,
    RULESETS,
    ArmSpec,
    Cell,
    EvalDef,
    Isolation,
    StagedFile,
    StagingManifest,
    ToolPolicy,
    disclosed_values_for,
    eval_label,
    eval_rulesets,
    ruleset_problem,
)

RW = ToolPolicy(frozenset({"read", "write"}))
ANSWER = "73.1415"


# -- rulesets ------------------------------------------------------------------------------


def test_ruleset_constants():
    assert RULESETS == ("paper", "v1", "as-run") and RULESET_AS_RUN == "as-run"
    assert DEFAULT_EVAL_RULESETS == (RULESET_PAPER, RULESET_V1)


def test_eval_rulesets_default_declared_and_invalid(toy):
    assert eval_rulesets(toy) == DEFAULT_EVAL_RULESETS
    toy.rulesets = (RULESET_PAPER, RULESET_V1, RULESET_AS_RUN)
    assert eval_rulesets(toy) == RULESETS
    for bad in ((), ("paper", "v9"), "paper"):
        toy.rulesets = bad
        with pytest.raises(ValueError):
            eval_rulesets(toy)


def test_ruleset_problem_messages(toy):
    assert ruleset_problem(toy, "v1") is None and ruleset_problem(toy, "paper") is None
    assert ruleset_problem(toy, "v9") == "unknown ruleset 'v9' (choose paper, v1, as-run)"
    assert ruleset_problem(toy, None).startswith("unknown ruleset None")
    assert ruleset_problem(toy, "as-run") == "toy does not define ruleset 'as-run' (its rulesets: paper, v1)"

    class Ev:
        eval_id = "eval2"

    assert eval_label(Ev()) == "eval 2"
    assert ruleset_problem(Ev(), "as-run") == "eval 2 does not define ruleset 'as-run' (its rulesets: paper, v1)"
    Ev.eval_id = "eval6-d3"
    assert eval_label(Ev()) == "eval6-d3"


def test_validate_regime_refuses_an_undefined_ruleset(toy):
    with pytest.raises(regime_mod.RegimeError, match="toy does not define ruleset 'as-run'"):
        regime_mod.build_regime(toy, {"model": "m", "adapter": "stub", "ruleset": "as-run"})
    toy.rulesets = RULESETS
    r = regime_mod.build_regime(toy, {"model": "m", "adapter": "stub", "ruleset": "as-run"})
    assert r.ruleset == "as-run"
    toy.rulesets = ("paper", "nope")
    with pytest.raises(regime_mod.RegimeError, match="must be a non-empty subset"):
        regime_mod.build_regime(toy, {"model": "m", "adapter": "stub"})


def test_shipped_evals_declare_valid_rulesets():
    from dbwm.core.regime import discover_evals

    for name, ev in discover_evals().items():
        if isinstance(ev, Exception):
            continue
        assert isinstance(ev, EvalDef), name
        rs = eval_rulesets(ev)
        assert set(rs) <= set(RULESETS) and RULESET_V1 in rs, (name, rs)


@pytest.mark.parametrize("name", ["eval1", "eval2", "eval3"])
def test_evals_without_as_run_refuse_it_in_score(name, tmp_path):
    import importlib

    ev = importlib.import_module(f"dbwm.{name}").EVAL
    assert RULESET_AS_RUN not in eval_rulesets(ev)
    inst = ev.instances()[0]
    arm = next(iter(ev.arms))
    with pytest.raises(ValueError, match="unknown ruleset 'as-run'"):
        ev.score(tmp_path, Cell(ev.eval_id, inst, arm, 0), RULESET_AS_RUN)


def test_cli_refuses_undefined_ruleset(iso_dirs, toy, capsys):
    from dbwm import cli
    from dbwm.core.runner import RunOptions, Runner

    regime = regime_mod.build_regime(toy, {"model": "dbwm-stub", "adapter": "stub", "auth_mode": "none"})
    res = Runner(toy, regime, RunOptions(run_id="r1", runs_root=iso_dirs[1], install_signal_handlers=False,
                                         log=lambda m: None)).run()
    assert res.status == "complete"
    capsys.readouterr()
    assert cli.main(["score", "--run", str(res.run_dir), "--ruleset", "as-run"]) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "dbwm score: toy does not define ruleset 'as-run' (its rulesets: paper, v1)" in err
    assert not list(res.run_dir.glob("cells/*/attempt-*/verdict.as-run.json"))
    # dbwm run refuses it before planning anything (eval 2 has no as-run ruleset)
    rc = cli.main(["run", "--eval", "2", "--adapter", "stub", "--ruleset", "as-run", "--dry-run"])
    assert rc == cli.EXIT_USAGE
    assert "eval 2 does not define ruleset 'as-run'" in capsys.readouterr().err


def test_cli_scores_as_run_when_the_eval_defines_it(iso_dirs, toy, monkeypatch):
    from dbwm import cli
    from dbwm.core.runner import RunOptions, Runner
    from dbwm.core.verdict import read_verdict, validate_verdict

    regime = regime_mod.build_regime(toy, {"model": "dbwm-stub", "adapter": "stub", "auth_mode": "none"})
    res = Runner(toy, regime, RunOptions(run_id="r2", runs_root=iso_dirs[1], install_signal_handlers=False,
                                         log=lambda m: None)).run()
    toy.rulesets = RULESETS
    monkeypatch.setattr(cli, "resolve_eval", lambda name: toy)
    assert cli.main(["score", "--run", str(res.run_dir), "--ruleset", "as-run"]) == cli.EXIT_OK
    vs = sorted(res.run_dir.glob("cells/*/attempt-1/verdict.as-run.json"))
    assert vs and all(read_verdict(p)["ruleset"] == "as-run" for p in vs)
    assert validate_verdict(read_verdict(vs[0])) == []


# -- disclosed values ------------------------------------------------------------------------


def test_armspec_disclosed_values_validation():
    a = ArmSpec("a", "d", RW, disclosed_values=("0.3512", "1250.00"))
    assert a.disclosed_values == ("0.3512", "1250.00")
    assert ArmSpec("b", "d", RW).disclosed_values == ()
    for bad in (["0.3512"], "0.3512", ("",), (1.5,)):
        with pytest.raises(ValueError, match="disclosed_values"):
            ArmSpec("c", "d", RW, disclosed_values=bad)


def test_disclosed_values_for_unions_arm_and_hook(toy):
    cell = Cell("toy", "alpha", "no-tool", 0)
    assert disclosed_values_for(toy, cell) == []
    toy_arms = dict(toy.arms)
    toy_arms["no-tool"] = dataclasses.replace(toy.arms["no-tool"], disclosed_values=("gm=0.3512", "fixed=1250.00"))
    toy.arms = toy_arms
    assert disclosed_values_for(toy, cell) == ["gm=0.3512", "fixed=1250.00"]
    toy.disclosed_values_for = lambda c: ["fixed=1250.00", f"x={c.instance}-only"]
    assert disclosed_values_for(toy, cell) == ["gm=0.3512", "fixed=1250.00", "x=alpha-only"]
    toy.disclosed_values_for = lambda c: None
    assert disclosed_values_for(toy, cell) == ["gm=0.3512", "fixed=1250.00"]
    toy.disclosed_values_for = lambda c: "gm=0.3512"
    with pytest.raises(ValueError, match="sequence of strings"):
        disclosed_values_for(toy, cell)
    toy.disclosed_values_for = lambda c: [None]
    with pytest.raises(ValueError, match="non-empty string"):
        disclosed_values_for(toy, cell)


@pytest.mark.parametrize("hay,disclosed,want", [
    ("y = 73.1415", [], True),
    ("y = 73.1415", ["73.1415"], False),                       # disclosed exactly
    ("rate 173.14159 disclosed", ["173.14159"], False),         # only inside a longer disclosed string
    ("rate 173.14159 and y = 73.1415", ["173.14159"], True),   # one occurrence outside it
    ("y = 73.1415", ["73.14"], True),                           # disclosed substring of the value excuses nothing
    ("y = 73.1415 x 73.1415", ["y = 73.1415"], True),
    ("y = 73.1415 then y = 73.1415", ["y = 73.1415"], False),
    ("no value here", ["73.1415"], False),
])
def test_occurs_undisclosed(hay, disclosed, want):
    cov = canary.disclosure_covers(ANSWER, disclosed)
    assert canary.occurs_undisclosed(hay, ANSWER, cov) is want
    covb = canary.disclosure_covers(ANSWER.encode(), [d.encode() for d in disclosed])
    assert canary.occurs_undisclosed(hay.encode(), ANSWER.encode(), covb) is want
    assert (canary.scan_text_for_values(hay, [ANSWER], disclosed=disclosed) == [ANSWER]) is want


def _manifest(task, files=()):
    return StagingManifest(files=[StagedFile(dst="input.json", content=b'{"x": 10}'), *files], task_md=task,
                           writable_dirs=["scratch"])


def test_precheck_exempts_disclosed_values_only_where_disclosed(iso_dirs):
    cell = Cell("toy", "alpha", "no-tool", 0)
    task = f"Gross margin {ANSWER}0 per unit (disclosed).\n"
    disclosed = [f"{ANSWER}0"]
    h = sandbox.build_sandbox(_manifest(task), cell, RW)
    try:
        assert any("answer-key value" in p for p in sandbox.precheck(h, answer_values=[ANSWER]))
        assert sandbox.precheck(h, answer_values=[ANSWER], disclosed_values=disclosed) == []
        probs = sandbox.precheck(h, answer_values=[ANSWER], disclosed_values=disclosed,
                                 prompt=f"the answer is {ANSWER}")
        assert probs == [f"prompt contains answer-key value {ANSWER!r}"]
    finally:
        sandbox.cleanup(h)
    leak = StagedFile(dst="data.txt", content=f"margin {ANSWER}0; y = {ANSWER}".encode())
    h = sandbox.build_sandbox(_manifest(task, [leak]), cell, RW)
    try:
        probs = sandbox.precheck(h, answer_values=[ANSWER], disclosed_values=disclosed)
        assert probs == [f"answer-key value {ANSWER!r} found in data.txt"]
    finally:
        sandbox.cleanup(h)


def test_runner_passes_disclosed_values_to_precheck_and_records_them(iso_dirs, toy):
    from dbwm.core.runner import RunOptions, Runner

    y = str(toy_true_y("alpha"))
    toy.extra_task_text = f"Disclosed calibration: y_ref={y}.\n"
    regime = regime_mod.build_regime(toy, {"model": "dbwm-stub", "adapter": "stub", "auth_mode": "none",
                                          "instances": ["alpha"], "arms": ["no-tool"],
                                          "max_attempts_per_cell": 1})
    opts = dict(runs_root=iso_dirs[1], install_signal_handlers=False, log=lambda m: None)
    res = Runner(toy, regime, RunOptions(run_id="leaky", **opts)).run()
    sb = json.loads(next(res.run_dir.glob("cells/*/attempt-1/sandbox.json")).read_text())
    assert any("answer-key value" in p for p in sb["precheck"]) and "disclosed_values" not in sb
    toy.disclosed_values_for = lambda c: [f"y_ref={y}"]
    res = Runner(toy, regime, RunOptions(run_id="disclosed", **opts)).run()
    adir = next(res.run_dir.glob("cells/*/attempt-1"))
    sb = json.loads((adir / "sandbox.json").read_text())
    assert sb["precheck"] == [] and sb["disclosed_values"] == [f"y_ref={y}"]
    assert res.cells_scored == 1


def toy_true_y(instance):
    import toy_eval

    return toy_eval.true_y(instance)


def test_audit_notes_disclosed_values_instead_of_flagging(tmp_path):
    ev = AuditToyEval()
    cell = Cell("evalX", "w1", "nobash", 0)

    def attempt(sub, text):
        adir = tmp_path / sub / "cells" / cell.cell_id / "attempt-1"
        adir.mkdir(parents=True)
        sb = tmp_path / sub / "sandbox"
        sb.mkdir()
        t = Transcript(adir / "transcript.jsonl", tools=["Read", "Write", "mcp__dbwm__run_model"], cwd=str(sb))
        t.text(text)
        t.result()
        t.write()
        return adir

    def flagged(adir, **kw):
        a = A.audit_attempt(adir, ev, cell, isolation=Isolation.HOST, model_pin="haiku", **kw)
        return A.F_ANSWER_KEY in a["reasons"], a

    adir = attempt("a", "the brief says seed=SECRET-SEED-12345")
    assert flagged(adir)[0]
    hit, a = flagged(adir, disclosed_values=["seed=SECRET-SEED-12345"])
    assert not hit and "exempted" in a["checks"]["disclosed_values"]
    adir = attempt("b", "the brief says seed=SECRET-SEED-12345, so SECRET-SEED-12345 it is")
    assert flagged(adir, disclosed_values=["seed=SECRET-SEED-12345"])[0]
    # looked up through the eval when not passed: the per-cell hook
    ev.disclosed_values_for = lambda c: ["seed=SECRET-SEED-12345"]
    assert not flagged(attempt("c", "the brief says seed=SECRET-SEED-12345"))[0]
