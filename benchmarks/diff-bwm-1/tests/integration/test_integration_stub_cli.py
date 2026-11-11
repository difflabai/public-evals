"""Every canonical arm of every eval through the real CLI with the deterministic stub agent.

``python -m dbwm run --adapter stub`` (no-bash arms at ``host``; bash arms at
``host-unsafe``, which is fine for an in-process stub), then ``dbwm score
--ruleset paper``, ``dbwm audit`` and ``dbwm report`` on the same run directory.
One instance and one rep per eval keep the file to well under a minute.

The expected per-arm outcomes are what the eval's stub agent is built to do
(read the eval's stub module): an arm with a usable oracle reaches the goal; the
ungrounded / prose / baseline arms (and eval 3 gen-code, eval 6, whose stubs do
not solve the task) fail and claim success where the stub claims it. They are the
same under both rulesets. A change here means a stub, a scorer or the runner
changed behaviour.

Also: resume (same run id launches nothing) on eval 2, and infra retry through
the claude_code adapter with the fake CLI on eval 1 (an API error, then a crash,
then a scored attempt: three attempts, all kept, the third scored).
"""

from __future__ import annotations

import json

import pytest

from .conftest import arm_table, attempts_log, fake_claude_dir, last_json, verdicts

# run id -> (eval, isolation, instances, arms (None = canonical), {arm: success}, eval-report marker)
STUB_RUNS = {
    "e1": ("1", "host", "atlas-advisory__soft", None,
           {"ungrounded": False, "grounded-readonly": True, "grounded-iterate": True, "prose-feedback": True,
            "raw-figures": True, "broken-model": True}, "# diff-bwm-1 eval 1 run e1"),
    "e2": ("2", "host", "orion-holdings", None,
           {"ungrounded": False, "grounded-readonly": True, "grounded-iterate": True, "grounded-boolean": False,
            "raw-figures": True, "prose-feedback": False}, "# Eval 2 (coupled portfolio) -- run report"),
    "e3": ("3", "host", "orion-holdings", "gen-figures,gen-both",
           {"gen-figures": True, "gen-both": True}, "# Eval 3 (generative vs grounded) -- run report"),
    "e3code": ("3", "host-unsafe", "orion-holdings", "gen-code",
               {"gen-code": False}, "# Eval 3 (generative vs grounded) -- run report"),
    "e4": ("4", "host-unsafe", "w1_t2", None,
           {"baseline": False, "checklist-prose": False, "checklist-verdict": True, "checklist-executable": True},
           "## Eval 4 (AA-Briefcase-Lite w1_t2)"),
    "e5": ("5", "host-unsafe", "solvara-market-access", None,
           {"baseline": False, "checklist-prose": False, "checklist-verdict": True, "checklist-executable": True},
           "# Eval 5 (consulting oracle) report"),
    "e6": ("6", "host-unsafe", "w1", None, {"unaided": False, "aided": False},
           "# Eval 6 (epistemic access) D1 report"),
}
# Arms whose stub calls the oracle (> 0 authorised calls expected).
ORACLE_ARMS = {"grounded-readonly", "grounded-iterate", "prose-feedback", "raw-figures", "broken-model",
               "grounded-boolean", "gen-figures", "gen-both", "checklist-verdict", "checklist-executable"}


@pytest.fixture(scope="module")
def stub_run(cli):
    """Run one STUB_RUNS entry through run -> score paper -> audit -> report; cached per module."""
    cache: dict[str, dict] = {}

    def get(run_id: str) -> dict:
        if run_id in cache:
            return cache[run_id]
        ev, iso, inst, arms, _exp, _marker = STUB_RUNS[run_id]
        if ev == "4":
            from dbwm.eval4 import dataset
            if not dataset.is_fetched("data"):  # a fresh clone: the dataset is fetched, never vendored
                pytest.skip("SKIPPED-NO-DATASET: run `dbwm fetch eval4`")
        args = ["run", "--eval", ev, "--adapter", "stub", "--model", "stub", "--isolation", iso,
                "--instances", inst, "--reps", "1", "--run-id", run_id]
        if arms:
            args += ["--arms", arms]
        out = {"run": last_json(cli(*args).stdout)}
        rd = cli.run_dir(run_id)
        out["score"] = last_json(cli("score", "--run", str(rd), "--ruleset", "paper").stdout)
        out["audit_stdout"] = cli("audit", "--run", str(rd)).stdout
        cli("report", "--run", str(rd))
        out["dir"] = rd
        cache[run_id] = out
        return out

    return get


