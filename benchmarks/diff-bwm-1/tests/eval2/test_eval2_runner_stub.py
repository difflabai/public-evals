"""Every eval-2 arm end to end through the real core runner with the stub adapter (Isolation.HOST).

Sandbox -> sealed oracle server (TCP) -> stub agent -> deliverables -> score -> verdict -> audit -> report.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from dbwm.core import paths


def test_stub_run_all_arms_through_the_runner(tmp_path):
    runs = tmp_path / "runs"
    arms = "ungrounded,grounded-readonly,grounded-iterate,grounded-boolean,raw-figures,prose-feedback,broken-interface"
    cmd = [sys.executable, "-m", "dbwm", "run", "--eval", "2", "--adapter", "stub", "--model", "dbwm-stub",
           "--isolation", "host", "--auth", "none", "--reps", "1", "--instances", "vega-group",
           "--arms", arms, "--runs-dir", str(runs), "--run-id", "e2"]
    p = subprocess.run(cmd, cwd=str(paths.PKG_ROOT), capture_output=True, text=True, timeout=600,
                       env={**os.environ, "PYTHONHASHSEED": "0"})
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]
    run = runs / "e2"
    verdicts = {}
    for vf in sorted(run.glob("cells/*/attempt-1/verdict.json")):
        v = json.loads(vf.read_text())
        verdicts[v["cell"]["arm"]] = v
    assert sorted(verdicts) == sorted(arms.split(","))
    for arm, v in verdicts.items():
        assert v["outcome"] == "completed" and v["scored"] and v["ruleset"] == "v1", arm
        assert v["deliverable_present"] is True, arm
        assert not (v.get("audit") or {}).get("flagged"), (arm, v.get("audit"))
        assert v["process"]["oracle_override_attempts"] == 0
        if arm == "ungrounded":
            assert v["process"]["oracle_calls"] == 0 and v["overclaim"] is True
        if arm == "grounded-readonly":
            assert v["process"]["oracle_calls"] <= 3
    calls = [json.loads(l) for l in (run / "cells" / "vega-group__grounded-boolean__r0" / "attempt-1" /
                                     "oracle_calls.jsonl").read_text().splitlines()]
    assert calls and all(c["op"] == "world_query" and c["ok"] for c in calls)
