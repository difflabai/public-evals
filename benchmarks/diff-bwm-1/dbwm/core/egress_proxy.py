"""Allowlisting HTTP CONNECT proxy: the agent container's only route out.

Why: in the paper's harness, network access was withheld only by instruction
(evals 1/4/5/6 had WebFetch/WebSearch available and full user settings), and eval 4's grading rubric is public on the internet. At
CONTAINER isolation the agent sits on an ``--internal`` docker network whose
only reachable peer is the sidecar; ``HTTPS_PROXY`` points at this proxy, which
tunnels CONNECT requests to allowlisted host:port pairs and refuses everything
else with 403. Every request, allowed or not, is appended to ``egress.jsonl``
{ts, client, method, host, port, allowed, status, bytes_up, bytes_down, error}
so the audit can flag denials.

Allowlist entries are ``host`` (port 443 implied) or ``host:port``; matching
is exact and case-insensitive (no wildcards, no IP ranges). Plain-HTTP proxy
requests (GET http://...) are always refused.
"""

from __future__ import annotations

import json
import select
import socket
import socketserver
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from dbwm.core import netaddr

DEFAULT_ALLOW = ("api.anthropic.com",)
DEFAULT_PORT = 3128
MAX_HEADER_BYTES = 16 * 1024
CONNECT_TIMEOUT_S = 15
IDLE_TIMEOUT_S = 600