@pytest.mark.parametrize("run_id", sorted(STUB_RUNS))
def test_run_completes_every_designed_cell(stub_run, run_id):
    ev, iso, _inst, _arms, expected, _m = STUB_RUNS[run_id]
    r = stub_run(run_id)["run"]
    n = len(expected)
    assert r["status"] == "complete" and r["halt_reason"] is None, r
    assert r["cells_total"] == n and r["cells_scored"] == n and r["outcomes"] == {"completed": n}, r
    m = json.loads((stub_run(run_id)["dir"] / "manifest.json").read_text())
    assert m["isolation"] == iso and m["effective_isolation"] == "in-process"
    assert m["conforming"] is (iso == "host"), m["nonconforming_reasons"]
    assert sorted(c["arm"] for c in m["cells"]) == sorted(expected)


@pytest.mark.parametrize("run_id", sorted(STUB_RUNS))
def test_verdicts_and_scored_outcomes(stub_run, run_id):
    ev, _iso, _inst, _arms, expected, _m = STUB_RUNS[run_id]
    rd = stub_run(run_id)["dir"]
    for name, ruleset in (("verdict.json", "v1"), ("verdict.paper.json", "paper")):
        vs = verdicts(rd, name)
        assert len(vs) == len(expected), (name, sorted(vs))
        got = {v["cell"]["arm"]: v["success"] for v in vs.values()}
        assert got == expected, (ruleset, got)
        for v in vs.values():
            assert v["schema"] == "dbwm.verdict/1" and v["ruleset"] == ruleset
            assert v["outcome"] == "completed" and v["scored"] is True and v["attempt"] == 1
            assert v["deliverable_present"] is True
            assert v["eval"] == f"eval{ev}"
            calls = v["process"]["oracle_calls"]
            assert (calls > 0) if v["cell"]["arm"] in ORACLE_ARMS else (calls == 0), (v["cell"], calls)
            assert v["process"]["oracle_override_attempts"] == 0
    assert stub_run(run_id)["score"]["rescored"] == len(expected)


@pytest.mark.parametrize("run_id", sorted(STUB_RUNS))
def test_audit_is_clean(stub_run, run_id):
    out = stub_run(run_id)
    assert out["audit_stdout"].strip().endswith("flagged attempts: 0"), out["audit_stdout"][-500:]
    summary = json.loads((out["dir"] / "audit_summary.json").read_text())
    assert summary["attempts"] == len(STUB_RUNS[run_id][4]) and summary["by_reason"] == {} and not summary["errors"]
    for v in verdicts(out["dir"]).values():
        assert v["audit"]["flagged"] is False and v["audit"]["reasons"] == []


@pytest.mark.parametrize("run_id", sorted(STUB_RUNS))
def test_report_contents(stub_run, run_id):
    ev, iso, _inst, _arms, expected, marker = STUB_RUNS[run_id]
    rd = stub_run(run_id)["dir"]
    md = (rd / "report.md").read_text()
    m = json.loads((rd / "manifest.json").read_text())
    assert md.startswith(f"# {m['eval_title']}")
    assert f"Isolation: `{iso}`; conforming: `{iso == 'host'}`" in md
    if iso == "host-unsafe":
        assert "NON-CONFORMING RUN" in md
    assert marker in md, "the eval's own report section is missing"
    for ruleset in ("paper", "v1"):
        rows = arm_table(md, f"eval{ev}", ruleset)
        assert sorted(rows) == sorted(expected), (ruleset, sorted(rows))
        for arm, ok in expected.items():
            designed, scored, excluded = rows[arm][0], rows[arm][1], rows[arm][2]
            assert (designed, scored, excluded) == ("1", "1", "0"), (arm, rows[arm])
            assert rows[arm][4].startswith(f"{int(ok)}/1 "), (ruleset, arm, rows[arm][4])
    rep = json.loads((rd / "report.json").read_text())
    assert rep["run_id"] == run_id and rep["verdicts_read"] == 2 * len(expected) and not rep["load_errors"]


