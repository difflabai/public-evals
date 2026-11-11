#!/usr/bin/env python3
"""Command-line client for a task tool. Run with --help for usage."""

import json
import os
import socket
import sys

SPEC = None  # replaced by the sandbox builder

_TIMEOUT_S = 600


def _usage():
    return "usage: " + SPEC["usage"]


def _help():
    return _usage() + "\n\n" + SPEC["description"].strip() + "\n"


def _address():
    addr = os.environ.get("DBWM_ORACLE", "")
    token = os.environ.get("DBWM_ORACLE_TOKEN", "")
    if not addr.startswith("tcp://") or not token:
        return None, None, None
    hostport = addr[len("tcp://"):]
    host, _, port = hostport.rpartition(":")
    try:
        return host.strip("[]"), int(port), token
    except ValueError:
        return None, None, None


def _request(host, port, token, payload):
    req = {"token": token, "op": SPEC["op"], "payload": payload}
    with socket.create_connection((host, port), timeout=_TIMEOUT_S) as s:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode("utf-8"))


def main(argv):
    args = list(argv)
    if any(a in ("-h", "--help") for a in args):
        sys.stdout.write(_help())
        return 0
    for a in args:
        if a.startswith("-"):
            sys.stderr.write("unknown option: %s\n%s\n" % (a, _usage()))
            return 2
    if SPEC["args"] == "json_file":
        if len(args) != 1:
            sys.stderr.write(_usage() + "\n")
            return 2
        try:
            with open(args[0], "r", encoding="utf-8") as fh:
                content = json.load(fh)
        except FileNotFoundError:
            sys.stderr.write("file not found: %s\n" % args[0])
            return 2
        except (OSError, ValueError) as e:
            sys.stderr.write("could not read %s as JSON: %s\n" % (args[0], e))
            return 2
        payload = {SPEC["payload_key"]: content}
    else:
        if args:
            sys.stderr.write(_usage() + "\n")
            return 2
        payload = {}
    host, port, token = _address()
    if host is None:
        sys.stderr.write("tool service unavailable\n")
        return 3
    try:
        reply = _request(host, port, token, payload)
    except (OSError, ValueError) as e:
        sys.stderr.write("tool service unavailable: %s\n" % e)
        return 3
    remaining = reply.get("calls_remaining")
    if not reply.get("ok"):
        sys.stderr.write("error: %s\n" % reply.get("error"))
        if remaining is not None:
            sys.stderr.write("calls remaining: %s\n" % remaining)
        return 1
    result = reply.get("result")
    if isinstance(result, str):
        sys.stdout.write(result if result.endswith("\n") else result + "\n")
    else:
        sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if remaining is not None:
        sys.stderr.write("calls remaining: %s\n" % remaining)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
