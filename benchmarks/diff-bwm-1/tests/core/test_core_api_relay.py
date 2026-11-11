"""The CONTAINER API relay (red-team finding RT-E): the agent holds a placeholder, the relay the key.

Host-only tests (no Docker): the relay in front of tests/core/fake_api.py, policy (path, model,
placeholder, client network), usage tally, logs without credentials, the agent-env rewrite,
regime/conformance, the egress allowlist, and post-attempt credential redaction + audit flag.
The in-container path is covered in tests/integration/test_integration_container.py.
"""

from __future__ import annotations

import http.client
import json
import sys
from pathlib import Path

import pytest

from dbwm.core import api_relay, audit, sandbox
from dbwm.core import regime as regime_mod
from dbwm.core.env import credential_values, relay_agent_env
from dbwm.core.types import Isolation

CORE_TESTS = Path(__file__).resolve().parent
if str(CORE_TESTS) not in sys.path:
    sys.path.insert(0, str(CORE_TESTS))

from fake_api import FakeMessagesAPI  # noqa: E402

REAL = "real-credential-value-0123456789"
PLACEHOLDER = "dbwm-relay-placeholder-abcdef"
PIN = "claude-haiku-4-5"


@pytest.fixture
def relay(tmp_path):
    with FakeMessagesAPI() as api:
        r = api_relay.ApiRelay(credential=REAL, placeholder=PLACEHOLDER, pin=PIN, upstream=api.base_url,
                               log_path=tmp_path / "api_relay.jsonl", host="127.0.0.1", port=0)
        r.start()
        try:
            yield r, api, tmp_path / "api_relay.jsonl"
        finally:
            r.stop()


