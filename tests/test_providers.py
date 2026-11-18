"""Providers against mock HTTP transports. Every response here is invented; nothing touches the network."""

from __future__ import annotations

import json

import httpx
import pytest

from ombro import keys
from ombro.native import native_registry
from ombro.providers import NotCalled, ProviderError
from ombro.providers.addresses import check_url
from ombro.providers.duckduckgo import DuckDuckGoProvider, parse_results
from ombro.providers.firecrawl import FirecrawlProvider
from ombro.settings import ToolSettings

PUBLIC_IP = "151.101.1.1"  # a public address string; never contacted, the transport is mocked


def public(host, port):
    return [PUBLIC_IP]


# -- Firecrawl --------------------------------------------------------------------


def firecrawl(handler, key="fc-invented-key-123456"):
    seen = []

    def wrapped(request: httpx.Request):
        seen.append(request)
        return handler(request)

    p = FirecrawlProvider(lambda: key, "FIRECRAWL_API_KEY", transport=httpx.MockTransport(wrapped))
    return p, seen


def test_firecrawl_search():
    body = {"success": True, "data": {"web": [
        {"title": "Ada Pots", "description": "Invented maker.", "url": "https://ada-pots.invalid/"},
        {"title": "No URL here"},
        {"title": ["Brin Ware"], "url": "https://brin.invalid/", "description": None},
    ]}, "creditsUsed": 2}
    p, seen = firecrawl(lambda r: httpx.Response(200, json=body))
    results = p.search("invented teapot makers", 5, 10)
    assert results == [
        {"title": "Ada Pots", "url": "https://ada-pots.invalid/", "snippet": "Invented maker."},
        {"title": "Brin Ware", "url": "https://brin.invalid/", "snippet": ""},
    ]
    req = seen[0]
    assert req.url == "https://api.firecrawl.dev/v2/search"
    assert req.headers["Authorization"] == "Bearer fc-invented-key-123456"
    assert json.loads(req.content)["query"] == "invented teapot makers"
    assert json.loads(req.content)["limit"] == 5


def test_firecrawl_search_accepts_v1_list_shape():
    p, _ = firecrawl(lambda r: httpx.Response(200, json={"success": True, "data": [{"url": "https://a.invalid", "title": "A"}]}))
    assert p.search("q", 3, 10)[0]["url"] == "https://a.invalid"


def test_firecrawl_scrape():
    body = {"success": True, "data": {"markdown": "# Ada Pots\n\nInvented text.",
                                      "metadata": {"title": "Ada Pots", "sourceURL": "https://ada.invalid/x",
                                                   "url": "https://ada.invalid/y", "statusCode": 200}}}
    p, seen = firecrawl(lambda r: httpx.Response(200, json=body))
    page = p.fetch("https://ada.invalid/x", 10, 10)
    assert page == {"url": "https://ada.invalid/y", "title": "Ada Pots", "content": "# Ada Pots", "truncated": True}
    sent = json.loads(seen[0].content)
    assert seen[0].url.path == "/v2/scrape" and sent["formats"] == ["markdown"] and sent["url"] == "https://ada.invalid/x"


@pytest.mark.parametrize(
    "status,body,expect",
    [
        (401, {"success": False, "error": "Unauthorized: fc-invented-key-123456"}, "key was refused"),
        (402, {"success": False, "error": "Payment required"}, "out of credits"),
        (429, {"error": "Request rate limit exceeded."}, "rate limit"),
        (500, {"success": False, "error": "boom"}, "HTTP 500"),
        (200, {"success": False, "error": "bad"}, "bad"),
    ],
)
def test_firecrawl_errors_are_plain_and_never_show_the_key(status, body, expect):
    p, _ = firecrawl(lambda r: httpx.Response(status, json=body))
    with pytest.raises(ProviderError) as e:
        p.search("q", 3, 10)
    assert expect in str(e.value)
    assert "fc-invented-key-123456" not in str(e.value)


