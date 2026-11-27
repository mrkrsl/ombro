"""DuckDuckGo search through its HTML results page. No key.

Unofficial and rate-limited, so best effort: when DuckDuckGo refuses (a bot
check or a rate limit), the role is told plainly. Ombro does not try to get
round a refusal.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import httpx

from ombro.providers import ProviderError, http_client

ENDPOINT = "https://html.duckduckgo.com/html/"
REFUSED = (
    "DuckDuckGo refused the search (a rate limit or bot check). Try again later with fewer searches, "
    "or ask the user to choose another search in Settings › Tools (in the browser, or a search service)."
)


class _ResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._field: str | None = None
        self._depth = 0
        self.blocked = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if "anomaly-modal" in " ".join(classes) or (tag == "form" and "challenge-form" in (a.get("id") or "")):
            self.blocked = True
        if self._field:
            if tag not in ("br", "img", "wbr", "hr", "input", "meta", "link"):
                self._depth += 1
            return
        if tag == "a" and "result__a" in classes:
            self.results.append({"title": "", "url": _unwrap(a.get("href") or ""), "snippet": ""})
            self._field, self._depth = "title", 0
        elif "result__snippet" in classes and self.results:
            self._field, self._depth = "snippet", 0

    def handle_endtag(self, tag):
        if self._field:
            if self._depth == 0:
                self._field = None
            else:
                self._depth -= 1

    def handle_data(self, data):
        if self._field and self.results:
            self.results[-1][self._field] += data


def _is_ddg(url: str) -> bool:
    """DuckDuckGo itself or a subdomain, not just any host ending in its name."""
    host = (urlparse(url).hostname or "").lower()
    return host == "duckduckgo.com" or host.endswith(".duckduckgo.com")


def _unwrap(href: str) -> str:
    """Result links go through a DuckDuckGo redirect (/l/?uddg=<target>)."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if _is_ddg(href) and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


def parse_results(html: str) -> tuple[list[dict[str, str]], bool]:
    p = _ResultParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    out = []
    for r in p.results:
        url = r["url"]
        if not url.startswith(("http://", "https://")) or _is_ddg(url):
            continue  # adverts and internal links
        out.append({"title": " ".join(r["title"].split()), "url": url, "snippet": " ".join(r["snippet"].split())})
    return out, p.blocked


class DuckDuckGoProvider:
    name = "duckduckgo"

    def __init__(self, *, endpoint: str = ENDPOINT, transport: httpx.BaseTransport | None = None):
        self.endpoint = endpoint
        self.transport = transport

    def search(self, query: str, limit: int, timeout: float) -> list[dict[str, str]]:
        try:
            with http_client(self.transport, timeout) as http:
                resp = http.post(self.endpoint, data={"q": query, "kl": "wt-wt"})
        except httpx.TimeoutException:
            raise ProviderError("DuckDuckGo did not answer in time") from None
        except httpx.HTTPError as e:
            raise ProviderError(f"could not reach DuckDuckGo: {type(e).__name__}") from None
        if resp.status_code in (202, 403, 429):
            raise ProviderError(REFUSED)
        if resp.status_code != 200:
            raise ProviderError(f"DuckDuckGo returned HTTP {resp.status_code}")
        results, blocked = parse_results(resp.text)
        if blocked and not results:
            raise ProviderError(REFUSED)
        return results[:limit]