def _post(port, path, body, headers=None, method="POST"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    data = json.dumps(body).encode() if body is not None else b""
    h = {"content-type": "application/json", **(headers or {})}
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, r.read()
    c.close()
    return out


def _records(log, n=1, timeout=10.0):
    """The relay's records, waiting until there are at least ``n`` (an allowed request is
    logged after its response has been streamed)."""
    import time

    deadline = time.monotonic() + timeout
    while True:
        recs = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        if len(recs) >= n or time.monotonic() > deadline:
            return recs
        time.sleep(0.02)


def test_allowed_request_is_forwarded_with_the_real_credential(relay):
    r, api, log = relay
    status, body = _post(r.port, "/v1/messages?beta=true",
                         {"model": "claude-haiku-4-5-20251001", "stream": True, "max_tokens": 5,
                          "messages": [{"role": "user", "content": "hi"}]}, {"x-api-key": PLACEHOLDER})
    assert status == 200 and b"message_stop" in body and b'"text": "done"' in body
    assert api.requests[-1]["auth_sha256"] == FakeMessagesAPI.auth_digest(REAL)
    rec = _records(log)[-1]
    assert rec["allowed"] is True and rec["status"] == 200 and rec["stream"] is True
    assert rec["usage"] == {"input_tokens": 10, "output_tokens": 2}
    assert rec["served_model"] == "claude-haiku-4-5-20251001" and rec["path"] == "/v1/messages"
    text = log.read_text()
    assert REAL not in text and PLACEHOLDER not in text


@pytest.mark.parametrize("path,body,headers,status,reason", [
    ("/v1/messages", {"model": PIN}, {"x-api-key": "wrong"}, 401, "placeholder"),
    ("/v1/messages", {"model": PIN}, {}, 401, "placeholder"),
    ("/v1/messages", {"model": PIN}, {"x-api-key": REAL}, 401, "placeholder"),
    ("/v1/messages", {"model": "claude-opus-4-8"}, {"x-api-key": PLACEHOLDER}, 403, "pinned model"),
    ("/v1/messages", {"model": None}, {"x-api-key": PLACEHOLDER}, 403, "pinned model"),
    ("/v1/models", {"model": PIN}, {"x-api-key": PLACEHOLDER}, 403, "not relayed"),
    ("/v1/messages/batches", {"model": PIN}, {"x-api-key": PLACEHOLDER}, 403, "not relayed"),
    ("/v1/complete", {"model": PIN}, {"x-api-key": PLACEHOLDER}, 403, "not relayed"),
])
def test_denials(relay, path, body, headers, status, reason):
    r, api, log = relay
    n = len(api.requests)
    got, resp = _post(r.port, path, body, headers)
    assert got == status
    err = json.loads(resp)
    assert err["type"] == "error" and reason in err["error"]["message"]
    assert len(api.requests) == n, "a denied request must never reach the upstream"
    rec = _records(log)[-1]
    assert rec["allowed"] is False and rec["status"] == status
    assert REAL not in log.read_text()


def test_get_is_not_relayed(relay):
    r, api, log = relay
    status, _ = _post(r.port, "/v1/messages", None, {"x-api-key": PLACEHOLDER}, method="GET")
    assert status == 403 and not api.requests


def test_cli_connectivity_probe_is_answered_locally(relay):
    r, api, log = relay
    assert _post(r.port, "/api/hello", None, {}, method="HEAD")[0] == 200
    assert not api.requests
    rec = _records(log)[-1]
    assert rec["local"] is True and rec["allowed"] is True
    assert api_relay.summarize([rec])["requests"] == 0


def test_count_tokens_path_is_relayed(relay):
    r, api, log = relay
    status, _ = _post(r.port, "/v1/messages/count_tokens", {"model": PIN, "messages": []},
                      {"x-api-key": PLACEHOLDER})
    assert status == 404  # the fake API has no count_tokens; what matters: it was forwarded
    assert api.requests[-1]["path"] == "/v1/messages/count_tokens"
    assert _records(log)[-1]["allowed"] is True


def test_extra_models_and_bearer_mode(tmp_path):
    with FakeMessagesAPI() as api:
        r = api_relay.ApiRelay(credential=REAL, placeholder=PLACEHOLDER, auth_header="bearer", pin=PIN,
                               extra_models=["claude-sonnet-4-6"], upstream=api.base_url,
                               log_path=tmp_path / "r.jsonl", host="127.0.0.1", port=0)
        r.start()
        try:
            assert _post(r.port, "/v1/messages", {"model": PIN}, {"x-api-key": PLACEHOLDER})[0] == 401
            assert _post(r.port, "/v1/messages", {"model": "claude-sonnet-4-6"},
                         {"authorization": f"Bearer {PLACEHOLDER}"})[0] == 200
            assert api.requests[-1]["auth_sha256"] == FakeMessagesAPI.auth_digest(f"Bearer {REAL}")
        finally:
            r.stop()


def test_clients_outside_the_trial_network_are_refused(tmp_path):
    with FakeMessagesAPI() as api:
        r = api_relay.ApiRelay(credential=REAL, placeholder=PLACEHOLDER, pin=PIN, upstream=api.base_url,
                               log_path=tmp_path / "r.jsonl", host="127.0.0.1", port=0,
                               allow_clients=["10.99.0.0/16"])
        r.start()
        try:
            with pytest.raises((ConnectionError, http.client.HTTPException, OSError)):
                _post(r.port, "/v1/messages", {"model": PIN}, {"x-api-key": PLACEHOLDER})
            assert not api.requests
            assert _records(tmp_path / "r.jsonl")[-1]["reason"] == "client not on the trial network"
        finally:
            r.stop()


def test_constructor_refuses_bad_configs():
    with pytest.raises(ValueError):
        api_relay.ApiRelay(credential=REAL, placeholder=REAL, pin=PIN)
    with pytest.raises(ValueError):
        api_relay.ApiRelay(credential="", placeholder=PLACEHOLDER, pin=PIN)
    with pytest.raises(ValueError):
        api_relay.ApiRelay(credential=REAL, placeholder=PLACEHOLDER, pin=PIN, upstream="ftp://x")


def test_usage_tally_non_streaming():
    t = api_relay.UsageTally(stream=False)
    t.feed(b'{"model": "m", "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 2}}')
    t.finish()
    assert t.served_model == "m" and t.usage == {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 2}
    s = api_relay.summarize([{"allowed": True, "usage": t.usage, "served_model": "m"},
                             {"allowed": False, "reason": "x"}])
    assert s["requests"] == 2 and s["denied"] == 1 and s["usage"]["input_tokens"] == 7 and s["models"] == ["m"]


def test_relay_agent_env_replaces_the_credential():
    env = {"ANTHROPIC_API_KEY": REAL, "PATH": "/usr/bin", "HTTPS_PROXY": "http://dbwm-sidecar:3128"}
    out = relay_agent_env(env, "ANTHROPIC_API_KEY", PLACEHOLDER, "http://dbwm-sidecar:8089")
    assert REAL not in json.dumps(out)
    assert out["ANTHROPIC_API_KEY"] == PLACEHOLDER and out["ANTHROPIC_BASE_URL"] == "http://dbwm-sidecar:8089"
    assert out["NO_PROXY"] == out["no_proxy"] == "dbwm-sidecar"
    assert env["ANTHROPIC_API_KEY"] == REAL  # input untouched


def test_relay_applies_only_to_container_claude_code_key_auth(toy):
    def reg(**kw):
        base = {"model": "haiku", "adapter": "claude_code", "isolation": "container", "auth_mode": "api_key"}
        return regime_mod.build_regime(toy, {**base, **kw}, validate=False)

    assert regime_mod.relay_auth(reg()) == ("x-api-key", "ANTHROPIC_API_KEY")
    assert regime_mod.relay_auth(reg(auth_mode="oauth_token")) == ("bearer", "CLAUDE_CODE_OAUTH_TOKEN")
    assert regime_mod.relay_auth(reg(auth_mode="bedrock")) is None
    assert regime_mod.relay_auth(reg(isolation="host")) is None
    assert regime_mod.relay_auth(reg(adapter="command")) is None
    off = reg(extra={"api_relay": False})
    assert regime_mod.relay_auth(off) is None
    assert any("api_relay disabled" in r for r in regime_mod.nonconforming_reasons(off))
    assert not regime_mod.nonconforming_reasons(reg())


def test_credential_values_and_redaction(tmp_path):
    vals = credential_values("api_key", {"ANTHROPIC_API_KEY": REAL, "PATH": "/x"})
    assert vals == [REAL]
    assert credential_values("session", {"USER": "someone"}) == []
    adir = tmp_path / "attempt"
    (adir / "deliverables").mkdir(parents=True)
    (adir / "transcript.jsonl").write_text(json.dumps({"out": f"KEY={REAL}"}) + "\n")
    (adir / "deliverables" / "notes.md").write_text(f"leaked {REAL}\n")
    (adir / "clean.txt").write_text("nothing here\n")
    done = sandbox.redact_secret_values(adir, vals)
    assert sorted(done) == ["deliverables/notes.md", "transcript.jsonl"]
    for f in adir.rglob("*"):
        if f.is_file():
            assert REAL not in f.read_text()
    assert sandbox.redact_secret_values(adir, ["short"]) == []


def test_audit_flags_a_redacted_credential_and_notes_relay_denials(tmp_path, toy):
    from dbwm.core.types import Cell

    adir = tmp_path / "attempt-1"
    (adir / "deliverables").mkdir(parents=True)
    (adir / "transcript.jsonl").write_text("")
    (adir / "credential_redactions.json").write_text(json.dumps({"files": ["transcript.jsonl"]}))
    (adir / "api_relay.jsonl").write_text(
        json.dumps({"allowed": True, "usage": {"input_tokens": 5}, "served_model": "m"}) + "\n"
        + json.dumps({"allowed": False, "reason": "model is not the pinned model"}) + "\n")
    a = audit.audit_attempt(adir, toy, Cell("toy", "alpha", "bash-oracle", 0), write=False)
    assert a["flagged"] and audit.F_CREDENTIAL in a["reasons"]
    assert audit.N_RELAY_DENIED in a["notes"]
    assert a["counts"]["api_relay_requests"] == 2 and a["api_relay"]["usage"]["input_tokens"] == 5


def test_runner_drops_the_api_host_from_the_tunnel_allowlist(toy, iso_dirs, monkeypatch):
    from dbwm.core.runner import Runner, RunOptions

    monkeypatch.setenv("ANTHROPIC_API_KEY", REAL)
    base = {"model": "haiku", "adapter": "claude_code", "isolation": "container", "auth_mode": "api_key"}
    on = Runner(toy, regime_mod.build_regime(toy, base, validate=False),
                RunOptions(runs_root=iso_dirs[1], install_signal_handlers=False))
    assert on.relay_auth and "api.anthropic.com" not in on.egress_allow
    off = Runner(toy, regime_mod.build_regime(toy, {**base, "extra": {"api_relay": False}}, validate=False),
                 RunOptions(runs_root=iso_dirs[1], install_signal_handlers=False))
    assert off.relay_auth is None and "api.anthropic.com" in off.egress_allow
    assert Isolation.CONTAINER.value == "container"


def test_runner_redacts_a_printed_credential_and_the_audit_flags_it(toy, iso_dirs, fake_claude, monkeypatch):
    """host-unsafe bash arm, api_key auth, the agent echoes the key: the attempt record keeps no
    copy of it and the attempt is excluded (credential_exposed)."""
    from dbwm.core.runner import RunOptions, Runner

    monkeypatch.setenv("ANTHROPIC_API_KEY", REAL)
    fake_claude.script({"actions": [
        {"op": "bash", "command": "echo KEY=$ANTHROPIC_API_KEY > leaked.txt; cat leaked.txt"},
        {"op": "bash", "command": "./toy_model input.json", "stdout_only": True},
        {"op": "write", "path": "answer.json", "json": {"y": "$last.y", "claimed_success": True}}]})
    regime = regime_mod.build_regime(toy, {"model": "haiku", "adapter": "claude_code", "auth_mode": "api_key",
                                           "isolation": "host-unsafe", "arms": ["bash-oracle"],
                                           "instances": ["alpha"]})
    res = Runner(toy, regime, RunOptions(run_id="redact", runs_root=iso_dirs[1], install_signal_handlers=False,
                                         log=lambda m: None, adapter_kwargs={"claude_bin": str(fake_claude.bin)})).run()
    adir = res.run_dir / "cells" / "alpha__bash-oracle__r0" / "attempt-1"
    assert (adir / "transcript.jsonl").exists()
    for f in adir.rglob("*"):
        if f.is_file() and not f.is_symlink():
            assert REAL.encode() not in f.read_bytes(), f
    assert "transcript.jsonl" in json.loads((adir / "credential_redactions.json").read_text())["files"]
    v = json.loads((adir / "verdict.json").read_text())
    assert v["audit"]["flagged"] is True and audit.F_CREDENTIAL in v["audit"]["reasons"]


@pytest.mark.parametrize("body,reason", [
    ({"model": PIN, "tools": [{"type": "web_search_20250305", "name": "web_search"}]}, "server-side tool"),
    ({"model": PIN, "tools": [{"type": "web_fetch_20250910", "name": "web_fetch"}]}, "server-side tool"),
    ({"model": PIN, "tools": [{"type": "code_execution_20250825", "name": "code_execution"}]}, "server-side tool"),
    ({"model": PIN, "tools": [{"name": "Read", "input_schema": {"type": "object"}},
                              {"type": "web_search_20250305", "name": "web_search"}]}, "server-side tool"),
    ({"model": PIN, "mcp_servers": [{"type": "url", "url": "https://example.com/mcp", "name": "x"}]},
     "'mcp_servers'"),
    ({"model": PIN, "container": "container_abc"}, "'container'"),
    ({"model": PIN, "tools": {"name": "Read"}}, "list of client tools"),
])
def test_server_side_tools_are_not_relayed(relay, body, reason):
    """PR #6 review: without this filter a bash agent could make the API reach the network for it (web search /
    fetch, remote MCP, code execution), routing around the egress allowlist through the relay."""
    r, api, log = relay
    n = len(api.requests)
    got, resp = _post(r.port, "/v1/messages", body, {"x-api-key": PLACEHOLDER})
    assert got == 403
    assert reason in json.loads(resp)["error"]["message"]
    assert len(api.requests) == n, "a refused request must never reach the upstream"
    assert _records(log)[-1]["allowed"] is False


def test_client_tools_are_relayed(relay):
    r, api, log = relay
    body = {"model": PIN, "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "Read", "description": "read", "input_schema": {"type": "object"}},
                      {"type": "custom", "name": "mcp__dbwm__run_model", "input_schema": {"type": "object"}}]}
    got, _ = _post(r.port, "/v1/messages", body, {"x-api-key": PLACEHOLDER})
    assert got == 200 and _records(log)[-1]["allowed"] is True


def test_server_side_refusal_unit():
    assert api_relay.server_side_refusal({"model": PIN}) is None
    assert api_relay.server_side_refusal({"model": PIN, "tools": []}) is None
    assert api_relay.server_side_refusal("not a dict") is None
    assert api_relay.server_side_refusal({"tools": [{"type": "bash_20250124", "name": "bash"}]})
