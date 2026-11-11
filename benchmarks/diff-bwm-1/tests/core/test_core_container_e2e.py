"""CONTAINER isolation end to end with the fake claude bind-mounted into the real image.

Per trial: --internal network, sidecar (oracle server + egress proxy), agent container that sees
only /work (the sandbox) and /dbwm-tools. Checks: the CLI shim and the MCP shim reach the sidecar
oracle; the proxy tunnels an allowlisted host and answers 403 (logged) for example.com; there is
no direct route out; the package is not visible; teardown removes everything.

Skipped (with the reason) when Docker or the dbwm-agent:1.0.0 image is unavailable; build the
image with ``dbwm image build``.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from dbwm.core import container
from dbwm.core import regime as regime_mod
from dbwm.core.oracle import client
from dbwm.core.runner import RunOptions, Runner
from dbwm.core.verdict import read_verdict

pytestmark = pytest.mark.skipif(
    not (container.docker_available() and container.image_exists()),
    reason="needs a running Docker daemon and the dbwm-agent:1.0.0 image (dbwm image build)")

FAKE_MOUNT = "/opt/fake"

PROBE = r'''
import json, os, socket, urllib.parse
u = urllib.parse.urlparse(os.environ.get("HTTPS_PROXY", ""))
def connect(target):
    s = socket.create_connection((u.hostname, u.port), timeout=15)
    s.sendall(("CONNECT %s HTTP/1.1\r\nHost: %s\r\n\r\n" % (target, target)).encode())
    s.settimeout(15)
    data = b""
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    return data.decode("latin-1")
out = {"denied": connect("example.com:443")[:40], "allowed": connect(open("allowed.txt").read().strip())}
try:
    socket.create_connection(("1.1.1.1", 443), timeout=5)
    out["direct"] = "connected"
except OSError as e:
    out["direct"] = "blocked"
out["pkg_visible"] = os.path.exists("/dbwm") or os.path.exists("/harness")
try:
    open("/dbwm-tools/x", "w")
    out["tools_writable"] = True
except OSError:
    out["tools_writable"] = False
try:
    open("/etc/x", "w")
    out["rootfs_writable"] = True
except OSError:
    out["rootfs_writable"] = False
out["uid"] = os.getuid()
out["cwd"] = os.getcwd()
out["has_token"] = bool(os.environ.get("DBWM_ORACLE_TOKEN"))
print(json.dumps(out))
'''


@pytest.fixture
def stub_upstream():
    """An 'allowed' upstream on the host loopback, reachable from the sidecar as host.docker.internal."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def serve():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                continue
            c.sendall(b"hello-from-allowed-host")
            c.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield f"host.docker.internal:{port}"
    stop.set()
    srv.close()


def leftovers(*attempt_dirs: Path) -> list[str]:
    """This test's trial containers/networks that still exist (other runs may share the daemon)."""
    names: list[str] = []
    for adir in attempt_dirs:
        info = json.loads((adir / "container.json").read_text())
        names += [info["network"], info["sidecar"], info["agent"], info["agent"] + "-x"]
    r = subprocess.run(["docker", "ps", "-a", "--filter", f"label={container.LABEL}", "--format", "{{.Names}}"],
                       capture_output=True, text=True)
    n = subprocess.run(["docker", "network", "ls", "--filter", f"label={container.LABEL}", "--format",
                        "{{.Name}}"], capture_output=True, text=True)
    return sorted(set(r.stdout.split() + n.stdout.split()) & set(names))


def run_container(toy, iso_dirs, fake, arms, run_id, egress=()):
    regime = regime_mod.build_regime(toy, {"model": "haiku", "auth_mode": "none", "adapter": "claude_code",
                                           "isolation": "container", "arms": arms, "instances": ["alpha"],
                                           "wall_clock_s": 180, "extra": {"egress_allow": list(egress)}})
    opts = RunOptions(run_id=run_id, runs_root=iso_dirs[1], install_signal_handlers=False, log=lambda m: None,
                      adapter_kwargs={"claude_bin": f"{FAKE_MOUNT}/claude"},
                      container_extra_mounts=[(str(fake.dir), FAKE_MOUNT, "rw")])
    return Runner(toy, regime, opts).run()


