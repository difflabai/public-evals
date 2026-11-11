"""Environment allowlist for agent processes, and the auth modes.

Why an allowlist: the paper harness passed ``dict(os.environ)`` minus a few
keys to ``claude -p`` (coupled_planner.py:295-334, planner.py:155-163). A trial
launched from inside a Claude Code session inherited ``CLAUDE_EFFORT=xhigh``
and ``CLAUDE_CODE_MAX_OUTPUT_TOKENS=80000`` and behaved like a different
instrument (ledger G03); the later fix stripped every ``CLAUDE_*`` variable,
which also stripped the auth variables standalone users need. Operator secrets
reached agent tool execution (ledger N13). Here the agent environment is built
from nothing: only the names listed below are ever passed, plus the variables
of the one auth mode the regime names.

Auth modes:
    session      the operator's logged-in Claude Code (keychain); host HOME and
                 USER are passed so the CLI can find it. HOST isolation only: it
                 cannot work under an isolated config dir ("Not logged in").
    api_key      ANTHROPIC_API_KEY; isolated HOME and CLAUDE_CONFIG_DIR. (No
                 --bare by default: it drops Write/Glob/Grep; see
                 dbwm.core.adapters.claude_code.)
    oauth_token  CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`); isolated
                 HOME and CLAUDE_CONFIG_DIR.
    bedrock      CLAUDE_CODE_USE_BEDROCK + the documented AWS_* variables;
                 host HOME (for ~/.aws) at HOST, isolated CLAUDE_CONFIG_DIR.
    vertex       CLAUDE_CODE_USE_VERTEX + the documented Google variables;
                 host HOME (for gcloud credentials) at HOST, isolated config dir.
    none         no credentials (stub, replay and most command agents).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

from dbwm.core.types import Isolation

AUTH_MODES = ("session", "api_key", "oauth_token", "bedrock", "vertex", "none")

# Passed through from the harness environment when present (HOST isolation).
PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")

# Set on every agent process regardless of the harness environment.
FIXED = {
    "TERM": "dumb",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    # The isolation rests on CLI behaviour verified for specific versions
    # (claude_code.VERIFIED_CLI_VERSIONS); a CLI that updates itself mid-run
    # would change the instrument between attempts (review M2). The image sets
    # the same variable (docker/Dockerfile).
    "DISABLE_AUTOUPDATER": "1",
}

# The variables each auth mode may pass. "required" must be present in the
# harness environment or the run refuses to start.
AUTH_VARS: dict[str, dict[str, tuple[str, ...]]] = {
    # USER: the macOS keychain item is looked up by account name. Verified on
    # 2.1.287: `claude auth status` reports loggedIn=false without USER and
    # true with it (HOME alone is not enough).
    "session": {"required": (), "optional": ("USER", "LOGNAME")},
    "api_key": {"required": ("ANTHROPIC_API_KEY",), "optional": ()},
    "oauth_token": {"required": ("CLAUDE_CODE_OAUTH_TOKEN",), "optional": ()},
    "bedrock": {
        "required": ("CLAUDE_CODE_USE_BEDROCK",),
        "optional": ("AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_ACCESS_KEY_ID",
                     "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK",
                     "ANTHROPIC_BEDROCK_BASE_URL", "CLAUDE_CODE_SKIP_BEDROCK_AUTH"),
    },
    "vertex": {
        "required": ("CLAUDE_CODE_USE_VERTEX",),
        "optional": ("CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID",
                     "GOOGLE_APPLICATION_CREDENTIALS", "ANTHROPIC_VERTEX_BASE_URL",
                     "CLAUDE_CODE_SKIP_VERTEX_AUTH"),
    },
    "none": {"required": (), "optional": ()},
}

# Inside the agent container (docker/Dockerfile).
CONTAINER_PATH = "/usr/local/bin:/usr/bin:/bin"
CONTAINER_HOME = "/home/agent"
CONTAINER_TMP = "/tmp"

_SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)


class AuthError(RuntimeError):
    pass


def is_secret_name(name: str) -> bool:
    """True for variable names that carry credentials (never put their values in argv or logs)."""
    return bool(_SECRET_RE.search(name))


def auth_problems(auth_mode: str, isolation: Isolation, source: Mapping[str, str] | None = None) -> list[str]:
    """Reasons this auth mode cannot run under this isolation with this environment."""
    src = os.environ if source is None else source
    probs: list[str] = []
    if auth_mode not in AUTH_MODES:
        return [f"unknown auth mode {auth_mode!r}; choose one of {', '.join(AUTH_MODES)}"]
    if auth_mode == "session" and isolation == Isolation.CONTAINER:
        probs.append("auth mode 'session' uses the host keychain and works only at HOST isolation; "
                     "use --auth api_key (ANTHROPIC_API_KEY) or --auth oauth_token "
                     "(CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`) for CONTAINER")
    for name in AUTH_VARS[auth_mode]["required"]:
        if not src.get(name):
            probs.append(f"auth mode {auth_mode!r} requires {name} in the environment")
    return probs


def auth_env(auth_mode: str, source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Only the variables of ``auth_mode`` that are present in ``source``."""
    src = os.environ if source is None else source
    spec = AUTH_VARS.get(auth_mode)
    if spec is None:
        raise AuthError(f"unknown auth mode {auth_mode!r}")
    out = {}
    for name in spec["required"] + spec["optional"]:
        if src.get(name):
            out[name] = src[name]
    return out


