"""Choosing models: set-up offers what the endpoint lists, models.md keeps one entry
per model, and the default, the Lead and each role can be pointed at any of them.
All invented; no network."""

from __future__ import annotations

import httpx

from conftest import write_role
from ombro import mdfile, models
from ombro.client import ModelReply, OpenAICompatibleClient
from ombro.home import Home
from ombro.settings import load_models
from test_ui import ScriptedUI

ENDPOINT = "https://models.invalid/v1"


class Listing:
    def __init__(self, ids):
        self.ids = ids

    def complete(self, *a, **k):
        return ModelReply(content="OK")

    def list_models(self):
        return list(self.ids)


def test_the_client_reads_an_endpoints_model_list():
    def handler(request):
        assert request.url.path == "/v1/models" and request.headers["Authorization"] == "Bearer sk-test-INVENTED"
        return httpx.Response(200, json={"data": [{"id": "invented-pro"}, {"id": "invented-chat"}]})

    c = OpenAICompatibleClient(ENDPOINT, "", lambda: "sk-test-INVENTED", transport=httpx.MockTransport(handler))
    assert c.list_models() == ["invented-chat", "invented-pro"]


def test_set_up_offers_every_listed_model_and_writes_each(tmp_path):
    from ombro.init_cmd import connected_model, run_init

    home = Home(tmp_path / "fresh")
    from ombro.init_cmd import ENDPOINTS  # the last is "another endpoint"

    ui = ScriptedUI(
        ["", str(len(ENDPOINTS)), ENDPOINT, "INVENTED_API_KEY", "sk-test-INVENTED-0123456789", "2", "", False]
    )
    assert run_init(home, ui=ui, client_factory=lambda s, h: Listing(["invented-chat", "invented-pro"]))
    pick = next(f for f in ui.fields if f.name == "model")
    assert pick.kind == "choice" and [o.label for o in pick.options] == ["invented-chat", "invented-pro"]
    assert connected_model(home).model == "invented-pro"
    raw = mdfile.read(home.models)[0]["models"]
    assert raw["default"] == "invented-pro" and set(raw) >= {"invented-chat", "invented-pro", "cheap"}


def test_a_full_default_is_kept_when_another_becomes_the_default(home):
    models.add_models(home, ENDPOINT, "INVENTED_API_KEY", ["invented-chat", "invented-pro"])
    old = load_models(home)["default"]
    models.set_default(home, "invented-pro")
    specs = load_models(home)
    assert specs["default"].model == "invented-pro"
    assert any(s.model == old.model and n not in ("default", "cheap") for n, s in specs.items())


def test_connections_group_models_and_say_who_uses_them(home):
    models.add_models(home, ENDPOINT, "INVENTED_API_KEY", ["invented-chat", "invented-pro"])
    models.set_default(home, "invented-chat")
    write_role(home, "researcher")
    models.set_role_model(home, "default/researcher", "invented-pro")
    conn = next(c for c in models.connections(home) if c["endpoint"] == ENDPOINT)
    by = {m["name"]: m for m in conn["models"]}
    assert by["invented-chat"]["default"] and "the Lead" in by["invented-chat"]["used_by"]
    assert any(u.startswith("Researcher") or "researcher" in u.lower() for u in by["invented-pro"]["used_by"])
    assert models.current_model(home, "default/researcher") == "invented-pro"


def test_a_new_role_is_asked_its_model_only_when_there_is_a_choice(home):
    write_role(home, "researcher")
    quiet = ScriptedUI([])
    models.offer_role_model(home, quiet, "default/researcher", "Researcher")
    assert not quiet.fields  # one model: nothing to ask
    models.add_models(home, ENDPOINT, "INVENTED_API_KEY", ["invented-chat", "invented-pro"])
    ui = ScriptedUI([lambda f: next(o.key for o in f.options if o.label.startswith("invented-pro"))])
    models.offer_role_model(home, ui, "default/researcher", "Researcher")
    assert mdfile.read(home.roles_dir("default") / "researcher" / "config.md")[0]["model"] == "invented-pro"


def test_the_command_line_lists_and_sets_models(home, capsys, monkeypatch):
    from ombro.cli import main

    write_role(home, "researcher")
    models.add_models(home, ENDPOINT, "TEST_MODEL_KEY", ["invented-chat", "invented-pro"])
    assert main(["models"]) == 0
    out = capsys.readouterr().out
    assert "invented-pro" in out and "(the default)" in out
    assert main(["model", "default/researcher", "invented-pro"]) == 0
    assert models.current_model(home, "default/researcher") == "invented-pro"
    assert main(["models", "default", "invented-chat"]) == 0
    assert load_models(home)["default"].model == "invented-chat"
    assert main(["model", "nobody/here", "invented-pro"]) == 1


def test_presets_name_their_keys_and_chat_models_leave_out_the_rest():
    from ombro.init_cmd import ENDPOINTS, model_form
    from ombro.models import PRESETS, chat_models

    labels = [p["label"] for p in PRESETS]
    assert "OpenAI API (key)" in labels and "Anthropic" in labels
    assert all(p["endpoint"].startswith("http") for p in PRESETS)
    local = next(p for p in PRESETS if p["key"] == "")
    assert model_form(local).fields == []  # a provider on this computer: no URL, no key name asked
    assert ENDPOINTS[-1]["endpoint"] is None  # "another" stays last in set-up
    ids = ["gpt-x", "text-embedding-3-small", "whisper-1", "tts-1", "gpt-x-realtime", "dall-e-3", "o9-mini"]
    assert chat_models(ids) == ["gpt-x", "o9-mini"]
    assert chat_models(["whisper-1"]) == ["whisper-1"]  # nothing looks like chat: keep the list
