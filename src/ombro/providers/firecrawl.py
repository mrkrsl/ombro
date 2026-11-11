"""Firecrawl: search (POST /v2/search) and page retrieval as Markdown (POST /v2/scrape).

Needs a key. The key is read from .env at every call, so a key set while a run
is in progress is used from the next call on. No automatic retries: each
attempt may cost credits, so a failure goes back to the role instead.
"""

from __future__ import annotations

from typing import Any

import httpx

from ombro.providers import KeyGetter, NotCalled, ProviderError, clip, http_client, scrub

DEFAULT_ENDPOINT = "https://api.firecrawl.dev"


class FirecrawlProvider:
    name = "firecrawl"

    def __init__(
        self,
        key_getter: KeyGetter,
        key_name: str,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        transport: httpx.BaseTransport | None = None,
    ):
        self.key_getter = key_getter
        self.key_name = key_name
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport

    def _post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        key = self.key_getter()
        if not key:
            raise NotCalled(
                f"Firecrawl has no key: {self.key_name} is not set. The user can set it with "
                f"`ombro keys set {self.key_name}`."
            )
        try:
            with http_client(self.transport, timeout) as http:
                resp = http.post(
                    self.endpoint + path,
                    json=body,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                )
        except httpx.TimeoutException:
            raise ProviderError("Firecrawl did not answer in time") from None
        except httpx.HTTPError as e:
            raise ProviderError(f"could not reach Firecrawl: {type(e).__name__}") from None
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code != 200 or not isinstance(data, dict) or data.get("success") is False:
            raise ProviderError(self._error(resp.status_code, data, key))
        return data

    @staticmethod
    def _error(status: int, data: Any, key: str) -> str:
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("error") or data.get("message") or "")
        detail = scrub(detail, key)[:300]
        meaning = {
            401: "the Firecrawl key was refused",
            402: "the Firecrawl account is out of credits",
            408: "Firecrawl timed out",
            429: "Firecrawl's rate limit was reached; try fewer calls",
        }.get(status, f"Firecrawl returned HTTP {status}")
        return meaning + (f": {detail}" if detail else "")

    def search(self, query: str, limit: int, timeout: float) -> list[dict[str, str]]:
        body = {"query": query, "limit": limit, "timeout": int(max(timeout, 1) * 1000)}
        data = self._post("/v2/search", body, timeout)
        raw = data.get("data")
        # v2 nests results by source ({"web": [...]}); v1 returned a plain list.
        items = raw.get("web") if isinstance(raw, dict) else raw
        results = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            results.append(
                {
                    "title": _text(item.get("title") or meta.get("title")),
                    "url": str(item["url"]),
                    "snippet": _text(item.get("description") or item.get("snippet") or meta.get("description")),
                }
            )
        return results[:limit]

    def fetch(self, url: str, max_chars: int, timeout: float) -> dict[str, Any]:
        body = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": int(min(max(timeout, 1), 300) * 1000),
        }
        data = self._post("/v2/scrape", body, timeout).get("data") or {}
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        status = meta.get("statusCode")
        if isinstance(status, int) and status >= 400:
            raise ProviderError(f"the page returned HTTP {status}")
        content, truncated = clip(str(data.get("markdown") or ""), max_chars)
        return {
            "url": str(meta.get("url") or meta.get("sourceURL") or url),
            "title": _text(meta.get("title")),
            "content": content,
            "truncated": truncated,
        }


def _text(value: Any) -> str:
    """Firecrawl metadata fields can be a string or a list of strings."""
    if isinstance(value, list):
        value = value[0] if value else ""
    return " ".join(str(value or "").split())
