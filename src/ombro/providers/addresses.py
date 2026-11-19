"""Which web addresses Ombro's tools may reach: public http and https addresses only.

The browser (ombro.browser) checks every request a page makes here, and its proxy
(ombro.proxy) checks each connection's address again with checked_addresses and connects to
that address, so neither a role nor a page can reach this machine or the local network. (Until 18 Nov 2026 a plain download,
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
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise NotCalled("the URL has an invalid port") from None
    checked_addresses(parsed.hostname, port, resolver)


def checked_addresses(host: str, port: int, resolver: Resolver) -> list[str]:
    """The addresses `host` resolves to, once, if every one is public; NotCalled otherwise.
    The browser's proxy connects to one of exactly these, so a name that answers differently
    the next time (DNS rebinding) can't lead it anywhere it didn't check."""
    host = host.strip("[]").lower().rstrip(".")
    if not host:
        raise NotCalled("the URL has no host")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise NotCalled(f"{host} is on this machine or a private network, so it is refused")
    try:
        addresses = [host] if _is_ip(host) else resolver(host, port)
    except NotCalled:
        raise
    except Exception:
        raise NotCalled(f"the address {host} could not be found") from None
    if not addresses:
        raise NotCalled(f"the address {host} could not be found")
    for a in addresses:
        if not is_public(a):
            raise NotCalled(f"{host} is on this machine or a private network, so it is refused")
    return list(addresses)


def is_public(address: str) -> bool:
    """A public unicast address: not this machine, the local network, link-local (cloud
    metadata), multicast or reserved. IPv4-mapped IPv6 is judged as the IPv4 it carries."""
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
