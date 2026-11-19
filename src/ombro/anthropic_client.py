"""Anthropic's own API (the Messages API), for Claude models.

Ombro keeps messages in the OpenAI shape; this client turns them into Anthropic's on the way out
and the reply back on the way in, so the rest of Ombro doesn't know the difference. What it adds
over Anthropic's OpenAI-compatible endpoint:

- Prompt caching. The system prompt is marked as a cache point (tools come before it, so they
  are cached with it), and the request asks Anthropic to cache the conversation so far as well,
  so each step of a job reads the earlier steps from the cache. Usage shows it as cached input.
- Thinking. A Claude reply can carry thinking blocks; they go back exactly as they came, kept on
  the message under "_blocks", so the model keeps its reasoning between tool calls.
- The model list, every page of it, as the key can use it.

Calls are not streamed: Ombro waits for whole replies everywhere. The key is fetched on every
call and never logged; any error text is scrubbed of it.
"""

from __future__ import annotations

import json
import time
from contextlib import nullcontext
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from ombro.client import (RETRYABLE, BUSY_RETRIES, ModelError, ModelReply, ToolCall, Usage, allowed_max_tokens,
                          busy_pause, busy_wait, no_slot_error)
from ombro.slots import NoSlot

API_VERSION = "2026-10-21"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
_EFFORT_ALIASES = {"minimal": "low"}  # OpenAI's words for the same idea
_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls",
           "pause_turn": "stop", "refusal": "refusal"}
_THINKING = ("thinking", "redacted_thinking")


