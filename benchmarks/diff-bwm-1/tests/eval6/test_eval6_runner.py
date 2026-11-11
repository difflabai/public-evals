"""Stub agents through the real runner (host-unsafe is acceptable for a stub; recorded non-conforming).

Wave-2 core tests extend this to containers. Here: every arm on two worlds via the CLI, then
an in-process run in which one cell commits nothing, to check that the report counts the
no-plan trial and shows both no-plan policies."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from dbwm.core.runner import RunOptions, run_eval
from dbwm.core.types import Isolation
from dbwm.eval6 import Eval6

from .conftest import PKG_ROOT


def test_stub_run_through_cli(tmp_path):
    env = dict(os.environ, DBWM_SANDBOX_ROOT=str(tmp_path / "sb"))
    (tmp_path / "sb").mkdir()
    runs = tmp_path / "runs"
    r = subprocess.run([sys.executable, "-m", "dbwm", "run", "--eval", "6", "--adapter", "stub",
                        "--isolation", "host-unsafe", "--instances", "w1,w7", "--reps", "1", "--run-id", "smoke",
                        "--runs-dir", str(runs), "--model", "dbwm-stub"], cwd=str(PKG_ROOT), env=env,
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr[-3000:]
    run = runs / "smoke"
    verdicts = [json.loads(p.read_text()) for p in sorted(run.glob("cells/*/attempt-*/verdict.json"))]
    assert len(verdicts) == 4 and all(v["scored"] and v["ruleset"] == "v1" and v["eval"] == "eval6" for v in verdicts)
    for v in verdicts:
        d = v["details"]
        assert d["no_plan"] is False and d["true_profit"] > 0 and v["deliverable_present"]
    for p in run.glob("cells/*/attempt-*/audit.json"):
        assert json.loads(p.read_text()).get("flagged") is False, p
    assert not list(tmp_path.joinpath("sb").iterdir())          # sandboxes removed
    rep = subprocess.run([sys.executable, "-m", "dbwm", "report", "--run", str(run)], cwd=str(PKG_ROOT), env=env,
                         capture_output=True, text=True, timeout=300)
    assert rep.returncode == 0, rep.stderr[-2000:]
    e6 = json.loads((run / "eval6_report.json").read_text())
    sec = e6["eval6"][0]
    assert sec["arms"]["unaided"]["n_scored"] == 2 and sec["uplift"]["reference"]["n_worlds"] == 2
    # report.md: generic tables once (core), then the eval-6 section without its own copy of them;
    # the standalone eval6_report.md keeps the generic summary.
    md = (run / "report.md").read_text()
    assert md.count("## Regime") == 1 and "Generic summary" not in md
    assert md.count("# Eval 6 (epistemic access) D1 report") == 1 and "| no-plan policy |" in md
    assert "Generic summary" in (run / "eval6_report.md").read_text()


class _OneSilentCell(Eval6):
    """The aided stub on w7 commits nothing (a no-plan trial); the unaided stub on w1 copies a
    true parameter into its rationale (an answer-key leak the audit must flag)."""

    def stub_agent(self, cell, sandbox, call_oracle):
        if cell.instance == "w7" and cell.arm == "aided":
            (Path(sandbox) / "notes.txt").write_text("gave up\n")
            return
        super().stub_agent(cell, sandbox, call_oracle)
        if cell.instance == "w1" and cell.arm == "unaided":
            from dbwm.eval6 import _paths
            theta = json.loads(_paths.answer_key("w1", "theta.json").read_text())
            c = json.loads((Path(sandbox) / "claims.json").read_text())
            c["rationale"] = f"elasticity {theta['segments']['core']['elast']!r}"
            (Path(sandbox) / "claims.json").write_text(json.dumps(c))


def test_no_plan_cell_counted_and_policies_differ(tmp_path, monkeypatch):
    monkeypatch.setenv("DBWM_SANDBOX_ROOT", str(tmp_path / "sb"))
    (tmp_path / "sb").mkdir()
    ev = _OneSilentCell()
    res = run_eval(ev, {"adapter": "stub", "isolation": Isolation.HOST_UNSAFE, "instances": ["w1", "w7"],
                        "reps": 1, "model": "dbwm-stub", "auth_mode": "none"},
                   RunOptions(run_id="np", runs_root=tmp_path / "runs", install_signal_handlers=False,
                              preflight=False, log=lambda m: None))
    run = tmp_path / "runs" / "np"
    rep = ev.report(run)
    sec = rep["eval6"][0]
    assert sec["arms"]["aided"]["no_plan"] == 1
    reasons = sec["exclusions_by_reason"]
    assert reasons.pop("no_plan (scored; policy-dependent)") == 1
    assert len(reasons) == 1 and next(iter(reasons)).startswith("audit:") and sum(reasons.values()) == 1
    assert sec["arms"]["unaided"]["n_scored"] == 1                    # the leaking cell is excluded
    assert sec["uplift"]["reference"]["n_worlds"] == 1 and sec["uplift"]["reference"]["dropped_worlds"] == ["w1"]
    assert sec["uplift"]["exclude"]["n_worlds"] == 0
    md = (run / "eval6_report.md").read_text()
    assert "| reference | 1 |" in md and "| exclude | 0 |" in md
    leak = json.loads(next(run.glob("cells/w1__unaided__r0/attempt-*/audit.json")).read_text())
    assert leak["flagged"] is True
    v = json.loads(next(run.glob("cells/w7__aided__r0/attempt-*/verdict.json")).read_text())
    assert v["scored"] and v["success"] is False and v["details"]["no_plan_reason"] == "agent produced no plan.json"
