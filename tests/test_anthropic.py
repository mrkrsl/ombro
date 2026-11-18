"""Anthropic's own API (anthropic_client.py), against recorded-shape fake replies. No network."""

from __future__ import annotations

import json

import httpx
import pytest

from ombro import jobs, keys, mdfile
from ombro.anthropic_client import API_VERSION, AnthropicClient, to_anthropic, to_anthropic_tools
from ombro.client import ModelError, OpenAICompatibleClient, api_for, make_client
from ombro.engine import Engine
from ombro.settings import ModelSpec, load_limits, load_models

KEY = "sk-ant-test-INVENTED-cccccccc"
EP = "https://api.anthropic.com/v1"

TOOL = {"type": "function", "function": {"name": "brief", "description": "Brief a role.",
                                          "parameters": {"type": "object", "properties": {"task": {"type": "string"}},
                                                         "required": ["task"]}}}


def message(content, stop="end_turn", usage=(100, 20, 0, 0)):
    """A Messages API reply, as Anthropic sends it."""
    return httpx.Response(200, json={
        "id": "msg_01INVENTED", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop, "stop_details": None,
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1], "cache_creation_input_tokens": usage[2],
                  "cache_read_input_tokens": usage[3]}})


def text(t):
    return {"type": "text", "text": t}


def client(handler, **kw):
    return AnthropicClient(EP, "claude-opus-5-5", lambda: KEY, transport=httpx.MockTransport(handler),
                           sleep=lambda s: None, **kw)


def test_a_call_has_the_key_in_its_header_and_caches_the_system_prompt_and_conversation():
    seen = []

    def handler(request):
        seen.append(request)
        return message([text("hello")])

    reply = client(handler).complete(
        [{"role": "system", "content": "You are the Lead."}, {"role": "user", "content": "hi"}],
        tools=[TOOL], max_output_tokens=900, timeout=5)
    req = seen[0]
    body = json.loads(req.content)
    assert str(req.url) == f"{EP}/messages"
    assert req.headers["x-api-key"] == KEY and "authorization" not in req.headers
    assert req.headers["anthropic-version"] == API_VERSION
    assert KEY not in req.content.decode()
    assert body["system"] == [{"type": "text", "text": "You are the Lead.", "cache_control": {"type": "ephemeral"}}]
    assert body["cache_control"] == {"type": "ephemeral"}  # the growing conversation, cached as it goes
    assert body["tools"] == [{"name": "brief", "description": "Brief a role.",
                              "input_schema": TOOL["function"]["parameters"]}]
    assert body["messages"] == [{"role": "user", "content": [text("hi")]}]
    assert body["max_tokens"] == 900 and "temperature" not in body
    assert reply.content == "hello" and reply.finish_reason == "stop"


def test_cached_input_is_counted_as_input_and_shown_as_cached():
    reply = client(lambda r: message([text("ok")], usage=(40, 12, 300, 5_000))).complete(
        [{"role": "user", "content": "hi"}], max_output_tokens=50, timeout=5)
    u = reply.usage
    assert (u.input_tokens, u.cached_input, u.output_tokens, u.estimated) == (5_340, 5_000, 12, False)