def _url_host(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlsplit

    try:
        u = urlsplit(url if "://" in url else f"https://{url}")
        host = u.hostname
        return f"{host}:{u.port}" if host and u.port and u.port != 443 else host
    except ValueError:
        return None


def auth_mode_hosts(auth_mode: str, environ: Mapping[str, str]) -> list[str]:
    """Endpoints the CLI needs for ``auth_mode`` beyond DEFAULT_ALLOW (review finding m7).

    REASONED FROM THE PROVIDERS' DOCUMENTED ENDPOINTS, NOT VERIFIED LIVE: no
    Bedrock, Vertex or OAuth credentials existed on the build machine; only the
    proxy mechanics were tested. Anything else the CLI needs is denied with a 403,
    logged in egress.jsonl (and noted by the audit); add it with --egress-allow.

    - bedrock: ``bedrock-runtime.<region>.amazonaws.com`` and
      ``bedrock.<region>.amazonaws.com`` for AWS_REGION (else AWS_DEFAULT_REGION),
      plus the host of ANTHROPIC_BEDROCK_BASE_URL if set. No region: nothing
      (the run fails visibly; set AWS_REGION or --egress-allow).
    - vertex: ``<region>-aiplatform.googleapis.com`` for CLOUD_ML_REGION
      (``aiplatform.googleapis.com`` for ``global``), ``oauth2.googleapis.com``
      (service-account token exchange), plus the host of ANTHROPIC_VERTEX_BASE_URL.
    - api_key, oauth_token, session, none: nothing beyond api.anthropic.com.
    """
    out: list[str] = []
    if auth_mode == "bedrock":
        region = environ.get("AWS_REGION") or environ.get("AWS_DEFAULT_REGION")
        if region:
            out += [f"bedrock-runtime.{region}.amazonaws.com", f"bedrock.{region}.amazonaws.com"]
        out.append(_url_host(environ.get("ANTHROPIC_BEDROCK_BASE_URL")) or "")
    elif auth_mode == "vertex":
        region = environ.get("CLOUD_ML_REGION")
        if region:
            out.append("aiplatform.googleapis.com" if region == "global" else f"{region}-aiplatform.googleapis.com")
        out.append("oauth2.googleapis.com")
        out.append(_url_host(environ.get("ANTHROPIC_VERTEX_BASE_URL")) or "")
    return [h for h in dict.fromkeys(out) if h]


def parse_allow(entries: Iterable[str]) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for e in entries:
        e = str(e).strip().lower()
        if not e:
            continue
        host, sep, port = e.rpartition(":")
        if sep and port.isdigit() and host:
            out.add((host.strip("[]"), int(port)))
        else:
            out.add((e, 443))
    return out


class EgressProxy:
    def __init__(self, allow: Iterable[str] = DEFAULT_ALLOW, log_path: Path | str = "egress.jsonl",
                 host: str = "0.0.0.0", port: int = DEFAULT_PORT, allow_clients: Iterable[str] = ()):
        self.allow = parse_allow(allow)
        self.log_path = Path(log_path)
        self._host = host
        self._port = port
        # Client networks allowed to use the proxy (empty: any); see dbwm.core.netaddr (review m1).
        self.allow_clients = netaddr.parse_networks(allow_clients)
        self._lock = threading.Lock()
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None

    def allowed(self, host: str, port: int) -> bool:
        return (host.lower().strip("[]"), int(port)) in self.allow

    def start(self) -> int:
        proxy = self

        class _Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                proxy._handle(self.request, self.client_address)

        class _Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True
            # socketserver's default listen backlog is 5; parallel tool calls
            # (or the CLI's parallel HTTPS connections) must not be reset.
            request_queue_size = 128

            def verify_request(self, request: Any, client_address: Any) -> bool:
                if netaddr.client_allowed(str(client_address[0]), proxy.allow_clients):
                    return True
                proxy._log({"client": str(client_address[0]), "method": None, "host": None, "port": None,
                            "allowed": False, "status": None, "error": "client not on the trial network"})
                return False

        self._server = _Server((self._host, self._port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="dbwm-egress",
                                        daemon=True)
        self._thread.start()
        return self.port

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("proxy not started")
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "EgressProxy":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- per connection ---------------------------------------------------------

    def _log(self, rec: dict) -> None:
        rec = {"ts": round(time.time(), 6), **rec}
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def _handle(self, client: socket.socket, addr: tuple) -> None:
        client.settimeout(30)
        buf = b""
        try:
            while b"\r\n\r\n" not in buf:
                chunk = client.recv(4096)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > MAX_HEADER_BYTES:
                    self._send(client, 431, "Request Header Fields Too Large")
                    self._log({"client": addr[0], "method": None, "host": None, "port": None,
                               "allowed": False, "status": 431, "error": "header too large"})
                    return
        except OSError:
            return
        head, _, rest = buf.partition(b"\r\n\r\n")
        first = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = first.split()
        method = parts[0].upper() if parts else ""
        target = parts[1] if len(parts) > 1 else ""
        if method != "CONNECT":
            host = _host_from_url(target)
            self._send(client, 403, "Forbidden")
            self._log({"client": addr[0], "method": method, "host": host, "port": None,
                       "allowed": False, "status": 403, "error": "only CONNECT is supported"})
            return
        host, sep, port_s = target.rpartition(":")
        if not sep or not port_s.isdigit():
            self._send(client, 400, "Bad Request")
            self._log({"client": addr[0], "method": method, "host": target, "port": None,
                       "allowed": False, "status": 400, "error": "bad CONNECT target"})
            return
        host = host.strip("[]")
        port = int(port_s)
        if not self.allowed(host, port):
            self._send(client, 403, "Forbidden")
            self._log({"client": addr[0], "method": method, "host": host, "port": port,
                       "allowed": False, "status": 403})
            return
        try:
            upstream = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S)
        except OSError as e:
            self._send(client, 502, "Bad Gateway")
            self._log({"client": addr[0], "method": method, "host": host, "port": port,
                       "allowed": True, "status": 502, "error": f"{type(e).__name__}: {e}"})
            return
        try:
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
            up, down = self._relay(client, upstream)
            up += len(rest)
        except OSError as e:
            self._log({"client": addr[0], "method": method, "host": host, "port": port,
                       "allowed": True, "status": 200, "error": f"{type(e).__name__}: {e}"})
            return
        finally:
            try:
                upstream.close()
            except OSError:
                pass
        self._log({"client": addr[0], "method": method, "host": host, "port": port,
                   "allowed": True, "status": 200, "bytes_up": up, "bytes_down": down})

    @staticmethod
    def _send(client: socket.socket, code: int, reason: str) -> None:
        body = f"{code} {reason}\n".encode()
        try:
            client.sendall(f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain\r\n"
                           f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        except OSError:
            pass

    @staticmethod
    def _relay(a: socket.socket, b: socket.socket) -> tuple[int, int]:
        up = down = 0
        a.settimeout(None)
        b.settimeout(None)
        socks = [a, b]
        last = time.monotonic()
        while True:
            r, _, x = select.select(socks, [], socks, 5.0)
            if x:
                break
            if not r:
                if time.monotonic() - last > IDLE_TIMEOUT_S:
                    break
                continue
            last = time.monotonic()
            done = False
            for s in r:
                try:
                    data = s.recv(65536)
                except OSError:
                    data = b""
                if not data:
                    done = True
                    break
                if s is a:
                    b.sendall(data)
                    up += len(data)
                else:
                    a.sendall(data)
                    down += len(data)
            if done:
                break
        return up, down


def _host_from_url(url: str) -> str | None:
    if "://" in url:
        rest = url.split("://", 1)[1]
        return rest.split("/", 1)[0].split(":")[0] or None
    return None


def read_egress_log(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
