"""Reference adapter: Claude Code (``claude -p``) under an explicit, verified regime.

Command (prompt on STDIN, cwd = sandbox):

    claude -p --model <M> [--effort E] --tools <T> --allowedTools <T>
      --permission-prompts none --restricted --strict-mcp-config
      [--mcp-config <harness_out>/mcp.json] --disable-slash-commands
      --no-session-persistence --max-turns <K> [--max-budget-usd <usd>]
      --output-format stream-json --verbose
      [--bare only if auth_mode=api_key AND regime.extra["claude_bare"] is true]

``<T>`` is the arm's builtins mapped to Read/Write/Edit/Bash/Glob/Grep plus
``mcp__dbwm__<op>`` for MCP oracle tools. Never ``--fallback-model``,
``--continue``, ``--resume`` or ``--dangerously-skip-permissions``.

Why each piece (paper behaviour -> fix):
  --tools/--restricted   ``--allowedTools`` alone was additive to the operator's
                         settings: "no-Bash" arms had Bash.
  --strict-mcp-config    only the dbwm MCP server, never the operator's.
  --disable-slash-commands, --restricted
                         operator skills, plugins and CLAUDE.md reached agents.
  --model required       an unpinned CLI default can change mid-batch.
  --max-turns            a dollar budget and a clock bound differently per model
                         (paper v7 Appendix A entry 14): pin the iterations,
                         let cost float and record it as an outcome.
  stdin prompt           argv length limits; uniform across evals.

Runtime assertions on the init event (any failure kills the agent at once
and makes the attempt INFRA_INVALID_REGIME, never scored): tools == expected
exactly; cwd == sandbox (realpath, so /tmp vs /private/tmp is fine; literal
``/work`` in the container); model matches the pin (alias -> full id allowed,
both recorded); skills == []; mcp_servers == ["dbwm"] (connected) for MCP
arms, [] otherwise; every ``memory_paths`` entry (the CLI's auto-memory
directory) absent, under the attempt's isolated HOME/CLAUDE_CONFIG_DIR
(or the container's private home), or the project directory keyed to this
sandbox and empty at launch (session auth at ``host``). After the run every
assistant message's model must match the pin too. ``permissionMode`` and the
memory paths are recorded in agent_result.json and the verdict
(``details.agent_init``).

Why the memory assertion (paper v7 Appendix A entries 10, 11, 17; D.4): the
operator's auto-memory directory for the harness repository was in context
for every eval-1 and eval-2 session, because the agents ran inside that
checkout; its index named eval 2 and its overclaim metric. The CLI keys the
directory to the working directory, so a fresh sandbox outside any repository
gets an empty one; the assertion makes that a checked property instead of an
accident. ``--bare`` would disable auto-memory but drops Write/Glob/Grep on
2.1.287, so it stays opt-in.

Harness backstops: max_turns+2 distinct assistant message ids -> kill
("turns"); wall clock -> SIGTERM then SIGKILL of the process group ("clock").
``claude --version`` is recorded per attempt, and whether it is one of
VERIFIED_CLI_VERSIONS (the isolation above rests on CLI behaviour checked for
those versions only; a newer CLI still runs, but manifest, verdicts and the
report say ``cli_version_verified: false``).

Why no ``--bare`` by default (review finding, 2026-10-02): the original design had ``--bare`` for api_key auth. Checked with the real CLI 2.1.287
against a local fake Messages API (dummy key, all non-loopback egress denied,
no model call): ``--bare`` keeps the ``--mcp-config`` server, but it is
"minimal mode" (CLAUDE_CODE_SIMPLE=1) and silently reduces the builtin tools to
Bash/Edit/Read (``--tools Read,Write`` -> init.tools ``["Read"]``), so every
api_key attempt failed the init assertion (INFRA_INVALID_REGIME, three in a
row halt the run). Without ``--bare`` an api_key run has the exact tool list
and ``apiKeySource == "ANTHROPIC_API_KEY"``; the isolated HOME and
CLAUDE_CONFIG_DIR plus ``--restricted`` already exclude operator settings,
hooks and CLAUDE.md. ``regime.extra["claude_bare"] = true`` turns it back on
(only arms whose tools are a subset of Read/Edit/Bash can pass the init check
then).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dbwm.core import stream_json
from dbwm.core.adapters.base import STDERR, TRANSCRIPT, write_json
from dbwm.core.budget import run_supervised
from dbwm.core.container import AGENT_WORKDIR, TOOLS_MOUNT
from dbwm.core.env import CONTAINER_HOME, redact_env
from dbwm.core.sandbox import MCP_CONFIG_FILENAME, MCP_SERVER_NAME, write_mcp_tools
from dbwm.core.types import AgentRequest, AgentResult, Isolation

FORBIDDEN_FLAGS = ("--fallback-model", "--continue", "--resume", "--dangerously-skip-permissions")

# Claude Code versions on which the isolation-relevant behaviour (--restricted, --tools, --disable-slash-commands,
# --strict-mcp-config/--mcp-config, the init event keys, --bare's tool
# reduction) was verified. Add a version only after re-running those checks
# (tests/core/test_core_cli_fake_api.py with DBWM_CLI_FAKE_API=1 covers the
# tool/MCP part without any model call).
VERIFIED_CLI_VERSIONS = ("2.1.287",)

# --bare under CLAUDE_CODE_SIMPLE keeps only these builtins (2.1.287, see the
# module docstring).
BARE_BUILTINS = frozenset({"Bash", "Edit", "Read"})


def use_bare(req: AgentRequest) -> bool:
    """``--bare`` only for api_key auth and only when ``regime.extra["claude_bare"]`` is true."""
    rx = req.extra.get("regime_extra") or {}
    return req.auth_mode == "api_key" and rx.get("claude_bare") is True


def cli_version_verified(text: str | None) -> bool | None:
    """True/False: is ``claude --version`` output one of VERIFIED_CLI_VERSIONS; None if unknown."""
    v = parse_version(text)
    if v is None:
        return None
    return v in VERIFIED_CLI_VERSIONS


def build_argv(req: AgentRequest, *, claude_bin: str = "claude", mcp_config: str | None = None) -> list[str]:
    tools = ",".join(stream_json.claude_tool_names(req.tools))
    argv = [claude_bin, "-p", "--model", req.model]
    if req.effort:
        argv += ["--effort", req.effort]
    argv += ["--tools", tools, "--allowedTools", tools,
             "--permission-prompts", "none", "--restricted", "--strict-mcp-config"]
    if mcp_config:
        argv += ["--mcp-config", mcp_config]
    argv += ["--disable-slash-commands", "--no-session-persistence",
             "--max-turns", str(int(req.budget.max_turns))]
    if req.budget.usd_rail is not None:
        argv += ["--max-budget-usd", f"{req.budget.usd_rail:g}"]
    argv += ["--output-format", "stream-json", "--verbose"]
    if use_bare(req):
        argv.append("--bare")
    for f in FORBIDDEN_FLAGS:
        assert f not in argv
    return argv


def init_expectations(req: AgentRequest, in_container: bool) -> stream_json.InitExpectations:
    mcp = req.tools.transport == "mcp" and bool(req.tools.oracle_tools)
    return stream_json.InitExpectations(
        tools=stream_json.claude_tool_names(req.tools),
        cwd=AGENT_WORKDIR if in_container else str(req.sandbox),
        model=req.model,
        mcp_servers=[MCP_SERVER_NAME] if mcp else [],
        resolve_cwd=not in_container)


# -- operator auto-memory -------------------------------------------------

MEMORY_KEY = "memory_paths"
MEMORY_STATUSES = ("absent", "isolated", "sandbox_project", "foreign", "malformed")


@dataclass(frozen=True)
class MemoryPolicy:
    """Where the CLI's auto-memory directory may be for this attempt.

    ``isolated_roots``: directories private to the attempt (the isolated HOME and CLAUDE_CONFIG_DIR of
    api_key/oauth_token auth, the isolated CLAUDE_CONFIG_DIR of bedrock/vertex, the container's home); any
    memory path below one of them is acceptable. ``sandbox_cwds``: the working directory as the CLI may
    spell it; a memory path ``<config>/projects/<slug of that cwd>/memory`` is acceptable anywhere (the
    host config dir of session auth) if it is empty at launch. ``in_container``: paths are compared
    literally and their contents cannot be checked from the host.
    """

    isolated_roots: tuple[str, ...]
    sandbox_cwds: tuple[str, ...]
    in_container: bool = False


def project_slug(path: str) -> str:
    """Claude Code's project-directory name for a working directory: every non-alphanumeric ASCII character
    becomes '-' (``/private/var/x/bwm_d1`` -> ``-private-var-x-bwm-d1``, as 2.1.287 reports it)."""
    return "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in path)


def memory_policy(req: AgentRequest, in_container: bool) -> MemoryPolicy:
    if in_container:
        return MemoryPolicy(isolated_roots=(CONTAINER_HOME,), sandbox_cwds=(AGENT_WORKDIR,), in_container=True)
    roots: list[str] = []
    if req.auth_mode in ("api_key", "oauth_token"):
        roots += [req.env.get("HOME") or "", req.env.get("CLAUDE_CONFIG_DIR") or ""]
    elif req.auth_mode in ("bedrock", "vertex"):
        roots.append(req.env.get("CLAUDE_CONFIG_DIR") or "")  # HOME is the host's (credentials)
    cwds = [str(req.sandbox), os.path.realpath(str(req.sandbox))]
    return MemoryPolicy(isolated_roots=tuple(dict.fromkeys(r for r in roots if r)),
                        sandbox_cwds=tuple(dict.fromkeys(cwds)), in_container=False)


def _memory_entries(value: Any) -> list[tuple[str, Any]] | None:
    """``memory_paths`` as (key, path) pairs; None if the shape is not a dict/list/str."""
    if isinstance(value, Mapping):
        return [(str(k), v) for k, v in value.items()]
    if isinstance(value, list):
        return [(str(i), v) for i, v in enumerate(value)]
    if isinstance(value, str):
        return [("", value)]
    return None


def _under(path: str, root: str, literal: bool) -> bool:
    variants = {(os.path.normpath(path), os.path.normpath(root))}
    if not literal:
        variants.add((os.path.realpath(path), os.path.realpath(root)))
    return any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for p, r in variants)


def _is_sandbox_project(path: str, cwds: tuple[str, ...]) -> bool:
    norm = os.path.normpath(path)
    mem, proj = os.path.basename(norm), os.path.dirname(norm)
    return (mem == "memory" and os.path.basename(os.path.dirname(proj)) == "projects"
            and os.path.basename(proj) in {project_slug(c) for c in cwds})


def check_memory_paths(init: Mapping[str, Any] | None, policy: MemoryPolicy, *,
                       check_empty: bool = True) -> tuple[list[str], dict[str, Any]]:
    """(assertion failures, record) for the init event's ``memory_paths``.

    Every entry must be (a) below one of ``policy.isolated_roots``, or (b) the project directory keyed to
    the sandbox (``.../projects/<slug>/memory``). At ``host`` either must also be empty or absent when the
    init event arrives (``check_empty``): a pre-filled memory directory would be in the agent's context.
    The key missing, None or empty is ``absent`` (acceptable: nothing is loaded, or a CLI that does not
    report it). The record carries the status per entry and overall, never file contents.
    """
    rec: dict[str, Any] = {"status": "absent", "entries": {}}
    if init is None or init.get(MEMORY_KEY) in (None, {}, [], ""):
        return [], rec
    entries = _memory_entries(init.get(MEMORY_KEY))
    if entries is None:
        rec["status"] = "malformed"
        return [f"init.{MEMORY_KEY} has an unexpected shape: {type(init.get(MEMORY_KEY)).__name__}"], rec
    fails: list[str] = []
    kinds: list[str] = []
    for key, p in entries:
        label = f"init.{MEMORY_KEY}" + (f".{key}" if key else "")
        if not isinstance(p, str) or not p:
            kinds.append("malformed")
            rec["entries"][key] = {"kind": "malformed"}
            fails.append(f"{label} is not a path: {p!r}")
            continue
        if any(_under(p, r, policy.in_container) for r in policy.isolated_roots):
            kind = "isolated"
        elif _is_sandbox_project(p, policy.sandbox_cwds):
            kind = "sandbox_project"
        else:
            kind = "foreign"
        info: dict[str, Any] = {"kind": kind}
        if kind == "foreign":
            fails.append(f"{label} {p!r} is neither under the attempt's isolated config/home nor the project "
                         "directory of this sandbox: the operator's auto-memory could be in context")
        elif check_empty and not policy.in_container:
            try:
                n = len(os.listdir(p)) if os.path.isdir(p) else 0
            except OSError as e:
                n = None
                fails.append(f"{label} {p!r} cannot be listed at launch: {e}")
            info["entries_at_launch"] = n
            if n:
                fails.append(f"{label} {p!r} is not empty at launch ({n} entries): its contents would be in "
                             "the agent's context")
        kinds.append(kind)
        rec["entries"][key] = info
    for status in ("malformed", "foreign", "sandbox_project", "isolated"):
        if status in kinds:
            rec["status"] = status
            break
    return fails, rec


def init_record(res: AgentResult) -> dict[str, Any]:
    """The path-free summary of the init event the verdict carries (``details.agent_init``)."""
    mc = res.memory_check or {}
    return {"permission_mode": res.permission_mode,
            "memory_paths": mc.get("status", "absent"),
            "memory_keys": sorted((mc.get("entries") or {}).keys())}


class ClaudeCodeAdapter:
    name = "claude_code"

    def __init__(self, claude_bin: str | None = None, *, version_timeout_s: float = 30):
        self.claude_bin = claude_bin or os.environ.get("DBWM_CLAUDE_BIN") or "claude"
        self.version_timeout_s = version_timeout_s
        self._version_cache: dict[tuple, str | None] = {}

    # -- helpers ------------------------------------------------------------------

    def cli_version(self, env: dict[str, str], container: Any = None) -> str | None:
        key = (self.claude_bin, getattr(container, "image", None),
               tuple(getattr(container, "extra_mounts", ()) or ()))
        if key in self._version_cache:
            return self._version_cache[key]
        ver: str | None
        try:
            if container is not None:
                rc, out, err = container.run_once([self.claude_bin, "--version"], timeout=self.version_timeout_s)
                ver = out.strip() if rc == 0 else None
            else:
                p = subprocess.run([self.claude_bin, "--version"], capture_output=True, text=True,
                                   env=env, timeout=self.version_timeout_s)
                ver = p.stdout.strip() if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            ver = None
        self._version_cache[key] = ver
        return ver

    @staticmethod
    def _python_for_mcp(env: dict[str, str]) -> str:
        """``python3`` if the agent's PATH has one (shims are stdlib-only, 3.8+), else this interpreter."""
        if shutil.which("python3", path=env.get("PATH")):
            return "python3"
        return sys.executable

    # -- run ----------------------------------------------------------------------

    def run(self, req: AgentRequest) -> AgentResult:
        # Absolute paths only: the CLI resolves --mcp-config against its own cwd
        # (the sandbox), so a relative harness_out would point into the sandbox.
        out = Path(req.harness_out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        tpath, epath = out / TRANSCRIPT, out / STDERR
        container = req.extra.get("container")
        in_container = req.isolation == Isolation.CONTAINER
        res = AgentResult(transcript_path=tpath, stderr_path=epath, returncode=None,
                          model_requested=req.model)
        if in_container and container is None:
            epath.write_text("[dbwm] CONTAINER isolation requested but no container session was set up\n")
            tpath.write_text("")
            res.error_text = "no container session"
            return res

        mcp_config: str | None = None
        if req.tools.transport == "mcp" and req.tools.oracle_tools:
            if not req.oracle_address or not req.oracle_token:
                res.assertion_failures = ["MCP arm has oracle tools but no oracle server address"]
                tpath.write_text("")
                epath.write_text("")
                return res
            if in_container:
                path = write_mcp_tools(container.tools_dir, req.tools.oracle_tools,
                                       address=req.oracle_address, token=req.oracle_token,
                                       python="python3", agent_tools_dir=TOOLS_MOUNT)
                mcp_config = f"{TOOLS_MOUNT}/{path.name}"
            else:
                path = write_mcp_tools(out / "tools", req.tools.oracle_tools,
                                       address=req.oracle_address, token=req.oracle_token,
                                       python=self._python_for_mcp(req.env))
                dst = out / MCP_CONFIG_FILENAME
                shutil.copyfile(path, dst)
                os.chmod(dst, 0o600)
                path.unlink()
                mcp_config = str(dst)

        env = dict(req.env)
        if req.tools.transport == "cli" and req.tools.oracle_tools and req.oracle_address:
            env["DBWM_ORACLE"] = req.oracle_address
            env["DBWM_ORACLE_TOKEN"] = req.oracle_token or ""

        argv = build_argv(req, claude_bin=self.claude_bin, mcp_config=mcp_config)
        exp = init_expectations(req, in_container)
        mem_policy = memory_policy(req, in_container)
        res.cli_version = self.cli_version(env, container if in_container else None)

        if in_container:
            full_argv, client_env = container.agent_command(argv, env)
            cwd = str(out)
            on_kill = container.kill_agent
        else:
            full_argv, client_env, cwd, on_kill = argv, env, str(req.sandbox), None

        write_json(out / "agent_invocation.json", {
            "adapter": self.name, "argv": argv, "cwd": exp.cwd, "env": redact_env(env),
            "isolation": req.isolation.value, "cli_version": res.cli_version,
            "cli_version_verified": cli_version_verified(res.cli_version),
            "expected_init": {"tools": list(exp.tools), "cwd": exp.cwd, "model": exp.model,
                              "mcp_servers": list(exp.mcp_servers),
                              "memory_paths": {"isolated_roots": list(mem_policy.isolated_roots),
                                               "sandbox_project_slugs": sorted(
                                                   {project_slug(c) for c in mem_policy.sandbox_cwds})}},
            "docker_argv": [a for a in full_argv] if in_container else None,
        })

        early: dict[str, Any] = {}

        def on_event(ev: Any) -> str | None:
            if isinstance(ev, dict) and ev.get("type") == "system" and ev.get("subtype") == "init" \
                    and "fails" not in early:
                fails = stream_json.check_init(ev, exp)
                # The memory directory is checked when the init event arrives, before the agent's
                # first tool call, so "empty at launch" means what it says.
                mfails, mrec = check_memory_paths(ev, mem_policy, check_empty=True)
                early["memory"] = (mfails, mrec)
                fails = fails + mfails
                early["fails"] = fails
                if fails:
                    return "invalid_regime"
            return None

        sup = run_supervised(full_argv, cwd=cwd, env=client_env, stdin_data=req.prompt.encode("utf-8"),
                             stdout_path=tpath, stderr_path=epath,
                             wall_clock_s=req.budget.wall_clock_s, max_turns=req.budget.max_turns,
                             on_event=on_event, on_kill=on_kill,
                             abort_event=req.extra.get("abort_event"))
        res.returncode = sup.returncode
        res.killed_by_harness = sup.killed_by
        res.duration_s = sup.duration_s
        if sup.start_error:
            res.error_text = sup.start_error
        summary = stream_json.parse_file(tpath, sup.timestamps)
        stream_json.fill_result(summary, res)

        fails: list[str] = []
        if summary.init is not None or summary.result is not None:
            fails.extend(stream_json.check_init(summary.init, exp))
        if summary.init is not None:
            res.permission_mode = summary.init.get("permissionMode") if isinstance(
                summary.init.get("permissionMode"), str) else None
            res.memory_paths = summary.init.get(MEMORY_KEY)
            # The launch-time reading if the init event was seen live; otherwise (it never is not, but a
            # supervisor change must not silently drop the check) the same check now.
            mfails, mrec = early.get("memory") or check_memory_paths(summary.init, mem_policy, check_empty=True)
            res.memory_check = mrec
            fails.extend(mfails)
        fails.extend(stream_json.check_served_models(summary, req.model))
        res.assertion_failures = list(dict.fromkeys(fails))
        return res


def parse_version(text: str | None) -> str | None:
    """'2.1.287 (Claude Code)' -> '2.1.287'."""
    if not text:
        return None
    return text.split()[0]


__all__ = ["BARE_BUILTINS", "MEMORY_KEY", "MEMORY_STATUSES", "VERIFIED_CLI_VERSIONS", "ClaudeCodeAdapter",
           "MemoryPolicy", "build_argv", "check_memory_paths", "cli_version_verified", "init_expectations",
           "init_record", "memory_policy", "parse_version", "project_slug", "use_bare"]
