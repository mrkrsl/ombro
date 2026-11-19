"""What a job pays for: caching through OpenRouter, how tool results and tool names reach the model (T9).
No network."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from ombro import prompts
from ombro.client import OpenAICompatibleClient, is_claude_model, is_openrouter
from ombro.engine import _tool_message
from ombro.roles import Role

KEY = "sk-or-INVENTED-cccccccc"


def ok(usage=None):
    body = {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 5}}
    return httpx.Response(200, json=body)


def client(endpoint, model, seen, usage=None):
    def handler(request):
        seen.append(json.loads(request.content))
        return ok(usage)

    return OpenAICompatibleClient(endpoint, model, lambda: KEY, transport=httpx.MockTransport(handler))


def test_claude_through_openrouter_asks_for_the_cache_and_sticks_to_one_provider():
    seen: list[dict] = []
    c = client("https://openrouter.ai/api/v1", "anthropic/claude-opus-5.5", seen)
    c.complete([{"role": "user", "content": "hi"}], max_output_tokens=50, timeout=5)
    c.complete([{"role": "user", "content": "hi again"}], max_output_tokens=50, timeout=5)
    assert seen[0]["cache_control"] == {"type": "ephemeral"}
    assert seen[0]["session_id"] and seen[0]["session_id"] == seen[1]["session_id"]


def test_other_models_and_endpoints_are_left_alone():
    seen: list[dict] = []
    client("https://openrouter.ai/api/v1", "deepseek/deepseek-v4-pro", seen).complete(
        [], max_output_tokens=50, timeout=5)
    client("https://api.deepseek.com", "deepseek-v4-pro", seen).complete([], max_output_tokens=50, timeout=5)
    assert "cache_control" not in seen[0] and seen[0]["session_id"]  # caching there is automatic
    assert "cache_control" not in seen[1] and "session_id" not in seen[1]


def test_openrouter_cache_reads_count_as_cached_input():
    seen: list[dict] = []
    usage = {"prompt_tokens": 50_000, "completion_tokens": 300,
             "prompt_tokens_details": {"cached_tokens": 48_000, "cache_write_tokens": 2_000}}
    reply = client("https://openrouter.ai/api/v1", "anthropic/claude-sonnet-5.5", seen, usage).complete(
        [], max_output_tokens=50, timeout=5)
    assert (reply.usage.input_tokens, reply.usage.cached_input) == (50_000, 48_000)


def test_names():
    assert is_openrouter("https://openrouter.ai/api/v1") and not is_openrouter("https://api.deepseek.com")
    assert is_claude_model("anthropic/claude-haiku-5.5") and is_claude_model("~anthropic/claude-sonnet-latest")
    assert not is_claude_model("deepseek-v4-pro")


def test_a_long_text_result_is_not_escaped_twice():
    text = json.dumps({"messages": [{"subject": f"Order {i}", "sender": "Shop <a@b.c>"} for i in range(40)]})
    msg = _tool_message("c1", {"status": "done", "result": text, "note": "from outside"})
    assert msg["content"].endswith(text)  # as it came: no \" for every quote
    assert json.loads(msg["content"].split("\nresult:\n", 1)[0]) == {"status": "done", "note": "from outside"}
    short = _tool_message("c2", {"status": "done", "result": "ok"})
    assert json.loads(short["content"]) == {"status": "done", "result": "ok"}


def test_outside_tools_are_not_named_twice_in_the_role_prompt():
    role = Role(name="helper", title="Helper", kind="specialist", description="d", soul="s", model="default",
                path=Path("."), max_steps=5, crew="c")
    names = ["workspace.read", "web.search"] + [f"composio.gmail.TOOL_{i}" for i in range(150)]
    text = prompts.role_system(role, "", names)
    assert "composio.gmail.TOOL_7" not in text
    assert "workspace.read, web.search, and the 150 tools of outside services listed in tools__load" in text
    assert "Tools named mcp.* and composio.* reach outside services" in text
