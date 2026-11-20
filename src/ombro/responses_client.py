"""Model calls on the person's ChatGPT plan: OpenAI's Responses API with the ChatGPT sign-in
(chatgpt.py), as OpenAI documents it for open-source apps.

What that route asks for, and how this client meets it:
- POST https://api.openai.com/v1/responses with `store: false` and `stream: true`; the stream is
  read to its end, and only `response.completed` counts as a finished reply.
- The whole conversation goes in `input` each time (no stored conversation); the system prompt
  goes in `instructions`, a later one as a developer message.
- Function tools are grouped in a namespace. Fields the route refuses (max_output_tokens,
  temperature, metadata and the like) are never sent, so the output limit isn't enforced by
  OpenAI; Ombro counts what comes back as usual.
- Plan errors stop the call; they never switch to paying another way. A usage limit says where
  to manage it (ChatGPT settings › Usage).

Ombro's messages stay in the OpenAI chat shape everywhere else; this client converts them.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx

from ombro.chatgpt import RESOURCE, USAGE_URL, Session
from ombro.client import ModelError, ModelReply, ToolCall, Usage
from ombro.oauth import SignInNeeded

URL = RESOURCE + "/responses"
NAMESPACE = "ombro"
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
_REPLAYED = ("message", "function_call")  # output items sent back as they came (reasoning is not kept)

LIMIT_CODES = {"subscription_sharing_usage_limit_exceeded"}
RETRY_CODES = {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"}


def _plan_error(status: int | None, code: str, text: str) -> ModelError:
    if code in LIMIT_CODES:
        return ModelError(
            "Your ChatGPT plan's usage limit was reached (it may be a limit you set for Ombro). "
            f"Manage usage: {USAGE_URL}",
            status=status,
        )
    if code == "subscription_sharing_user_not_eligible":
        return ModelError(
            "This ChatGPT account or workspace can't use its plan in Ombro. Use an OpenAI API key instead.",
            status=status,
        )
    if code in ("subscription_sharing_invalid_user",):
        return ModelError(
            "ChatGPT couldn't confirm your sign-in. Sign in with ChatGPT again: Settings › General, "
            "or `ombro chatgpt sign-in`.",
            status=status,
        )
    return ModelError(
        f"ChatGPT plan: HTTP {status}" + (f" ({code})" if code else "") + (f": {text}" if text else ""), status=status
    )


def _error_of(body: Any) -> tuple[str, str]:
    """(code, message) from an error body: the Responses `error` object, or `{"detail": …}`."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("code") or err.get("type") or ""), str(err.get("message") or "")[:300]
        if body.get("detail"):
            return "", str(body["detail"])[:300]
    return "", ""