def test_tool_calls_and_their_results_go_back_in_anthropics_shape():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return message([text("Briefing."), {"type": "tool_use", "id": "toolu_1", "name": "brief",
                                                 "input": {"task": "x"}}], stop="tool_use")
        return message([text("done")])

    c = client(handler)
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "go"}]
    reply = c.complete(msgs, tools=[TOOL], max_output_tokens=50, timeout=5)
    assert reply.finish_reason == "tool_calls" and reply.blocks is None
    tc = reply.tool_calls[0]
    assert (tc.id, tc.name, tc.arguments) == ("toolu_1", "brief", {"task": "x"})
    msgs += [reply.as_message(), {"role": "tool", "tool_call_id": "toolu_1", "content": "briefed"}]
    c.complete(msgs, tools=[TOOL], max_output_tokens=50, timeout=5)
    assert seen[1]["messages"][1:] == [
        {"role": "assistant", "content": [text("Briefing."),
                                          {"type": "tool_use", "id": "toolu_1", "name": "brief", "input": {"task": "x"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "briefed"}]},
    ]


def test_thinking_goes_back_exactly_as_it_came():
    thinking = {"type": "thinking", "thinking": "", "signature": "sig-INVENTED-1"}
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return message([thinking, {"type": "tool_use", "id": "toolu_1", "name": "brief", "input": {"task": "x"}}],
                           stop="tool_use")
        return message([text("done")])

    c = client(handler)
    msgs = [{"role": "user", "content": "go"}]
    reply = c.complete(msgs, tools=[TOOL], max_output_tokens=50, timeout=5)
    assert reply.blocks and reply.as_message()["_blocks"][0] == thinking
    msgs += [reply.as_message(), {"role": "tool", "tool_call_id": "toolu_1", "content": "ok"}]
    c.complete(msgs, tools=[TOOL], max_output_tokens=50, timeout=5)
    assert seen[1]["messages"][1]["content"][0] == thinking


def test_other_providers_never_see_the_kept_blocks():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    c = OpenAICompatibleClient("https://models.invalid/v1", "m", lambda: "k", transport=httpx.MockTransport(handler))
    c.complete([{"role": "user", "content": "a"},
                {"role": "assistant", "content": "b", "_blocks": [{"type": "thinking", "signature": "s"}]}],
               max_output_tokens=5, timeout=5)
    assert seen[0]["messages"][1] == {"role": "assistant", "content": "b"}


def test_thinking_anthropic_no_longer_accepts_is_left_out_from_then_on():
    """A trimmed chat changes earlier turns; Anthropic then refuses the thinking that came with them."""
    old = {"type": "thinking", "thinking": "", "signature": "sig-OLD"}
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if any(b.get("signature") == "sig-OLD" for m in body["messages"] for b in m["content"]):
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "messages.1.content.0: Invalid `signature` in `thinking` block. The block is bound to a "
                           "different conversation."}})
        return message([text("fine")])

    c = client(handler)
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b", "_blocks": [old, text("b")]},
            {"role": "user", "content": "c"}]
    assert c.complete(msgs, max_output_tokens=5, timeout=5).content == "fine"
    assert len(seen) == 2 and seen[1]["messages"][1]["content"] == [text("b")]
    c.complete(msgs, max_output_tokens=5, timeout=5)
    assert len(seen) == 3  # no refused attempt the second time


def test_a_model_that_takes_no_fallbacks_or_effort_gets_calls_without_them():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((body, request.headers.get("anthropic-beta")))
        if "fallbacks" in body:
            return httpx.Response(400, json={"error": {"message": "fallbacks: not supported for this model"}})
        if "output_config" in body:
            return httpx.Response(400, json={"error": {"message": "output_config.effort: not supported"}})
        return message([text("ok")])

    c = client(handler, effort="high")
    assert c.complete([{"role": "user", "content": "a"}], max_output_tokens=5, timeout=5).content == "ok"
    assert seen[0][1] == "server-side-fallback-2026-07-01" and seen[0][0]["fallbacks"] == "default"
    assert seen[0][0]["output_config"] == {"effort": "high"}
    assert "fallbacks" not in seen[-1][0] and "output_config" not in seen[-1][0] and seen[-1][1] is None
    c.complete([{"role": "user", "content": "a"}], max_output_tokens=5, timeout=5)
    assert len(seen) == 4  # remembered


def test_effort_words_from_models_md():
    assert AnthropicClient(EP, "m", lambda: KEY, effort="minimal").effort == "low"
    assert AnthropicClient(EP, "m", lambda: KEY, effort="none").effort is None
    assert AnthropicClient(EP, "m", lambda: KEY, effort="XHIGH").effort == "xhigh"


def test_the_output_limit_is_learned_from_a_refusal():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body["max_tokens"])
        if body["max_tokens"] > 64000:
            return httpx.Response(400, json={"error": {"message": "max_tokens: 200000 > 64000, which is the maximum "
                                                                  "allowed number of output tokens for claude-haiku-4-5"}})
        return message([text("ok")])

    c = client(handler)
    c.complete([{"role": "user", "content": "a"}], max_output_tokens=200000, timeout=5)
    assert seen == [200000, 64000] and c.learned_max == 64000


