"""The fake provider: invented results, and a record of every request.

Used by the tests and for dry runs. Each request is kept in `calls` and, when
the tool context gives a run folder, appended to that run's fake-calls.jsonl.
Nothing touches the network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ombro.journal import utc_now
from ombro.providers import ProviderError, clip

RECORD_FILE = "fake-calls.jsonl"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "page"


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        results: list[dict[str, str]] | None = None,
        pages: dict[str, str] | None = None,
        error: str | None = None,
    ):
        self.results = results
        self.pages = pages or {}
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.record_dir: Path | None = None  # set per call by the tool

    def _record(self, entry: dict[str, Any]) -> None:
        entry = {"ts": utc_now(), **entry}
        self.calls.append(entry)
        if self.record_dir is not None:
            with (self.record_dir / RECORD_FILE).open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def search(self, query: str, limit: int, timeout: float) -> list[dict[str, str]]:
        self._record({"tool": "web.search", "query": query, "limit": limit})
        if self.error:
            raise ProviderError(self.error)
        if self.results is not None:
            return [dict(r) for r in self.results[:limit]]
        slug = _slug(query)
        return [
            {
                "title": f"Invented result {i} for {query}",
                "url": f"https://example.invalid/{slug}-{i}",
                "snippet": f"An invented page about {query}, result {i}.",
            }
            for i in range(1, min(limit, 3) + 1)
        ]

    def fetch(self, url: str, max_chars: int, timeout: float) -> dict[str, Any]:
        self._record({"tool": "web.fetch", "url": url, "max_chars": max_chars})
        if self.error:
            raise ProviderError(self.error)
        text = self.pages.get(url) or f"# Invented page\n\nThis invented page stands in for {url}."
        content, truncated = clip(text, max_chars)
        return {"url": url, "title": "Invented page", "content": content, "truncated": truncated}
