"""paths, regime and env: roots and overrides, repo detection, regime merge/validation, env allowlist."""

from __future__ import annotations


import pytest

from dbwm.core import env as envmod
from dbwm.core import paths
from dbwm.core import regime as regime_mod
from dbwm.core.types import Isolation, RegimeConfig


# -- paths ---------------------------------------------------------------------------


def test_roots_default_and_override(tmp_path, monkeypatch):
    for v in ("DBWM_DATA_DIR", "DBWM_PRIVATE_DIR", "DBWM_RUNS_DIR", "DBWM_SANDBOX_ROOT"):
        monkeypatch.delenv(v, raising=False)
    assert paths.data_root() == paths.PKG_ROOT / "data"
    assert paths.private("2", "x.json") == paths.PKG_ROOT / "private" / "eval2" / "x.json"
    monkeypatch.setenv("DBWM_PRIVATE_DIR", str(tmp_path / "priv"))
    monkeypatch.setenv("DBWM_SANDBOX_ROOT", str(tmp_path / "sb"))
    assert paths.private(3) == (tmp_path / "priv").resolve() / "eval3"
    assert paths.sandbox_root() == (tmp_path / "sb").resolve()


def test_eval_name():
    assert paths.eval_name(2) == "eval2"
    assert paths.eval_name("5") == "eval5"
    assert paths.eval_name("eval6") == "eval6"
    assert paths.eval_name("toy_eval") == "toy_eval"


def test_assert_outside_repos(tmp_path):
    clean = tmp_path / "clean" / "a"
    clean.mkdir(parents=True)
    paths.assert_outside_repos(clean)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "sub").mkdir()
    with pytest.raises(paths.InsideRepoError):
        paths.assert_outside_repos(repo / "sub")
    proj = tmp_path / "proj"
    (proj / "deep").mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("instructions")
    with pytest.raises(paths.InsideRepoError):
        paths.assert_outside_repos(proj / "deep")
    # The package checkout itself is inside a repo/project in development; a
    # sandbox there must be refused.
    gitfile = tmp_path / "wt"
    gitfile.mkdir()
    (gitfile / ".git").write_text("gitdir: elsewhere")  # worktree marker is a FILE
    with pytest.raises(paths.InsideRepoError):
        paths.assert_outside_repos(gitfile)


# -- regime --------------------------------------------------------------------------


def test_build_regime_precedence_and_extra(toy):
    r = regime_mod.build_regime(toy, {"model": "m1", "max_turns": 9, "arms": "no-tool", "foo": 1,
                                      "extra": {"bar": 2}, "reps": None})
    assert r.model == "m1" and r.max_turns == 9 and r.arms == ["no-tool"]
    assert r.wall_clock_s == 60  # from the eval's default_regime()
    assert r.reps == 1  # None override never clears a default
    assert r.extra == {"foo": 1, "bar": 2}
    assert r.isolation == Isolation.HOST
    assert len(r.regime_hash()) == 64
    assert RegimeConfig.from_json(r.to_json()) == r


def test_regime_refuses_missing_model(toy):
    with pytest.raises(regime_mod.RegimeError, match="no model"):
        regime_mod.build_regime(toy, {"adapter": "stub", "auth_mode": "none"})


def test_regime_isolation_rules(toy):
    base = {"model": "m", "adapter": "stub", "auth_mode": "none"}
    with pytest.raises(regime_mod.RegimeError, match="bash-oracle"):
        regime_mod.build_regime(toy, {**base, "arms": ["bash-oracle"], "isolation": "host"})
    r = regime_mod.build_regime(toy, {**base, "arms": ["bash-oracle"], "isolation": "host-unsafe"})
    assert not regime_mod.is_conforming(r)
    assert "host-unsafe" in regime_mod.nonconforming_reasons(r)[0]
    r = regime_mod.build_regime(toy, {**base, "arms": ["bash-oracle", "no-tool"], "isolation": "container"})
    assert regime_mod.is_conforming(r)
    r = regime_mod.build_regime(toy, {**base, "arms": ["no-tool", "mcp-oracle"], "isolation": "host"})
    assert regime_mod.is_conforming(r)


def test_command_adapter_at_host_is_nonconforming(toy):
    """--restricted/--tools confine claude_code at HOST and openai_compat implements
    sandbox-confined tools itself, but nothing confines an external command there."""
    base = {"model": "m", "auth_mode": "none", "arms": ["no-tool", "mcp-oracle"]}
    r = regime_mod.build_regime(toy, {**base, "adapter": "command", "isolation": "host"})
    reasons = regime_mod.nonconforming_reasons(r)
    assert not regime_mod.is_conforming(r) and len(reasons) == 1
    assert "adapter command at isolation host" in reasons[0] and "container" in reasons[0]
    r = regime_mod.build_regime(toy, {**base, "adapter": "command", "isolation": "container"})
    assert regime_mod.is_conforming(r)
    for adapter in ("claude_code", "openai_compat", "stub"):
        r = regime_mod.build_regime(toy, {**base, "adapter": adapter, "isolation": "host"})
        assert regime_mod.is_conforming(r), adapter
    r = regime_mod.build_regime(toy, {**base, "adapter": "command", "isolation": "host-unsafe"})
    assert len(regime_mod.nonconforming_reasons(r)) == 1  # host-unsafe already says it


def test_regime_rejects_unknowns(toy):
    with pytest.raises(regime_mod.RegimeError) as ei:
        regime_mod.build_regime(toy, {"model": "m", "adapter": "nope", "arms": ["zzz"], "ruleset": "v9",
                                      "instances": ["gamma"], "reps": 0})
    msg = str(ei.value)
    for frag in ("unknown arm", "unknown adapter", "unknown ruleset", "unknown instance", "reps must be"):
        assert frag in msg


