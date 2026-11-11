"""The ``dbwm`` command line as a subprocess: help, selftest, sandbox-preview, run, signals, canary."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG = HERE.parents[1]


def dbwm(*args, env_extra=None, timeout=300, **kw):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(HERE), str(PKG)]), **(env_extra or {})}
    return subprocess.run([sys.executable, "-m", "dbwm", *args], cwd=PKG, env=env, capture_output=True, text=True,
                          timeout=timeout, **kw)


def test_help_lists_commands():
    p = dbwm("--help")
    assert p.returncode == 0
    for c in ("selftest", "run", "score", "report", "audit", "sandbox-preview", "fetch", "hygiene", "canary",
              "image"):
        assert c in p.stdout
    assert dbwm("run", "--help").returncode == 0
    assert dbwm().returncode == 2


def test_selftest_core_only():
    p = dbwm("selftest", "--core-only")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "0 FAIL" in p.stdout


def test_selftest_toy_eval_and_broken_eval_isolated():
    p = dbwm("selftest", "--evals-only", "--eval", "toy_eval,no_such_eval_module")
    assert "toy selftest" in p.stdout
    assert "FAIL" in p.stdout and p.returncode == 1  # the missing one fails, the toy one still ran


def test_sandbox_preview(iso_dirs):
    p = dbwm("sandbox-preview", "--eval", "toy_eval", "--instance", "alpha", "--arm", "bash-oracle",
             env_extra={"DBWM_SANDBOX_ROOT": str(iso_dirs[0])})
    assert p.returncode == 0, p.stderr
    assert "toy_model" in p.stdout and "## TASK.md" in p.stdout and "clean" in p.stdout
    assert list(iso_dirs[0].iterdir()) == []  # deleted afterwards


def test_run_refuses_without_model(iso_dirs):
    p = dbwm("run", "--eval", "toy_eval", "--adapter", "claude_code", "--auth", "none",
             env_extra={"DBWM_RUNS_DIR": str(iso_dirs[1])})
    assert p.returncode == 2 and "no model" in p.stderr


def test_run_refuses_bash_arm_at_host(iso_dirs):
    p = dbwm("run", "--eval", "toy_eval", "--adapter", "stub", "--arms", "bash-oracle", "--isolation", "host",
             env_extra={"DBWM_RUNS_DIR": str(iso_dirs[1])})
    assert p.returncode == 2 and "needs isolation container" in p.stderr


def test_run_stub_and_report(iso_dirs):
    env = {"DBWM_RUNS_DIR": str(iso_dirs[1]), "DBWM_SANDBOX_ROOT": str(iso_dirs[0])}
    p = dbwm("run", "--eval", "toy_eval", "--adapter", "stub", "--run-id", "cli1", env_extra=env)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["status"] == "complete" and out["cells_scored"] == 4
    p = dbwm("report", "--run", str(iso_dirs[1] / "cli1"), env_extra=env)
    assert p.returncode == 0 and "no-tool" in p.stdout


def test_sigterm_finishes_inflight_attempt_and_manifest(iso_dirs, fake_claude):
    """First SIGTERM: no new launches, the in-flight trial finishes and records (ledger G05)."""
    fake_claude.script({"actions": [{"op": "sleep", "seconds": 3},
                                    {"op": "write", "path": "answer.json", "content": "{}"}]})
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(HERE), str(PKG)]),
           "DBWM_RUNS_DIR": str(iso_dirs[1]), "DBWM_SANDBOX_ROOT": str(iso_dirs[0])}
    proc = subprocess.Popen([sys.executable, "-m", "dbwm", "run", "--eval", "toy_eval", "--adapter", "claude_code",
                             "--model", "haiku", "--auth", "none", "--claude-bin", str(fake_claude.bin),
                             "--arms", "no-tool", "--reps", "3", "--run-id", "sig"], cwd=PKG, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 60
    while not fake_claude.calls() and time.time() < deadline:
        time.sleep(0.1)
    assert fake_claude.calls(), "the first trial never started"
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=120)
    assert proc.returncode == 3, err
    run = iso_dirs[1] / "sig"
    m = json.loads((run / "manifest.json").read_text())
    assert m["status"] == "interrupted"
    recs = [json.loads(x) for x in (run / "attempts.jsonl").read_text().splitlines()]
    attempts = [r for r in recs if r["type"] == "attempt"]
    assert len(attempts) == 1 and attempts[0]["outcome"] == "completed"  # finished and recorded
    assert len(fake_claude.calls()) == 1  # nothing new launched


def test_canary_check():
    p = dbwm("canary")
    assert p.returncode == 0 and "CANARY.txt" in p.stdout


def test_fetch_dispatches_to_the_eval(monkeypatch, capsys):
    """`dbwm fetch eval4 ...` hands every later option (even --help) to eval 4's own fetch parser."""
    import dbwm.eval4 as e4
    from dbwm import cli

    seen = []
    configure, _ = e4.EXTRA_COMMANDS["fetch"]
    monkeypatch.setitem(e4.EXTRA_COMMANDS, "fetch", (configure, lambda a: seen.append(vars(a)) or 0))
    assert cli.main(["fetch", "eval4", "--from-dir", "/nonexistent-copy", "--force"]) == 0
    assert cli.main(["fetch", "4", "--verify-only"]) == 0
    assert seen[0]["from_dir"] == "/nonexistent-copy" and seen[0]["force"] is True
    assert seen[1]["verify_only"] is True
    with pytest.raises(SystemExit) as ex:
        cli.main(["fetch", "eval4", "--help"])
    assert ex.value.code == 0 and "--from-dir" in capsys.readouterr().out
    assert cli.main(["fetch", "eval2"]) == 2  # nothing to fetch


