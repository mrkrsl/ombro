"""Search services reached by their own API: Brave Search, Tavily, Exa, Serper, a SearXNG of your
own, and any other search API given by its address (tools.md `web.search: provider: <name>`).

Each takes the standard search arguments and gives the standard results (ombro.providers). Keys
are read from secrets/keys.env at every call, so a key set while a job runs is used from the next
call on. No automatic retries: a call may be billed, so a failure goes back to the role.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

import httpx

from ombro.providers import KeyGetter, NotCalled, ProviderError, http_client, scrub

_TAGS = re.compile(r"<[^>]+>")


def _text(value: Any, n: int = 400) -> str:
    if isinstance(value, list):
        value = " … ".join(str(v) for v in value if v)
    return " ".join(_TAGS.sub("", str(value or "")).split())[:n]


class ApiSearch:
    """What every search API shares: the key, the call, and plain errors."""

    name = ""
    label = ""
    endpoint = ""

    def __init__(
        self,
        key_getter: KeyGetter | None,
        key_name: str | None,
        *,
        endpoint: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.key_getter = key_getter
        self.key_name = key_name
        self.endpoint = (endpoint or self.endpoint).rstrip("/")
        self.transport = transport

    def _key(self, required: bool = True) -> str | None:
        key = self.key_getter() if self.key_getter else None
        if required and not key:
            raise NotCalled(
                f"{self.label} has no key: {self.key_name} is not set. The user can set it in "
                f"Settings › Tools, or with `ombro keys set {self.key_name}`."
            )
        return key or None

    def _call(self, method: str, url: str, timeout: float, key: str | None, **kw: Any) -> Any:
        try:
            with http_client(self.transport, timeout) as http:
                resp = http.request(method, url, **kw)
        except httpx.TimeoutException:
            raise ProviderError(f"{self.label} did not answer in time") from None
        except httpx.HTTPError as e:
            raise ProviderError(f"could not reach {self.label}: {type(e).__name__}") from None
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code != 200 or data is None:
            raise ProviderError(self._error(resp.status_code, data, key))
        return data

    def _error(self, status: int, data: Any, key: str | None) -> str:
        detail = ""
        if isinstance(data, dict):
            d = data.get("error") or data.get("message") or data.get("detail") or ""
            detail = scrub(_text(d.get("message") if isinstance(d, dict) else d, 300), key)
        meaning = {
            401: f"the {self.label} key was refused",
            403: f"the {self.label} key was refused",
            402: f"the {self.label} account is out of credits",
            432: f"the {self.label} account is out of credits",
            429: f"{self.label}'s rate limit was reached; try fewer searches",
        }.get(status, f"{self.label} returned HTTP {status}")
        if status == 200:
            meaning = f"{self.label} did not answer with results"
        return meaning + (f": {detail}" if detail else "")

    @staticmethod
    def _results(items: Any, url: str, title: str, snippet: str, limit: int) -> list[dict[str, str]]:
        out = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not str(item.get(url) or "").startswith(("http://", "https://")):
                continue
            out.append(
                {"title": _text(item.get(title), 200), "url": str(item[url]), "snippet": _text(item.get(snippet))}
            )
        return out[:limit]


class BraveSearch(ApiSearch):
    name, label, endpoint = "brave", "Brave Search", "https://api.search.brave.com/res/v1"

    def search(self, query: str, limit: int, timeout: float) -> list[dict[str, str]]:
        key = self._key()
        data = self._call(
            "GET",
            self.endpoint + "/web/search",
            timeout,
            key,
            params={"q": query, "count": limit},
            headers={"X-Subscription-Token": key or "", "Accept": "application/json"},
        )
        web = data.get("web") if isinstance(data, dict) else None
        return self._results((web or {}).get("results"), "url", "title", "description", limit)


class Tavily(ApiSearch):
    name, label, endpoint = "tavily", "Tavily", "https://api.tavily.com"

    def search(self, query: str, limit: int, timeout: float) -> list[dict[str, str]]:
        key = self._key()
        data = self._call(
            "POST",
            self.endpoint + "/search",
            timeout,
            key,
            json={"query": query, "max_results": limit},
            headers={"Authorization": f"Bearer {key}"},
        )
        return self._results(data.get("results") if isinstance(data, dict) else None, "url", "title", "content", limit)


class Exa(ApiSearch):
    name, label, endpoint = "exa", "Exa", "https://api.exa.ai"

    def search(self, query: str, limit: int, timeout: float) -> list[dict[str, str]]:
        key = self._key()
        data = self._call(
            "POST",
            self.endpoint + "/search",
            timeout,
            key,
            json={"query": query, "numResults": limit, "contents": {"text": {"maxCharacters": 400}}},
            headers={"x-api-key": key or ""},
        )
        items = data.get("results") if isinstance(data, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and not item.get("text") and item.get("highlights"):
                item["text"] = item["highlights"]
        return self._results(items, "url", "title", "text", limit)


class Serper(ApiSearch):
    name, label, endpoint = "serper", "Serper", "https://google.serper.dev"

    def search(self, query: str, limit: int, timeout: float) -> list[dict[str, str]]:
        key = self._key()
        data = self._call(
            "POST",
            self.endpoint + "/search",
            timeout,
            key,
            json={"q": query, "num": limit},
            headers={"X-API-KEY": key or ""},
        )
        return self._results(data.get("organic") if isinstance(data, dict) else None, "link", "title", "snippet", limit)


class SearXNG(ApiSearch):
    """A SearXNG you run, at the address in tools.md. It may be on your own network: you set the
    address, not a role. Its JSON output must be turned on (search: formats: [html, json])."""

    name, label = "searxng", "SearXNG"

    def search(self, query: str, limit: int, timeout: float) -> list[dict[str, str]]:
        if not self.endpoint:
            raise NotCalled("SearXNG has no address. The user can set it in Settings › Tools.")
        key = self._key(required=False)
        data = self._call(
            "GET",
            self.endpoint + "/search",
            timeout,
            key,
            params={"q": query, "format": "json"},
            headers={"Authorization": f"Bearer {key}"} if key else {},
        )
        return self._results(data.get("results") if isinstance(data, dict) else None, "url", "title", "content", limit)

    def _error(self, status: int, data: Any, key: str | None) -> str:
        if status == 403 and not key:
            return "SearXNG refused JSON results: its settings.yml needs json under search: formats:"
        return super()._error(status, data, key)


class CustomSearch(ApiSearch):
    """Another search API, by its address. `{query}` or `%s` in the address marks where the words go;
    without one they are sent as ?q=. The key, if any, is sent as `send_key` says (bearer,
    header:<Name> or query:<param>, as for web APIs). Results are found in the answer by their
    fields: the first list of items with an address (url or link)."""

    name, label = "custom", "the search API"

    def __init__(self, *args: Any, send_key: str = "bearer", **kw: Any):
        super().__init__(*args, **kw)
        self.send_key = send_key or "bearer"

    def search(self, query: str, limit: int, timeout: float) -> list[dict[str, str]]:
        if not self.endpoint:
            raise NotCalled("The search API has no address. The user can set it in Settings › Tools.")
        key = self._key(required=False)
        url, params, headers = self.endpoint, {}, {}
        if "{query}" in url or "%s" in url:
            url = url.replace("{query}", quote_plus(query)).replace("%s", quote_plus(query))
        else:
            params["q"] = query
        if key:
            if self.send_key.startswith("header:"):
                headers[self.send_key[7:]] = key
            elif self.send_key.startswith("query:"):
                params[self.send_key[6:]] = key
            else:
                headers["Authorization"] = f"Bearer {key}"
        # The address's own query stays: what Ombro adds is merged into it.
        data = self._call("GET", str(httpx.URL(url).copy_merge_params(params)), timeout, key, headers=headers)
        items = find_results(data)
        if items is None:
            raise ProviderError("the search API answered, but with no list of results Ombro could find")
        field = "url" if any(isinstance(i, dict) and "url" in i for i in items) else "link"
        snippet = next(
            (
                f
                for f in ("snippet", "description", "content", "text", "summary")
                if any(isinstance(i, dict) and i.get(f) for i in items)
            ),
            "snippet",
        )
        return self._results(items, field, "title", snippet, limit)


def find_results(data: Any, depth: int = 0) -> list[Any] | None:
    """The first list of items with an address, wherever it is in the answer (breadth first)."""
    if depth > 4:
        return None
    if isinstance(data, list):
        if any(isinstance(i, dict) and (i.get("url") or i.get("link")) for i in data):
            return data
        children = data
    elif isinstance(data, dict):
        children = list(data.values())
        for v in children:
            if isinstance(v, list) and any(isinstance(i, dict) and (i.get("url") or i.get("link")) for i in v):
                return v
    else:
        return None
    for v in children:
        if isinstance(v, (dict, list)):
            found = find_results(v, depth + 1)
            if found is not None:
                return found
    return None


SERVICES: dict[str, type[ApiSearch]] = {c.name: c for c in (BraveSearch, Tavily, Exa, Serper, SearXNG, CustomSearch)}
