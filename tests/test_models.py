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
    ui = ScriptedUI(["", "2", ENDPOINT, "INVENTED_API_KEY", "sk-test-INVENTED-0123456789", "2", "", False])
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