def test_bash_arm_in_container_sidecar_and_proxy(iso_dirs, toy, fake_claude, stub_upstream, monkeypatch):
    # The eval's per-command limit must reach the agent container's environment (eval 3 gen-code).
    monkeypatch.setattr(toy, "command_timeout_s", lambda cell: 45, raising=False)
    fake_claude.script({"actions": [
        {"op": "bash", "command": "./toy_model input.json", "stdout_only": True},
        {"op": "write", "path": "answer.json", "json": {"y": "$last.y", "claimed_success": True}},
        {"op": "write", "path": "probe.py", "content": PROBE},
        {"op": "write", "path": "allowed.txt", "content": stub_upstream},
        {"op": "bash", "command": "python3 probe.py", "stdout_only": True},
    ]})
    res = run_container(toy, iso_dirs, fake_claude, ["bash-oracle"], "c1", egress=[stub_upstream])
    adir = res.run_dir / "cells" / "alpha__bash-oracle__r0" / "attempt-1"
    v = read_verdict(adir / "verdict.json")
    assert v["outcome"] == "completed", (v["details"], (adir / "stderr.txt").read_text()[-2000:])
    assert v["success"] is True and v["process"]["oracle_calls"] == 1
    call = fake_claude.calls()[0]
    assert call["cwd"] == "/work"
    assert call["env"]["HTTPS_PROXY"] == "http://dbwm-sidecar:3128"
    assert call["env"]["DBWM_ORACLE"].startswith("tcp://dbwm-sidecar:")
    assert call["env"]["HOME"] == "/home/agent"
    assert call["env"]["BASH_DEFAULT_TIMEOUT_MS"] == call["env"]["BASH_MAX_TIMEOUT_MS"] == "45000"
    assert call["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert v["details"]["command_timeout_s"] == 45
    # probe output is the last tool result in the transcript
    results = [json.loads(x) for x in (adir / "transcript.jsonl").read_text().splitlines()]
    probe_txt = [r for r in results if r.get("type") == "user"][-1]["message"]["content"][0]["content"]
    probe = json.loads(probe_txt)
    assert probe["denied"].startswith("HTTP/1.1 403")
    assert probe["allowed"].startswith("HTTP/1.1 200") and "hello-from-allowed-host" in probe["allowed"]
    assert probe["direct"] == "blocked"
    assert probe["pkg_visible"] is False
    assert probe["tools_writable"] is False and probe["rootfs_writable"] is False
    assert probe["uid"] != 0 and probe["cwd"] == "/work"
    egress = [json.loads(x) for x in (adir / "egress.jsonl").read_text().splitlines()]
    assert any(e["host"] == "example.com" and e["status"] == 403 and e["allowed"] is False for e in egress)
    assert any(e["status"] == 200 and e["allowed"] is True for e in egress)
    assert (adir / "sidecar.log").exists() and (adir / "oracle_calls.jsonl").exists()
    assert "egress_denied" in v["audit"].get("notes", []) or v["audit"]["flagged"] is False
    m = json.loads((res.run_dir / "manifest.json").read_text())
    assert m["isolation"] == "container" and m["conforming"] is True and m["container_image_id"]
    assert leftovers(adir) == []


def test_mcp_arm_in_container(iso_dirs, toy, fake_claude):
    fake_claude.script({"actions": [
        {"op": "mcp", "tool": "run_model", "arguments": {"plan": {"x": 10}}},
        {"op": "write", "path": "answer.json", "json": {"y": "$last.y", "claimed_success": True}}]})
    res = run_container(toy, iso_dirs, fake_claude, ["mcp-oracle"], "c2")
    adir = res.run_dir / "cells" / "alpha__mcp-oracle__r0" / "attempt-1"
    v = read_verdict(adir / "verdict.json")
    assert v["outcome"] == "completed" and v["success"] is True, v["details"]
    argv = fake_claude.calls()[0]["argv"]
    assert argv[argv.index("--mcp-config") + 1] == "/dbwm-tools/mcp.json"
    assert "DBWM_ORACLE" not in fake_claude.calls()[0]["env"]
    assert leftovers(adir) == []


def test_exec_in_agent_and_published_oracle(iso_dirs, toy, tmp_path):
    sb = iso_dirs[0] / "dbwm_exec"
    sb.mkdir()
    (sb / "hello.txt").write_text("hi")
    out = tmp_path / "attempt"
    out.mkdir()
    ocfg = {"eval_id": "toy", "eval_module": "toy_eval", "eval_module_path": "tests/core",
            "cell": {"eval_id": "toy", "instance": "alpha", "arm": "mcp-oracle", "rep": 0}, "mode": "numeric",
            "token": "t" * 32, "query_budget": 3}
    trial = container.start_trial(trial_id="exec-test", sandbox=sb, harness_out=out, oracle_config=ocfg,
                                  egress_allow=[], publish_oracle=True)
    try:
        assert trial.host_oracle_address and trial.host_oracle_address.startswith("tcp://127.0.0.1:")
        assert client.call(trial.host_oracle_address, "t" * 32, "run_model", {"plan": {"x": 10}}) == {"y": 73.1415}
        name = trial.ensure_idle_agent({"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/home/agent"})
        rc, so, se = container.exec_in_agent(name, ["sh", "-c", "pwd; cat hello.txt; echo; ls /dbwm 2>&1 | head -1"], 30)
        assert rc == 0 and so.splitlines()[:2] == ["/work", "hi"] and "No such file" in so
        rc, so, se = container.exec_in_agent(name, ["sleep", "30"], 2)
        assert rc == 124
    finally:
        trial.teardown()
    assert leftovers(out) == []


BRIDGE_PROBE = r"""
import json, socket, sys
out = {}
for name, host, port in json.loads(sys.argv[1]):
    try:
        s = socket.create_connection((host, port), timeout=3)
        s.sendall(b'{"token": "x", "op": "describe", "payload": {}}\n')
        s.settimeout(3)
        out[name] = "answered" if s.recv(100) else "closed"
    except (ConnectionResetError, BrokenPipeError):
        out[name] = "closed"
    except OSError as e:
        out[name] = "unreachable: " + type(e).__name__
print(json.dumps(out))
"""


def _bridge_ip(name: str) -> str:
    r = subprocess.run(["docker", "inspect", "-f", '{{(index .NetworkSettings.Networks "bridge").IPAddress}}', name],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def _probe_from_bridge(targets) -> dict:
    """Run a throwaway container on the DEFAULT bridge and try to talk to ``targets``."""
    r = subprocess.run(["docker", "run", "--rm", "--network", "bridge", container.IMAGE, "python3", "-c",
                        BRIDGE_PROBE, json.dumps(targets)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("publish", [False, True])
def test_sidecar_not_reachable_from_the_default_bridge(iso_dirs, tmp_path, publish):
    """Review m1: other containers on the default bridge cannot use the oracle or the proxy."""
    sb = iso_dirs[0] / f"dbwm_bridge_{int(publish)}"
    sb.mkdir()
    out = tmp_path / "attempt"
    out.mkdir()
    ocfg = {"eval_id": "toy", "eval_module": "toy_eval", "eval_module_path": "tests/core",
            "cell": {"eval_id": "toy", "instance": "alpha", "arm": "mcp-oracle", "rep": 0}, "mode": "numeric",
            "token": "t" * 32, "query_budget": 3}
    trial = container.start_trial(trial_id=f"bridge-test-{int(publish)}", sandbox=sb, harness_out=out,
                                  oracle_config=ocfg, egress_allow=[], publish_oracle=publish)
    try:
        sealed = json.loads((out / "sealed.json").read_text())
        listen = json.loads((out / "sidecar-ready.json").read_text())["listen"]
        assert listen["proxy_clients"] == [sealed["bind_subnet"]]
        assert listen["proxy_host"] != "0.0.0.0"
        assert listen["oracle_host"] == ("0.0.0.0" if publish else listen["proxy_host"])
        ip = _bridge_ip(trial.sidecar)
        assert ip
        got = _probe_from_bridge([["oracle", ip, trial.oracle_port], ["proxy", ip, trial.proxy_port]])
        # Refused (not a timeout): the bridge address is reachable, nothing listens there.
        assert got["proxy"] == "unreachable: ConnectionRefusedError", got
        # Published oracle: listens on all interfaces but closes on clients outside the trial network.
        assert got["oracle"] == ("closed" if publish else "unreachable: ConnectionRefusedError"), got
        if publish:  # the host still reaches the published oracle (from the bridge gateway)
            assert client.call(trial.host_oracle_address, "t" * 32, "run_model", {"plan": {"x": 10}}) == {"y": 73.1415}
    finally:
        trial.teardown()
    assert leftovers(out) == []