def test_resume_launches_nothing(cli, stub_run):
    out = stub_run("e2")
    rd = out["dir"]
    before = attempts_log(rd, kind=None)
    r = last_json(cli("run", "--eval", "2", "--adapter", "stub", "--model", "stub", "--isolation", "host",
                      "--instances", "orion-holdings", "--reps", "1", "--run-id", "e2").stdout)
    assert r["status"] == "complete" and r["cells_scored"] == r["cells_total"] == 6
    assert attempts_log(rd, kind=None) == before, "resume re-ran a cell that already had a scored attempt"
    m = json.loads((rd / "manifest.json").read_text())
    assert len(m["invocations"]) == 2


def test_resume_refuses_a_different_regime(cli, stub_run):
    stub_run("e2")
    r = cli("run", "--eval", "2", "--adapter", "stub", "--model", "stub", "--isolation", "host",
            "--instances", "orion-holdings", "--reps", "1", "--run-id", "e2", "--max-turns", "7", check=False)
    assert r.returncode != 0 and "regime" in (r.stdout + r.stderr).lower()


def test_infra_retry_keeps_every_attempt_and_scores_the_first_good_one(cli, tmp_path):
    """claude_code adapter + fake CLI at host: attempt 1 API error (infra_api), attempt 2
    crash (no result event), attempt 3 writes the deliverables and completes."""
    plan = {"bindings": {"bd_spend": ["720000.00"] * 6, "hires_per_quarter": ["17"] * 6}}
    claims = {"goal_met": True, "constraints_satisfied": True, "projected_final_revenue": "4642560.00",
              "rationale": "fake"}
    fake = fake_claude_dir(tmp_path, {"sequence": [
        {"actions": [{"op": "result", "subtype": "success", "is_error": True,
                      "result": "API Error: 529 Overloaded", "num_turns": 1, "total_cost_usd": 0}]},
        {"actions": [{"op": "exit", "code": 1}]},
        {"actions": [{"op": "write", "path": "plan.json", "json": plan},
                     {"op": "write", "path": "claims.json", "json": claims}]},
    ]})
    r = cli("run", "--eval", "1", "--adapter", "claude_code", "--claude-bin", str(fake / "claude"),
            "--model", "haiku", "--auth", "none", "--isolation", "host", "--instances", "atlas-advisory__soft",
            "--arms", "ungrounded", "--reps", "1", "--run-id", "retry")
    summary = last_json(r.stdout)
    assert summary["status"] == "complete" and summary["cells_scored"] == 1, summary
    rd = cli.run_dir("retry")
    log = attempts_log(rd)
    assert [a["outcome"] for a in log] == ["infra_api", "crash", "completed"], log
    assert [a["k"] for a in log] == [1, 2, 3] and [a["scored"] for a in log] == [False, False, True]
    cell = rd / "cells" / "atlas-advisory__soft__ungrounded__r0"
    assert sorted(p.name for p in cell.glob("attempt-*")) == ["attempt-1", "attempt-2", "attempt-3"]
    v = json.loads((cell / "attempt-3" / "verdict.json").read_text())
    assert v["scored"] is True and v["attempt"] == 3 and v["success"] is False and v["claimed_success"] is True
    v1 = json.loads((cell / "attempt-1" / "verdict.json").read_text())
    assert v1["scored"] is False and v1["outcome"] == "infra_api"
    # score / audit / report treat the cell as one scored attempt
    cli("score", "--run", str(rd), "--ruleset", "paper")
    assert cli("audit", "--run", str(rd)).stdout.strip().endswith("flagged attempts: 0")
    cli("report", "--run", str(rd))
    rows = arm_table((rd / "report.md").read_text(), "eval1")
    assert rows["ungrounded"][:3] == ["1", "1", "0"] and rows["ungrounded"][4].startswith("0/1 ")
