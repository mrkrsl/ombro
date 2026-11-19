"""Settings and set-up: a skill added from a folder the app sends, a pasted
SKILL.md in the skill form, the default crew, APIs added and edited in the terminal, keys removed
with what used them, and set-up that saves nothing when a key is refused. All data is invented."""

from __future__ import annotations

import pytest

from conftest import write_role
from ombro import cli, keys, mdfile, skills
from ombro.client import ModelError, ModelReply
from ombro.home import Home, ensure_crew
from ombro.settings import load_models, load_preferences, load_tool_settings
from test_cli import ScriptedPrompts
from test_serve_sessions import loopback, serve  # noqa: F401 - fixtures
from test_ui import ScriptedUI

SKILL = "---\nname: kettle-survey\ndescription: Surveys invented kettles.\n---\n# Method\n\nBoil one, then note it.\n"


# -- a whole SKILL.md pasted into the method box ------------------------------------------


def test_a_pasted_skill_md_gives_its_name_and_description_and_loses_its_front_matter(serve, home):
    made = serve.http.post("/skills", json={"title": "", "description": "", "body": SKILL})
    assert made.status_code == 201, made.text
    sk = skills.load(home, "kettle-survey")
    assert sk.description == "Surveys invented kettles."
    assert sk.body.startswith("# Method") and "---" not in sk.body  # not nested
    # The field wins over the front matter when both are given.
    serve.http.put("/skills/kettle-survey", json={"description": "Checks kettles.", "body": SKILL})
    assert skills.load(home, "kettle-survey").description == "Checks kettles."
    bad = serve.http.put("/skills/kettle-survey", json={"description": "", "body": "---\nname: [oops\n---\nx\n"})
    assert bad.status_code == 400 and "can't be read" in bad.json()["error"]
    unclosed = serve.http.post("/skills", json={"title": "x", "description": "d", "body": "---\nname: x\nbody"})
    assert unclosed.status_code == 400 and "front matter" in unclosed.json()["error"]


# -- a skill from a folder, sent by the app -----------------------------------------------


def test_a_skill_folder_sent_by_the_app_is_checked_then_added(serve, home):
    write_role(home, "surveyor", skills=["other"])
    write_role(home, "helper", kind="helper", skills="all")
    files = {"SKILL.md": SKILL, "references/models.md": "Invented kettle models."}
    body = {"folder": "kettle-survey", "path": "/Users/ada/Skills/kettle-survey", "files": files,
            "skipped": ["scripts/boil.sh"]}
    seen = serve.post("/skills/import", body)
    assert seen["name"] == "kettle-survey" and not seen["replaces"]
    assert seen["references"] == ["references/models.md"] and seen["skipped"] == ["scripts/boil.sh"]
    assert skills.load(home, "kettle-survey") is None  # only looked at

    added = serve.post("/skills/import", {**body, "add": True})
    assert added["added"] == "kettle-survey"
    assert "default/surveyor" in added["not_given"] and "default/helper" not in added["not_given"]
    assert any(s["name"] == "kettle-survey" for s in added["skills"])
    sk = skills.load(home, "kettle-survey")
    assert sk.source == "imported from /Users/ada/Skills/kettle-survey"
    assert (home.skills_dir / "kettle-survey" / "references" / "models.md").read_text() == "Invented kettle models."
    serve.post("/skills/kettle-survey/give", {"role": "default/surveyor"})
    assert "default/surveyor" not in skills.without(home, "kettle-survey")

    again = serve.post("/skills/import", body)
    assert again["replaces"] and again["diff"] is not None


@pytest.mark.parametrize("files", [{"../SKILL.md": SKILL}, {"/etc/SKILL.md": SKILL}, {"notes.md": "x"}, {}])
def test_a_folder_sent_can_only_hold_files_inside_it_and_needs_a_skill_md(serve, home, files):
    r = serve.http.post("/skills/import", json={"folder": "x", "files": files})
    assert r.status_code == 400, r.text


