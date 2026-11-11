"""Generic external agent: run any command in the sandbox under the same regime.

Contract for the command (what a third-party agent must do to be scored):

  * It is started with cwd = the sandbox (``/work`` at CONTAINER isolation,
    where it runs inside the agent container: it must exist in the image or
    in the sandbox) and an allowlisted environment plus:
        DBWM_PROMPT_FILE   path to a file holding the prompt (outside the
                           sandbox at HOST; ``/dbwm-tools/prompt.txt`` in the
                           container)
        DBWM_SANDBOX       the sandbox path (== cwd)
        DBWM_TOOLS         comma list of permitted builtin tools (read, write,
                           edit, bash, glob, grep) followed by oracle ops
        DBWM_MAX_TURNS     the turn budget (the agent should stop itself)
        DBWM_ORACLE, DBWM_ORACLE_TOKEN   only if the arm has oracle tools; the
                           protocol is in dbwm/core/oracle/server.py and the
                           shims in the sandbox (bash arms) speak it already
  * It writes the eval's deliverables into the sandbox.
  * Optionally it prints JSON lines on stdout for metrics, in the Claude
    stream-json subset (stream_json.py) or the simplified form
        {"type": "tool_use", "name": "<tool>", "input": {...}}
        {"type": "assistant"}                       one per agent turn
        {"type": "result", "subtype": "success", "is_error": false,
         "num_turns": N, "total_cost_usd": X}       last line
    Other stdout lines are kept in the transcript and ignored.
  * Exit status 0 means it finished. Without a result line, exit 0 is
    recorded as subtype "success" and non-zero as a CRASH.

The harness enforces the wall clock (process-group kill -> CLOCK_STOP) and a
turn backstop at DBWM_MAX_TURNS+2 "assistant" lines. The command is never
given credentials unless the regime's auth mode passes them; it must not need
anything else from the host environment.
"""

from __future__ import annotations

import json
import shlex
import time
from pathlib import Path
from typing import Sequence

from dbwm.core import stream_json
from dbwm.core.adapters.base import stderr_path, transcript_path, write_json
from dbwm.core.budget import run_supervised
from dbwm.core.container import AGENT_WORKDIR, TOOLS_MOUNT
from dbwm.core.env import redact_env
from dbwm.core.types import BUILTIN_TOOLS, AgentRequest, AgentResult, Isolation


class CommandAdapter:
    name = "command"

    def __init__(self, command: Sequence[str] | str | None = None):
        if isinstance(command, str):
            command = shlex.split(command)
        self.command = list(command) if command else None

    def _command(self, req: AgentRequest) -> list[str] | None:
        cmd = req.extra.get("command") or self.command
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        return list(cmd) if cmd else None

    def run(self, req: AgentRequest) -> AgentResult:
        out = Path(req.harness_out)
        out.mkdir(parents=True, exist_ok=True)
        tpath, epath = transcript_path(req), stderr_path(req)
        res = AgentResult(transcript_path=tpath, stderr_path=epath, returncode=None,
                          model_requested=req.model)
        cmd = self._command(req)
        container = req.extra.get("container")
        in_container = req.isolation == Isolation.CONTAINER
        if not cmd or (in_container and container is None):
            res.error_text = "no command configured" if not cmd else "no container session"
            tpath.write_text("")
            epath.write_text(res.error_text + "\n")
            return res
        tools = [t for t in BUILTIN_TOOLS if t in req.tools.builtin] + [s.name for s in req.tools.oracle_tools]
        env = dict(req.env)
        if in_container:
            prompt_host = container.tools_dir / "prompt.txt"
            prompt_seen = f"{TOOLS_MOUNT}/prompt.txt"
            sandbox_seen = AGENT_WORKDIR
        else:
            prompt_host = out / "prompt.txt"
            prompt_seen = str(prompt_host)
            sandbox_seen = str(req.sandbox)
        prompt_host.parent.mkdir(parents=True, exist_ok=True)
        prompt_host.write_text(req.prompt, encoding="utf-8")
        env.update({"DBWM_PROMPT_FILE": prompt_seen, "DBWM_SANDBOX": sandbox_seen,
                    "DBWM_TOOLS": ",".join(tools), "DBWM_MAX_TURNS": str(req.budget.max_turns)})
        if req.tools.oracle_tools and req.oracle_address:
            env["DBWM_ORACLE"] = req.oracle_address
            env["DBWM_ORACLE_TOKEN"] = req.oracle_token or ""
        if in_container:
            argv, client_env = container.agent_command(cmd, env)
            cwd, on_kill = str(out), container.kill_agent
        else:
            argv, client_env, cwd, on_kill = cmd, env, str(req.sandbox), None
        write_json(out / "agent_invocation.json", {"adapter": self.name, "argv": cmd, "cwd": sandbox_seen,
                                                   "env": redact_env(env), "isolation": req.isolation.value})
        t0 = time.monotonic()
        sup = run_supervised(argv, cwd=cwd, env=client_env, stdin_data=None, stdout_path=tpath,
                             stderr_path=epath, wall_clock_s=req.budget.wall_clock_s,
                             max_turns=req.budget.max_turns, on_kill=on_kill,
                             abort_event=req.extra.get("abort_event"))
        res.returncode = sup.returncode
        res.killed_by_harness = sup.killed_by
        res.duration_s = round(time.monotonic() - t0, 3)
        if sup.start_error:
            res.error_text = sup.start_error
            return res
        summary = stream_json.parse_file(tpath, sup.timestamps)
        stream_json.fill_result(summary, res)
        if summary.result is None and sup.killed_by is None and sup.returncode == 0:
            # A clean exit without a result line is a normal finish under this contract.
            synthetic = stream_json.result_event(subtype="success", is_error=False,
                                                 num_turns=summary.turns_counted, total_cost_usd=None,
                                                 result_text="", synthesized_by="dbwm.command")
            with open(tpath, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(synthetic, sort_keys=True) + "\n")
            summary = stream_json.parse_file(tpath, sup.timestamps)
            stream_json.fill_result(summary, res)
        return res