def test_run_dry_run_prints_plan_and_launches_nothing(iso_dirs):
    """Review m4: cost exposure before any money is spent; nothing is written."""
    env = {"DBWM_RUNS_DIR": str(iso_dirs[1]), "DBWM_SANDBOX_ROOT": str(iso_dirs[0])}
    p = dbwm("run", "--eval", "toy_eval", "--model", "haiku", "--adapter", "claude_code", "--auth", "none",
             "--usd-rail", "2.5", "--reps", "3", "--max-total-usd", "10", "--dry-run", env_extra=env)
    assert p.returncode == 0, p.stderr
    plan = json.loads(p.stdout)
    assert plan["cells"] == 12 and plan["cells_by_arm"] == {"mcp-oracle": 6, "no-tool": 6}  # 2 instances x 3 reps
    assert plan["usd_bound_scored"] == 30.0 and plan["usd_bound_all_attempts"] == 90.0
    assert plan["max_total_usd"] == 10.0 and plan["conforming"] is True and len(plan["regime_hash"]) == 64
    assert "DRY RUN" in p.stderr and "$30.00" in p.stderr
    assert list(iso_dirs[1].iterdir()) == [] and list(iso_dirs[0].iterdir()) == []


def test_run_dry_run_refuses_an_invalid_regime(iso_dirs):
    p = dbwm("run", "--eval", "toy_eval", "--adapter", "stub", "--arms", "bash-oracle", "--isolation", "host",
             "--dry-run", env_extra={"DBWM_RUNS_DIR": str(iso_dirs[1])})
    assert p.returncode == 2 and "needs isolation container" in p.stderr


def test_claude_bare_flag_reaches_the_regime():
    from dbwm import cli

    ap, _ = cli.build_parser()
    args = ap.parse_args(["run", "--eval", "1", "--model", "m", "--claude-bare"])
    assert cli.regime_overrides(args)["extra"] == {"claude_bare": True}
    args = ap.parse_args(["run", "--eval", "1", "--model", "m"])
    assert "extra" not in cli.regime_overrides(args)


