"""In-process deterministic agent: calls ``EVAL.stub_agent(cell, sandbox, call_oracle)``.

Used by selftests and CI to drive every arm end to end (sandbox -> oracle
server -> deliverables -> score -> verdict) with no LLM. ``call_oracle(op,
payload)`` goes through the real oracle server over TCP, so the token, the
sealed mode, override logging and the query budget all apply exactly as for a
live agent. It returns the result on ``ok: true`` and raises
``dbwm.core.oracle.client.OracleError`` (with ``.reply`` and
``.calls_remaining``) on ``ok: false``; a stub that wants to respect a query
budget catches it.

The transcript is written in the normalized stream subset (stream_json): an
init event, one assistant/tool_use event per oracle call (named
``mcp__dbwm__<op>``), and a result event. An exception in the stub is a CRASH
(traceback in stderr.txt); exceeding the wall clock is a CLOCK_STOP (the
thread cannot be killed; it is abandoned as a daemon).
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from dbwm.core import stream_json
from dbwm.core.adapters.base import stderr_path, transcript_path
from dbwm.core.oracle.client import OracleError, make_caller
from dbwm.core.types import AgentRequest, AgentResult, EvalDef

STUB_MODEL = "dbwm-stub"


class StubAdapter:
    name = "stub"

    def __init__(self, eval_def: EvalDef | None = None):
        self.eval_def = eval_def

    def run(self, req: AgentRequest) -> AgentResult:
        ev: EvalDef | None = req.extra.get("eval_def") or self.eval_def
        tpath, epath = transcript_path(req), stderr_path(req)
        Path(req.harness_out).mkdir(parents=True, exist_ok=True)
        res = AgentResult(transcript_path=tpath, stderr_path=epath, returncode=None,
                          model_requested=req.model, cli_version=f"stub/{STUB_MODEL}")
        events: list[dict[str, Any]] = []
        names = stream_json.claude_tool_names(req.tools)
        # The "served model" of an in-process agent is whatever the regime
        # names (default dbwm-stub), so the pin check is trivially consistent.
        model = req.model or STUB_MODEL
        events.append(stream_json.init_event(tools=names, model=model, cwd=str(req.sandbox),
                                             mcp_servers=[{"name": "dbwm", "status": "connected"}]
                                             if req.tools.transport == "mcp" and req.tools.oracle_tools else []))
        lock = threading.Lock()
        counter = {"n": 0}
        base_call: Callable[..., Any] | None = None
        if req.oracle_address and req.oracle_token:
            base_call = make_caller(req.oracle_address, req.oracle_token)

        def call_oracle(op: str, payload: dict | None = None) -> Any:
            if base_call is None:
                raise OracleError("this arm has no oracle tools", {"ok": False, "error": "no oracle"})
            with lock:
                counter["n"] += 1
                n = counter["n"]
            tid = f"stub_tool_{n}"
            # Name the call the way a real agent would make it on this arm:
            # an MCP tool on no-bash arms, a Bash call of the CLI shim otherwise.
            spec = next((s for s in req.tools.oracle_tools if s.name == op), None)
            if req.tools.transport == "cli" and spec is not None:
                block = stream_json.tool_use_block(tid, "Bash", {"command": f"./{spec.cli_name}",
                                                                 "stub_payload": payload or {}})
            else:
                block = stream_json.tool_use_block(tid, stream_json.MCP_PREFIX + op, payload or {})
            events.append(stream_json.assistant_event(f"stub_msg_{n}", model, [block]))
            try:
                result = base_call(op, payload or {})
            except OracleError as e:
                events.append(stream_json.tool_result_event(tid, f"error: {e}", is_error=True))
                raise
            events.append(stream_json.tool_result_event(
                tid, result if isinstance(result, str) else json.dumps(result, sort_keys=True)))
            return result

        err: dict[str, str] = {}

        def target() -> None:
            try:
                if ev is None:
                    raise RuntimeError("stub adapter needs the eval definition")
                ev.stub_agent(req.cell, Path(req.sandbox), call_oracle)
            except Exception:  # noqa: BLE001 - a stub bug is a CRASH, recorded verbatim
                err["tb"] = traceback.format_exc()

        t0 = time.monotonic()
        th = threading.Thread(target=target, name=f"dbwm-stub-{req.cell.cell_id}", daemon=True)
        th.start()
        th.join(timeout=req.budget.wall_clock_s)
        res.duration_s = round(time.monotonic() - t0, 3)
        n_turns = counter["n"] + 1
        if th.is_alive():
            res.killed_by_harness = "clock"
        elif "tb" in err:
            res.returncode = 1
            res.error_text = err["tb"].strip().splitlines()[-1]
        else:
            res.returncode = 0
            events.append(stream_json.assistant_event(f"stub_msg_{n_turns}", model,
                                                      [stream_json.text_block("done")]))
            events.append(stream_json.result_event(subtype="success", is_error=False, num_turns=n_turns,
                                                   total_cost_usd=0.0, result_text="stub agent finished"))
        with open(tpath, "w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e, sort_keys=True, default=str) + "\n")
        epath.write_text(err.get("tb", ""), encoding="utf-8")
        summary = stream_json.parse_file(tpath)
        stream_json.fill_result(summary, res)
        if "tb" in err:
            res.raw_result_event = None
            res.result_subtype = None
        return res
