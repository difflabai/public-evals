"""Address helpers for the CONTAINER sidecar's listeners (stdlib only).

Why (review finding m1, 2026-10-02): the sidecar is started on Docker's default
``bridge`` network (for egress and port publishing) and then attached to the
trial's ``--internal`` network. Listening on 0.0.0.0 made the token-protected
oracle and the CONNECT proxy reachable from ANY container on the bridge
(another trial's sidecar, an unrelated container). The sidecar now binds to its
address on the trial network (found with ``own_address_in``) and, where it must
listen more widely (the oracle port published to the host for openai_compat),
accepts only clients in the allowed networks (``client_allowed``).
"""

from __future__ import annotations

import ipaddress
import socket
import time
from typing import Iterable, Sequence

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(entries: Iterable[str] | None) -> tuple[Network, ...]:
    """'172.18.0.0/16' or a bare address ('172.17.0.1' -> /32) -> networks. Raises ValueError."""
    return tuple(ipaddress.ip_network(str(e).strip(), strict=False) for e in (entries or ()) if str(e).strip())


def client_allowed(host: str, nets: Sequence[Network]) -> bool:
    """True if ``nets`` is empty (no filter) or ``host`` is inside one of them."""
    if not nets:
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip.version == n.version and ip in n for n in nets)


def own_address_in(subnet: str) -> str | None:
    """This host's address on ``subnet``, or None if no interface is on it (yet).

    A UDP ``connect`` sends nothing; it only makes the kernel pick the source
    address of the route to the subnet. Before the interface exists that route is
    the default one, so the address falls outside the subnet and None is returned.
    """
    net = ipaddress.ip_network(subnet, strict=False)
    probe = net.network_address + 1 if net.num_addresses > 1 else net.network_address
    fam = socket.AF_INET if net.version == 4 else socket.AF_INET6
    try:
        with socket.socket(fam, socket.SOCK_DGRAM) as s:
            s.connect((str(probe), 9))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return ip if client_allowed(ip, (net,)) else None


def wait_for_address_in(subnet: str, timeout_s: float = 60.0, poll_s: float = 0.1) -> str:
    """Poll ``own_address_in`` until the interface appears; raise TimeoutError after ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    while True:
        ip = own_address_in(subnet)
        if ip is not None:
            return ip
        if time.monotonic() > deadline:
            raise TimeoutError(f"no interface on {subnet} after {timeout_s:g} s")
        time.sleep(poll_s)


__all__ = ["client_allowed", "own_address_in", "parse_networks", "wait_for_address_in"]
