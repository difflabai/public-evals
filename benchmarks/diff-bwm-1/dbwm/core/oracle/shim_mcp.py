#!/usr/bin/env python3
"""Stdio MCP server exposing task tools (JSON-RPC 2.0, newline-delimited)."""

import json
import os
import socket
import sys

SPECS = None  # replaced by the sandbox builder

SERVER_NAME = "dbwm"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2025-06-18"
_TIMEOUT_S = 600


def _address():
    addr = os.environ.get("DBWM_ORACLE", "")
    token = os.environ.get("DBWM_ORACLE_TOKEN", "")
    if not addr.startswith("tcp://") or not token:
        return None, None, None
    host, _, port = addr[len("tcp://"):].rpartition(":")
    try:
        return host.strip("[]"), int(port), token
    except ValueError:
        return None, None, None


def _call(op, payload):
    host, port, token = _address()
    if host is None:
        return {"ok": False, "error": "tool service unavailable", "result": None}
    req = {"token": token, "op": op, "payload": payload}
    try:
        with socket.create_connection((host, port), timeout=_TIMEOUT_S) as s:
            s.sendall((json.dumps(req) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.decode("utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "error": "tool service unavailable: %s" % e, "result": None}


def _coerce(arguments, schema):
    """Decode JSON-string arguments whose schema type is object/array.

    Transport-level only: some clients pass a nested object as a JSON string.
    The CLI path parses a JSON file, so both transports hand the service the
    same parsed value.
    """
    props = (schema or {}).get("properties") or {}
    out = dict(arguments)
    for key, sub in props.items():
        val = out.get(key)
        want = (sub or {}).get("type")
        if isinstance(val, str) and want in ("object", "array"):
            try:
                parsed = json.loads(val)
            except ValueError:
                continue
            if (want == "object" and isinstance(parsed, dict)) or (
                    want == "array" and isinstance(parsed, list)):
                out[key] = parsed
    return out


def _text(result):
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=2, sort_keys=True)


def _send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _error(mid, code, message):
    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def handle(msg):
    """Return the response dict for one JSON-RPC message, or None for notifications."""
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    method = msg.get("method")
    mid = msg.get("id")
    is_notification = "id" not in msg
    if is_notification:
        return None
    params = msg.get("params") or {}
    if method == "initialize":
        version = params.get("protocolVersion") or DEFAULT_PROTOCOL
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        tools = [{"name": s["name"], "description": s["description"],
                  "inputSchema": s["input_schema"]} for s in SPECS]
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
    if method == "tools/call":
        name = params.get("name")
        spec = None
        for s in SPECS:
            if s["name"] == name:
                spec = s
        if spec is None:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": "unknown tool: %s" % name}}
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": "arguments must be an object"}}
        reply = _call(name, _coerce(arguments, spec.get("input_schema")))
        if reply.get("ok"):
            content = [{"type": "text", "text": _text(reply.get("result"))}]
            if reply.get("calls_remaining") is not None:
                # Same information the CLI shim prints on stderr.
                content.append({"type": "text", "text": "calls remaining: %s" % reply.get("calls_remaining")})
            res = {"content": content, "isError": False}
        else:
            text = "error: %s" % reply.get("error")
            if reply.get("calls_remaining") is not None:
                text += "\ncalls remaining: %s" % reply.get("calls_remaining")
            res = {"content": [{"type": "text", "text": text}], "isError": True}
        return {"jsonrpc": "2.0", "id": mid, "result": res}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}


def main():
    stdin = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
    for raw in stdin:
        line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _error(None, -32700, "parse error")
            continue
        if isinstance(msg, list):  # JSON-RPC batch
            responses = [r for r in (handle(m) for m in msg) if r is not None]
            if responses:
                sys.stdout.write(json.dumps(responses) + "\n")
                sys.stdout.flush()
            continue
        resp = handle(msg)
        if resp is not None:
            _send(resp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