class ChatGPTClient:
    """One model on the ChatGPT plan. The sign-in's token is fetched (and renewed) on every call."""

    def __init__(
        self,
        model: str,
        session: Session,
        *,
        transport: httpx.BaseTransport | None = None,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        effort: str | None = None,
    ):
        self.model = model
        self.session = session
        self.transport = transport
        self.retries = retries
        self.sleep = sleep
        effort = (effort or "").lower()
        self.effort = effort if effort in EFFORTS else None
        self.effort_refused = False
        self.learned_max = None  # the route takes no output limit, so none is ever learned

    def _token(self) -> str:
        try:
            return self.session.token()
        except SignInNeeded as e:
            raise ModelError(str(e)) from None

    def complete(self, messages, *, tools=None, max_output_tokens, timeout) -> ModelReply:
        token = self._token()
        instructions, items = to_responses(messages)
        body: dict[str, Any] = {"model": self.model, "input": items, "store": False, "stream": True}
        if instructions:
            body["instructions"] = instructions
        if tools:
            body["tools"] = [to_namespace(tools)]
            body["tool_choice"] = "auto"
        if self.effort and not self.effort_refused:
            body["reasoning"] = {"effort": self.effort}

        deadline = time.monotonic() + timeout
        attempt = 0
        lost = 0
        renewed = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                err = ModelError("the model call ran out of time", timed_out=True)
                err.lost_attempts = lost
                raise err
            retryable = False
            try:
                with httpx.Client(
                    transport=self.transport, timeout=httpx.Timeout(remaining, connect=min(remaining, 15.0))
                ) as http:
                    with http.stream(
                        "POST",
                        URL,
                        json=body,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                            "Accept": "text/event-stream",
                        },
                    ) as resp:
                        if resp.status_code == 200:
                            reply = read_stream(resp.iter_lines())
                            reply.lost_attempts = lost
                            return reply
                        resp.read()
                        status = resp.status_code
                        try:
                            data = resp.json()
                        except ValueError:
                            data = {"detail": resp.text[:300]}
            except ModelError as e:
                if not e.status or e.status < 500 or attempt >= self.retries:
                    e.lost_attempts = lost
                    raise
                err, retryable = e, True
            except httpx.TimeoutException:
                err = ModelError("the model call ran out of time", timed_out=True)
                retryable = True
                lost += 1
            except httpx.HTTPError as e:
                err = ModelError(f"could not reach api.openai.com: {type(e).__name__}")
                retryable = True
            else:
                code, text = _error_of(data)
                if status == 401 and not renewed and code not in ("subscription_sharing_invalid_user",):
                    renewed = True  # the token may have just expired: renew it once
                    try:
                        token = self.session.refresh(stale=token)
                    except SignInNeeded as e:
                        raise ModelError(str(e), status=401) from None
                    continue
                if status == 400 and "reasoning" in body and ("reasoning" in text.lower() or "effort" in text.lower()):
                    self.effort_refused = True
                    del body["reasoning"]
                    continue
                err = _plan_error(status, code, text)
                retryable = code in RETRY_CODES or (status >= 500 and code not in LIMIT_CODES)
            if not retryable or attempt >= self.retries:
                err.lost_attempts = lost
                raise err
            attempt += 1
            pause = min(2.0**attempt, max(0.0, deadline - time.monotonic() - 1))
            if pause > 0:
                self.sleep(pause)

    def list_models(self, timeout: float = 20.0) -> list[str]:
        from ombro.chatgpt import list_models

        return list_models(self._token(), self.transport, timeout)


# -- the stream ----------------------------------------------------------------------


def read_stream(lines) -> ModelReply:
    """Read a Responses event stream to its end. Only response.completed (or .incomplete) is a
    reply; response.failed, an error event, or a stream that just stops is an error."""
    data_lines: list[str] = []
    final: dict[str, Any] | None = None
    kind = None

    def event(text: str) -> None:
        nonlocal final, kind
        try:
            ev = json.loads(text)
        except ValueError:
            return
        t = ev.get("type")
        if t in ("response.completed", "response.incomplete"):
            final, kind = ev.get("response") or {}, t
        elif t == "response.failed":
            code, msg = _error_of(ev.get("response") or {})
            raise (
                _plan_error(None, code, msg)
                if code.startswith("subscription_sharing")
                else ModelError(
                    "the ChatGPT plan call failed" + (f" ({code})" if code else "") + (f": {msg}" if msg else "")
                )
            )
        elif t == "error":
            code, msg = str(ev.get("code") or ""), str(ev.get("message") or "")
            if not code and isinstance(ev.get("error"), dict):
                code, msg = _error_of(ev)
            raise _plan_error(None, code, msg)

    for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data_lines:
                event("\n".join(data_lines))
                data_lines = []
            if final is not None:
                break
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines and final is None:
        event("\n".join(data_lines))
    if final is None:
        raise ModelError("the ChatGPT plan's reply stopped before it finished", status=502)
    return parse_response(final, incomplete=kind == "response.incomplete")


