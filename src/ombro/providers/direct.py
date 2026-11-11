"""Direct page retrieval: a plain download, no key.

Only http and https. Addresses on this machine or a private network are
refused (at the first request and at every redirect), so a role cannot use
web.fetch to reach local services. Size and time are capped. HTML is turned
into plain text; other text types are passed through; binary types are refused.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import httpx

from ombro.providers import NotCalled, ProviderError, clip, http_client
from ombro.providers.html import html_to_text

MAX_BYTES = 3_000_000
MAX_REDIRECTS = 5
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml", "application/rss+xml",
              "application/atom+xml", "application/ld+json")  # fmt: skip

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


class DirectProvider:
    name = "direct"

    def __init__(self, *, transport: httpx.BaseTransport | None = None, resolver: Resolver = system_resolver):
        self.transport = transport
        self.resolver = resolver

    def fetch(self, url: str, max_chars: int, timeout: float) -> dict[str, Any]:
        current = url
        try:
            with http_client(self.transport, timeout) as http:
                for _ in range(MAX_REDIRECTS + 1):
                    check_url(current, self.resolver)
                    with http.stream("GET", current, headers={"Accept": "text/html,text/plain;q=0.9,*/*;q=0.5"}) as resp:
                        if resp.is_redirect:
                            location = resp.headers.get("location")
                            if not location:
                                raise ProviderError(f"the page returned HTTP {resp.status_code} with no location")
                            current = urljoin(current, location)
                            continue
                        if resp.status_code >= 400:
                            raise ProviderError(f"the page returned HTTP {resp.status_code}")
                        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                        if ctype and not ctype.startswith(TEXT_TYPES):
                            raise ProviderError(f"the page is {ctype}, which web.fetch cannot read")
                        body = bytearray()
                        for chunk in resp.iter_bytes():
                            body += chunk
                            if len(body) > MAX_BYTES:
                                break
                        text = bytes(body[:MAX_BYTES]).decode(resp.encoding or "utf-8", errors="replace")
                        too_big = len(body) > MAX_BYTES
                        break
                else:
                    raise ProviderError(f"more than {MAX_REDIRECTS} redirects")
        except httpx.TimeoutException:
            raise ProviderError("the page did not load in time") from None
        except httpx.HTTPError as e:
            raise ProviderError(f"could not fetch the page: {type(e).__name__}") from None

        title = ""
        if ctype in ("text/html", "application/xhtml+xml") or (not ctype and "<html" in text[:2000].lower()):
            title, text = html_to_text(text)
        content, truncated = clip(text, max_chars)
        return {"url": current, "title": title, "content": content, "truncated": truncated or too_big}
