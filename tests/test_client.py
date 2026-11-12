"""The OpenAI-compatible client, against a mock transport. No network."""

from __future__ import annotations

import json

import httpx
import pytest

from conftest import LEAD_REPORT, write_role
from ombro import keys, runs
from ombro.client import ModelError, OpenAICompatibleClient
from ombro.engine import Engine
from ombro.settings import load_limits

KEY_A = "sk-test-INVENTED-aaaaaaaa"
KEY_B = "sk-test-INVENTED-bbbbbbbb"


def chat_response(content=None, tool_calls=None, usage=(120, 30)):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [
            {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(tool_calls)
        ]
    body = {"choices": [{"message": msg, "finish_reason": "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": usage[0], "completion_tokens": usage[1]}
    return httpx.Response(200, json=body)


def test_parses_tool_calls_and_usage():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return chat_response(tool_calls=[("brief", {"role": "researcher", "task": "x"})])

    c = OpenAICompatibleClient("https://models.invalid/v1", "m", lambda: KEY_A, transport=httpx.MockTransport(handler))
    reply = c.complete([{"role": "user", "content": "hi"}], tools=[{"type": "function"}], max_output_tokens=77, timeout=5)
    assert reply.tool_calls[0].name == "brief"
    assert reply.tool_calls[0].arguments == {"role": "researcher", "task": "x"}
    assert (reply.usage.input_tokens, reply.usage.output_tokens, reply.usage.estimated) == (120, 30, False)
    assert seen[0]["max_tokens"] == 77 and seen[0]["model"] == "m" and seen[0]["tools"]
    assert reply.as_message()["tool_calls"][0]["function"]["name"] == "brief"


def test_bad_tool_arguments_are_kept_raw():
    def handler(request):
        body = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c0", "type": "function", "function": {"name": "brief", "arguments": "{not json"}}]}}]}
        return httpx.Response(200, json=body)

    c = OpenAICompatibleClient("https://models.invalid", "m", lambda: KEY_A, transport=httpx.MockTransport(handler))
    tc = c.complete([], max_output_tokens=10, timeout=5).tool_calls[0]
    assert tc.arguments is None and tc.raw_arguments == "{not json"


def test_key_is_read_at_the_moment_of_use(home):
    keys.set_key(home, "TEST_MODEL_KEY", KEY_A)
    auth = []

    def handler(request):
        auth.append(request.headers["Authorization"])
        return chat_response("ok")

    c = OpenAICompatibleClient(
        "https://models.invalid/v1", "m", lambda: keys.get_key(home, "TEST_MODEL_KEY"),
        transport=httpx.MockTransport(handler),
    )
    c.complete([], max_output_tokens=5, timeout=5)
    keys.set_key(home, "TEST_MODEL_KEY", KEY_B)  # changed while "running"
    c.complete([], max_output_tokens=5, timeout=5)
    assert auth == [f"Bearer {KEY_A}", f"Bearer {KEY_B}"]


def test_retries_transient_errors_then_succeeds():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(503, json={"error": {"message": "busy"}}) if len(attempts) < 3 else chat_response("ok")

    c = OpenAICompatibleClient("https://models.invalid", "m", lambda: KEY_A,
                               transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert c.complete([], max_output_tokens=5, timeout=30).content == "ok"
    assert len(attempts) == 3


def test_errors_never_contain_the_key():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": f"key {KEY_A} is not valid"}})

    c = OpenAICompatibleClient("https://models.invalid", "m", lambda: KEY_A, transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as e:
        c.complete([], max_output_tokens=5, timeout=5)
    assert KEY_A not in str(e.value) and "401" in str(e.value)


def test_missing_key_fails_without_a_call():
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("no request should be made")

    c = OpenAICompatibleClient("https://models.invalid", "m", lambda: None, transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError, match="no API key"):
        c.complete([], max_output_tokens=5, timeout=5)


def test_keys_never_reach_run_files(home):
    """A whole run through the real client: the key must not appear in any file of the run."""
    keys.set_key(home, "TEST_MODEL_KEY", KEY_A)
    replies = iter([
        chat_response(tool_calls=[("brief", {"role": "researcher", "task": "Find invented things."})]),
        chat_response(json.dumps({"summary": "Found them.", "deliverables": [], "open_questions": [],
                                  "did_not_do": [], "requests": []})),
        chat_response(tool_calls=[("finish", {"outcome": "done", "summary": "ok"})]),
        httpx.Response(401, json={"error": {"message": f"key {KEY_A} expired"}}),
    ])
    sent = []

    def handler(request):
        sent.append(request.content.decode())
        return next(replies)

    transport = httpx.MockTransport(handler)
    from ombro.keys import get_key

    def factory(spec):
        return OpenAICompatibleClient(spec.endpoint, spec.model, lambda: get_key(home, spec.key),
                                      transport=transport, retries=0)

    run_dir = runs.create_run(home, ["default"], {}, "Find invented things.", load_limits(home), source="test")
    status = Engine(home, run_dir, client_factory=factory).execute()
    assert status == "completed"
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert KEY_A not in path.read_text(), path
    for body in sent:
        assert KEY_A not in body  # the key goes in a header, never in what the model reads


def test_attempts_that_timed_out_are_reported_so_they_can_be_counted():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("invented timeout", request=request)
        return chat_response("ok")

    c = OpenAICompatibleClient("https://models.invalid", "m", lambda: KEY_A,
                               transport=httpx.MockTransport(handler), sleep=lambda s: None)
    reply = c.complete([], max_output_tokens=5, timeout=30)
    assert reply.content == "ok" and reply.lost_attempts == 1

    def always(request):
        raise httpx.ReadTimeout("invented timeout", request=request)

    c = OpenAICompatibleClient("https://models.invalid", "m", lambda: KEY_A,
                               transport=httpx.MockTransport(always), sleep=lambda s: None)
    with pytest.raises(ModelError) as caught:
        c.complete([], max_output_tokens=5, timeout=30)
    assert caught.value.lost_attempts == c.retries + 1


def test_a_run_counts_lost_attempts_at_their_worst_case():
    from ombro.gate import Gate
    from ombro.settings import Limits, resolve_allowance
    from ombro.home import LIMITS_META

    g = Gate(Limits.from_dict(resolve_allowance(dict(LIMITS_META))), lambda: 0.0)
    assert g.record_lost_calls("m", 2, 1_000, 500) == 3_000
    assert g.usage.total_tokens == 3_000 and g.usage.estimated_calls == 2