def test_overloaded_is_tried_again_and_errors_never_show_the_key():
    tries = []

    def handler(request):
        tries.append(1)
        if len(tries) < 3:
            return httpx.Response(529, json={"error": {"type": "overloaded_error", "message": "Overloaded"}})
        return message([text("ok")])

    assert client(handler).complete([{"role": "user", "content": "a"}], max_output_tokens=5, timeout=30).content == "ok"

    bad = client(lambda r: httpx.Response(401, json={"error": {"message": f"invalid x-api-key {KEY}"}}))
    with pytest.raises(ModelError) as e:
        bad.complete([{"role": "user", "content": "a"}], max_output_tokens=5, timeout=5)
    assert e.value.status == 401 and KEY not in str(e.value)


def test_a_declined_request_says_so():
    resp = httpx.Response(200, json={"content": [], "stop_reason": "refusal",
                                     "stop_details": {"type": "refusal", "category": "cyber", "explanation": None},
                                     "usage": {"input_tokens": 10, "output_tokens": 0}})
    reply = client(lambda r: resp).complete([{"role": "user", "content": "a"}], max_output_tokens=5, timeout=5)
    assert reply.finish_reason == "refusal" and reply.content == "The model declined this request."


def test_every_model_the_key_can_use_is_listed_page_by_page():
    pages = {None: (["claude-opus-5-5", "claude-sonnet-5-5"], True), "claude-sonnet-5-5": (["claude-haiku-5-5"], False)}
    seen = []

    def handler(request):
        after = request.url.params.get("after_id")
        seen.append((request.headers.get("x-api-key"), after, request.url.params.get("limit")))
        ids, more = pages[after]
        return httpx.Response(200, json={"data": [{"type": "model", "id": i, "display_name": i} for i in ids],
                                         "has_more": more, "first_id": ids[0], "last_id": ids[-1]})

    assert client(handler).list_models() == ["claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-5-5"]
    assert seen == [(KEY, None, "1000"), (KEY, "claude-sonnet-5-5", "1000")]


def test_a_refused_key_stops_the_list():
    c = client(lambda r: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))
    with pytest.raises(ModelError) as e:
        c.list_models()
    assert e.value.status == 401


def test_conversion_details():
    system, msgs = to_anthropic([
        {"role": "system", "content": "one"}, {"role": "system", "content": "two"},
        {"role": "assistant", "content": ""},  # nothing to send: left out
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "x", "arguments": "{not json"}},
            {"id": "b", "type": "function", "function": {"name": "y", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": ""},
        {"role": "system", "content": "later"},
    ])
    assert system == "one\n\ntwo"
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"][0]["input"] == {}  # unreadable arguments go back as an empty object
    assert [b["type"] for b in msgs[2]["content"]] == ["tool_result", "tool_result", "text"]
    assert msgs[2]["content"][1]["content"] == "(no output)"
    assert "later" in msgs[2]["content"][2]["text"]
    assert to_anthropic_tools([{"type": "function", "function": {"name": "n"}}]) == [
        {"name": "n", "input_schema": {"type": "object", "properties": {}}}]


# -- models.md -------------------------------------------------------------------


def _models(home, models):
    mdfile.write(home.models, {"models": models})


def test_anthropic_addresses_get_the_native_client_and_new_entries_say_so(home):
    assert api_for(EP) == "anthropic" and api_for("https://api.deepseek.com") == "openai"
    old = ModelSpec(name="m", api="openai", model="claude-opus-5-5", endpoint=EP, key="ANTHROPIC_API_KEY")
    assert isinstance(make_client(old, home), AnthropicClient)  # an older models.md keeps working
    from ombro.models import add_models, set_default

    _models(home, {})
    add_models(home, EP, "ANTHROPIC_API_KEY", ["claude-opus-5-5"])
    set_default(home, "claude-opus-5-5")
    entry = mdfile.read(home.models)[0]["models"]["claude-opus-5-5"]
    assert entry["api"] == "anthropic"
    assert load_models(home)["claude-opus-5-5"].api == "anthropic"


