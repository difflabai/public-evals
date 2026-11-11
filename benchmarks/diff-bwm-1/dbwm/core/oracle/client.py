"""Harness-side client for the per-trial oracle server (stub agents, tests, adapters).

Protocol (one JSON object per line over TCP, see server.py):
    request  {"token": str, "op": str, "payload": {...}}
    reply    {"ok": bool, "result": any, "error": str|None, "calls_remaining": int|None}
"""

from __future__ import annotations

import json
import socket
from typing import Any, Mapping

DEFAULT_TIMEOUT_S = 600.0


class OracleError(RuntimeError):
    """The server answered ``ok: false``. ``reply`` holds the full reply."""

    def __init__(self, message: str, reply: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.reply = dict(reply or {})

    @property
    def calls_remaining(self) -> int | None:
        return self.reply.get("calls_remaining")


def parse_address(address: str) -> tuple[str, int]:
    """``tcp://host:port`` -> (host, port)."""
    if not address or not address.startswith("tcp://"):
        raise ValueError(f"oracle address must look like tcp://host:port, got {address!r}")
    host, sep, port = address[len("tcp://"):].rpartition(":")
    if not sep or not host:
        raise ValueError(f"oracle address missing host or port: {address!r}")
    return host.strip("[]"), int(port)


def request(address: str, token: str, op: str, payload: Mapping[str, Any] | None = None,
            timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Send one request and return the full reply dict (never raises on ok:false)."""
    host, port = parse_address(address)
    req = {"token": token, "op": op, "payload": dict(payload or {})}
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    if not buf:
        raise ConnectionError("oracle server closed the connection without replying")
    return json.loads(buf.decode("utf-8"))


def call(address: str, token: str, op: str, payload: Mapping[str, Any] | None = None,
         timeout: float = DEFAULT_TIMEOUT_S) -> Any:
    """Send one request; return ``result`` or raise OracleError on ``ok: false``."""
    reply = request(address, token, op, payload, timeout=timeout)
    if not reply.get("ok"):
        raise OracleError(str(reply.get("error")), reply)
    return reply.get("result")


def make_caller(address: str, token: str, timeout: float = DEFAULT_TIMEOUT_S):
    """A ``call_oracle(op, payload)`` function bound to one trial's server."""

    def call_oracle(op: str, payload: Mapping[str, Any] | None = None) -> Any:
        return call(address, token, op, payload, timeout=timeout)

    return call_oracle