class AnthropicClient:
    """POSTs to {endpoint}/messages, where the endpoint is https://api.anthropic.com/v1."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        key_getter: Callable[[], str | None],
        *,
        transport: httpx.BaseTransport | None = None,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        effort: str | None = None,
        slots=None,
        thinking: str | None = None,
    ):
        self.slots = slots  # ombro.slots.ProviderSlots: how many calls may be in flight to Anthropic at once
        self.base = endpoint.rstrip("/")
        self.url = self.base + "/messages"
        self.host = urlparse(endpoint).netloc or endpoint
        self.model = model
        self.key_getter = key_getter
        self.transport = transport
        self.retries = retries
        self.sleep = sleep
        self.learned_max: int | None = None  # the most the model allows per reply, learned from its refusal
        effort = _EFFORT_ALIASES.get((effort or "").lower(), (effort or "").lower())
        self.effort = effort if effort in EFFORTS else None  # "none" and unknown words: the model's default
        self.effort_refused = False
        self.thinking = thinking  # a level (ombro.thinking) when models.md gives no effort word of its own
        self.thinking_refused = False
        self.fallback_refused = False  # a model or account that doesn't take server-side fallbacks
        self.stale_thinking: set[str] = set()  # signatures of thinking blocks Anthropic no longer accepts

    # -- calls ---------------------------------------------------------------

    def _headers(self, key: str | None, beta: bool = False) -> dict[str, str]:
        h = {"Content-Type": "application/json", "anthropic-version": API_VERSION}
        if key:
            h["x-api-key"] = key
        if beta:
            h["anthropic-beta"] = FALLBACK_BETA
        return h

    def complete(self, messages, *, tools=None, max_output_tokens, timeout) -> ModelReply:
        key = self.key_getter()
        if not key:
            raise ModelError("no API key is set for this model; run `ombro init` or set it with `ombro keys set`")
        if self.learned_max:
            max_output_tokens = min(int(max_output_tokens), self.learned_max)
        system, converted = to_anthropic(messages, self.stale_thinking)
        body: dict[str, Any] = {"model": self.model, "max_tokens": int(max_output_tokens), "messages": converted,
                                # Cache the conversation so far too: each step reads the steps before it.
                                "cache_control": {"type": "ephemeral"}}
        if system:
            # The stable part: tools and the system prompt, cached together (tools come first).
            body["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if tools:
            body["tools"] = to_anthropic_tools(tools)
        if self.effort and not self.effort_refused:
            body["output_config"] = {"effort": self.effort}
        elif self.thinking and not self.effort:
            from ombro.thinking import anthropic_fields

            fields = anthropic_fields(self.thinking, self.model, int(body["max_tokens"]))
            if self.thinking_refused:
                fields.pop("thinking", None)
            if self.effort_refused:
                fields.pop("output_config", None)
            body.update(fields)
        if not self.fallback_refused:
            # If Claude's safety checks decline a request, Anthropic tries another Claude model.
            body["fallbacks"] = "default"

        deadline = time.monotonic() + timeout
        attempt = 0
        busy = 0
        lost = 0
        stripped = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                err = ModelError("the model call ran out of time", timed_out=True)
                err.lost_attempts = lost
                raise err
            retryable = False
            try:
                with self.slots.hold(deadline) if self.slots else nullcontext(), httpx.Client(
                        transport=self.transport,
                        timeout=httpx.Timeout(max(0.1, deadline - time.monotonic()),
                                              connect=min(remaining, 15.0))) as http:
                    resp = http.post(self.url, json=body, headers=self._headers(key, "fallbacks" in body))
            except NoSlot:
                err = no_slot_error(self.host)
                err.lost_attempts = lost
                raise err from None
            except httpx.TimeoutException:
                err = ModelError("the model call ran out of time", timed_out=True)
                retryable = True
                lost += 1  # it may have reached the model and been billed, with no usage to show
            except httpx.HTTPError as e:
                err = ModelError(f"could not reach {self.host}: {type(e).__name__}")
                retryable = True
            else:
                if resp.status_code == 200:
                    reply = self._parse(resp)
                    reply.lost_attempts = lost
                    return reply
                detail = _error_text(resp, key)
                low = detail.lower()
                if resp.status_code == 400:
                    if "fallback" in low and "fallbacks" in body:
                        self.fallback_refused = True  # this model doesn't take them: carry on without
                        del body["fallbacks"]
                        continue
                    if "thinking" in low and "thinking" in body and "signature" not in low:
                        self.thinking_refused = True  # this model doesn't take that thinking setting
                        del body["thinking"]
                        continue
                    if "effort" in low and "output_config" in body:
                        self.effort_refused = True
                        del body["output_config"]
                        continue
                    if "thinking" in low and "signature" in low and not stripped:
                        # Ombro changed earlier turns (a long chat trimmed, say), so Anthropic won't take
                        # the thinking that came with them. Send them without it, once, and from now on.
                        stripped = True
                        self.stale_thinking |= _signatures(converted)
                        _, converted = to_anthropic(messages, self.stale_thinking, strip_all=True)
                        body["messages"] = converted
                        continue
                allowed = allowed_max_tokens(detail) if resp.status_code == 400 else None
                if allowed and allowed < body["max_tokens"] and not self.learned_max:
                    self.learned_max = allowed
                    body["max_tokens"] = allowed
                    continue
                err = ModelError(f"{self.host} returned HTTP {resp.status_code}: {detail}", status=resp.status_code)
                wait = busy_wait(resp, detail)
                if wait is not None and busy < BUSY_RETRIES:
                    busy += 1
                    pause = busy_pause(wait, busy)
                    if pause < deadline - time.monotonic() - 1:
                        self.sleep(pause)
                        continue
                retryable = resp.status_code in RETRYABLE and wait is None
            if not retryable or attempt >= self.retries:
                err.lost_attempts = lost
                raise err
            attempt += 1
            pause = min(2.0**attempt, max(0.0, deadline - time.monotonic() - 1))
            if pause > 0:
                self.sleep(pause)

    def list_models(self, timeout: float = 20.0) -> list[str]:
        """Every model this key can use (GET {endpoint}/models, page by page), sorted.
        Raises ModelError if the endpoint does not say."""
        key = self.key_getter()
        ids: set[str] = set()
        after: str | None = None
        with httpx.Client(transport=self.transport, timeout=httpx.Timeout(timeout)) as http:
            for _ in range(50):  # pages of 1,000: far more than Anthropic has
                params = {"limit": "1000", **({"after_id": after} if after else {})}
                try:
                    resp = http.get(self.base + "/models", params=params, headers=self._headers(key))
                except httpx.HTTPError as e:
                    raise ModelError(f"could not reach {self.host}: {type(e).__name__}") from None
                if resp.status_code != 200:
                    raise ModelError(f"{self.host} returned HTTP {resp.status_code}: {_error_text(resp, key)}",
                                     status=resp.status_code)
                try:
                    data = resp.json()
                    page = [str(m["id"]) for m in data["data"] if isinstance(m, dict) and m.get("id")]
                except (ValueError, KeyError, TypeError):
                    raise ModelError(f"{self.host} sent a model list Ombro could not read") from None
                ids.update(page)
                after = data.get("last_id") or (page[-1] if page else None)
                if not data.get("has_more") or not page or not after:
                    break
        if not ids:
            raise ModelError(f"{self.host} listed no models")
        return sorted(ids)

    # -- the reply -----------------------------------------------------------

    def _parse(self, resp: httpx.Response) -> ModelReply:
        try:
            data = resp.json()
            content = data["content"]
            if not isinstance(content, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            raise ModelError(f"{self.host} sent a reply Ombro could not read") from None
        texts: list[str] = []
        calls: list[ToolCall] = []
        for i, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                texts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                args = block.get("input")
                calls.append(ToolCall(id=str(block.get("id") or f"call_{i}"), name=str(block.get("name") or ""),
                                      arguments=args if isinstance(args, dict) else None,
                                      raw_arguments=json.dumps(args if args is not None else {}, ensure_ascii=False)))
        stop = data.get("stop_reason")
        text = "".join(texts) or None
        if stop == "refusal" and not text and not calls:
            details = data.get("stop_details") or {}
            why = details.get("explanation") if isinstance(details, dict) else None
            text = "The model declined this request" + (f": {why}" if why else ".")
        usage = data.get("usage") or {}
        if usage.get("input_tokens") is not None:
            read = int(usage.get("cache_read_input_tokens") or 0)
            written = int(usage.get("cache_creation_input_tokens") or 0)
            # Anthropic counts cached and newly cached input apart from input_tokens; Ombro counts all of it.
            u = Usage(int(usage.get("input_tokens") or 0) + read + written, int(usage.get("output_tokens") or 0),
                      cached_input=read)
        else:
            out_chars = len(text or "") + sum(len(c.raw_arguments) for c in calls)
            u = Usage(0, out_chars // 3 + 1, estimated=True)
        # Thinking (and a fallback's marker) must go back exactly as sent; plain text and tool calls rebuild.
        exact = any(isinstance(b, dict) and b.get("type") not in ("text", "tool_use") for b in content)
        return ModelReply(content=text, tool_calls=calls, usage=u, finish_reason=_FINISH.get(stop, stop),
                          blocks=content if exact else None)


# -- turning Ombro's messages into Anthropic's ---------------------------------


def to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI function tools as Anthropic tools, in the same order (so they cache)."""
    out = []
    for t in tools:
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        schema = fn.get("parameters") or fn.get("input_schema") or {}
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        tool: dict[str, Any] = {"name": str(fn.get("name") or ""), "input_schema": schema}
        if fn.get("description"):
            tool["description"] = str(fn["description"])
        out.append(tool)
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return "" if content is None else str(content)