def test_regime_extra_file_and_openai_flags_reach_the_regime(tmp_path):
    """SF-02: openai_compat is configurable from the CLI."""
    from dbwm import cli
    from dbwm.core import regime as regime_mod

    f = tmp_path / "extra.json"
    f.write_text(json.dumps({"_comment": "dropped", "openai_compat": {
        "base_url": "https://example.invalid/v1", "pricing": {"input": 1.0, "output": 2.0}}, "egress_allow": ["a"]}))
    ap, _ = cli.build_parser()
    args = ap.parse_args(["run", "--eval", "2", "--model", "m", "--adapter", "openai_compat", "--auth", "none",
                          "--regime-extra", str(f), "--openai-key-env", "FIREWORKS_API_KEY"])
    ov = cli.regime_overrides(args)
    assert ov["extra"] == {"openai_compat": {"base_url": "https://example.invalid/v1", "pricing": {
        "input": 1.0, "output": 2.0}, "api_key_env": "FIREWORKS_API_KEY"}, "egress_allow": ["a"]}
    # flags override the file key by key; explicit --egress-allow replaces the file's list
    args = ap.parse_args(["run", "--eval", "2", "--model", "m", "--adapter", "openai_compat", "--auth", "none",
                          "--regime-extra", str(f), "--openai-base-url", "http://127.0.0.1:9/v1",
                          "--egress-allow", "b"])
    ov = cli.regime_overrides(args)
    assert ov["extra"]["openai_compat"] == {"base_url": "http://127.0.0.1:9/v1",
                                            "pricing": {"input": 1.0, "output": 2.0}}
    assert ov["extra"]["egress_allow"] == ["b"]
    r = regime_mod.build_regime(cli.resolve_eval("2"), ov)
    assert r.extra["openai_compat"]["base_url"] == "http://127.0.0.1:9/v1" and r.adapter == "openai_compat"
    # flags alone, no file
    args = ap.parse_args(["run", "--eval", "2", "--model", "m", "--openai-base-url", "https://x.invalid/v1"])
    assert cli.regime_overrides(args)["extra"] == {"openai_compat": {"base_url": "https://x.invalid/v1"}}


@pytest.mark.parametrize("content, match", [
    ("[1, 2]", "must be a JSON object"),
    ("{not json", "regime-extra"),
    ('{"model": "x"}', "regime fields"),
    ('{"openai_compat": {"api_key": "sk-123"}}', "openai_compat.api_key"),
    ('{"headers": {"Authorization": "Bearer x"}}', "headers.Authorization"),
])
def test_regime_extra_file_is_validated(tmp_path, content, match):
    from dbwm import cli
    from dbwm.core import regime as regime_mod

    f = tmp_path / "extra.json"
    f.write_text(content)
    with pytest.raises(regime_mod.RegimeError, match=match):
        cli.read_regime_extra(f)


def test_regime_extra_cli_end_to_end(iso_dirs, tmp_path):
    """The file lands in the dry-run plan's regime (nothing launched); a bad file is a usage error."""
    f = tmp_path / "extra.json"
    f.write_text(json.dumps({"openai_compat": {"base_url": "https://example.invalid/v1"}}))
    env = {"DBWM_RUNS_DIR": str(iso_dirs[1]), "DBWM_SANDBOX_ROOT": str(iso_dirs[0])}
    p = dbwm("run", "--eval", "toy_eval", "--model", "m", "--adapter", "openai_compat", "--auth", "none",
             "--regime-extra", str(f), "--openai-key-env", "MY_KEY", "--dry-run", env_extra=env)
    assert p.returncode == 0, p.stderr
    plan = json.loads(p.stdout)
    assert plan["regime_extra"]["openai_compat"] == {"base_url": "https://example.invalid/v1",
                                                        "api_key_env": "MY_KEY"}
    f.write_text('{"openai_compat": {"api_key": "sk-x"}}')
    p = dbwm("run", "--eval", "toy_eval", "--model", "m", "--adapter", "openai_compat", "--auth", "none",
             "--regime-extra", str(f), "--dry-run", env_extra=env)
    assert p.returncode == 2 and "credential-like" in p.stderr and "sk-x" not in p.stderr
    assert list(iso_dirs[1].iterdir()) == []
