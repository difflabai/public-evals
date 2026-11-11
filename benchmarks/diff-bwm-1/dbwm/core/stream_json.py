"""Parse Claude Code ``--output-format stream-json --verbose`` output (and the normalized subset).

Why: the paper planners' ``_parse_stream`` kept only num_turns,
total_cost_usd and the final text (coupled_planner.py:380-416,
planner.py:209-258, briefcase_planner.py:338-375), dropping result.subtype,
is_error, permission_denials, modelUsage and the init event's tools/model/cwd,
so budget kills, clock kills and natural finishes could not be told apart and
the served model was never recorded. Tool usage was counted
by substring over tool inputs. This module keeps every structured field.

Event shapes handled (one JSON object per line):
    {"type":"system","subtype":"init","tools":[...],"model":...,"cwd":...,
     "mcp_servers":[{"name":..,"status":..}],"skills":[...],...}
    {"type":"assistant","message":{"id":..,"model":..,"content":[blocks]}}
        Claude Code emits ONE event per content block, several events sharing
        a message id, so turns are counted by DISTINCT message id.
    {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":..,
        "content":..,"is_error":..}]}}
    {"type":"result","subtype":"success"|"error_max_turns"|"error_max_budget_usd"|
        "error_during_execution",...,"is_error":..,"num_turns":..,
        "total_cost_usd":..,"modelUsage":{..},"permission_denials":[..],"result":..}
    {"type":"rate_limit_event","rate_limit_info":{"status":"allowed"|"rejected"|..,
        "resetsAt":<epoch s>,"rateLimitType":"five_hour"|..}}
        subscription quota state (seen on 2.1.287); the last one is kept and
        used by outcomes.classify for the usage-cap decision and reset time.
    {"type":"tool_use","name":..,"input":{..},"id":..}
        simplified line accepted from external agents (command adapter).

Other adapters (openai_compat, command agents) may write their transcript in
the same subset using the *_event helpers below so audit and outcome
classification work identically.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from dbwm.core.types import BUILTIN_TOOLS, AgentResult, ToolCall, ToolPolicy

CLI_TOOL_NAMES = {"read": "Read", "write": "Write", "edit": "Edit", "bash": "Bash",
                  "glob": "Glob", "grep": "Grep"}
MCP_SERVER = "dbwm"
MCP_PREFIX = f"mcp__{MCP_SERVER}__"
SYNTHETIC_MODEL = "<synthetic>"


def claude_tool_names(policy: ToolPolicy) -> list[str]:
    """Claude Code tool names for a ToolPolicy: builtins in BUILTIN_TOOLS order, then MCP tools."""
    names = [CLI_TOOL_NAMES[t] for t in BUILTIN_TOOLS if t in policy.builtin]
    if policy.transport == "mcp":
        names += [MCP_PREFIX + s.name for s in policy.oracle_tools]
    return names


@dataclass
class StreamSummary:
    init: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    assistant_message_ids: list[str] = field(default_factory=list)
    assistant_models: list[str] = field(default_factory=list)
    rate_limit: dict[str, Any] | None = None  # last rate_limit_event.rate_limit_info
    events: int = 0
    parse_errors: int = 0
    _anon: int = 0

    @property
    def turns_counted(self) -> int:
        return len(self.assistant_message_ids)

    @property
    def models_served(self) -> list[str]:
        """Distinct models that produced assistant messages (synthetic messages excluded)."""
        return [m for m in self.assistant_models if m != SYNTHETIC_MODEL]

    @property
    def model_usage_keys(self) -> list[str]:
        mu = (self.result or {}).get("modelUsage") or {}
        return list(mu.keys()) if isinstance(mu, dict) else []

    def feed(self, ev: Any, ts: float | None = None) -> None:
        """Account one decoded event."""
        self.events += 1
        if not isinstance(ev, dict):
            return
        et = ev.get("type")
        if et == "system" and ev.get("subtype") == "init":
            if self.init is None:
                self.init = ev
        elif et == "assistant":
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            mid = msg.get("id")
            if not mid:
                self._anon += 1
                mid = f"_anon{self._anon}"
            if mid not in self.assistant_message_ids:
                self.assistant_message_ids.append(mid)
            model = msg.get("model")
            if isinstance(model, str) and model and model not in self.assistant_models:
                self.assistant_models.append(model)
            for blk in msg.get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    inp = blk.get("input")
                    self.tool_calls.append(ToolCall(ts=ts, name=str(blk.get("name", "?")),
                                                    input=inp if isinstance(inp, dict) else {"_raw": inp},
                                                    tool_use_id=blk.get("id")))
        elif et == "user":
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            content = msg.get("content")
            if isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_result":
                        self.tool_results.append({
                            "tool_use_id": blk.get("tool_use_id"),
                            "is_error": bool(blk.get("is_error")),
                            "text": _content_text(blk.get("content")),
                            "ts": ts,
                        })
        elif et == "tool_use":
            inp = ev.get("input")
            self.tool_calls.append(ToolCall(ts=ts, name=str(ev.get("name", "?")),
                                            input=inp if isinstance(inp, dict) else {"_raw": inp},
                                            tool_use_id=ev.get("id")))
        elif et == "rate_limit_event":
            info = ev.get("rate_limit_info")
            if isinstance(info, dict):
                self.rate_limit = info
        elif et == "result":
            self.result = ev

    def feed_line(self, line: str, ts: float | None = None) -> Any:
        line = line.strip()
        if not line:
            return None
        try:
            ev = json.loads(line)
        except ValueError:
            self.parse_errors += 1
            return None
        self.feed(ev, ts)
        return ev


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(str(c.get("text", "")))
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)
    return json.dumps(content)


def parse_lines(lines: Iterable[str], timestamps: Sequence[float | None] | None = None) -> StreamSummary:
    s = StreamSummary()
    for i, line in enumerate(lines):
        ts = timestamps[i] if timestamps is not None and i < len(timestamps) else None
        s.feed_line(line, ts)
    return s


def parse_file(path: Path | str | None, timestamps: Sequence[float | None] | None = None) -> StreamSummary:
    if path is None or not Path(path).is_file():
        return StreamSummary()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_lines(fh.read().splitlines(), timestamps)


# -- model pin matching ------------------------------------------------------------

_BRACKET = re.compile(r"\[[^\]]*\]$")
_PROVIDER = re.compile(r"^(?:[a-z]{2}\.)?anthropic\.")
_BEDROCK_VER = re.compile(r"-v\d+(?::\d+)?$")
_ALIASES = {"haiku", "sonnet", "opus", "fable", "default", "best", "opusplan"}


def _norm_model(m: str) -> str:
    m = m.strip().lower()
    m = _BRACKET.sub("", m)
    m = _PROVIDER.sub("", m)
    m = m.split("@", 1)[0]
    m = _BEDROCK_VER.sub("", m)
    return m


def model_matches(pin: str | None, served: str | None) -> bool:
    """True if ``served`` is the pinned model, allowing alias -> full id and date suffixes.

    Accepted: identical ids; ``claude-sonnet-4-6`` vs ``claude-sonnet-4-6-20260101``;
    a family alias (``haiku``) vs any id containing that family
    (``claude-haiku-4-5-20251001``); context-window suffixes (``[1m]``) and
    Bedrock/Vertex decorations are ignored.
    """
    if not pin or not served:
        return False
    p, s = _norm_model(pin), _norm_model(served)
    if p == s:
        return True
    if s.startswith(p + "-") and re.fullmatch(r"\d{8}", s[len(p) + 1:]):
        return True
    if p in _ALIASES and p not in ("default", "best", "opusplan"):
        return f"-{p}-" in f"-{s}-"
    return False


# -- runtime assertions ---------------------------------------------------------------


@dataclass
class InitExpectations:
    """What the init event must say for the regime to be the one we asked for."""

    tools: Sequence[str]
    cwd: str | None
    model: str | None
    mcp_servers: Sequence[str] = ()
    resolve_cwd: bool = True  # False inside a container (compare literally)
    empty_keys: Sequence[str] = ("skills",)
    # Claude Code 2.1.287 always lists two builtin plugins (path "builtin",
    # source "...@builtin") even under --restricted; anything else is an
    # operator plugin leaking into the regime.
    builtin_plugins_only: bool = True


def _is_builtin_plugin(p: Any) -> bool:
    if isinstance(p, dict):
        return p.get("path") == "builtin" or str(p.get("source", "")).endswith("@builtin")
    return str(p).endswith("@builtin")


def _same_dir(a: str, b: str, resolve: bool) -> bool:
    if not resolve:
        return os.path.normpath(a) == os.path.normpath(b)
    return os.path.realpath(a) == os.path.realpath(b)


def check_init(init: Mapping[str, Any] | None, exp: InitExpectations) -> list[str]:
    """Assertion failures for one init event (empty list = regime confirmed)."""
    if init is None:
        return ["no system/init event in the stream"]
    fails: list[str] = []
    tools = init.get("tools")
    if not isinstance(tools, list):
        fails.append(f"init.tools missing or not a list: {tools!r}")
    elif sorted(map(str, tools)) != sorted(exp.tools):
        extra = sorted(set(map(str, tools)) - set(exp.tools))
        missing = sorted(set(exp.tools) - set(map(str, tools)))
        fails.append(f"init.tools mismatch: extra={extra} missing={missing}")
    if exp.cwd is not None:
        cwd = init.get("cwd")
        if not isinstance(cwd, str) or not _same_dir(cwd, exp.cwd, exp.resolve_cwd):
            fails.append(f"init.cwd {cwd!r} != sandbox {exp.cwd!r}")
    if exp.model is not None:
        m = init.get("model")
        if not model_matches(exp.model, m if isinstance(m, str) else None):
            fails.append(f"init.model {m!r} does not match pinned model {exp.model!r}")
    for key in exp.empty_keys:
        if key in init and init.get(key) not in ([], None):
            fails.append(f"init.{key} not empty: {init.get(key)!r}")
    plugins = init.get("plugins")
    if exp.builtin_plugins_only and isinstance(plugins, list):
        foreign = [p for p in plugins if not _is_builtin_plugin(p)]
        if foreign:
            fails.append(f"init.plugins has non-builtin plugins: {foreign!r}")
    servers = init.get("mcp_servers")
    names: list[str] = []
    bad_status: list[str] = []
    if isinstance(servers, list):
        for s in servers:
            if isinstance(s, dict):
                names.append(str(s.get("name")))
                if s.get("name") in exp.mcp_servers and s.get("status") not in (None, "connected"):
                    bad_status.append(f"{s.get('name')}={s.get('status')}")
            else:
                names.append(str(s))
    elif servers is not None:
        fails.append(f"init.mcp_servers not a list: {servers!r}")
    if sorted(names) != sorted(exp.mcp_servers):
        fails.append(f"init.mcp_servers {sorted(names)} != expected {sorted(exp.mcp_servers)}")
    if bad_status:
        fails.append(f"MCP server(s) not connected: {bad_status}")
    return fails


def check_served_models(summary: StreamSummary, pin: str | None) -> list[str]:
    """Every model that produced an assistant message must match the pin."""
    if not pin:
        return []
    return [f"served model {m!r} does not match pinned model {pin!r}"
            for m in summary.models_served if not model_matches(pin, m)]


def fill_result(summary: StreamSummary, res: AgentResult) -> AgentResult:
    """Copy parsed fields into an AgentResult (outcome is left for outcomes.classify)."""
    init, r = summary.init, summary.result
    res.raw_init_event = init
    res.raw_result_event = r
    if init is not None:
        tools = init.get("tools")
        res.tools_available = list(tools) if isinstance(tools, list) else None
        ms = init.get("mcp_servers")
        res.mcp_servers = list(ms) if isinstance(ms, list) else None
    res.tool_calls = list(summary.tool_calls)
    res.assistant_messages = summary.turns_counted
    served = list(summary.models_served)
    for k in summary.model_usage_keys:
        if k not in served:
            served.append(k)
    res.models_served = served
    if r is not None:
        res.result_subtype = r.get("subtype")
        res.is_error = bool(r.get("is_error")) if r.get("is_error") is not None else None
        nt = r.get("num_turns")
        res.turns = nt if isinstance(nt, int) else summary.turns_counted
        cost = r.get("total_cost_usd")
        res.cost_usd = float(cost) if isinstance(cost, (int, float)) else None
        pd = r.get("permission_denials")
        res.permission_denials = list(pd) if isinstance(pd, list) else []
        if res.is_error:
            parts = []
            if isinstance(r.get("result"), str):
                parts.append(r["result"])
            errs = r.get("errors")
            if isinstance(errs, list):
                parts.extend(str(e) for e in errs)
            res.error_text = "\n".join(p for p in parts if p) or res.error_text
    else:
        res.turns = summary.turns_counted
    return res


def result_text(summary: StreamSummary) -> str:
    r = summary.result or {}
    return r.get("result") if isinstance(r.get("result"), str) else ""


# -- writers for the normalized subset ------------------------------------------------


def init_event(*, tools: Sequence[str], model: str, cwd: str, mcp_servers: Sequence[Mapping[str, Any]] = (),
               session_id: str | None = None, **extra: Any) -> dict[str, Any]:
    ev = {"type": "system", "subtype": "init", "tools": list(tools), "model": model, "cwd": cwd,
          "mcp_servers": [dict(s) for s in mcp_servers], "skills": [], "slash_commands": [],
          "session_id": session_id}
    ev.update(extra)
    return ev


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def tool_use_block(tool_use_id: str, name: str, input: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": dict(input)}


def assistant_event(message_id: str, model: str, content: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"type": "assistant", "message": {"id": message_id, "type": "message", "role": "assistant",
                                             "model": model, "content": [dict(c) for c in content]}}


def tool_result_event(tool_use_id: str, content: str, is_error: bool = False) -> dict[str, Any]:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}]}}


def result_event(*, subtype: str, is_error: bool, num_turns: int, total_cost_usd: float | None,
                 result_text: str = "", **extra: Any) -> dict[str, Any]:
    ev = {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": num_turns,
          "total_cost_usd": total_cost_usd, "result": result_text, "permission_denials": []}
    ev.update(extra)
    return ev