def test_regime_session_auth_refused_in_container(toy):
    with pytest.raises(regime_mod.RegimeError, match="session"):
        regime_mod.build_regime(toy, {"model": "m", "adapter": "claude_code", "auth_mode": "session",
                                      "isolation": "container"})


def test_discover_evals_never_raises():
    found = regime_mod.discover_evals()
    assert set(found) == set(regime_mod.EVAL_IDS)


# -- env ----------------------------------------------------------------------------


HOME_U = "/" + "home/u"  # placeholder Linux HOME, assembled at run time (dbwm hygiene)
SRC = {"PATH": "/usr/bin", "HOME": HOME_U, "USER": "u", "LANG": "C.UTF-8", "TMPDIR": "/tmp",
       "CLAUDE_EFFORT": "xhigh", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "80000", "AWS_SECRET_ACCESS_KEY": "s",
       "ANTHROPIC_API_KEY": "sk-test", "FIREWORKS_API_KEY": "fw", "GITHUB_TOKEN": "g",
       "CLAUDE_CODE_OAUTH_TOKEN": "oat"}


def test_env_session_host_allowlist_only():
    env = envmod.build_agent_env("session", Isolation.HOST, source=SRC)
    assert env["HOME"] == HOME_U and env["USER"] == "u" and env["PATH"] == "/usr/bin"
    assert env["TERM"] == "dumb" and env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    for leaked in ("CLAUDE_EFFORT", "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "ANTHROPIC_API_KEY", "FIREWORKS_API_KEY",
                   "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert leaked not in env
    assert "DBWM_ORACLE" not in env


def test_env_api_key_isolated_config(tmp_path):
    env = envmod.build_agent_env("api_key", Isolation.HOST, harness_out=tmp_path, source=SRC)
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude-config")
    assert env["HOME"] == str(tmp_path / "home")
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "USER" not in env


def test_env_container():
    env = envmod.build_agent_env("oauth_token", Isolation.CONTAINER, proxy_url="http://dbwm-sidecar:3128",
                                 source=SRC)
    assert env["HOME"] == "/home/agent" and env["PATH"] == envmod.CONTAINER_PATH
    assert env["HTTPS_PROXY"] == "http://dbwm-sidecar:3128"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oat"
    assert "ANTHROPIC_API_KEY" not in env and HOME_U not in env.values()


def test_auth_problems():
    assert envmod.auth_problems("api_key", Isolation.HOST, {}) == [
        "auth mode 'api_key' requires ANTHROPIC_API_KEY in the environment"]
    assert envmod.auth_problems("none", Isolation.CONTAINER, {}) == []
    assert envmod.auth_problems("session", Isolation.CONTAINER, {})


def test_redact_and_tool_env(tmp_path):
    red = envmod.redact_env({"ANTHROPIC_API_KEY": "x", "PATH": "/bin", "DBWM_ORACLE_TOKEN": "t"})
    assert red == {"ANTHROPIC_API_KEY": "<redacted>", "PATH": "/bin", "DBWM_ORACLE_TOKEN": "<redacted>"}
    te = envmod.build_tool_env(tmp_path, oracle_address="tcp://h:1", oracle_token="t")
    assert te["PWD"] == str(tmp_path) and te["DBWM_ORACLE"] == "tcp://h:1"
    assert not any("KEY" in k for k in te)


def test_bash_timeout_env():
    from dbwm.core.env import bash_timeout_env

    assert bash_timeout_env(None) == {}
    assert bash_timeout_env(120) == {"BASH_DEFAULT_TIMEOUT_MS": "120000", "BASH_MAX_TIMEOUT_MS": "120000",
                                     "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}
    assert bash_timeout_env(1.5)["BASH_MAX_TIMEOUT_MS"] == "1500"
    for bad in (0, -1, True, "120"):
        with pytest.raises(ValueError):
            bash_timeout_env(bad)


@pytest.mark.parametrize("auth,iso", [("session", Isolation.HOST), ("api_key", Isolation.HOST),
                                      ("oauth_token", Isolation.CONTAINER), ("none", Isolation.HOST)])
def test_env_disables_cli_autoupdate(tmp_path, auth, iso):
    """Review M2: a CLI that updates itself mid-run would change the instrument between attempts."""
    env = envmod.build_agent_env(auth, iso, harness_out=tmp_path, source=SRC)
    assert env["DISABLE_AUTOUPDATER"] == "1"


def test_command_adapter_at_container_with_a_real_credential_is_nonconforming(toy):
    """PR #6 review: the API relay covers claude_code only, so a command agent in the container is handed the
    real credential; like claude_code with the relay disabled, that run is not conforming."""
    base = {"model": "m", "arms": ["no-tool", "mcp-oracle"], "adapter": "command", "isolation": "container"}
    for auth in ("api_key", "oauth_token", "bedrock", "vertex"):
        r = regime_mod.build_regime(toy, {**base, "auth_mode": auth}, validate=False)
        reasons = regime_mod.nonconforming_reasons(r)
        assert len(reasons) == 1 and "adapter command at isolation container" in reasons[0], auth
        assert "real credential" in reasons[0]
    r = regime_mod.build_regime(toy, {**base, "auth_mode": "none"})
    assert regime_mod.is_conforming(r)
    r = regime_mod.build_regime(toy, {**base, "adapter": "claude_code", "auth_mode": "api_key"}, validate=False)
    assert regime_mod.is_conforming(r)  # the relay covers it
