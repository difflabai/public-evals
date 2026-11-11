"""Builders shared by the core-analysis tests: verdicts, transcripts, a toy eval, a fake HTTP API."""

from __future__ import annotations

import http.server
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from dbwm.core import stream_json
from dbwm.core.types import (
    ArmSpec,
    Cell,
    Isolation,
    OracleToolSpec,
    ScoreResult,
    StagedFile,
    StagingManifest,
    ToolPolicy,
)
from dbwm.core.verdict import build_verdict

H64 = "a" * 64
H64B = "b" * 64

ORACLE_SPEC = OracleToolSpec(
    name="run_model", description="Run the model on a plan.",
    input_schema={"type": "object", "properties": {"plan": {"type": "object"}}, "required": ["plan"]},
    cli_name="run_model", cli_usage="./run_model plan.json")


def score(success: bool | None, *, present: bool = True, claimed: bool | None = None,
          claimed_paper: bool | None = None, status: str = "ok", ruleset: str = "v1",
          overclaim: bool | None = None, details: dict | None = None) -> ScoreResult:
    if overclaim is None and claimed is not None and success is not None:
        overclaim = bool(claimed and not success)
    return ScoreResult(deliverable_present=present, success=success, claimed_success=claimed,
                       claimed_success_paper=claimed if claimed_paper is None else claimed_paper,
                       claims_parse_status=status, overclaim=overclaim, ruleset=ruleset,
                       details=dict(details or {}))


def verdict(instance: str, arm: str, rep: int = 0, attempt: int = 1, outcome: str = "completed",
            sc: ScoreResult | None = None, ruleset: str = "v1", audit: dict | None = None,
            eval_id: str = "evalX") -> dict[str, Any]:
    cell = Cell(eval_id, instance, arm, rep)
    if sc is None and outcome in ("completed", "budget_exhausted", "clock_stop", "cost_stop"):
        sc = score(True, ruleset=ruleset)
    return build_verdict(cell=cell, attempt=attempt, outcome=outcome, score=sc, regime_hash=H64,
                         engine_hash=H64B, ruleset=None if sc is not None else ruleset, audit=audit)


def write_run_verdict(run_dir: Path, v: dict[str, Any], name: str = "verdict.json") -> Path:
    d = Path(run_dir) / "cells" / v["cell"]["cell_id"] / f"attempt-{v['attempt']}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps(v, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


# -- transcripts ------------------------------------------------------------------------

class Transcript:
    """Write a normalized stream-json transcript turn by turn."""

    def __init__(self, path: Path, *, tools: list[str], cwd: str, model: str = "claude-haiku-4-5-20251001",
                 **init_extra: Any):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict[str, Any]] = [stream_json.init_event(tools=tools, model=model, cwd=cwd,
                                                                   **init_extra)]
        self.model = model
        self.n = 0

    def tool(self, name: str, inp: dict[str, Any], result: str = "ok", is_error: bool = False) -> str:
        self.n += 1
        tid = f"toolu_{self.n}"
        self.events.append(stream_json.assistant_event(f"msg_{self.n}", self.model,
                                                       [stream_json.tool_use_block(tid, name, inp)]))
        self.events.append(stream_json.tool_result_event(tid, result, is_error))
        return tid

    def text(self, text: str) -> None:
        self.n += 1
        self.events.append(stream_json.assistant_event(f"msg_{self.n}", self.model,
                                                       [stream_json.text_block(text)]))

    def result(self, **kw: Any) -> None:
        fields = {"subtype": "success", "is_error": False, "num_turns": self.n + 1, "total_cost_usd": 0.01}
        fields.update(kw)
        self.events.append(stream_json.result_event(**fields))

    def write(self) -> Path:
        self.path.write_text("".join(json.dumps(e) + "\n" for e in self.events), encoding="utf-8")
        return self.path


# -- a toy eval for the audit -------------------------------------------------------------

@dataclass
class ToyEval:
    """Just enough of types.EvalDef for audit_attempt."""

    eval_id: str = "evalX"
    title: str = "toy"
    answer_values: list[str] = field(default_factory=lambda: ["7391.25", "SECRET-SEED-12345"])
    staged: list[str] = field(default_factory=lambda: ["TASK.md", "inputs/data.csv"])
    SCORER_ONLY_NAMES: tuple = ("reference.json", "world.json", "ANSWER_KEY", "checks.jsonl", "answer_key",
                                "grader", "private")

    def __post_init__(self) -> None:
        self.arms = {
            "nobash": ArmSpec("nobash", "read/write + oracle via MCP",
                              ToolPolicy(frozenset({"read", "write"}), (ORACLE_SPEC,))),
            "bash": ArmSpec("bash", "bash + oracle CLI",
                            ToolPolicy(frozenset({"read", "write", "bash"}), (ORACLE_SPEC,)),
                            min_isolation=Isolation.CONTAINER),
        }
        self.deliverables = ["plan.json", "claims.json"]

    def stage(self, cell: Cell) -> StagingManifest:
        return StagingManifest(files=[StagedFile(dst=d, content=b"x") for d in self.staged if d != "TASK.md"],
                               task_md="task")

    def answer_key_values(self, cell: Cell) -> list[str]:
        return list(self.answer_values)


# -- fake OpenAI-compatible HTTP API ---------------------------------------------------------

class FakeChatAPI:
    """Local stdlib HTTP server answering POST /v1/chat/completions from a script.

    ``script`` is a list of items: a dict (JSON response body, status 200), or
    a tuple (status, body_dict_or_str[, headers]). A callable item receives the
    request JSON and returns either form. Requests and headers are recorded.
    """

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        api = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:  # silence
                pass

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                api.requests.append(body)
                api.headers.append({k.lower(): v for k, v in self.headers.items()})
                item: Any = api.script.pop(0) if api.script else (500, {"error": "script exhausted"})
                if callable(item):
                    item = item(body)
                if isinstance(item, tuple):
                    status, payload = item[0], item[1]
                    extra_headers = item[2] if len(item) > 2 else {}
                else:
                    status, payload, extra_headers = 200, item, {}
                data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                for k, v in extra_headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __enter__(self) -> "FakeChatAPI":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()


def chat_response(content: str = "", tool_calls: list[tuple[str, dict]] | None = None, *,
                  model: str = "acme/model-1", finish_reason: str | None = None,
                  usage: dict | None = None) -> dict[str, Any]:
    calls = [{"id": f"call_{i}", "type": "function",
              "function": {"name": name, "arguments": json.dumps(args) if not isinstance(args, str) else args}}
             for i, (name, args) in enumerate(tool_calls or [])]
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return {"id": "chatcmpl-x", "model": model,
            "choices": [{"index": 0, "message": msg,
                         "finish_reason": finish_reason or ("tool_calls" if calls else "stop")}],
            "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 100,
                               "prompt_tokens_details": {"cached_tokens": 400}}}


def run_in_subprocess(code: str, env_extra: dict[str, str], python: str) -> str:
    import os
    import subprocess

    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run([python, "-c", code], env=env, capture_output=True, text=True, check=True).stdout


Caller = Callable[..., Any]
