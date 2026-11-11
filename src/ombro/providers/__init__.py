"""Providers: the services behind native tools.

A provider is a small adaptor with one job: take a tool's standard arguments,
call the service, and return the standard result. Roles never see which
provider is used; tools.md decides.

Standard results:
- search(query, limit, timeout) -> list of {"title", "url", "snippet"}
- fetch(url, max_chars, timeout) -> {"url", "title", "content", "truncated"}

Failures raise ProviderError with a message fit to show the role.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

import httpx

from ombro.tools import ToolError

USER_AGENT = "Mozilla/5.0 (compatible; Ombro/0.2; +local research harness)"


class ProviderError(ToolError):
    """The provider could not do what was asked. The message is shown to the role."""


class NotCalled(ProviderError):
    """Refused before the service was contacted (no key, a refused address). Never charged."""

    billable = False


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int, timeout: float) -> list[dict[str, str]]: ...


class FetchProvider(Protocol):
    name: str

    def fetch(self, url: str, max_chars: int, timeout: float) -> dict[str, Any]: ...


KeyGetter = Callable[[], "str | None"]


def http_client(transport: httpx.BaseTransport | None, timeout: float) -> httpx.Client:
    return httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(max(timeout, 0.5), connect=min(max(timeout, 0.5), 15.0)),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=False,
    )


def scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


def clip(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True
