"""Sidecar container entry point: egress proxy + oracle server for one CONTAINER trial.

Run as ``python -m dbwm.core.sidecar --config /harness/sealed.json`` inside
the ``dbwm-agent`` image, with the package mounted read-only at /dbwm, the
sandbox read-only at /work-ro and the attempt directory read-write at
/harness. The sidecar is attached to the trial's ``--internal`` network
(alias ``dbwm-sidecar``) and to the default bridge for egress; the agent
container is attached to the internal network only, so this process is the
only thing it can talk to. The sealed config never enters the sandbox.

Config:
    {"proxy": {"allow": [...], "port": 3128, "log_path": "/harness/egress.jsonl"},
     "oracle": <server.server_from_config config> | null,
     "relay": {"port", "upstream", "auth_header", "pin", "extra_models", "placeholder",
               "log_path", "credential_env"} | absent,
     "bind_subnet": "<the trial network's subnet>" | absent,
     "publish_gateway": "<bridge gateway address>" | absent,
     "ready_file": "/harness/sidecar-ready.json",
     "error_file": "/harness/sidecar-error.txt"}

Listening addresses (review finding m1): with ``bind_subnet`` the sidecar waits
until the harness has attached it to the trial network, then binds the proxy
and the oracle to its address there and accepts only clients from that subnet,
so containers on the default bridge cannot reach either. ``publish_gateway`` is
set only when the oracle port is published to the host loopback (openai_compat
at CONTAINER): published connections arrive from the bridge gateway, so the
oracle then listens on all interfaces but accepts only the trial subnet and
that one gateway address. Without ``bind_subnet`` (older configs, tests) both
listen on 0.0.0.0 as before.

API relay (red-team finding RT-E): with ``relay`` the sidecar also runs
dbwm.core.api_relay on the proxy's address and client filter. The real
credential arrives ONLY in the environment variable named by
``credential_env`` (never in this config file, which is written to the attempt
directory); it is removed from ``os.environ`` once read.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import traceback
from pathlib import Path
from typing import Sequence

from dbwm.core import netaddr
from dbwm.core.api_relay import ApiRelay
from dbwm.core.egress_proxy import DEFAULT_PORT, EgressProxy
from dbwm.core.oracle.server import server_from_config, write_ready


def listen_plan(cfg: dict, own_ip: str | None) -> dict:
    """Where the proxy and the oracle listen and whom they accept (see the module docstring)."""
    subnet = cfg.get("bind_subnet")
    if not subnet or not own_ip:
        return {"proxy_host": (cfg.get("proxy") or {}).get("host", "0.0.0.0"), "proxy_clients": [],
                "oracle_host": (cfg.get("oracle") or {}).get("host", "0.0.0.0"), "oracle_clients": []}
    gw = cfg.get("publish_gateway")
    return {"proxy_host": own_ip, "proxy_clients": [subnet],
            "oracle_host": "0.0.0.0" if gw else own_ip,
            "oracle_clients": [subnet, gw] if gw else [subnet]}


def serve(cfg: dict, stop: threading.Event) -> None:
    own_ip = None
    if cfg.get("bind_subnet"):
        own_ip = netaddr.wait_for_address_in(cfg["bind_subnet"], float(cfg.get("bind_timeout_s", 60)))
    plan = listen_plan(cfg, own_ip)
    pcfg = cfg.get("proxy") or {}
    proxy = EgressProxy(allow=pcfg.get("allow") or [], log_path=pcfg.get("log_path", "egress.jsonl"),
                        host=plan["proxy_host"], port=int(pcfg.get("port", DEFAULT_PORT)),
                        allow_clients=plan["proxy_clients"])
    proxy.start()
    server = None
    relay = None
    try:
        if cfg.get("oracle"):
            server = server_from_config({**cfg["oracle"], "host": plan["oracle_host"],
                                         "allow_clients": plan["oracle_clients"]})
            server.start()
        if cfg.get("relay"):
            relay = start_relay(cfg["relay"], plan)
        write_ready(cfg.get("ready_file"), {"proxy_port": proxy.port,
                                            "oracle_port": server.port if server else None,
                                            "relay_port": relay.port if relay else None,
                                            "listen": plan, "pid": os.getpid()})
        while not stop.wait(0.5):
            pass
    finally:
        if relay is not None:
            relay.stop()
        if server is not None:
            server.stop()
        proxy.stop()


def start_relay(rcfg: dict, plan: dict) -> ApiRelay:
    """Start the API relay from its sealed config; the credential comes from the environment only."""
    var = rcfg.get("credential_env") or "DBWM_RELAY_CREDENTIAL"
    credential = os.environ.pop(var, None)
    if not credential:
        raise RuntimeError(f"relay configured but {var} is not set in the sidecar environment")
    relay = ApiRelay(credential=credential, placeholder=rcfg["placeholder"],
                     auth_header=rcfg.get("auth_header", "x-api-key"), pin=rcfg.get("pin"),
                     extra_models=rcfg.get("extra_models") or (),
                     upstream=rcfg.get("upstream") or "https://api.anthropic.com",
                     log_path=rcfg.get("log_path", "api_relay.jsonl"), host=plan["proxy_host"],
                     port=int(rcfg.get("port", 0)), allow_clients=plan["proxy_clients"])
    relay.start()
    return relay


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dbwm.core.sidecar")
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        serve(cfg, stop)
    except Exception:  # noqa: BLE001 - report to the harness, then fail
        err = cfg.get("error_file")
        if err:
            Path(err).write_text(traceback.format_exc(), encoding="utf-8")
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