def test_the_terminal_says_which_roles_dont_get_an_added_skill(home, tmp_path, capsys):
    folder = tmp_path / "kettle-survey"
    folder.mkdir()
    (folder / "SKILL.md").write_text(SKILL)
    write_role(home, "surveyor", skills=["other"])
    assert cli.main(["skills", "add", str(folder), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "default/surveyor" in out and "ombro skills give kettle-survey" in out


# -- the default crew ----------------------------------------------------------------------


def test_the_default_crew_is_chosen_over_serve(serve, home):
    ensure_crew(home, "legal", title="Legal Crew")
    v = serve.http.put("/crews/legal/default", json={})
    assert v.status_code == 200 and v.json()["default_crew"] == "legal"
    assert next(c for c in v.json()["crews"] if c["name"] == "legal")["default"]
    assert load_preferences(home).default_crew == "legal"
    assert serve.http.put("/crews/nowhere/default", json={}).status_code == 404


# -- APIs in the terminal -------------------------------------------------------------------


def test_an_api_is_added_from_the_command_line_and_its_key_typed_hidden(home, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "pk-INVENTED-weather-0000")
    assert cli.main(["tools", "add", "weather", "--url", "https://api.weather.invalid/v1", "--about",
                     "Invented weather.", "--class", "read-only", "--send-key", "header:X-API-Key", "--yes"]) == 0
    api = load_tool_settings(home).apis["weather"]
    assert api == {"url": "https://api.weather.invalid/v1", "key": "WEATHER_API_KEY", "send_key": "header:X-API-Key",
                   "about": "Invented weather.", "class": "read-only", "max_calls": 30}
    assert keys.get_key(home, "WEATHER_API_KEY") == "pk-INVENTED-weather-0000"
    assert "pk-INVENTED" not in capsys.readouterr().out
    assert cli.main(["tools", "edit", "weather", "--class", "external", "--yes"]) == 0
    assert load_tool_settings(home).apis["weather"]["class"] == "external"
    assert keys.get_key(home, "WEATHER_API_KEY")  # an edit that doesn't touch the key keeps it
    assert cli.main(["tools", "edit", "weather", "--no-key", "--yes"]) == 0
    assert "key" not in load_tool_settings(home).apis["weather"] and keys.get_key(home, "WEATHER_API_KEY") is None
    assert cli.main(["tools", "edit", "nothing", "--yes"]) != 0


def test_an_api_is_added_by_answering_questions(home):
    from ombro import tool_cmd

    ui = ScriptedUI(["https://api.podcasts.invalid/v2", "Invented podcasts.",
                     lambda f: next(o.key for o in f.options if o.label.startswith("internal")),
                     True,  # its GETs only look things up
                     "pk-INVENTED-podcasts-0000",
                     lambda f: next(o.key for o in f.options if o.label.startswith("in the address")),
                     "api_key", "y"])
    assert tool_cmd.api_flow(home, ui, "add", "podcasts", {}) == 0
    api = load_tool_settings(home).apis["podcasts"]
    assert api["class"] == "internal" and api["send_key"] == "query:api_key" and api["key"] == "PODCASTS_API_KEY"
    assert keys.get_key(home, "PODCASTS_API_KEY") == "pk-INVENTED-podcasts-0000"


# -- keys go with what used them ---------------------------------------------------------------


def test_deleting_an_api_or_server_removes_its_key_unless_another_uses_it(serve, home):
    serve.post("/tools/apis", {"name": "weather", "url": "https://api.weather.invalid", "key": "pk-INVENTED-1111",
                               "about": "Invented."}, status=201)
    # A second API sharing the key by name, by hand in tools.md.
    meta, body = mdfile.read(home.tools)
    meta["apis"]["forecast"] = {**meta["apis"]["weather"], "url": "https://api.forecast.invalid"}
    mdfile.write(home.tools, meta, body)
    check = serve.get("/tools/apis/weather/check")
    assert any("WEATHER_API_KEY stays" in w for w in check["warnings"])
    serve.http.delete("/tools/apis/weather")
    assert keys.get_key(home, "WEATHER_API_KEY") == "pk-INVENTED-1111"
    check = serve.get("/tools/apis/forecast/check")
    assert any("Its key (WEATHER_API_KEY) is removed" in w for w in check["warnings"])
    serve.http.delete("/tools/apis/forecast")
    assert keys.get_key(home, "WEATHER_API_KEY") is None

    serve.post("/tools/servers", {"name": "notes", "url": "https://mcp.notes.invalid",
                                  "secrets": {"Authorization": "sk-INVENTED-2222"}}, status=201)
    check = serve.get("/tools/servers/notes/check")
    assert any("NOTES_AUTHORIZATION" in w and "removed" in w for w in check["warnings"])
    serve.http.delete("/tools/servers/notes")
    assert keys.get_key(home, "NOTES_AUTHORIZATION") is None


def test_an_add_that_fails_part_way_leaves_no_key_behind(serve, home):
    serve.post("/tools/servers", {"name": "notes", "url": "https://mcp.notes.invalid",
                                  "secrets": {"Authorization": "sk-INVENTED-first"}}, status=201)
    # The same name again: refused, and the key it wrote is put back as it was.
    keys.set_key(home, "OTHER_AUTHORIZATION", "sk-INVENTED-kept")
    meta, body = mdfile.read(home.tools)
    meta["apis"] = {"other": {"url": "https://api.other.invalid", "key": "OTHER_AUTHORIZATION"}}
    mdfile.write(home.tools, meta, body)
    r = serve.http.post("/tools/servers", json={"name": "other", "url": "https://mcp.other.invalid",
                                                "secrets": {"Authorization": "sk-INVENTED-second"}})
    assert r.status_code == 400
    assert keys.get_key(home, "OTHER_AUTHORIZATION") == "sk-INVENTED-kept"
    r = serve.http.post("/tools/apis", json={"name": "notes", "url": "https://api.n.invalid", "key": "pk-INVENTED"})
    assert r.status_code == 400 and keys.get_key(home, "NOTES_API_KEY") is None


def test_an_edit_that_drops_a_secret_removes_its_key(serve, home):
    serve.post("/tools/servers", {"name": "files", "command": "uvx invented-mcp==1.0",
                                  "secrets": {"TOKEN": "sk-INVENTED-env-0000"}}, status=201)
    assert keys.get_key(home, "FILES_TOKEN")
    serve.http.put("/tools/servers/files", json={"command": "uvx invented-mcp==1.0"})
    assert keys.get_key(home, "FILES_TOKEN") is None


# -- no phantom model ----------------------------------------------------------------------


class Refusing:
    """An endpoint that refuses the first key it's given."""

    def __init__(self):
        self.tries = 0

    def list_models(self, timeout=20.0):
        self.tries += 1
        if self.tries == 1:
            raise ModelError("api.deepseek.com returned HTTP 401: invalid key", status=401)
        return ["deepseek-chat", "deepseek-reasoner"]

    def complete(self, *a, **k):
        return ModelReply(content="OK")


def test_set_up_says_a_key_was_refused_and_saves_no_model(tmp_path):
    from ombro.init_cmd import KeyRefused, Saved, connect_model, offered_models  # noqa: F401
    from ombro.ui import ConsoleUI

    home = Home(tmp_path / "fresh")
    from ombro.home import ensure_home

    ensure_home(home)
    endpoint = Refusing()
    said: list[str] = []
    ask = ScriptedPrompts(["1", "1", "2"])  # DeepSeek; DeepSeek again; the second model listed
    secret = ScriptedPrompts(["sk-INVENTED-wrong", "sk-INVENTED-right"])
    ui = ConsoleUI(ask=ask, ask_secret=secret, say=said.append, home=home)
    # Watch models.md and keys.env as they stand when the key is refused.
    seen = {}
    real = endpoint.list_models

    def listing(timeout=20.0):
        seen.setdefault("models", load_models(home))
        return real(timeout)

    endpoint.list_models = listing
    _, model, _ = connect_model(home, ui, lambda s, h: endpoint)
    out = "\n".join(said)
    assert "DeepSeek refused the key" in out and "Nothing was saved" in out
    assert seen["models"] == {}
    assert model == "deepseek-reasoner" and load_models(home)["default"].model == "deepseek-reasoner"
    assert keys.get_key(home, "DEEPSEEK_API_KEY") == "sk-INVENTED-right"


def test_set_up_keeps_nothing_from_a_connection_whose_test_call_fails(tmp_path):
    from ombro.init_cmd import Saved

    home = Home(tmp_path / "fresh")
    from ombro.home import ensure_home

    ensure_home(home)
    before = Saved(home)
    models_text = home.models.read_text()
    keys.set_key(home, "DEEPSEEK_API_KEY", "sk-INVENTED")
    from ombro import models

    models.add_models(home, "https://api.deepseek.com", "DEEPSEEK_API_KEY", ["deepseek-chat"])
    before.restore()
    assert home.models.read_text() == models_text and keys.get_key(home, "DEEPSEEK_API_KEY") is None


def test_a_provider_whose_key_is_refused_is_not_added(serve, home):
    def refuse(timeout=20.0):
        raise ModelError("api.invented.invalid returned HTTP 401: invalid key", status=401)

    serve.client.list_models = refuse
    r = serve.http.post("/models/providers", json={"endpoint": "https://api.invented.invalid",
                                                  "key": "sk-INVENTED-refused-0000", "model": "invented-chat"})
    assert r.status_code == 400 and "refused the key" in r.json()["error"]
    assert keys.get_key(home, "INVENTED_API_KEY") is None
    assert not any(c["endpoint"] == "https://api.invented.invalid" for c in serve.get("/models")["connections"])