def test_firecrawl_without_a_key_is_not_called():
    p, seen = firecrawl(lambda r: httpx.Response(200, json={}), key=None)
    with pytest.raises(NotCalled, match="ombro keys set FIRECRAWL_API_KEY"):
        p.fetch("https://a.invalid", 100, 10)
    assert seen == []


def test_firecrawl_key_is_read_at_each_call(home):
    seen = []

    def handler(request):
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"success": True, "data": {"web": []}})

    settings = ToolSettings.from_dict({"tools": {"web.search": {"provider": "firecrawl"}}})
    reg = native_registry(home, settings, transport=httpx.MockTransport(handler))
    from ombro.tools import ToolContext

    ctx = ToolContext(home.root, "r", timeout=5)
    keys.set_key(home, "FIRECRAWL_API_KEY", "fc-invented-first")
    reg.get("web.search").handler({"query": "a"}, ctx)
    keys.set_key(home, "FIRECRAWL_API_KEY", "fc-invented-second")
    reg.get("web.search").handler({"query": "b"}, ctx)
    assert seen == ["Bearer fc-invented-first", "Bearer fc-invented-second"]


# -- DuckDuckGo -------------------------------------------------------------------

DDG_PAGE = """
<html><body>
<div class="result results_links result--ad">
  <a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=ads.invalid">An advert</a>
</div>
<div class="result results_links">
  <h2><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fada-pots.invalid%2Fabout&amp;rut=x">
    Ada <b>Pots</b> &amp; Co</a></h2>
  <a class="result__snippet" href="#">Invented <b>teapot</b> maker<br> in Lowtown.</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://brin-ware.invalid/">Brin Ware</a>
  <div class="result__snippet">Another invented maker.</div>
</div>
</body></html>
"""


def test_duckduckgo_results_are_parsed_and_unwrapped():
    results, blocked = parse_results(DDG_PAGE)
    assert not blocked
    assert results == [
        {"title": "Ada Pots & Co", "url": "https://ada-pots.invalid/about", "snippet": "Invented teapot maker in Lowtown."},
        {"title": "Brin Ware", "url": "https://brin-ware.invalid/", "snippet": "Another invented maker."},
    ]


def test_duckduckgo_search_and_limit():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text=DDG_PAGE)

    p = DuckDuckGoProvider(transport=httpx.MockTransport(handler))
    assert len(p.search("invented teapots", 1, 10)) == 1
    assert seen[0].method == "POST" and b"q=invented+teapots" in seen[0].content


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(202, text="<html></html>"),
        httpx.Response(429, text="slow down"),
        httpx.Response(200, text='<html><div class="anomaly-modal__title">Unfortunately, bots…</div></html>'),
    ],
)
def test_duckduckgo_refusals_are_reported_not_bypassed(response):
    p = DuckDuckGoProvider(transport=httpx.MockTransport(lambda r: response))
    with pytest.raises(ProviderError, match="refused"):
        p.search("q", 5, 10)


# -- which addresses may be reached -----------------------------------------------


@pytest.mark.parametrize(
    "url,addresses",
    [
        ("http://localhost:8080/admin", [PUBLIC_IP]),
        ("http://127.0.0.1/", []),
        ("http://[::1]/", []),
        ("http://169.254.169.254/latest/meta-data/", []),
        ("http://192.168.1.1/", []),
        ("http://router.invalid/", ["192.168.0.1"]),
        ("http://sneaky.invalid/", [PUBLIC_IP, "127.0.0.1"]),
        ("http://mapped.invalid/", ["::ffff:10.1.2.3"]),
        ("http://user:pass@ada.invalid/", [PUBLIC_IP]),
        ("ftp://ada.invalid/", [PUBLIC_IP]),
    ],
)
def test_local_and_private_addresses_are_refused(url, addresses):
    # The browser checks every request a page makes with this (ombro.browser).
    with pytest.raises(NotCalled):
        check_url(url, lambda h, port: addresses)


def test_a_public_address_is_allowed():
    check_url("https://ada-pots.invalid/", public)
