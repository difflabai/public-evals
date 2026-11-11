"""The agent adapter protocol and helpers shared by every adapter.

An adapter turns an AgentRequest (sandbox, tools, budget, model, isolation,
oracle address) into an AgentResult (transcript path, structured fields). It
never scores and never classifies: the runner calls outcomes.classify on the
result and the eval's score() on the collected deliverables.

Contract every adapter honours:
  * cwd of the agent is ``req.sandbox`` (``/work`` inside the container);
  * the environment is ``req.env`` (already allowlisted by dbwm.core.env)
    plus only adapter-specific variables documented in the adapter;
  * the transcript is written to ``req.harness_out / "transcript.jsonl"``,
    stderr to ``req.harness_out / "stderr.txt"``; nothing is written into the
    sandbox except by the agent itself;
  * the wall-clock rail ``req.budget.wall_clock_s`` is enforced by the
    adapter (killed_by_harness="clock"); turn limits likewise ("turns").
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from dbwm.core.types import AgentRequest, AgentResult, Outcome, ToolCall

TRANSCRIPT = "transcript.jsonl"
STDERR = "stderr.txt"


@runtime_checkable
class AgentAdapter(Protocol):
    name: str

    def run(self, req: AgentRequest) -> AgentResult: ...


def transcript_path(req: AgentRequest) -> Path:
    return Path(req.harness_out) / TRANSCRIPT


def stderr_path(req: AgentRequest) -> Path:
    return Path(req.harness_out) / STDERR


def _jsonable(o: Any) -> Any:
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, ToolCall):
        return {"ts": o.ts, "name": o.name, "input": dict(o.input), "tool_use_id": o.tool_use_id}
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(o).items()}
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in o]
    return o


def agent_result_to_json(res: AgentResult) -> dict[str, Any]:
    d = {}
    for f in dataclasses.fields(res):
        v = getattr(res, f.name)
        if f.name == "tool_calls":
            d[f.name] = [_jsonable(tc) for tc in v]
        else:
            d[f.name] = _jsonable(v)
    return d


def agent_result_from_json(d: dict[str, Any]) -> AgentResult:
    d = dict(d)
    d["transcript_path"] = Path(d["transcript_path"]) if d.get("transcript_path") else None
    d["stderr_path"] = Path(d["stderr_path"]) if d.get("stderr_path") else None
    d["outcome"] = Outcome(d["outcome"]) if d.get("outcome") else None
    d["tool_calls"] = [ToolCall(ts=t.get("ts"), name=t.get("name", "?"), input=t.get("input") or {},
                                tool_use_id=t.get("tool_use_id")) for t in d.get("tool_calls") or []]
    known = {f.name for f in dataclasses.fields(AgentResult)}
    return AgentResult(**{k: v for k, v in d.items() if k in known})


def write_json(path: Path, obj: Any) -> None:
    """Atomic JSON write (unique temp name, so concurrent writers never share one)."""
    p = Path(path)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(_jsonable(obj), indent=2, sort_keys=True, default=str) + "\n",
                   encoding="utf-8")
    tmp.replace(p)