def test_older_anthropic_models_move_to_its_own_api_once_with_a_note(home):
    from ombro.home import tidy_layout

    _models(home, {"default": "claude-opus-5-5",
                   "claude-opus-5-5": {"api": "openai", "endpoint": EP, "model": "claude-opus-5-5",
                                       "key": "ANTHROPIC_API_KEY"},
                   "deepseek-chat": {"api": "openai", "endpoint": "https://api.deepseek.com", "model": "deepseek-chat",
                                     "key": "DEEPSEEK_API_KEY"}})
    lines = tidy_layout(home)
    assert any("Anthropic's own API" in line for line in lines)
    models = mdfile.read(home.models)[0]["models"]
    assert models["claude-opus-5-5"]["api"] == "anthropic" and models["claude-opus-5-5"]["key"] == "ANTHROPIC_API_KEY"
    assert models["deepseek-chat"]["api"] == "openai" and models["default"] == "claude-opus-5-5"
    assert not any("Anthropic" in line for line in tidy_layout(home))  # once


def test_the_key_is_still_known_to_be_in_use(home):
    _models(home, {"c": {"api": "anthropic", "endpoint": EP, "model": "claude-opus-5-5", "key": "ANTHROPIC_API_KEY"}})
    assert "ANTHROPIC_API_KEY" in keys.key_uses(home)


def test_set_up_lists_every_anthropic_model_and_asks_for_a_name_if_it_cannot(home):
    from ombro.init_cmd import KeyRefused, offered_models

    keys.set_key(home, "ANTHROPIC_API_KEY", KEY)
    listing = httpx.Response(200, json={"data": [{"id": "claude-opus-5-5"}, {"id": "claude-haiku-5-5"}],
                                        "has_more": False})

    def factory(handler):
        def make(spec, h):
            assert spec.api == "anthropic"
            return AnthropicClient(spec.endpoint, spec.model, lambda: keys.get_key(h, spec.key),
                                   transport=httpx.MockTransport(handler))
        return make

    assert offered_models(home, EP, "ANTHROPIC_API_KEY", factory(lambda r: listing)) == [
        "claude-haiku-5-5", "claude-opus-5-5"]
    assert offered_models(home, EP, "ANTHROPIC_API_KEY",
                          factory(lambda r: httpx.Response(500, json={"error": {"message": "down"}}))) == []
    with pytest.raises(KeyRefused):
        offered_models(home, EP, "ANTHROPIC_API_KEY",
                       factory(lambda r: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})))


def test_a_whole_job_through_anthropic_and_the_key_stays_out_of_its_files(home):
    keys.set_key(home, "TEST_MODEL_KEY", KEY)
    report = json.dumps({"summary": "Found them.", "deliverables": [], "open_questions": [], "did_not_do": [],
                         "requests": []})
    replies = iter([
        message([{"type": "thinking", "thinking": "", "signature": "sig-1"},
                 {"type": "tool_use", "id": "toolu_1", "name": "brief",
                  "input": {"role": "researcher", "task": "Find invented things."}}], stop="tool_use",
                usage=(50, 20, 2_000, 0)),
        message([text(report)]),
        message([{"type": "tool_use", "id": "toolu_2", "name": "finish", "input": {"outcome": "done", "summary": "ok"}}],
                stop="tool_use", usage=(60, 10, 0, 2_000)),
        message([text("## What was done\nIt went well.\n\n## What was not done and why\nNothing.\n\n"
                      "## Decisions for you\nNone.")]),
    ])
    sent = []

    def handler(request):
        sent.append(request.content.decode())
        return next(replies)

    transport = httpx.MockTransport(handler)

    def factory(spec):
        return AnthropicClient(EP, spec.model, lambda: keys.get_key(home, spec.key), transport=transport, retries=0)

    job_dir = jobs.create_job(home, ["default"], {}, "Find invented things.", load_limits(home), source="test")
    assert Engine(home, job_dir, client_factory=factory).execute() == "completed"
    for path in job_dir.rglob("*"):
        if path.is_file():
            assert KEY not in path.read_text(errors="replace"), path
    lead_calls = [json.loads(b) for b in sent if '"finish"' in b]
    assert any(m["role"] == "assistant" and m["content"][0].get("signature") == "sig-1"
               for b in lead_calls for m in b["messages"])  # the Lead's thinking came back to it
