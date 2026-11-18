"""Which web addresses Ombro's tools may reach: public http and https addresses only.

The browser (ombro.browser) checks every request a page makes here, so neither a role nor a
page can reach this machine or the local network. (Until 18 Nov 2026 a plain download,
the `direct` provider, used it too; web pages are now always read in the browser.)
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import urlparse

from ombro.providers import NotCalled

Resolver = Callable[[str, int], list[str]]


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise NotCalled(f"the address {host} could not be found") from None
    return sorted({info[4][0] for info in infos})


def check_url(url: str, resolver: Resolver) -> None:
    """Refuse URLs that are not public http(s) addresses."""
    try:
        parsed = urlparse(url)
    except ValueError:
        raise NotCalled("that is not a valid URL") from None
    if parsed.scheme not in ("http", "https"):
        raise NotCalled("only http and https URLs can be fetched")
    if not parsed.hostname:
        raise NotCalled("the URL has no host")
    if parsed.username or parsed.password:
        raise NotCalled("URLs with a user name or password are refused")
    host = parsed.hostname
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise NotCalled(f"{host} is on this machine or a private network, so it is refused")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise NotCalled("the URL has an invalid port") from None
    try:
        addresses = [host] if _is_ip(host) else resolver(host, port)
    except NotCalled:
        raise
    except Exception:
        raise NotCalled(f"the address {host} could not be found") from None
    if not addresses:
        raise NotCalled(f"the address {host} could not be found")
    for a in addresses:
        ip = ipaddress.ip_address(a.split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise NotCalled(f"{host} is on this machine or a private network, so it is refused")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
