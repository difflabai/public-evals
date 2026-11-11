"""CONTAINER isolation end to end for one bash arm of eval 3 (gen-code), 4, 5 and 6, plus the API relay.

For each eval: the eval's own stub agent first runs in-process (``--adapter stub``
at host-unsafe) to produce its deliverables; then the fake claude, bind-mounted
into ``dbwm-agent:1.0.0``, replays them as a scripted agent inside the agent
container (writing each file through the shell, running the arm's ``qa_check``
CLI shim where the arm has one, running ``python3 model.py`` for gen-code), with
the oracle served by the sidecar. Then ``dbwm score``, ``dbwm audit`` and ``dbwm
report`` run on the result through the real CLI. The verdict must equal the
stub's (same deliverables, same scorer), the oracle calls must have gone through
the sidecar, and the audit must be clean.

The API relay test (red-team RT-E): ``--auth api_key`` with a dummy key at
CONTAINER. The agent container must hold only the relay placeholder; a request
for the pinned model reaches a host-side fake Messages API with the REAL dummy
key; a request for another model is refused and noted by the audit; the key
appears nowhere in the attempt directory.

Skipped with the reason when Docker or the image is unavailable. Marked slow
(about a minute per eval on Docker Desktop).
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from dbwm.core import container
from dbwm.core import regime as regime_mod
from dbwm.core.runner import Runner, RunOptions

from .conftest import PKG_ROOT, arm_table, fake_claude_dir, verdicts

_DOCKER = container.docker_available() and container.image_exists()
pytestmark = [
    pytest.mark.skipif(not _DOCKER, reason="needs a running Docker daemon and the dbwm-agent:1.0.0 image "
                                           "(python -m dbwm image build)"),
    pytest.mark.slow,
    pytest.mark.docker,
]
FAKE_MOUNT = "/opt/fake"
CORE_TESTS = PKG_ROOT / "tests" / "core"

# eval -> (instance, bash arm, expected success (the stub's), shim to call or None)
CASES = {
    "eval3": ("orion-holdings", "gen-code", False, None),
    "eval4": ("w1_t2", "checklist-executable", True, "qa_check"),
    "eval5": ("solvara-market-access", "checklist-verdict", True, "qa_check"),
    "eval6": ("w1", "aided", False, None),
}


def _eval(name: str):
    import importlib

    return importlib.import_module(f"dbwm.{name}").EVAL


def _stub_deliverables(ev, instance: str, arm: str, base: Path) -> dict[str, bytes]:
    regime = regime_mod.build_regime(ev, {"model": "stub", "adapter": "stub", "isolation": "host-unsafe",
                                          "arms": [arm], "instances": [instance], "reps": 1})
    res = Runner(ev, regime, RunOptions(run_id="stub", runs_root=base / "stub-runs",
                                        install_signal_handlers=False, log=lambda m: None)).run()
    d = res.run_dir / "cells" / f"{instance}__{arm}__r0" / "attempt-1" / "deliverables"
    files = {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()}
    assert files, f"the {ev.eval_id} stub wrote no deliverables"
    return files


def _replay_script(files: dict[str, bytes], shim: str | None, run_code: bool) -> dict:
    """Fake-claude actions that write ``files`` from inside the container via the shell."""
    actions: list[dict] = []
    for name, data in files.items():
        actions.append({"op": "write", "path": f".{name}.b64", "content": base64.b64encode(data).decode()})
        actions.append({"op": "bash", "command": (
            f"python3 -c \"import base64; open('{name}', 'wb').write(base64.b64decode(open('.{name}.b64').read()))\""
            f" && rm .{name}.b64")})
    if shim:
        actions.append({"op": "bash", "command": f"./{shim}", "stdout_only": True})
    if run_code:
        actions.append({"op": "bash", "command": "python3 model.py && echo MODEL_OK", "stdout_only": True})
    return {"actions": actions}


def _container_run(ev, instance: str, arm: str, fake: Path, runs: Path, run_id: str, *, auth: str = "none",
                   extra: dict | None = None):
    regime = regime_mod.build_regime(ev, {"model": "haiku", "adapter": "claude_code", "auth_mode": auth,
                                          "isolation": "container", "arms": [arm], "instances": [instance],
                                          "reps": 1, "wall_clock_s": 300, "extra": dict(extra or {})})
    opts = RunOptions(run_id=run_id, runs_root=runs, install_signal_handlers=False, log=lambda m: None,
                      adapter_kwargs={"claude_bin": f"{FAKE_MOUNT}/claude"},
                      container_extra_mounts=[(str(fake), FAKE_MOUNT, "rw")])
    return Runner(ev, regime, opts).run()


def _tool_results(adir: Path) -> list[str]:
    out = []
    for line in (adir / "transcript.jsonl").read_text().splitlines():
        ev = json.loads(line)
        if ev.get("type") == "user":
            for b in ev["message"].get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    out.append(c if isinstance(c, str) else json.dumps(c))
    return out


@pytest.mark.parametrize("eval_name", sorted(CASES))
def test_bash_arm_in_container_end_to_end(eval_name, cli, tmp_path):
    instance, arm, expected, shim = CASES[eval_name]
    if eval_name == "eval4":
        from dbwm.eval4 import dataset
        if not dataset.is_fetched("data"):
            pytest.skip("SKIPPED-NO-DATASET: run `dbwm fetch eval4`")
    ev = _eval(eval_name)
    files = _stub_deliverables(ev, instance, arm, tmp_path)
    fake = fake_claude_dir(tmp_path, _replay_script(files, shim, run_code=(arm == "gen-code")))
    res = _container_run(ev, instance, arm, fake, cli.runs, f"c-{eval_name}")
    rd = res.run_dir
    adir = rd / "cells" / f"{instance}__{arm}__r0" / "attempt-1"
    v = json.loads((adir / "verdict.json").read_text())
    assert v["outcome"] == "completed", (v["details"], (adir / "stderr.txt").read_text()[-2000:])
    assert v["success"] is expected and v["scored"] is True and v["deliverable_present"] is True, v["details"]
    # the files the scorer saw are byte-identical to the stub's
    for name, data in files.items():
        assert (adir / "deliverables" / name).read_bytes() == data, name
    call = json.loads((fake / "fake_log.jsonl").read_text().splitlines()[0])
    assert call["cwd"] == "/work" and call["env"]["HOME"] == "/home/agent"
    assert call["env"]["HTTPS_PROXY"] == "http://dbwm-sidecar:3128"
    results = _tool_results(adir)
    if shim:
        assert v["process"]["oracle_calls"] == 1
        assert call["env"]["DBWM_ORACLE"].startswith("tcp://dbwm-sidecar:")
        assert any("CHECKLIST RESULT" in r or "PASS" in r for r in results), results[-1][:500]
        calls = [json.loads(x) for x in (adir / "oracle_calls.jsonl").read_text().splitlines()]
        assert [c["op"] for c in calls if c.get("call_index")] == ["qa_check"]
    else:
        assert v["process"]["oracle_calls"] == 0 and "DBWM_ORACLE" not in call["env"]
    if arm == "gen-code":
        assert any("MODEL_OK" in r for r in results), results[-3:]
    m = json.loads((rd / "manifest.json").read_text())
    assert m["isolation"] == "container" and m["effective_isolation"] == "container" and m["conforming"] is True
    assert m["api_relay"] is False  # auth none: nothing to relay
    # the real CLI on the container run directory
    cli("score", "--run", str(rd), "--ruleset", "paper")
    vp = json.loads((adir / "verdict.paper.json").read_text())
    assert vp["ruleset"] == "paper" and vp["success"] is expected
    out = cli("audit", "--run", str(rd)).stdout
    assert out.strip().endswith("flagged attempts: 0"), out[-1500:]
    cli("report", "--run", str(rd))
    md = (rd / "report.md").read_text()
    assert "Isolation: `container`; conforming: `True`" in md
    rows = arm_table(md, eval_name)
    assert rows[arm][:3] == ["1", "1", "0"] and rows[arm][4].startswith(f"{int(expected)}/1 ")
    assert sorted(verdicts(rd)) == [f"{instance}__{arm}__r0"]


RELAY_PROBE = r'''
import json, os, sys, urllib.request, urllib.error
base = os.environ["ANTHROPIC_BASE_URL"]
key = os.environ.get("ANTHROPIC_API_KEY", "")
def post(model):
    body = json.dumps({"model": model, "max_tokens": 5, "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request(base + "/v1/messages", data=body, method="POST",
                                 headers={"x-api-key": key, "content-type": "application/json",
                                          "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
out = {"pinned": post(sys.argv[1]), "other": post("claude-opus-4-8"),
       "keylen": len(key), "placeholder": key.startswith("dbwm-relay-")}
print(json.dumps(out))
'''


def test_api_relay_in_container(cli, tmp_path, monkeypatch):
    """RT-E: the agent holds a placeholder; the relay adds the real key and enforces the pin."""
    sys.path.insert(0, str(CORE_TESTS))
    try:
        import toy_eval
        from fake_api import FakeMessagesAPI
    finally:
        sys.path.remove(str(CORE_TESTS))
    dummy = "dummy-key-for-the-relay-e2e-0123456789"
    monkeypatch.setenv("ANTHROPIC_API_KEY", dummy)
    toy = toy_eval.ToyEval()
    with FakeMessagesAPI() as api:
        fake = fake_claude_dir(tmp_path, {"actions": [
            {"op": "write", "path": "probe.py", "content": RELAY_PROBE},
            {"op": "bash", "command": "python3 probe.py claude-haiku-4-5-20251001", "stdout_only": True},
            {"op": "bash", "command": "./toy_model input.json", "stdout_only": True},
            {"op": "write", "path": "answer.json", "json": {"y": "$last.y", "claimed_success": True}},
        ]})
        res = _container_run(toy, "alpha", "bash-oracle", fake, cli.runs, "c-relay", auth="api_key",
                             extra={"api_relay_upstream": f"http://host.docker.internal:{api.port}"})
        upstream = list(api.requests)
    rd = res.run_dir
    adir = rd / "cells" / "alpha__bash-oracle__r0" / "attempt-1"
    v = json.loads((adir / "verdict.json").read_text())
    assert v["outcome"] == "completed" and v["success"] is True, (v["details"], (adir / "stderr.txt").read_text()[-2000:])
    call = json.loads((fake / "fake_log.jsonl").read_text().splitlines()[0])
    assert call["env"]["ANTHROPIC_API_KEY"].startswith("dbwm-relay-")
    assert call["env"]["ANTHROPIC_BASE_URL"] == "http://dbwm-sidecar:8089"
    assert call["env"]["NO_PROXY"] == "dbwm-sidecar"
    assert dummy not in json.dumps(call)
    probe = json.loads([r for r in _tool_results(adir) if r.startswith("{\"pinned\"")][0])
    assert probe["placeholder"] is True
    assert probe["pinned"][0] == 200 and "message_start" in probe["pinned"][1], probe
    assert probe["other"][0] == 403 and "pinned model" in probe["other"][1], probe
    posts = [r for r in upstream if r["method"] == "POST"]
    assert len(posts) == 1 and posts[0]["auth_sha256"] == FakeMessagesAPI.auth_digest(dummy)
    relay = [json.loads(x) for x in (adir / "api_relay.jsonl").read_text().splitlines()]
    assert [r["allowed"] for r in relay] == [True, False], relay
    assert relay[0]["usage"] == {"input_tokens": 10, "output_tokens": 2}
    m = json.loads((rd / "manifest.json").read_text())
    assert m["api_relay"] is True and "api.anthropic.com" not in m["egress_allow"] and m["conforming"] is True
    sealed = (adir / "sealed.json").read_text()
    assert dummy not in sealed and json.loads(sealed)["relay"]["credential_env"] == "DBWM_RELAY_CREDENTIAL"
    for f in adir.rglob("*"):
        if f.is_file() and not f.is_symlink():
            assert dummy.encode() not in f.read_bytes(), f
    assert not (adir / "credential_redactions.json").exists()
    a = json.loads((adir / "audit.json").read_text())
    assert a["flagged"] is False and "api_relay_denied" in a["notes"] and a["counts"]["api_relay_requests"] == 2
    leftovers = subprocess.run(["docker", "ps", "-aq", "--filter", f"label={container.LABEL}", "--filter",
                                "name=c-relay"], capture_output=True, text=True).stdout.split()
    assert leftovers == []
