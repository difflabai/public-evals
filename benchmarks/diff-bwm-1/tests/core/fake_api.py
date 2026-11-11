"""A local stand-in for the Anthropic Messages API, for checking the REAL Claude Code CLI without a model call.

Point the CLI at it with ``ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`` and a dummy
``ANTHROPIC_API_KEY``. It answers ``POST /v1/messages`` with a server-sent-event
stream: when the request offers the tool named ``call_tool`` and the conversation
has no tool result yet, the "model" calls it once with ``tool_input``; otherwise it
ends its turn with the text "done". Every request is recorded (path, model, offered
tool names, and a sha256 prefix of the credential header so tests can tell WHICH
credential arrived; never a header value, so no key is logged).
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


class FakeMessagesAPI:
    def __init__(self, call_tool: str | None = None, tool_input: dict[str, Any] | None = None) -> None:
        self.call_tool = call_tool
        self.tool_input = tool_input or {}
        self.requests: list[dict[str, Any]] = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                api.requests.append({"method": "GET", "path": self.path})
                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("content-length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    body = {}
                tools = [t.get("name") for t in body.get("tools") or []]
                has_result = any(isinstance(m.get("content"), list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
                    for m in body.get("messages") or [])
                cred = self.headers.get("x-api-key") or self.headers.get("authorization") or ""
                api.requests.append({"method": "POST", "path": self.path, "model": body.get("model"),
                                     "tools": tools, "has_tool_result": has_result,
                                     "auth_sha256": hashlib.sha256(cred.encode()).hexdigest()[:16]})
                if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = _sse(api._events(body.get("model") or "claude-test", tools, has_result,
                                        len(api.requests)))
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def _events(self, model: str, tools: list[str], has_result: bool, n: int) -> list[dict[str, Any]]:
        start = {"type": "message_start", "message": {
            "id": f"msg_fake_{n}", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}
        if self.call_tool and self.call_tool in tools and not has_result:
            block = {"type": "tool_use", "id": f"toolu_fake_{n}", "name": self.call_tool, "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(self.tool_input)}
            stop = "tool_use"
        else:
            block = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": "done"}
            stop = "end_turn"
        return [start,
                {"type": "content_block_start", "index": 0, "content_block": block},
                {"type": "content_block_delta", "index": 0, "delta": delta},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                 "usage": {"output_tokens": 2}},
                {"type": "message_stop"}]

    @staticmethod
    def auth_digest(value: str) -> str:
        """What ``auth_sha256`` holds for a credential header value."""
        return hashlib.sha256(value.encode()).hexdigest()[:16]

    @property
    def port(self) -> int:
        return int(self._srv.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}"

    def __enter__(self) -> "FakeMessagesAPI":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._srv.shutdown()
        self._srv.server_close()
