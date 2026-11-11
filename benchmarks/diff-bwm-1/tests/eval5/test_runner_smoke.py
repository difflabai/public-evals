"""One smoke run through the real CLI/runner with the stub adapter (wave-2 tests extend this).

host-unsafe is acceptable for a stub (no model, no bash use); the run is recorded as non-conforming.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from .conftest import PKG_ROOT


def test_stub_run_all_cells(tmp_path):
    env = dict(os.environ, DBWM_SANDBOX_ROOT=str(tmp_path / "sb"))
    (tmp_path / "sb").mkdir()
    runs = tmp_path / "runs"
    r = subprocess.run([sys.executable, "-m", "dbwm", "run", "--eval", "5", "--adapter", "stub",
                        "--isolation", "host-unsafe", "--reps", "1", "--run-id", "smoke", "--runs-dir", str(runs),
                        "--model", "dbwm-stub"], cwd=str(PKG_ROOT), env=env, capture_output=True, text=True,
                       timeout=900)
    assert r.returncode == 0, r.stderr[-3000:]
    run = runs / "smoke"
    verdicts = [json.loads(p.read_text()) for p in sorted(run.glob("cells/*/attempt-*/verdict.json"))]
    assert len(verdicts) == 16 and all(v["scored"] and v["ruleset"] == "v1" for v in verdicts)
    by_arm = {}
    for v in verdicts:
        by_arm.setdefault(v["cell"]["arm"], []).append(v["success"])
    assert by_arm["checklist-verdict"] == [True] * 4 and by_arm["checklist-executable"] == [True] * 4
    assert not any(by_arm["baseline"]) and not any(by_arm["checklist-prose"])
    for p in run.glob("cells/*/attempt-*/audit.json"):
        assert json.loads(p.read_text()).get("flagged") is False, p
    # per-scenario deliverables (EVAL.deliverables_for): no other scenario's names declared or "missing"
    from dbwm.eval5 import dataset as D

    want = {s["id"]: s["deliverables"] for s in D.manifest()["scenarios"]}
    dj = sorted(run.glob("cells/*/attempt-*/deliverables.json"))
    assert len(dj) == 16
    for p in dj:
        rec = json.loads(p.read_text())
        sid = p.parent.parent.name.split("__")[0]
        assert rec["declared"] == want[sid], p
        assert set(rec["missing"]) <= set(want[sid]), p
    calls = [json.loads(x) for p in run.glob("cells/*checklist-verdict*/attempt-*/oracle_calls.jsonl")
             for x in p.read_text().splitlines() if x.strip()]
    assert len(calls) == 8 and all(c["op"] == "qa_check" for c in calls)
    rep = subprocess.run([sys.executable, "-m", "dbwm", "report", "--run", str(run)], cwd=str(PKG_ROOT), env=env,
                         capture_output=True, text=True, timeout=300)
    assert rep.returncode == 0, rep.stderr[-2000:]
    assert (run / "eval5_report.json").exists()
    # the checklist arms staged PASSES WHEN lines and the sandbox record lists them as disclosed
    for p in run.glob("cells/*/attempt-*/sandbox.json"):
        rec = json.loads(p.read_text())
        assert bool(rec.get("disclosed_values")) == ("checklist-" in p.parent.parent.name), p
    # re-score with the as-run scorer and the oracle of record (PLAN 1.1 acceptance)
    want = {"as-run": "0.1", "paper": "0.1+A13-fix"}
    for rs, version in want.items():
        sc = subprocess.run([sys.executable, "-m", "dbwm", "score", "--run", str(run), "--ruleset", rs],
                            cwd=str(PKG_ROOT), env=env, capture_output=True, text=True, timeout=300)
        assert sc.returncode == 0, sc.stderr[-2000:]
        vs = [json.loads(p.read_text()) for p in sorted(run.glob(f"cells/*/attempt-*/verdict.{rs}.json"))]
        assert len(vs) == 16 and all(v["ruleset"] == rs for v in vs)
        assert {v["details"]["registry_version"] for v in vs} == {version}
