"""A provider that is busy: 429s, OpenRouter's in-flight 402, and the limit on calls in flight. No network."""

from __future__ import annotations

import json
import threading

import httpx
import pytest

from ombro.anthropic_client import AnthropicClient
from ombro.client import ModelError, OpenAICompatibleClient, busy_wait, make_client
from ombro.settings import ModelSpec
from ombro.slots import NoSlot, ProviderSlots

KEY = "sk-or-INVENTED-dddddddd"
IN_FLIGHT = (
    "This request would exceed your available credits given your current in-flight requests. Retry after "
    "in-flight requests settle, or add credits."
)


def ok():
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": "fine"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        },
    )


def scripted(replies, seen):
    def handler(request):
        seen.append(json.loads(request.content))
        return replies.pop(0)

    return httpx.MockTransport(handler)


def test_in_flight_402_is_waited_out_with_a_smaller_reservation():
    seen, pauses = [], []
    replies = [
        httpx.Response(402, json={"error": {"code": 402, "message": IN_FLIGHT}}, headers={"Retry-After": "3"}),
        httpx.Response(402, json={"error": {"code": 402, "message": IN_FLIGHT}}),
        ok(),
    ]
    c = OpenAICompatibleClient(
        "https://openrouter.ai/api/v1",
        "anthropic/claude-opus-5.5",
        lambda: KEY,
        transport=scripted(replies, seen),
        sleep=pauses.append,
    )
    reply = c.complete([{"role": "user", "content": "go"}], max_output_tokens=32_000, timeout=600)
    assert reply.content == "fine"
    assert len(pauses) == 2 and pauses[0] >= 3  # Retry-After is honoured
    assert [b["max_tokens"] for b in seen] == [32_000, 16_000, 8_192]


def test_a_402_for_credit_itself_is_not_retried():
    seen, pauses = [], []
    replies = [httpx.Response(402, json={"error": {"code": 402, "message": "Insufficient credits. Add more."}})]
    c = OpenAICompatibleClient(
        "https://openrouter.ai/api/v1", "m", lambda: KEY, transport=scripted(replies, seen), sleep=pauses.append
    )
    with pytest.raises(ModelError) as e:
        c.complete([], max_output_tokens=100, timeout=600)
    assert e.value.status == 402 and not pauses and len(seen) == 1


def test_429s_are_retried_more_than_ordinary_errors_then_give_up():
    seen, pauses = [], []
    replies = [httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}}) for _ in range(7)]
    c = OpenAICompatibleClient(
        "https://openrouter.ai/api/v1", "m", lambda: KEY, transport=scripted(replies, seen), sleep=pauses.append
    )
    with pytest.raises(ModelError) as e:
        c.complete([], max_output_tokens=100, timeout=3600)
    assert e.value.status == 429 and len(seen) == 7 and len(pauses) == 6
    assert pauses == sorted(pauses)  # backing off


def test_no_retry_when_the_wait_would_pass_the_call_time():
    seen, pauses = [], []
    replies = [httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"Retry-After": "50"})]
    c = OpenAICompatibleClient(
        "https://x.invalid/v1", "m", lambda: KEY, transport=scripted(replies, seen), sleep=pauses.append
    )
    with pytest.raises(ModelError):
        c.complete([], max_output_tokens=100, timeout=10)
    assert not pauses


def test_anthropic_waits_out_a_429_too():
    pauses, calls = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "rate_limit_error"}}, headers={"retry-after": "2"})
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
        )

    c = AnthropicClient(
        "https://api.anthropic.com/v1",
        "claude-haiku-5-5",
        lambda: KEY,
        transport=httpx.MockTransport(handler),
        sleep=pauses.append,
    )
    assert c.complete([{"role": "user", "content": "x"}], max_output_tokens=100, timeout=600).content == "hi"
    assert len(pauses) == 1 and pauses[0] >= 2


def test_busy_wait_reads_the_kind_of_refusal():
    r = lambda code, msg, **h: httpx.Response(code, json={"error": {"message": msg}}, headers=h)  # noqa: E731
    assert busy_wait(r(429, "x", **{"retry-after": "7"}), "x") == 7
    assert busy_wait(r(402, IN_FLIGHT), IN_FLIGHT) == 0
    assert busy_wait(r(402, "no credit"), "no credit") is None
    assert busy_wait(r(500, "x"), "x") is None


def test_slots_limit_calls_in_flight_and_free_when_done(tmp_path):
    slots = ProviderSlots(tmp_path, "openrouter.ai", 2, sleep=lambda s: None)
    import time

    far = time.monotonic() + 60
    with slots.hold(far), slots.hold(far):
        with pytest.raises(NoSlot):
            with slots.hold(time.monotonic()):  # both taken, and no time to wait
                pass
    with slots.hold(time.monotonic()):  # free again
        pass


def test_a_call_waits_for_a_slot_held_elsewhere(tmp_path):
    import time

    slots = ProviderSlots(tmp_path, "openrouter.ai", 1)
    held, release = threading.Event(), threading.Event()

    def other():
        with ProviderSlots(tmp_path, "openrouter.ai", 1).hold(time.monotonic() + 30):
            held.set()
            release.wait(5)

    t = threading.Thread(target=other)
    t.start()
    held.wait(5)
    threading.Timer(0.3, release.set).start()
    began = time.monotonic()
    with slots.hold(time.monotonic() + 10) as waited:
        assert waited >= 0.2
    assert time.monotonic() - began >= 0.2
    t.join()


def test_clients_get_slots_for_their_provider(home):
    spec = ModelSpec(
        name="m",
        api="openai",
        model="anthropic/claude-opus-5.5",
        endpoint="https://openrouter.ai/api/v1",
        key="OPENROUTER_API_KEY",
        max_concurrent=3,
    )
    c = make_client(spec, home)
    assert c.slots.size == 3 and c.slots.folder.name == "openrouter.ai"
    local = ModelSpec(name="l", api="openai", model="x", endpoint="http://localhost:11434/v1")
    assert make_client(local, home).slots is None