def build_agent_env(
    auth_mode: str,
    isolation: Isolation,
    *,
    harness_out: Path | None = None,
    proxy_url: str | None = None,
    source: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The complete environment of an agent process. Nothing else is inherited.

    ``harness_out`` hosts the isolated HOME/CLAUDE_CONFIG_DIR for non-session
    auth at HOST isolation (it is the attempt directory, never the sandbox).
    ``proxy_url`` is the sidecar egress proxy (CONTAINER only). ``extra`` is
    for test hooks and adapter-specific variables; the runner records its keys.
    Oracle variables (DBWM_ORACLE, DBWM_ORACLE_TOKEN) are added by the
    adapter, and only for bash arms.
    """
    src = os.environ if source is None else source
    env: dict[str, str] = {}
    if isolation == Isolation.CONTAINER:
        env["PATH"] = CONTAINER_PATH
        env["HOME"] = CONTAINER_HOME
        env["TMPDIR"] = CONTAINER_TMP
        env["LANG"] = "C.UTF-8"
        if auth_mode != "none":
            env["CLAUDE_CONFIG_DIR"] = f"{CONTAINER_HOME}/.claude"
        if proxy_url:
            env["HTTPS_PROXY"] = proxy_url
            env["https_proxy"] = proxy_url
            env["HTTP_PROXY"] = proxy_url
            env["http_proxy"] = proxy_url
    else:
        for name in PASSTHROUGH:
            if src.get(name):
                env[name] = src[name]
        if auth_mode in ("api_key", "oauth_token", "bedrock", "vertex"):
            if harness_out is None:
                raise AuthError(f"auth mode {auth_mode!r} needs harness_out for an isolated config dir")
            cfg = Path(harness_out) / "claude-config"
            cfg.mkdir(parents=True, exist_ok=True)
            env["CLAUDE_CONFIG_DIR"] = str(cfg)
            if auth_mode in ("api_key", "oauth_token"):
                home = Path(harness_out) / "home"
                home.mkdir(parents=True, exist_ok=True)
                env["HOME"] = str(home)
    env.update(FIXED)
    if auth_mode != "none":
        env.update(auth_env(auth_mode, src))
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def bash_timeout_env(command_timeout_s: float | int | None) -> dict[str, str]:
    """Claude Code variables that enforce a per-command Bash limit (empty when there is none).

    Verified in the claude 2.1.287 binary (read-only scan, 2026-10-02):
    ``cSe()`` returns BASH_DEFAULT_TIMEOUT_MS (else 120000) and ``dSe()``
    returns max(BASH_MAX_TIMEOUT_MS, default) (else max(600000, default));
    the Bash tool clamps a model-chosen ``timeout`` with
    ``Math.min(timeout > 0 ? timeout : cSe(), max)``. Setting both to the
    eval's limit therefore pins every command to it: the agent cannot raise
    one call's timeout to the 10-minute default maximum.
    ``run_in_background`` would escape any per-command limit, so it is
    disabled with CLAUDE_CODE_DISABLE_BACKGROUND_TASKS (the binary's ``Za()``
    gate: ``backgroundTasksDisabled || CLAUDE_CODE_DISABLE_BACKGROUND_TASKS``).
    """
    if command_timeout_s is None:
        return {}
    if isinstance(command_timeout_s, bool) or not isinstance(command_timeout_s, (int, float)) \
            or not command_timeout_s > 0:
        raise ValueError(f"command_timeout_s must be a positive number of seconds, got {command_timeout_s!r}")
    ms = str(int(round(float(command_timeout_s) * 1000)))
    return {"BASH_DEFAULT_TIMEOUT_MS": ms, "BASH_MAX_TIMEOUT_MS": ms, "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}


def build_tool_env(sandbox: Path | str, *, home: str | None = None, path: str | None = None,
                   oracle_address: str | None = None, oracle_token: str | None = None) -> dict[str, str]:
    """Minimal environment for executing an agent's shell command (no credentials).

    Used by adapters that run tool calls themselves (openai_compat run_bash).
    The model API key never appears here (ledger N13).
    """
    env = {
        "PATH": path or os.environ.get("PATH", CONTAINER_PATH),
        "HOME": home or str(sandbox),
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        "PWD": str(sandbox),
    }
    if oracle_address and oracle_token:
        env["DBWM_ORACLE"] = oracle_address
        env["DBWM_ORACLE_TOKEN"] = oracle_token
    return env


def credential_values(auth_mode: str, source: Mapping[str, str] | None = None) -> list[str]:
    """The credential values of ``auth_mode`` present in ``source`` (for redaction; never logged)."""
    src = os.environ if source is None else source
    spec = AUTH_VARS.get(auth_mode) or {}
    names = [*spec.get("required", ()), *spec.get("optional", ())]
    return [src[n] for n in names if is_secret_name(n) and src.get(n)]


RELAY_NO_PROXY = "dbwm-sidecar"


def relay_agent_env(env: Mapping[str, str], credential_var: str, placeholder: str,
                    relay_url: str) -> dict[str, str]:
    """The agent environment with the real credential replaced by the relay's placeholder.

    Red-team finding RT-E: the CLI's Bash tool inherits the CLI's environment,
    so at CONTAINER isolation the agent must never hold the real credential.
    ``ANTHROPIC_BASE_URL`` points the CLI at the sidecar's API relay
    (dbwm.core.api_relay), which swaps the placeholder for the real credential;
    ``NO_PROXY`` keeps that plain-HTTP call off the CONNECT-only egress proxy.
    """
    out = dict(env)
    out[credential_var] = placeholder
    out["ANTHROPIC_BASE_URL"] = relay_url
    out["NO_PROXY"] = out["no_proxy"] = RELAY_NO_PROXY
    return out


def redact_env(env: Mapping[str, str]) -> dict[str, str]:
    """A copy safe to write to disk: credential values replaced by '<redacted>'."""
    return {k: ("<redacted>" if is_secret_name(k) else v) for k, v in env.items()}
