"""One thinking preference, put to each provider in the form it takes. No network."""

from __future__ import annotations

import json

import httpx
import pytest

from ombro import mdfile, thinking
from ombro.anthropic_client import AnthropicClient
from ombro.client import OpenAICompatibleClient, make_client
from ombro.settings import SetupError, load_models, load_preferences, set_thinking

KEY = "sk-INVENTED-eeeeeeee"


def openai_body(endpoint, model, **kw):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    OpenAICompatibleClient(endpoint, model, lambda: KEY, transport=httpx.MockTransport(handler), **kw).complete(
        [], max_output_tokens=4000, timeout=5)
    return seen[0]


def anthropic_body(model, level, max_tokens=32000):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                                         "usage": {"input_tokens": 1, "output_tokens": 1}})

    AnthropicClient("https://api.anthropic.com/v1", model, lambda: KEY, transport=httpx.MockTransport(handler),
                    thinking=level).complete([{"role": "user", "content": "x"}], max_output_tokens=max_tokens,
                                             timeout=5)
    return seen[0]


@pytest.mark.parametrize("level,fields", [
    ("off", {"thinking": {"type": "disabled"}}),
    ("low", {"reasoning_effort": "low"}),
    ("medium", {"reasoning_effort": "high"}),  # DeepSeek has no medium: its own default
    ("xhigh", {"reasoning_effort": "max"}),
])
def test_deepseek(level, fields):
    body = openai_body("https://api.deepseek.com", "deepseek-v4-pro", thinking=level)
    assert {k: body[k] for k in ("thinking", "reasoning_effort") if k in body} == fields


def test_openrouter_takes_reasoning_effort_in_its_own_form():
    assert openai_body("https://openrouter.ai/api/v1", "anthropic/claude-opus-5.5", thinking="off")["reasoning"] \
        == {"effort": "none"}
    body = openai_body("https://openrouter.ai/api/v1", "anthropic/claude-opus-5.5", thinking="high")
    assert body["reasoning"] == {"effort": "high"} and "reasoning_effort" not in body
    # A provider's own word from models.md goes in OpenRouter's form too
    assert openai_body("https://openrouter.ai/api/v1", "x/y", reasoning_effort="minimal")["reasoning"] \
        == {"effort": "minimal"}


def test_openai_compatible_and_models_that_dont_think():
    assert openai_body("https://api.openai.com/v1", "gpt-5.5", thinking="off")["reasoning_effort"] == "none"
    assert openai_body("https://api.openai.com/v1", "gpt-5.5", thinking="xhigh")["reasoning_effort"] == "xhigh"
    body = openai_body("https://api.openai.com/v1", "gpt-4o-mini", thinking="high")
    assert not {"reasoning_effort", "reasoning", "thinking"} & set(body)


def test_a_refused_field_is_dropped_and_remembered():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "Unrecognized request argument supplied"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    c = OpenAICompatibleClient("https://llm.invented.invalid/v1", "small", lambda: KEY,
                               transport=httpx.MockTransport(handler), thinking="medium")
    c.complete([], max_output_tokens=100, timeout=5)
    c.complete([], max_output_tokens=100, timeout=5)
    assert ["reasoning_effort" in b for b in seen] == [True, False, False]


def test_off_falls_back_to_low_where_a_model_must_think():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body.get("reasoning"))
        if body.get("reasoning") == {"effort": "none"}:
            return httpx.Response(400, json={"error": {"message": "Reasoning is mandatory for this model"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    c = OpenAICompatibleClient("https://openrouter.ai/api/v1", "x/thinker", lambda: KEY,
                               transport=httpx.MockTransport(handler), thinking="off")
    c.complete([], max_output_tokens=100, timeout=5)
    assert seen == [{"effort": "none"}, {"effort": "low"}]


def test_anthropic():
    assert anthropic_body("claude-sonnet-5", "high")["output_config"] == {"effort": "high"}
    off = anthropic_body("claude-haiku-5-5", "off")
    assert off["thinking"] == {"type": "disabled"} and off["output_config"] == {"effort": "low"}
    always = anthropic_body("claude-opus-5-5", "off")  # its thinking can't be turned off
    assert "thinking" not in always and always["output_config"] == {"effort": "low"}
    assert anthropic_body("claude-sonnet-5-5", "off")["thinking"] == {"type": "between_tools"}
    budget = anthropic_body("claude-haiku-4-5", "medium")
    assert budget["thinking"] == {"type": "enabled", "budget_tokens": 8000} and "output_config" not in budget
    assert "thinking" not in anthropic_body("claude-haiku-4-5", "off")
    assert anthropic_body("claude-haiku-4-5", "xhigh", max_tokens=4000)["thinking"]["budget_tokens"] == 2976


def test_the_preference_reaches_every_client_and_models_md_wins(home):
    meta, body = mdfile.read(home.models)
    models = meta["models"]
    models["or-opus"] = {"api": "openai", "endpoint": "https://openrouter.ai/api/v1",
                         "model": "anthropic/claude-opus-5.5", "key": "OPENROUTER_API_KEY"}
    models["ds"] = {"api": "openai", "endpoint": "https://api.deepseek.com", "model": "deepseek-v4-pro",
                    "key": "DEEPSEEK_API_KEY", "thinking": False}  # YAML: `thinking: off`
    models["ds-raw"] = {"api": "openai", "endpoint": "https://api.deepseek.com", "model": "deepseek-v4-pro",
                        "key": "DEEPSEEK_API_KEY", "reasoning_effort": "low"}
    mdfile.write(home.models, meta, body)
    specs = load_models(home)
    assert make_client(specs["or-opus"], home).thinking == thinking.DEFAULT == "medium"
    assert set_thinking(home, "Extra high") == "xhigh"
    assert load_preferences(home).thinking == "xhigh"
    assert make_client(specs["or-opus"], home).thinking == "xhigh"
    assert make_client(specs["ds"], home).thinking == "off"
    raw = make_client(specs["ds-raw"], home)
    assert (raw.thinking, raw.reasoning_effort) == ("low", None)  # a level word: mapped, not sent raw
    with pytest.raises(SetupError):
        set_thinking(home, "lots")


def test_a_bad_level_in_models_md_is_named(home):
    meta, body = mdfile.read(home.models)
    meta["models"]["bad"] = {"api": "openai", "endpoint": "https://x.invalid", "model": "m", "thinking": "lots"}
    mdfile.write(home.models, meta, body)
    with pytest.raises(SetupError, match="thinking"):
        load_models(home)


def test_levels_as_people_write_them():
    assert [thinking.level_of(w) for w in ("none", False, "Extra high", "max", "minimal", "nonsense", None)] == \
        ["off", "off", "xhigh", "xhigh", "low", None, None]


def test_cli_sets_and_shows_it(home, capsys):
    from ombro.cli import main

    assert main(["preferences", "thinking", "low"]) == 0
    assert "Thinking is now Low" in capsys.readouterr().out
    assert main(["preferences"]) == 0
    assert "Thinking      Low" in capsys.readouterr().out
    assert main(["preferences", "thinking", "lots"]) == 2


def test_the_app_sets_it_through_serve(home):
    from test_serve import APP, TOKEN, client

    c = client()
    h = {"Authorization": f"Bearer {TOKEN}", "Origin": APP}
    assert c.get("/preferences", headers=h).json()["thinking"] == "medium"
    r = c.put("/preferences", json={"thinking": "high"}, headers=h)
    assert r.status_code == 200 and r.json()["thinking"] == "high" and r.json()["thinking_set"] is True
    assert c.put("/preferences", json={"thinking": "lots"}, headers=h).status_code == 400