def _images(content: Any) -> list[dict[str, Any]]:
    """Image parts in a user's message (OpenAI's form, data URLs) as Anthropic's image blocks."""
    out = []
    for p in content if isinstance(content, list) else []:
        url = str(((p or {}).get("image_url") or {}).get("url") or "") if isinstance(p, dict) else ""
        if p.get("type") == "image_url" and url.startswith("data:") and ";base64," in url:
            head, data = url[5:].split(";base64,", 1)
            out.append({"type": "image", "source": {"type": "base64", "media_type": head, "data": data}})
    return out


def _signatures(messages: list[dict[str, Any]]) -> set[str]:
    return {str(b.get("signature") or b.get("data")) for m in messages if isinstance(m.get("content"), list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") in _THINKING}


def _assistant_blocks(m: dict[str, Any], stale: set[str], strip_all: bool) -> list[dict[str, Any]]:
    raw = m.get("_blocks")
    if isinstance(raw, list) and raw:
        return [b for b in raw if isinstance(b, dict) and not (
            b.get("type") in _THINKING and (strip_all or str(b.get("signature") or b.get("data")) in stale))]
    blocks: list[dict[str, Any]] = []
    text = _text_of(m.get("content"))
    if text.strip():
        blocks.append({"type": "text", "text": text})
    for i, tc in enumerate(m.get("tool_calls") or []):
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments")
        try:
            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        except (TypeError, ValueError):
            args = {}
        blocks.append({"type": "tool_use", "id": str(tc.get("id") or f"call_{i}"), "name": str(fn.get("name") or ""),
                       "input": args if isinstance(args, dict) else {}})
    return blocks


def to_anthropic(messages: list[dict[str, Any]], stale: set[str] | None = None, *,
                 strip_all: bool = False) -> tuple[str, list[dict[str, Any]]]:
    """(system, messages) for the Messages API. The system messages at the start become the
    system prompt; a later one goes in as the user's note. Tool results join one user turn, ahead
    of any text, and turns of the same role are joined."""
    stale = stale or set()
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    start = True

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            prev = out[-1]["content"]
            if role == "user" and blocks[0].get("type") == "tool_result":
                # tool results first in the turn, as Anthropic asks
                results = [b for b in prev if b.get("type") == "tool_result"]
                rest = [b for b in prev if b.get("type") != "tool_result"]
                out[-1]["content"] = results + blocks + rest
            else:
                prev.extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m.get("role")
        if role == "system" and start:
            system_parts.append(_text_of(m.get("content")))
            continue
        start = False
        if role == "system":
            text = _text_of(m.get("content"))
            if text.strip():
                add("user", [{"type": "text", "text": f"<system-note>\n{text}\n</system-note>"}])
        elif role == "assistant":
            add("assistant", _assistant_blocks(m, stale, strip_all))
        elif role == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": str(m.get("tool_call_id") or ""),
                          "content": _text_of(m.get("content")) or "(no output)"}])
        else:
            images = _images(m.get("content"))
            text = _text_of(m.get("content"))
            if text.strip() or images:
                add("user", [*images, *([{"type": "text", "text": text}] if text.strip() else [])])
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "Begin."}]})
    return "\n\n".join(p for p in system_parts if p), out


def _error_text(resp: httpx.Response, key: str | None) -> str:
    try:
        data = resp.json()
        err = data.get("error", data) if isinstance(data, dict) else data
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except ValueError:
        msg = resp.text
    text = str(msg or "no detail")
    if key:
        text = text.replace(key, "***")
    return text[:300]
