"""Review finding m1: the sidecar's oracle and proxy listen only where the agent is.

No Docker needed here; tests/core/test_core_container_e2e.py checks the same thing
with real containers (a container on the default bridge cannot reach the sidecar).
"""

from __future__ import annotations

import socket

import pytest

from dbwm.core import netaddr, sidecar
from dbwm.core.egress_proxy import EgressProxy
from dbwm.core.oracle import client
from dbwm.core.oracle.server import OracleServer
from dbwm.core.types import Cell, OracleContext

TOKEN = "t" * 32


def test_client_allowed_and_parse():
    nets = netaddr.parse_networks(["172.18.0.0/16", "172.17.0.1"])
    assert netaddr.client_allowed("172.18.3.4", nets) and netaddr.client_allowed("172.17.0.1", nets)
    assert not netaddr.client_allowed("172.17.0.5", nets)  # another container on the default bridge
    assert not netaddr.client_allowed("127.0.0.1", nets) and not netaddr.client_allowed("not-an-ip", nets)
    assert netaddr.client_allowed("::ffff:172.18.0.9", nets)  # IPv4-mapped
    assert netaddr.client_allowed("10.0.0.1", ())  # no filter
    with pytest.raises(ValueError):
        netaddr.parse_networks(["300.1.1.1/8"])


def test_own_address_in():
    assert netaddr.own_address_in("127.0.0.0/8") == "127.0.0.1"
    assert netaddr.own_address_in("192.0.2.0/24") is None  # TEST-NET-1: no interface here
    with pytest.raises(TimeoutError):
        netaddr.wait_for_address_in("192.0.2.0/24", timeout_s=0.2, poll_s=0.05)


def test_listen_plan():
    base = {"proxy": {}, "oracle": {"host": "0.0.0.0"}}
    assert sidecar.listen_plan(base, None) == {"proxy_host": "0.0.0.0", "proxy_clients": [],
                                               "oracle_host": "0.0.0.0", "oracle_clients": []}
    cfg = {**base, "bind_subnet": "172.30.0.0/16"}
    assert sidecar.listen_plan(cfg, "172.30.0.2") == {"proxy_host": "172.30.0.2", "proxy_clients": ["172.30.0.0/16"],
                                                      "oracle_host": "172.30.0.2", "oracle_clients": ["172.30.0.0/16"]}
    pub = {**cfg, "publish_gateway": "172.17.0.1"}
    assert sidecar.listen_plan(pub, "172.30.0.2") == {
        "proxy_host": "172.30.0.2", "proxy_clients": ["172.30.0.0/16"],
        "oracle_host": "0.0.0.0", "oracle_clients": ["172.30.0.0/16", "172.17.0.1"]}


def _server(toy, tmp_path, allow):
    cell = Cell("toy", "alpha", "mcp-oracle", 0)
    arm = toy.arms["mcp-oracle"]
    ctx = OracleContext(cell, arm, arm.oracle_mode, tmp_path, {})
    return OracleServer(toy.oracle_handlers(cell), ctx, TOKEN, tmp_path / "calls.jsonl", 3,
                        specs=arm.tools.oracle_tools, allow_clients=allow)


def test_oracle_server_rejects_clients_outside_the_allowed_networks(toy, tmp_path):
    srv = _server(toy, tmp_path, ["10.255.0.0/16"])
    addr = srv.start()
    try:
        with pytest.raises(Exception):
            client.call(addr, TOKEN, "run_model", {"plan": {"x": 10}})
        assert srv.rejected_clients >= 1 and srv.calls_made == 0
    finally:
        srv.stop()
    ok = _server(toy, tmp_path, ["127.0.0.0/8"])
    addr = ok.start()
    try:
        assert client.call(addr, TOKEN, "run_model", {"plan": {"x": 10}}) == {"y": 73.1415}
    finally:
        ok.stop()


def test_egress_proxy_rejects_clients_outside_the_allowed_networks(tmp_path):
    log = tmp_path / "egress.jsonl"
    with EgressProxy(allow=["example.org"], log_path=log, host="127.0.0.1", port=0,
                     allow_clients=["10.255.0.0/16"]) as p:
        with socket.create_connection(("127.0.0.1", p.port), timeout=5) as s:
            s.sendall(b"CONNECT example.org:443 HTTP/1.1\r\nHost: example.org:443\r\n\r\n")
            try:
                got = s.recv(100)
            except ConnectionResetError:
                got = b""
            assert got == b""  # closed (or reset) without an answer
    assert "client not on the trial network" in log.read_text()
