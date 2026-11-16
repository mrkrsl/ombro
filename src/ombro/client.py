"""Model clients. An OpenAI-compatible chat client with tool calling."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import httpx


class ModelError(Exception):
    def __init__(self, message: str, *, timed_out: bool = False, status: int | None = None):
        super().__init__(message)
        self.timed_out = timed_out
        self.status = status
        self.lost_attempts = 0  # attempts that timed out: the provider may have done (and billed) them


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None  # None when the model sent arguments that are not a JSON object
    raw_arguments: str = ""


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    estimated: bool = False
    cached_input: int = 0  # of input_tokens, how many the provider served from its cache


@dataclass
class ModelReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=lambda: Usage(0, 0, True))
    finish_reason: str | None = None
    lost_attempts: int = 0  # earlier attempts at this call that timed out and were tried again
    cut_off: bool = False  # it reached the output limit, so its text or a tool call is incomplete

    def as_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content if self.content is not None else ""}
        if self.tool_calls:
            msg["content"] = self.content
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.raw_arguments or json.dumps(tc.arguments or {})},
                }
                for tc in self.tool_calls
            ]
        return msg


class ChatClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        max_output_tokens: int,
        timeout: float,
    ) -> ModelReply: ...


def estimate_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
    """A deliberately generous estimate of input tokens, used before a call."""
    chars = len(json.dumps(messages, ensure_ascii=False))
    if tools:
        chars += len(json.dumps(tools, ensure_ascii=False))
    return chars // 3 + 16


def parse_tool_arguments(raw: Any) -> dict | None:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


_RANGE = re.compile(r"\[\s*\d+\s*,\s*(\d{3,7})\s*\]")
_AT_MOST = re.compile(r"(?:<=|less than or equal to|at most|no more than|maximum(?: value)?(?: of| is)?|up to|"
                      r"must not exceed|cannot exceed|exceeds?(?: the)?(?: maximum| limit)?(?: of)?)"
                      r"\s*:?\s*`?(\d{3,7})")


def allowed_max_tokens(text: str) -> int | None:
    """The most output tokens an endpoint says it allows, from its refusal of max_tokens."""
    low = (text or "").lower()
    if not re.search(r"max_(?:completion_)?tokens|output tokens|max tokens|completion tokens", low):
        return None
    m = _RANGE.search(low) or _AT_MOST.search(low)
    n = int(m.group(1)) if m else 0
    return n if n >= 256 else None


class OpenAICompatibleClient:
    """POSTs to {endpoint}/chat/completions. The key is fetched on every call."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        key_getter: Callable[[], str | None],
        *,
        key_required: bool = True,
        transport: httpx.BaseTransport | None = None,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.url = endpoint.rstrip("/") + "/chat/completions"
        self.host = urlparse(endpoint).netloc or endpoint
        self.model = model
        self.key_getter = key_getter
        self.key_required = key_required
        self.transport = transport
        self.retries = retries
        self.sleep = sleep
        self.learned_max: int | None = None  # the most the endpoint allows per reply, learned from its refusal

    def complete(self, messages, *, tools=None, max_output_tokens, timeout) -> ModelReply:
        key = self.key_getter()
        if self.key_required and not key:
            raise ModelError("no API key is set for this model; run `ombro init` or set it with `ombro keys set`")
        if self.learned_max:
            max_output_tokens = min(int(max_output_tokens), self.learned_max)
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": int(max_output_tokens)}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        deadline = time.monotonic() + timeout
        attempt = 0
        lost = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                err = ModelError("the model call ran out of time", timed_out=True)
                err.lost_attempts = lost
                raise err
            retryable = False
            try:
                with httpx.Client(
                    transport=self.transport,
                    timeout=httpx.Timeout(remaining, connect=min(remaining, 15.0)),
                ) as http:
                    resp = http.post(self.url, json=body, headers=headers)
            except httpx.TimeoutException:
                err = ModelError("the model call ran out of time", timed_out=True)
                retryable = True
                lost += 1  # it may have reached the model and been billed, with no usage to show
            except httpx.HTTPError as e:
                err = ModelError(f"could not reach {self.host}: {type(e).__name__}")
                retryable = True
            else:
                if resp.status_code == 200:
                    reply = self._parse(resp, key)
                    reply.lost_attempts = lost
                    return reply
                detail = self._error_text(resp, key)
                allowed = allowed_max_tokens(detail) if resp.status_code in (400, 422) else None
                if allowed and allowed < body["max_tokens"] and not self.learned_max:
                    # The endpoint allows fewer output tokens than asked: use its maximum from now on.
                    self.learned_max = allowed
                    body["max_tokens"] = allowed
                    continue
                err = ModelError(f"{self.host} returned HTTP {resp.status_code}: {detail}", status=resp.status_code)
                retryable = resp.status_code in RETRYABLE
            if not retryable or attempt >= self.retries:
                err.lost_attempts = lost
                raise err
            attempt += 1
            pause = min(2.0**attempt, max(0.0, deadline - time.monotonic() - 1))
            if pause > 0:
                self.sleep(pause)

    def list_models(self, timeout: float = 20.0) -> list[str]:
        """The models this endpoint offers to this key (GET {endpoint}/models), sorted.
        Raises ModelError if the endpoint does not say."""
        key = self.key_getter()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        url = self.url.removesuffix("/chat/completions") + "/models"
        try:
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(timeout)) as http:
                resp = http.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise ModelError(f"could not reach {self.host}: {type(e).__name__}") from None
        if resp.status_code != 200:
            raise ModelError(f"{self.host} returned HTTP {resp.status_code}: {self._error_text(resp, key)}",
                             status=resp.status_code)
        try:
            data = resp.json().get("data")
            ids = sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})
        except (ValueError, AttributeError, TypeError):
            raise ModelError(f"{self.host} sent a model list Ombro could not read") from None
        if not ids:
            raise ModelError(f"{self.host} listed no models")
        return ids

    @staticmethod
    def _scrub(text: str, key: str | None) -> str:
        if key:
            text = text.replace(key, "***")
        return text

    def _error_text(self, resp: httpx.Response, key: str | None) -> str:
        try:
            data = resp.json()
            err = data.get("error", data) if isinstance(data, dict) else data
            msg = err.get("message") if isinstance(err, dict) else str(err)
        except ValueError:
            msg = resp.text
        return self._scrub(str(msg or "no detail"), key)[:300]

    def _parse(self, resp: httpx.Response, key: str | None) -> ModelReply:
        try:
            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError(f"{self.host} sent a reply Ombro could not read") from None
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw = fn.get("arguments")
            raw_s = raw if isinstance(raw, str) else json.dumps(raw or {})
            calls.append(
                ToolCall(
                    id=tc.get("id") or f"call_{i}",
                    name=str(fn.get("name") or ""),
                    arguments=parse_tool_arguments(raw),
                    raw_arguments=raw_s,
                )
            )
        usage = data.get("usage") or {}
        content = msg.get("content")
        if usage.get("prompt_tokens") is not None:
            # DeepSeek says prompt_cache_hit_tokens; OpenAI (and those like it) prompt_tokens_details.cached_tokens.
            details = usage.get("prompt_tokens_details") or {}
            cached = usage.get("prompt_cache_hit_tokens") or (details.get("cached_tokens") if isinstance(details, dict)
                                                              else 0) or 0
            u = Usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
                      cached_input=int(cached))
        else:
            out_chars = len(content or "") + sum(len(c.raw_arguments) for c in calls)
            u = Usage(0, out_chars // 3 + 1, estimated=True)
        return ModelReply(content=content, tool_calls=calls, usage=u, finish_reason=choice.get("finish_reason"))


def make_client(spec, home) -> ChatClient:
    """Build the client for a ModelSpec from models.md."""
    from ombro import keys
    from ombro.fake import ScriptedClient

    if spec.api == "fake":
        return ScriptedClient.shared(spec.script, base=home.root)
    key_name = spec.key
    return OpenAICompatibleClient(
        spec.endpoint,
        spec.model,
        (lambda: keys.get_key(home, key_name)) if key_name else (lambda: None),
        key_required=bool(key_name),
    )