def parse_response(resp: dict[str, Any], *, incomplete: bool = False) -> ModelReply:
    texts: list[str] = []
    calls: list[ToolCall] = []
    kept: list[dict[str, Any]] = []
    for i, item in enumerate(resp.get("output") or []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                    texts.append(str(part.get("text") or ""))
        elif item.get("type") == "function_call":
            raw = item.get("arguments")
            raw_s = raw if isinstance(raw, str) else json.dumps(raw or {})
            try:
                args = json.loads(raw_s or "{}")
            except ValueError:
                args = None
            calls.append(
                ToolCall(
                    id=str(item.get("call_id") or f"call_{i}"),
                    name=str(item.get("name") or ""),
                    arguments=args if isinstance(args, dict) else None,
                    raw_arguments=raw_s,
                )
            )
        if item.get("type") in _REPLAYED:
            kept.append({k: v for k, v in item.items() if k not in ("id", "status")})
    usage = resp.get("usage") or {}
    content = "".join(texts) or None
    if usage.get("input_tokens") is not None:
        details = usage.get("input_tokens_details") or {}
        out_details = usage.get("output_tokens_details") or {}
        u = Usage(
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            cached_input=int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0,
        )
        u.reasoning = int(out_details.get("reasoning_tokens") or 0) if isinstance(out_details, dict) else 0
    else:
        u = Usage(0, (len(content or "") + sum(len(c.raw_arguments) for c in calls)) // 3 + 1, estimated=True)
    reason = (resp.get("incomplete_details") or {}).get("reason") if incomplete else None
    finish = "length" if incomplete and reason in (None, "max_output_tokens") else ("tool_calls" if calls else "stop")
    return ModelReply(content=content, tool_calls=calls, usage=u, finish_reason=finish, items=kept or None)


# -- Ombro's messages as Responses input ---------------------------------------------


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def to_namespace(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Ombro's function tools, grouped in one namespace as this route asks, strict mode off (as
    Chat Completions treats them)."""
    out = []
    for t in tools:
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        schema = fn.get("parameters") or {"type": "object", "properties": {}}
        tool = {"type": "function", "name": str(fn.get("name") or ""), "parameters": schema, "strict": False}
        if fn.get("description"):
            tool["description"] = str(fn["description"])
        out.append(tool)
    return {"type": "namespace", "name": NAMESPACE, "description": "Ombro's tools for this step.", "tools": out}


def to_responses(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """(instructions, input items) for the Responses API."""
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    start = True
    for m in messages:
        role = m.get("role")
        if role == "system" and start:
            instructions.append(_text(m.get("content")))
            continue
        start = False
        if role == "system":
            if _text(m.get("content")).strip():
                items.append({"role": "developer", "content": _text(m.get("content"))})
        elif role == "assistant":
            kept = m.get("_items")
            if isinstance(kept, list) and kept:
                items.extend(dict(i) for i in kept if isinstance(i, dict))
                continue
            text = _text(m.get("content"))
            if text.strip():
                items.append({"role": "assistant", "content": text})
            for i, tc in enumerate(m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                raw = fn.get("arguments")
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(tc.get("id") or f"call_{i}"),
                        "name": str(fn.get("name") or ""),
                        "arguments": raw if isinstance(raw, str) else json.dumps(raw or {}),
                    }
                )
        elif role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(m.get("tool_call_id") or ""),
                    "output": _text(m.get("content")) or "(no output)",
                }
            )
        else:
            content = m.get("content")
            text = _text(content)
            images = [
                {"type": "input_image", "image_url": str(p["image_url"].get("url") or "")}
                for p in (content if isinstance(content, list) else [])
                if isinstance(p, dict) and p.get("type") == "image_url" and isinstance(p.get("image_url"), dict)
            ]
            if images:
                items.append({"role": "user", "content": [{"type": "input_text", "text": text}, *images]})
            elif text.strip():
                items.append({"role": "user", "content": text})
    return "\n\n".join(p for p in instructions if p), items
