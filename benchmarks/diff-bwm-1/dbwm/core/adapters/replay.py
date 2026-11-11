"""Replay adapter: copies a recorded submission into the sandbox instead of running an agent.

Used by tests: a recorded submission goes through the same collect -> score
-> verdict path as a live run.

Where the fixture comes from, first match wins:
  1. ``req.extra["replay_dir"]`` (a directory);
  2. the ``source`` given to the constructor: a callable ``cell -> Path|None``,
     or a root directory searched as ``<root>/<cell_id>``,
     ``<root>/<instance>/<arm>/r<rep>``, ``<root>/<instance>/<arm>``,
     ``<root>/<instance>``.
The fixture directory's contents are copied into the sandbox root (existing
staged files are not overwritten). A missing fixture is a CRASH with the
reason in error_text, never a silent "no deliverable".
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Callable, Union

from dbwm.core import stream_json
from dbwm.core.adapters.base import stderr_path, transcript_path
from dbwm.core.types import AgentRequest, AgentResult, Cell

REPLAY_MODEL = "dbwm-replay"
Source = Union[Path, str, Callable[[Cell], Union[Path, None]], None]


class ReplayAdapter:
    name = "replay"

    def __init__(self, source: Source = None):
        self.source = source

    def find(self, req: AgentRequest) -> Path | None:
        if req.extra.get("replay_dir"):
            p = Path(req.extra["replay_dir"])
            return p if p.is_dir() else None
        src = self.source
        if src is None:
            return None
        if callable(src):
            p = src(req.cell)
            return Path(p) if p is not None and Path(p).is_dir() else None
        root = Path(src)
        c = req.cell
        for cand in (root / c.cell_id, root / c.instance / c.arm / f"r{c.rep}",
                     root / c.instance / c.arm, root / c.instance):
            if cand.is_dir():
                return cand
        return None

    def run(self, req: AgentRequest) -> AgentResult:
        tpath, epath = transcript_path(req), stderr_path(req)
        Path(req.harness_out).mkdir(parents=True, exist_ok=True)
        res = AgentResult(transcript_path=tpath, stderr_path=epath, returncode=None,
                          model_requested=req.model, cli_version=f"replay/{REPLAY_MODEL}")
        t0 = time.monotonic()
        fixture = self.find(req)
        events = [stream_json.init_event(tools=stream_json.claude_tool_names(req.tools), model=req.model or REPLAY_MODEL,
                                         cwd=str(req.sandbox), mcp_servers=[], replay_fixture=str(fixture))]
        if fixture is None:
            res.error_text = f"replay fixture not found for {req.cell.cell_id}"
            epath.write_text(res.error_text + "\n", encoding="utf-8")
            with open(tpath, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(events[0], sort_keys=True) + "\n")
            res.returncode = 1
            res.duration_s = round(time.monotonic() - t0, 3)
            summary = stream_json.parse_file(tpath)
            stream_json.fill_result(summary, res)
            return res
        copied = []
        for p in sorted(fixture.rglob("*")):
            rel = p.relative_to(fixture)
            dst = Path(req.sandbox) / rel
            if p.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
            elif p.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dst)
                copied.append(rel.as_posix())
        events.append(stream_json.result_event(subtype="success", is_error=False, num_turns=0,
                                               total_cost_usd=0.0, result_text="replayed",
                                               replayed_files=copied))
        with open(tpath, "w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e, sort_keys=True) + "\n")
        epath.write_text("", encoding="utf-8")
        res.returncode = 0
        res.duration_s = round(time.monotonic() - t0, 3)
        summary = stream_json.parse_file(tpath)
        stream_json.fill_result(summary, res)
        return res
