"""OpenAI-compatible chat-completions agent: a minimal tool loop for non-Claude models.

Generalizes the paper's ``bwm_coupled/fireworks_backend.py`` (the GLM 5.1 arms
of evals 2 and 6) into an adapter that runs against any endpoint speaking the
OpenAI ``/chat/completions`` tool-calling protocol (Fireworks, Together, vLLM,
OpenAI, ...). Fixed defects of the original:

* The API key was copied into ``os.environ`` and then into the agent's shell
  environment (fireworks_backend.py:123,223), so the agent could read it.
  Here the key is read from the HARNESS environment at request time, sent
  only in the Authorization header, never put in any tool environment, never
  written to a transcript (it is also masked out of any error text).
* The key lookup walked up to six parent directories for a ``.env``
  (fireworks_backend.py:109-127). Here it comes from one named variable.
* A browser User-Agent was spoofed to pass a Cloudflare filter
  (fireworks_backend.py:147-150). Here the User-Agent is ``diff-bwm-1/<version>``.
  If an endpoint rejects it, that is reported as an API error, not evaded.
* ``run_bash`` ran on the host with no path restriction (fireworks_backend.py:
  191). Here it exists only for arms granted ``bash``, and only at CONTAINER
  isolation (``docker exec`` into the per-trial agent container, which mounts
  nothing but the sandbox) or at HOST_UNSAFE (recorded as non-conforming).
* The loop injected turn-count reminders and "you were cut off" nudges that
  the Claude arms never got. Here the system preamble is neutral and the only
  injected message is a one-line "continue" after an output-length cut-off.

Tools (adapter-native names; same information as the Claude Code arm):
    read  -> read_file(path)                  write -> write_file(path, content)
    edit  -> edit_file(path, old_string, new_string, replace_all?)
    glob  -> list_dir(path?, pattern?)        grep  -> grep_files(pattern, path?, glob?)
    bash  -> run_bash(command)                (CONTAINER / HOST_UNSAFE only)
    oracle tools (MCP-transport arms) -> one function per OracleToolSpec, named
        ``spec.name``, with its input_schema; calls go to the per-trial oracle
        server. Bash arms reach the oracle through the staged CLI shims instead,
        exactly like the Claude arm.
File tools resolve every path against the sandbox (symlinks included) and
refuse anything that escapes it.

Budget: ``max_turns`` counts assistant turns (API responses). Reaching it ends
the trial with ``subtype: error_max_turns`` (scored). The wall-clock rail ends
it with ``killed_by_harness="clock"``; the USD rail (needs ``pricing``) with
``killed_by_harness="cost"``. API failures after retries end it as an
``is_error`` result whose text the outcome classifier maps to infra_*.

Transcript: ``transcript.jsonl`` in the normalized stream-json subset
(dbwm.core.stream_json writers): an init event (``adapter: openai_compat``),
one assistant event per response (text + tool_use blocks), user tool_result
events, a result event. Audit and outcome classification read it exactly as
they read Claude Code's stream. Raw exchanges go to ``openai_exchanges.jsonl``.

Configuration: ``regime.extra["openai_compat"]`` (the runner passes the
regime's extra as ``AgentRequest.extra["regime_extra"]``), then
``AgentRequest.extra["openai_compat"]``, over constructor arguments::

    base_url        e.g. "https://api.fireworks.ai/inference/v1"  (required)
    api_key_env     name of the harness env var holding the key (default OPENAI_API_KEY)
    max_tokens      per-response cap sent as "max_tokens" (default: not sent)
    pricing         {"input": usd/1M, "cached_input": usd/1M, "output": usd/1M}
    extra_body      dict merged into every request (e.g. {"temperature": 0})
    request_timeout_s, retries, bash_timeout_s, max_read_chars, system_preamble

When the eval states a per-command limit (``EVAL.command_timeout_s(cell)``,
passed by the runner as ``AgentRequest.extra["command_timeout_s"]``), it
replaces ``bash_timeout_s``: the prompt promises that limit, so the instrument
enforces it whatever the regime says (the Claude Code adapter gets the same
value through BASH_DEFAULT_TIMEOUT_MS / BASH_MAX_TIMEOUT_MS).
The model is ``AgentRequest.model``. ``AgentRequest.effort`` has no portable
equivalent and is not sent; put a provider's knob (e.g. ``reasoning_effort``)
in ``extra_body``, where the regime records it. ``extra_body`` may not set
model, messages, tools or tool_choice.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

import dbwm
from dbwm.core import stream_json
from dbwm.core.adapters.base import stderr_path, transcript_path
from dbwm.core.types import AgentRequest, AgentResult, Isolation, ToolCall, ToolPolicy

ADAPTER_NAME = "openai_compat"
USER_AGENT = f"diff-bwm-1/{dbwm.__version__}"
DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
EXCHANGES = "openai_exchanges.jsonl"
AGENT_WORKDIR = "/work"  # the sandbox inside the agent container (dbwm.core.container)
TOOLS_MOUNT = "/dbwm-tools"
ORACLE_CALL_SCRIPT = "dbwm_oracle_call.py"
_RETRY_STATUS = (408, 409, 429, 500, 502, 503, 504, 529)
MAX_BASH_OUTPUT = 30_000
MAX_LIST_ENTRIES = 1_000
MAX_GREP_MATCHES = 500
MAX_GREP_FILE_BYTES = 2 * 1024 * 1024

BUILTIN_FUNCTIONS = {
    "read": "read_file",
    "write": "write_file",
    "edit": "edit_file",
    "glob": "list_dir",
    "grep": "grep_files",
    "bash": "run_bash",
}

DEFAULT_PREAMBLE = (
    "You are an autonomous agent working in a working directory through tool calls. "
    "There is no human to answer questions: act. Use paths relative to the working "
    "directory. When you have finished the task, reply with a short final message and "
    "do not call any more tools; that ends the session."
)
CONTINUE_AFTER_LENGTH = "Your previous reply reached the output length limit. Continue."

_FUNCTION_SPECS: dict[str, dict[str, Any]] = {
    "read_file": {
        "description": "Read a text file in the working directory.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "file path relative to the working directory"}},
            "required": ["path"]},
    },
    "write_file": {
        "description": "Create or overwrite a file in the working directory.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "file path relative to the working directory"},
            "content": {"type": "string"}},
            "required": ["path", "content"]},
    },
    "edit_file": {
        "description": "Replace old_string with new_string in a file. old_string must occur exactly "
                       "once unless replace_all is true.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_string": {"type": "string"},
            "new_string": {"type": "string"}, "replace_all": {"type": "boolean"}},
            "required": ["path", "old_string", "new_string"]},
    },
    "list_dir": {
        "description": "List files in a directory of the working directory, optionally filtered by a "
                       "glob pattern such as '**/*.json'.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "directory (default: the working directory)"},
            "pattern": {"type": "string"}}},
    },
    "grep_files": {
        "description": "Search file contents for a regular expression; returns path:line:text matches.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string"},
            "glob": {"type": "string", "description": "only files matching this glob"}},
            "required": ["pattern"]},
    },
    "run_bash": {
        "description": "Run a shell command (bash -lc) in the working directory and return its output.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]},
    },
}

# Stdlib client staged into the container's tools dir for oracle calls of
# MCP-transport arms at CONTAINER isolation (the sidecar address is reachable
# only from inside the trial network). argv: op, payload-file.
ORACLE_CALL_SOURCE = r'''#!/usr/bin/env python3
import json, os, socket, sys
op, payload_file = sys.argv[1], sys.argv[2]
with open(payload_file, encoding="utf-8") as fh:
    payload = json.load(fh)
addr = os.environ["DBWM_ORACLE"][len("tcp://"):]
host, _, port = addr.rpartition(":")
req = {"token": os.environ["DBWM_ORACLE_TOKEN"], "op": op, "payload": payload}
with socket.create_connection((host.strip("[]"), int(port)), timeout=600) as s:
    s.sendall((json.dumps(req) + "\n").encode("utf-8"))
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
sys.stdout.write(buf.decode("utf-8"))
'''


class ConfigError(ValueError):
    pass


def expected_tool_names(policy: ToolPolicy, isolation: Isolation | str | None = None) -> list[str]:
    """Function names this adapter offers an arm (what its init event lists).

    ``run_bash`` is offered for bash arms whatever the isolation; the adapter
    itself refuses to start a bash arm at HOST (an INFRA_INVALID_REGIME).
    """
    names = [BUILTIN_FUNCTIONS[b] for b in ("read", "write", "edit", "glob", "grep", "bash")
             if b in policy.builtin]
    if policy.transport == "mcp":
        names += [s.name for s in policy.oracle_tools]
    return names


def function_specs(policy: ToolPolicy) -> list[dict[str, Any]]:
    out = []
    for name in expected_tool_names(policy):
        spec = _FUNCTION_SPECS.get(name)
        if spec is not None:
            out.append({"type": "function", "function": {"name": name, **spec}})
    if policy.transport == "mcp":
        for s in policy.oracle_tools:
            schema = dict(s.input_schema) if s.input_schema else {"type": "object", "properties": {}}
            out.append({"type": "function", "function": {"name": s.name, "description": s.description,
                                                         "parameters": schema}})
    return out


def _cost(usage: Mapping[str, Any], pricing: Mapping[str, float] | None) -> float | None:
    if not pricing:
        return None
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    cached = min(cached, pt)
    p_in = float(pricing.get("input", 0.0))
    p_cached = float(pricing.get("cached_input", p_in))
    p_out = float(pricing.get("output", 0.0))
    return ((pt - cached) * p_in + cached * p_cached + ct * p_out) / 1_000_000


class _Escape(ValueError):
    pass


class OpenAICompatAdapter:
    """AgentAdapter for OpenAI-compatible chat-completions endpoints."""

    name = ADAPTER_NAME

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key_env: str | None = None,
        *,
        max_tokens: int | None = None,
        pricing: Mapping[str, float] | None = None,
        request_timeout_s: float = 420.0,
        retries: int = 4,
        bash_timeout_s: float = 120.0,
        max_read_chars: int = 100_000,
        extra_body: Mapping[str, Any] | None = None,
        system_preamble: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.defaults: dict[str, Any] = {
            "base_url": base_url, "api_key_env": api_key_env or DEFAULT_API_KEY_ENV,
            "max_tokens": max_tokens, "pricing": dict(pricing) if pricing else None,
            "request_timeout_s": request_timeout_s, "retries": retries, "bash_timeout_s": bash_timeout_s,
            "max_read_chars": max_read_chars, "extra_body": dict(extra_body or {}),
            "system_preamble": system_preamble,
        }
        self.model_default = model
        self.sleep = sleep
        self.clock = clock

    # -- configuration ---------------------------------------------------------------

    def config(self, req: AgentRequest) -> dict[str, Any]:
        cfg = dict(self.defaults)
        extra = req.extra if isinstance(req.extra, Mapping) else {}
        regime_extra = extra.get("regime_extra") if isinstance(extra.get("regime_extra"), Mapping) else {}
        for over in (regime_extra.get("openai_compat"), extra.get("openai_compat")):
            if over is None:
                continue
            if not isinstance(over, Mapping):
                raise ConfigError("openai_compat settings must be an object")
            for k, v in over.items():
                if k not in cfg:
                    raise ConfigError(f"unknown openai_compat setting {k!r} (known: {sorted(cfg)})")
                if v is not None:
                    cfg[k] = v
        cmd_t = extra.get("command_timeout_s")
        if cmd_t is not None:
            if isinstance(cmd_t, bool) or not isinstance(cmd_t, (int, float)) or not cmd_t > 0:
                raise ConfigError(f"command_timeout_s must be a positive number, got {cmd_t!r}")
            cfg["bash_timeout_s"] = float(cmd_t)
        clash = sorted(set(cfg["extra_body"] or {}) & {"model", "messages", "tools", "tool_choice"})
        if clash:
            raise ConfigError(f"openai_compat extra_body may not set {clash} (the harness owns them)")
        if not cfg["base_url"]:
            raise ConfigError("openai_compat needs base_url (regime extra openai_compat.base_url)")
        if not str(cfg["base_url"]).startswith(("http://", "https://")):
            raise ConfigError(f"openai_compat base_url must be http(s): {cfg['base_url']!r}")
        return cfg

    # -- run ------------------------------------------------------------------------

    def run(self, req: AgentRequest) -> AgentResult:
        out_dir = Path(req.harness_out)
        out_dir.mkdir(parents=True, exist_ok=True)
        tpath, epath = transcript_path(req), stderr_path(req)
        res = AgentResult(transcript_path=tpath, stderr_path=epath, returncode=None,
                          model_requested=req.model, cli_version=f"{ADAPTER_NAME}/{dbwm.__version__}")
        t0 = self.clock()
        session = _Session(self, req, res, tpath, epath, out_dir, t0)
        try:
            session.run()
        except Exception as e:  # noqa: BLE001 - an adapter bug is a crash of this attempt, never a score
            import traceback

            session.note("[dbwm] openai_compat adapter error:\n" + traceback.format_exc())
            try:
                session._finish(None)
            except Exception:  # noqa: BLE001
                pass
            res.error_text = session.mask(f"adapter error: {type(e).__name__}: {e}")
            res.raw_result_event = None  # no result event: classified as a (retryable) crash
            res.result_subtype = None
        finally:
            session.close()
        res.duration_s = round(self.clock() - t0, 3)
        return res


class _Session:
    """One trial: the message loop, tool execution and transcript writing."""

    def __init__(self, adapter: OpenAICompatAdapter, req: AgentRequest, res: AgentResult, tpath: Path,
                 epath: Path, out_dir: Path, t0: float):
        self.a = adapter
        self.req = req
        self.res = res
        self.tpath = tpath
        self.epath = epath
        self.out_dir = out_dir
        self.t0 = t0
        self.sandbox = Path(req.sandbox).resolve()
        self.in_container = req.isolation == Isolation.CONTAINER
        self.container = req.extra.get("container") if isinstance(req.extra, Mapping) else None
        self.container_name: str | None = None
        self.key: str | None = None
        self._tf = open(tpath, "w", encoding="utf-8")
        self._ef = open(epath, "w", encoding="utf-8")
        self._xf = open(out_dir / EXCHANGES, "w", encoding="utf-8")
        self.cost = 0.0
        self.cost_known = True
        self.turns = 0
        self.served: list[str] = []
        self.usage_by_model: dict[str, dict[str, int]] = {}
        self.payload_k = 0
        self.model = req.model or adapter.model_default

    def close(self) -> None:
        for fh in (self._tf, self._ef, self._xf):
            try:
                fh.close()
            except OSError:
                pass

    # -- logging ----------------------------------------------------------------------

    def emit(self, ev: Mapping[str, Any]) -> None:
        self._tf.write(json.dumps(ev, sort_keys=True, default=str) + "\n")
        self._tf.flush()

    def note(self, text: str) -> None:
        self._ef.write(self.mask(text) + "\n")
        self._ef.flush()

    def mask(self, text: str) -> str:
        if self.key and self.key in text:
            text = text.replace(self.key, "[redacted]")
        return text

    def remaining_s(self) -> float:
        return self.req.budget.wall_clock_s - (self.a.clock() - self.t0)

    def aborted(self) -> bool:
        ev = self.req.extra.get("abort_event") if isinstance(self.req.extra, Mapping) else None
        return bool(ev is not None and ev.is_set())

    # -- main loop --------------------------------------------------------------------

    def run(self) -> None:
        req, res = self.req, self.res
        try:
            cfg = self.a.config(req)
        except ConfigError as e:
            res.assertion_failures.append(str(e))
            self.note(f"[dbwm] {e}")
            return
        policy = req.tools
        if policy.needs_bash and req.isolation == Isolation.HOST:
            res.assertion_failures.append("openai_compat: a bash arm cannot run at isolation host")
            self.note("[dbwm] refused: bash arm at isolation host")
            return
        if self.in_container and self.container is None:
            res.assertion_failures.append("openai_compat: CONTAINER isolation but no container session")
            self.note("[dbwm] no container session")
            return
        self.key = os.environ.get(cfg["api_key_env"])
        if not self.key:
            res.error_text = (f"authentication_error: API key variable {cfg['api_key_env']} is not set "
                              "in the harness environment")
            self.note("[dbwm] " + res.error_text)
            return
        if req.budget.usd_rail is not None and not cfg["pricing"]:
            self.note("[dbwm] warning: usd_rail set but no openai_compat.pricing; the USD rail is not enforced")

        tool_names = expected_tool_names(policy, req.isolation)
        functions = function_specs(policy)
        init = stream_json.init_event(
            tools=tool_names, model=self.model, cwd=AGENT_WORKDIR if self.in_container else str(self.sandbox),
            adapter=ADAPTER_NAME, base_url=cfg["base_url"], user_agent=USER_AGENT,
            usd_rail_enforced=bool(cfg["pricing"]) and req.budget.usd_rail is not None)
        self.emit(init)
        if self.in_container:
            try:
                self._start_container()
            except Exception as e:  # noqa: BLE001 - container failure is an infra problem
                res.assertion_failures.append(f"openai_compat: could not start the agent container: {e}")
                self.note(f"[dbwm] container start failed: {e}")
                self._finish(None)
                return

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": cfg["system_preamble"] or DEFAULT_PREAMBLE},
            {"role": "user", "content": req.prompt},
        ]
        result_ev: dict[str, Any] | None = None
        final_text = ""
        max_turns = req.budget.max_turns
        while True:
            if self.aborted():
                res.killed_by_harness = "interrupt"  # operator stop: a retryable crash, never a score
                self.note("[dbwm] interrupted by the runner")
                break
            if self.remaining_s() <= 0:
                res.killed_by_harness = "clock"
                self.note(f"[dbwm] wall-clock rail ({req.budget.wall_clock_s}s) reached")
                break
            if (req.budget.usd_rail is not None and cfg["pricing"] and self.cost >= req.budget.usd_rail):
                res.killed_by_harness = "cost"
                result_ev = self._result("error_max_budget_usd", True, final_text)
                self.note(f"[dbwm] USD rail ({req.budget.usd_rail}) reached")
                break
            if self.turns >= max_turns:
                result_ev = self._result("error_max_turns", True, final_text)
                break
            body = {"model": self.model, "messages": messages}
            if functions:
                body["tools"] = functions
            if cfg["max_tokens"]:
                body["max_tokens"] = int(cfg["max_tokens"])
            body.update(cfg["extra_body"])
            status, resp, err = self._post(cfg, body)
            if resp is None:
                if err == "clock":
                    res.killed_by_harness = "clock"
                    self.note("[dbwm] wall-clock rail reached during an API request")
                    break
                result_ev = self._result("error_during_execution", True, err or "",
                                         errors=[err or f"status {status}"])
                break
            self.turns += 1
            model_served = str(resp.get("model") or self.model)
            if model_served not in self.served:
                self.served.append(model_served)
            usage = resp.get("usage") or {}
            u = self.usage_by_model.setdefault(model_served, {"prompt_tokens": 0, "completion_tokens": 0,
                                                               "cached_tokens": 0})
            u["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            u["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            u["cached_tokens"] += int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
            c = _cost(usage, cfg["pricing"])
            if c is None:
                self.cost_known = False
            else:
                self.cost += c

            choices = resp.get("choices") or [{}]
            choice = choices[0] if isinstance(choices[0], dict) else {}
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            if not isinstance(content, str):
                content = json.dumps(content)
            final_text = content
            tool_calls = [tc for tc in (msg.get("tool_calls") or []) if isinstance(tc, dict)]
            msg_id = f"oc-msg-{self.turns}"
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append(stream_json.text_block(content))
            parsed_calls = []
            for i, tc in enumerate(tool_calls):
                fn = tc.get("function") or {}
                tc_id = str(tc.get("id") or f"oc-call-{self.turns}-{i}")
                fname = str(fn.get("name") or "")
                raw_args = fn.get("arguments")
                args, arg_err = _parse_args(raw_args)
                parsed_calls.append((tc_id, fname, raw_args, args, arg_err))
                blocks.append(stream_json.tool_use_block(tc_id, fname, args if args is not None else
                                                         {"_unparsed_arguments": str(raw_args)[:2000]}))
                res.tool_calls.append(ToolCall(ts=time.time(), name=fname, input=args or {}, tool_use_id=tc_id))
            self.emit(stream_json.assistant_event(msg_id, model_served, blocks or [stream_json.text_block("")]))

            if not tool_calls:
                if choice.get("finish_reason") == "length":
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": CONTINUE_AFTER_LENGTH})
                    continue
                result_ev = self._result("success", False, final_text)
                break

            messages.append({"role": "assistant", "content": content or None, "tool_calls": [
                {"id": tc_id, "type": "function", "function": {"name": fname, "arguments":
                                                               raw_args if isinstance(raw_args, str)
                                                               else json.dumps(raw_args or {})}}
                for tc_id, fname, raw_args, _a, _e in parsed_calls]})
            clock_hit = False
            for tc_id, fname, _raw, args, arg_err in parsed_calls:
                if self.remaining_s() <= 0:
                    clock_hit = True
                    text, is_err = "error: not run (wall-clock rail reached)", True
                elif arg_err:
                    text, is_err = f"error: {arg_err}", True
                else:
                    text, is_err = self._exec(fname, args or {}, cfg)
                text = self.mask(text)
                self.emit(stream_json.tool_result_event(tc_id, text, is_err))
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": text})
            if self.aborted():
                res.killed_by_harness = "interrupt"
                self.note("[dbwm] interrupted by the runner")
                break
            if clock_hit:
                res.killed_by_harness = "clock"
                self.note("[dbwm] wall-clock rail reached during tool execution")
                break
        self._finish(result_ev)

    def _result(self, subtype: str, is_error: bool, text: str, **extra: Any) -> dict[str, Any]:
        usage = {m: dict(u) for m, u in self.usage_by_model.items()}
        return stream_json.result_event(
            subtype=subtype, is_error=is_error, num_turns=self.turns,
            total_cost_usd=round(self.cost, 6) if self.cost_known else None,
            result_text=self.mask(text), modelUsage=usage, **extra)

    def _finish(self, result_ev: dict[str, Any] | None) -> None:
        if result_ev is not None:
            self.emit(result_ev)
        self._tf.flush()
        summary = stream_json.parse_file(self.tpath)
        stream_json.fill_result(summary, self.res)
        self.res.turns = self.turns
        self.res.assistant_messages = self.turns
        self.res.models_served = list(self.served)
        if not self.turns:
            self.res.cost_usd = 0.0  # no request succeeded, nothing was billed
        else:
            self.res.cost_usd = round(self.cost, 6) if self.cost_known else None
        self.res.returncode = 0 if result_ev is not None and not result_ev.get("is_error") else None
        self.res.mcp_servers = []
        if self.res.error_text:
            self.res.error_text = self.mask(self.res.error_text)

    # -- HTTP --------------------------------------------------------------------------

    def _post(self, cfg: Mapping[str, Any], body: Mapping[str, Any]) -> tuple[int, dict | None, str | None]:
        """(status, parsed response or None, error text or None). Error text "clock" = rail hit."""
        url = str(cfg["base_url"]).rstrip("/") + "/chat/completions"
        data = json.dumps(body).encode("utf-8")
        last_status, last_err = 0, None
        retries = int(cfg["retries"])
        for attempt in range(retries + 1):
            remaining = self.remaining_s()
            if remaining <= 0:
                return 0, None, "clock"
            timeout = max(1.0, min(float(cfg["request_timeout_s"]), remaining))
            request = urllib.request.Request(url, data=data, method="POST", headers={
                "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                "Accept": "application/json", "User-Agent": USER_AGENT})
            retry_after = None
            t_req = time.time()
            try:
                with urllib.request.urlopen(request, timeout=timeout) as r:
                    raw = r.read().decode("utf-8", errors="replace")
                    status = r.status
                try:
                    resp = json.loads(raw)
                except ValueError:
                    resp = None
                self._log_exchange(attempt, status, body, resp if resp is not None else raw[:4000], t_req)
                if isinstance(resp, dict) and resp.get("choices"):
                    return status, resp, None
                last_status, last_err = status, f"API Error: {status} (status {status}): response without choices"
                if isinstance(resp, dict) and resp.get("error"):
                    last_err += f": {json.dumps(resp.get('error'))[:500]}"
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", errors="replace")
                self._log_exchange(attempt, e.code, body, raw[:4000], t_req)
                last_status = e.code
                # Phrased so dbwm.core.outcomes.classify recognizes the class:
                # "api error: 401" / "invalid api key" -> infra_auth (with no cost),
                # "api error: 429" / "status 5xx" -> infra_api; anything else -> crash.
                if e.code in (401, 403):
                    last_err = (f"API Error: {e.code} (status {e.code}; invalid api key or no access): "
                                f"{self.mask(raw[:300])}")
                else:
                    last_err = f"API Error: {e.code} (status {e.code}): {self.mask(raw[:500])}"
                if e.code not in _RETRY_STATUS:
                    return e.code, None, last_err
                ra = e.headers.get("retry-after") if e.headers else None
                try:
                    retry_after = min(float(ra), 60.0) if ra else None
                except ValueError:
                    retry_after = None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_status, last_err = 0, f"connection error: {type(e).__name__}: {self.mask(str(e))[:300]}"
                self._log_exchange(attempt, 0, body, last_err, t_req)
            if attempt < retries:
                delay = retry_after if retry_after is not None else min(2.0 ** attempt, 30.0)
                if delay >= self.remaining_s():
                    return 0, None, "clock"
                self.note(f"[dbwm] {last_err}; retrying in {delay:.0f}s")
                self.a.sleep(delay)
        return last_status, None, last_err

    def _log_exchange(self, attempt: int, status: int, body: Mapping[str, Any], resp: Any, ts: float) -> None:
        rec = {"ts": ts, "turn": self.turns + 1, "attempt": attempt, "status": status,
               "request": {"model": body.get("model"), "n_messages": len(body.get("messages") or []),
                           "last_message": (body.get("messages") or [{}])[-1]},
               "response": resp}
        self._xf.write(self.mask(json.dumps(rec, sort_keys=True, default=str)) + "\n")
        self._xf.flush()

    # -- tools -------------------------------------------------------------------------

    def _exec(self, name: str, args: Mapping[str, Any], cfg: Mapping[str, Any]) -> tuple[str, bool]:
        policy = self.req.tools
        allowed = set(expected_tool_names(policy, self.req.isolation))
        if name not in allowed:
            return f"error: unknown tool {name!r}", True
        try:
            if name == "read_file":
                return self._read(args, int(cfg["max_read_chars"])), False
            if name == "write_file":
                return self._write(args), False
            if name == "edit_file":
                return self._edit(args)
            if name == "list_dir":
                return self._list(args), False
            if name == "grep_files":
                return self._grep(args), False
            if name == "run_bash":
                return self._bash(args, float(cfg["bash_timeout_s"]))
            return self._oracle(name, args)
        except _Escape as e:
            return f"error: {e}", True
        except (KeyError, TypeError) as e:
            return f"error: bad arguments for {name}: {e}", True
        except (ValueError, NotImplementedError) as e:
            return f"error: {e}", True
        except OSError as e:
            return f"error: {type(e).__name__}: {e.strerror or e}", True

    def _resolve(self, raw: Any, *, must_exist: bool = False) -> Path:
        """Sandbox path for an agent-supplied path; refuses escapes (``..``, absolute, symlinks)."""
        if not isinstance(raw, str) or not raw.strip():
            raise _Escape("path must be a non-empty string")
        p = raw.strip()
        if self.in_container and (p == AGENT_WORKDIR or p.startswith(AGENT_WORKDIR + "/")):
            p = p[len(AGENT_WORKDIR):].lstrip("/") or "."
        cand = Path(p)
        if not cand.is_absolute():
            cand = self.sandbox / cand
        full = Path(os.path.realpath(cand))
        if full != self.sandbox and self.sandbox not in full.parents:
            raise _Escape(f"path {raw!r} is outside the working directory")
        if must_exist and not full.exists():
            raise _Escape(f"{raw} does not exist")
        return full

    def _rel(self, p: Path) -> str:
        try:
            r = p.relative_to(self.sandbox).as_posix()
        except ValueError:
            return p.name
        return r or "."

    def _read(self, args: Mapping[str, Any], limit: int) -> str:
        p = self._resolve(args["path"], must_exist=True)
        if p.is_dir():
            raise _Escape(f"{args['path']} is a directory")
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as fh:
            data = fh.read(limit * 4 + 1)
        text = data.decode("utf-8", errors="replace")
        if len(text) > limit:
            text = text[:limit] + f"\n[truncated at {limit} characters]"
        return text

    def _write(self, args: Mapping[str, Any]) -> str:
        content = args["content"]
        if not isinstance(content, str):
            content = json.dumps(content, indent=2)
        p = self._resolve(args["path"])
        if p == self.sandbox or p.is_dir():
            raise _Escape(f"{args['path']} is a directory")
        p.parent.mkdir(parents=True, exist_ok=True)
        self._resolve(self._rel(p.parent))  # the created parents are still inside
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        return f"wrote {len(content.encode('utf-8'))} bytes to {self._rel(p)}"

    def _edit(self, args: Mapping[str, Any]) -> tuple[str, bool]:
        p = self._resolve(args["path"], must_exist=True)
        old, new = args["old_string"], args["new_string"]
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            return "error: old_string and new_string must be strings and old_string non-empty", True
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n == 0:
            return "error: old_string not found", True
        if n > 1 and not args.get("replace_all"):
            return f"error: old_string occurs {n} times; pass replace_all or a longer old_string", True
        p.write_text(text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1),
                     encoding="utf-8")
        return f"edited {self._rel(p)} ({n if args.get('replace_all') else 1} replacement(s))", False

    def _list(self, args: Mapping[str, Any]) -> str:
        base = self._resolve(args.get("path") or ".", must_exist=True)
        if not base.is_dir():
            raise _Escape(f"{args.get('path')} is not a directory")
        pattern = args.get("pattern")
        entries: list[str] = []
        it = base.glob(pattern) if pattern else base.iterdir()
        for q in sorted(it):
            try:
                self._resolve(str(q))
            except _Escape:
                continue  # a symlink pointing outside: not listed
            entries.append(self._rel(q) + ("/" if q.is_dir() else ""))
            if len(entries) >= MAX_LIST_ENTRIES:
                entries.append(f"[truncated at {MAX_LIST_ENTRIES} entries]")
                break
        return "\n".join(entries) if entries else "(no entries)"

    def _grep(self, args: Mapping[str, Any]) -> str:
        try:
            rx = re.compile(str(args["pattern"]))
        except re.error as e:
            raise _Escape(f"bad regular expression: {e}") from None
        base = self._resolve(args.get("path") or ".", must_exist=True)
        files = [base] if base.is_file() else sorted(q for q in base.rglob("*") if q.is_file())
        glob = args.get("glob")
        out: list[str] = []
        for f in files:
            try:
                self._resolve(str(f))
            except _Escape:
                continue
            rel = self._rel(f)
            if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(f.name, glob)):
                continue
            try:
                if f.stat().st_size > MAX_GREP_FILE_BYTES:
                    continue
                data = f.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:8192]:
                continue
            for i, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    out.append(f"{rel}:{i}:{line[:500]}")
                    if len(out) >= MAX_GREP_MATCHES:
                        out.append(f"[truncated at {MAX_GREP_MATCHES} matches]")
                        return "\n".join(out)
        return "\n".join(out) if out else "(no matches)"

    def _bash(self, args: Mapping[str, Any], bash_timeout: float) -> tuple[str, bool]:
        command = args["command"]
        if not isinstance(command, str):
            return "error: command must be a string", True
        timeout = max(1.0, min(bash_timeout, self.remaining_s()))
        if self.in_container:
            from dbwm.core import container as core_container

            rc, out, err = core_container.exec_in_agent(self.container_name, ["bash", "-lc", command], timeout)
        elif self.req.isolation == Isolation.HOST_UNSAFE:
            rc, out, err = self._host_bash(command, timeout)
        else:  # pragma: no cover - refused in run()
            return "error: run_bash is not available at this isolation level", True
        text = f"[exit {rc}]\n" + _clip(out, MAX_BASH_OUTPUT)
        if err:
            text += "\n[stderr]\n" + _clip(err, MAX_BASH_OUTPUT // 3)
        return text, rc != 0

    def _host_bash(self, command: str, timeout: float) -> tuple[int, str, str]:
        from dbwm.core.env import build_tool_env

        env = build_tool_env(self.sandbox, oracle_address=self.req.oracle_address,
                             oracle_token=self.req.oracle_token)
        proc = subprocess.Popen(["bash", "-lc", command], cwd=str(self.sandbox), env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = proc.communicate()
            return 124, out.decode("utf-8", "replace"), (err.decode("utf-8", "replace")
                                                         + f"\ncommand timed out after {timeout:.0f}s")
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    def _oracle(self, name: str, args: Mapping[str, Any]) -> tuple[str, bool]:
        if not self.req.oracle_address or not self.req.oracle_token:
            return "error: oracle tool unavailable (no oracle server for this trial)", True
        address = self.req.oracle_address
        if self.in_container:
            # The sidecar's address resolves only inside the trial network. The
            # runner publishes the oracle port on 127.0.0.1 for this adapter
            # (extra["oracle_host_address"]); without it, call from inside the
            # agent container.
            address = self.req.extra.get("oracle_host_address")
            if not address:
                return _oracle_text(self._oracle_in_container(name, args))
        from dbwm.core.oracle import client

        try:
            reply = client.request(address, self.req.oracle_token, name, dict(args),
                                   timeout=max(1.0, self.remaining_s()))
        except (OSError, ValueError) as e:
            return f"error: oracle unreachable: {type(e).__name__}", True
        return _oracle_text(reply)

    def _start_container(self) -> None:
        env = {k: v for k, v in (self.req.env or {}).items() if not _is_secret(k)}
        if self.req.oracle_address and self.req.oracle_token:
            env["DBWM_ORACLE"] = self.req.oracle_address
            env["DBWM_ORACLE_TOKEN"] = self.req.oracle_token
        if (self.req.tools.transport == "mcp" and self.req.tools.oracle_tools
                and not self.req.extra.get("oracle_host_address")):
            tools_dir = Path(self.container.tools_dir)
            tools_dir.mkdir(parents=True, exist_ok=True)
            (tools_dir / ORACLE_CALL_SCRIPT).write_text(ORACLE_CALL_SOURCE, encoding="utf-8")
        self.container_name = self.container.ensure_idle_agent(env)

    def _oracle_in_container(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        from dbwm.core import container as core_container

        self.payload_k += 1
        fname = f"payload-{self.payload_k}.json"
        (Path(self.container.tools_dir) / fname).write_text(json.dumps(dict(args)), encoding="utf-8")
        rc, out, err = core_container.exec_in_agent(
            self.container_name, ["python3", f"{TOOLS_MOUNT}/{ORACLE_CALL_SCRIPT}", name, f"{TOOLS_MOUNT}/{fname}"],
            max(1.0, self.remaining_s()))
        try:
            reply = json.loads(out)
        except ValueError:
            return {"ok": False, "error": f"oracle call failed (exit {rc})", "calls_remaining": None}
        return reply if isinstance(reply, dict) else {"ok": False, "error": "bad oracle reply"}


def _is_secret(name: str) -> bool:
    try:
        from dbwm.core.env import is_secret_name
    except ImportError:  # pragma: no cover
        return bool(re.search(r"KEY|TOKEN|SECRET|PASSWORD|AUTH", name, re.I))
    return is_secret_name(name) or bool(re.search(r"API_KEY|_TOKEN$|SECRET", name, re.I))


def _parse_args(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    try:
        v = json.loads(raw)
    except (TypeError, ValueError) as e:
        return None, f"tool arguments are not valid JSON ({e})"
    if not isinstance(v, dict):
        return None, "tool arguments must be a JSON object"
    return v, None


def _oracle_text(reply: Mapping[str, Any]) -> tuple[str, bool]:
    """Same text the MCP shim returns (dbwm.core.oracle.shim_mcp), so both transports read alike."""
    cr = reply.get("calls_remaining")
    if reply.get("ok"):
        result = reply.get("result")
        text = result if isinstance(result, str) else json.dumps(result, indent=2, sort_keys=True)
        if cr is not None:
            text += f"\ncalls remaining: {cr}"
        return text, False
    text = f"error: {reply.get('error')}"
    if cr is not None:
        text += f"\ncalls remaining: {cr}"
    return text, True


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"\n[output truncated at {n} characters]"


# Registry compatibility (dbwm.core.adapters.registry looks for these names).
Adapter = OpenAICompatAdapter


def make_adapter(**kw: Any) -> OpenAICompatAdapter:
    keys = ("base_url", "model", "api_key_env", "max_tokens", "pricing", "request_timeout_s", "retries",
            "bash_timeout_s", "max_read_chars", "extra_body", "system_preamble")
    return OpenAICompatAdapter(**{k: v for k, v in kw.items() if k in keys})
