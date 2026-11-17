"""Stage 7 of the desktop app: what the Settings pages change in the core and over `ombro serve`.
Deleting moves aside (C4), providers are edited and removed (C5), the profile is written, and
secrets go in but never come back out. All data is invented."""

from __future__ import annotations

import os

import pytest

from conftest import write_role
from ombro import keys, mdfile, models, removing, server_cmd, skills
from ombro.fake import ScriptedClient
from ombro.home import ensure_crew
from ombro.jobs import new_job_id
from ombro.settings import load_preferences, load_tool_settings, set_instructions, set_outward
from test_serve_sessions import loopback, serve  # noqa: F401 - fixtures


def _running_job(home, crew: str) -> str:
    jid = new_job_id("invented-report")
    mdfile.write(home.jobs_dir / jid / "job.md", {"status": "running", "pid": os.getpid(), "crews": [crew],
                                                   "brief": "Invented", "created_at": "2026-11-14T09:00:00"})
    return jid


def _two_providers(home) -> None:
    keys.set_key(home, "OTHER_API_KEY", "sk-INVENTED-other-0000")
    models.add_models(home, "https://other.invalid/v1", "OTHER_API_KEY", ["other-large", "other-small"])


# -- removing (C4) --------------------------------------------------------------------


def test_a_crew_moves_to_removed_and_never_while_its_job_runs(home):
    ensure_crew(home, "legal", title="Legal Crew")
    write_role(home, "counsel", crew="legal")
    jid = _running_job(home, "legal")
    check = removing.check_crew(home, "legal")
    assert check.blocked and "still running" in check.blocked
    with pytest.raises(removing.RemoveError):
        removing.remove_crew(home, "legal")
    mdfile.write(home.jobs_dir / jid / "job.md", {"status": "finished", "crews": ["legal"]})
    moved = removing.remove_crew(home, "legal")
    assert "legal" not in home.crews() and (moved / "roles" / "counsel" / "soul.md").is_file()
    assert moved.parent == home.removed_dir / "crews"


def test_the_last_crew_stays_and_the_default_moves_on(home):
    assert removing.check_crew(home, "default").blocked.startswith("It's your only crew")
    ensure_crew(home, "legal")
    c = removing.check_crew(home, "default")
    assert c.blocked is None and any("default crew" in w for w in c.warnings)
    removing.remove_crew(home, "default")
    assert load_preferences(home).default_crew == "legal"


def test_a_crew_warns_about_its_schedules_and_saved_briefs(home):
    ensure_crew(home, "legal")
    mdfile.write(home.specs_dir / "invented-terms.md", {"crews": ["legal"]}, "# Invented terms\n")
    mdfile.write(home.schedules_dir / "terms-check.md", {"spec": "invented-terms", "cron": "0 6 * * 1"}, "")
    warnings = " ".join(removing.check_crew(home, "legal").warnings)
    assert "terms-check" in warnings and "invented-terms" in warnings


def test_a_role_and_a_skill_move_aside(home):
    moved = removing.remove_role(home, "default", "researcher")
    assert moved == home.crew_dir("default") / "removed" / "researcher"
    with pytest.raises(removing.RemoveError):
        removing.remove_role(home, "", "lead")
    write_role(home, "writer", skills=["invented-check"])
    skills.write(home, "invented-check", skills.compose("invented-check", "Checks invented things.", "Check."),
                 source="you", accepted="now")
    assert any("default/writer" in w for w in removing.check_skill(home, "invented-check").warnings)
    moved = removing.remove_skill(home, "invented-check")
    assert moved == home.removed_dir / "skills" / "invented-check" and skills.load(home, "invented-check") is None


# -- providers (C5) and preferences -----------------------------------------------------


def test_a_provider_is_added_edited_and_removed(home):
    lister = ScriptedClient([])
    lister.models = ["invented-pro", "invented-flash"]
    added = models.add_provider(home, "https://api.invented.invalid/", "INVENTED_API_KEY",
                                client_factory=lambda spec, h: lister)
    assert added == ["invented-flash", "invented-pro"] or set(added) == {"invented-pro", "invented-flash"}
    with pytest.raises(Exception, match="already connected"):
        models.add_provider(home, "https://api.invented.invalid", None, client_factory=lambda spec, h: lister)
    models.edit_provider(home, "https://api.invented.invalid", new_endpoint="https://v2.invented.invalid",
                         key_name="")
    conn = next(c for c in models.connections(home) if c["endpoint"] == "https://v2.invented.invalid")
    assert conn["key"] is None and conn["label"] == "Invented"
    models.set_role_model(home, "default/researcher", "invented-pro")
    assert "researcher (Default Crew)" in models.check_remove_provider(home, "https://v2.invented.invalid")["blocked"]
    models.set_role_model(home, "default/researcher", "default")
    assert models.remove_provider(home, "https://v2.invented.invalid") == ["invented-flash", "invented-pro"]
    assert models.check_remove_provider(home, "https://models.invalid/v1")["blocked"].startswith("Your default")


def test_provider_names_for_people():
    assert models.provider_label("https://api.deepseek.com") == "DeepSeek"
    assert models.provider_label("http://192.168.1.100:1234/v1") == "Local"
    assert models.provider_label("https://models.example.org/v1") == "Example"
    assert models.key_name_for("https://api.deepseek.com") == "DEEPSEEK_API_KEY"


def test_the_profile_keeps_its_guidance_and_the_mode_keeps_per_tool_choices(home):
    set_instructions(home, "I run an invented bakery.")
    text = home.instructions.read_text()
    assert "<!--" in text and text.rstrip().endswith("I run an invented bakery.")
    assert load_preferences(home).instructions == "I run an invented bakery."
    from ombro.settings import set_preference

    set_preference(home, "outward", {"preset": "manual", "allow": ["mcp.fake.post_message"]})
    set_outward(home, "auto")
    assert load_preferences(home).outward == {"preset": "auto", "allow": ["mcp.fake.post_message"]}


def test_a_server_is_updated_in_place(home):
    server_cmd.add_server(home, "invented", {"url": "https://mcp.invented.invalid", "class": "internal"})
    mdfile.write(home.tools, {**mdfile.read(home.tools)[0], "tools": {"mcp.invented.post": {"class": "external"}}},
                 mdfile.read(home.tools)[1])
    server_cmd.update_server(home, "invented", {"url": "https://mcp2.invented.invalid", "class": "internal"})
    t = load_tool_settings(home)
    assert t.servers["invented"]["url"] == "https://mcp2.invented.invalid" and "mcp.invented.post" in t.tools


# -- over serve -------------------------------------------------------------------------


def test_general_settings_over_http(serve, home):
    prefs = serve.http.put("/preferences", json={"name": "  Ada   Invented ", "instructions": "Invented profile.",
                                                  "outward": "manual", "theme": "dark"}).json()
    assert prefs["name"] == "Ada Invented" and prefs["instructions"] == "Invented profile."
    assert prefs["outward"] == "manual" and prefs["theme"] == "dark"
    assert serve.http.put("/preferences", json={"outward": "reckless"}).status_code == 400

    serve.client.models = ["invented-pro"]
    r = serve.http.post("/models/providers", json={"endpoint": "https://api.invented.invalid",
                                                  "key": "sk-INVENTED-provider-0000"})
    assert r.status_code == 201, r.text
    assert "sk-INVENTED" not in r.text and keys.get_key(home, "INVENTED_API_KEY") == "sk-INVENTED-provider-0000"
    conn = next(c for c in r.json()["connections"] if c["endpoint"] == "https://api.invented.invalid")
    assert conn["key"] == "INVENTED_API_KEY" and conn["label"] == "Invented"
    assert serve.http.put("/models/providers", json={"endpoint": "https://api.invented.invalid",
                                                     "new_endpoint": "https://v2.invented.invalid"}).status_code == 200
    blocked = serve.http.post("/models/providers/check", json={"endpoint": "https://models.invalid/v1"}).json()
    assert blocked["blocked"]
    assert serve.http.post("/models/providers/remove", json={"endpoint": "https://models.invalid/v1"}).status_code == 409
    gone = serve.http.post("/models/providers/remove", json={"endpoint": "https://v2.invented.invalid"})
    assert gone.status_code == 200 and gone.json()["removed"] == ["invented-pro"]


def test_crews_and_roles_over_http(serve, home):
    serve.post("/crews", {"name": "legal", "title": "Legal Crew"}, status=201)
    write_role(home, "counsel", crew="legal", soul="<!-- a note -->\nYou are invented counsel.\n")
    role = serve.get("/roles/legal/counsel")
    assert role["soul"] == "You are invented counsel."
    serve.http.put("/roles/legal/counsel/soul", json={"text": "You are careful, invented counsel."})
    assert (home.roles_dir("legal") / "counsel" / "soul.md").read_text().startswith("<!-- a note -->")
    assert serve.http.put("/roles/legal/counsel/model", json={"name": "default"}).json()["model"] == "default"
    jid = _running_job(home, "legal")
    assert serve.get("/crews/legal/check")["blocked"]
    assert serve.http.delete("/roles/legal/counsel").status_code == 409
    mdfile.write(home.jobs_dir / jid / "job.md", {"status": "finished", "crews": ["legal"]})
    assert "/legal/removed/counsel" in serve.http.delete("/roles/legal/counsel").json()["moved_to"]
    after = serve.http.delete("/crews/legal").json()
    assert "/removed/crews/legal" in after["moved_to"] and [c["name"] for c in after["crews"]] == ["default"]
    assert serve.http.delete("/crews/legal").status_code == 404


def test_tools_over_http_never_show_a_secret(serve, home):
    r = serve.http.post("/tools/servers", json={"name": "Invented Notes", "url": "https://mcp.invented.invalid",
                                               "secrets": {"Authorization": "sk-INVENTED-header-0000"}})
    assert r.status_code == 201, r.text
    assert "sk-INVENTED" not in r.text
    row = next(x for x in r.json()["servers"] if x["server"] == "invented-notes")
    assert row["secrets"] == ["Authorization"] and row["sign_in"] == "uses its key"
    cfg = load_tool_settings(home).servers["invented-notes"]
    assert cfg["headers"] == {"Authorization": "Bearer ${INVENTED_NOTES_AUTHORIZATION}"}
    # An edit with the secret left blank keeps it.
    serve.http.put("/tools/servers/invented-notes", json={"url": "https://mcp2.invented.invalid",
                                                         "keep": ["Authorization"]})
    cfg = load_tool_settings(home).servers["invented-notes"]
    assert cfg["url"] == "https://mcp2.invented.invalid" and "Authorization" in cfg["headers"]

    local = serve.http.post("/tools/servers", json={"name": "files", "command": "uvx invented-mcp",
                                                   "secrets": {"TOKEN": "sk-INVENTED-env-0000"}}).json()
    assert "not pinned" in local["warning"] and "sk-INVENTED" not in str(local)
    assert serve.http.post("/tools/servers/files/sign-in").status_code == 400

    assert serve.http.put("/tools/web.search", json={"provider": "firecrawl"}).status_code == 400  # no key yet
    native = serve.http.put("/tools/web.search", json={"provider": "firecrawl", "key": "fc-INVENTED-0000",
                                                       "max_calls": 12})
    assert native.status_code == 200 and "fc-INVENTED" not in native.text
    search = next(t for t in native.json()["native"] if t["tool"] == "web.search")
    assert search == {"tool": "web.search", "provider": "firecrawl", "max_calls": 12, "calls": 12}
    assert serve.http.delete("/tools/servers/files").status_code == 200
    assert "files" not in load_tool_settings(home).servers


def test_skills_over_http(serve, home):
    made = serve.http.post("/skills", json={"title": "Invented Fact Check", "description": "Checks invented facts.",
                                           "body": "Read it twice."})
    assert made.status_code == 201, made.text
    assert [s["name"] for s in made.json()["skills"]] == ["invented-fact-check"]
    assert serve.http.post("/skills", json={"title": "Invented fact check", "description": "x",
                                            "body": "y"}).status_code == 409
    one = serve.get("/skills/invented-fact-check")
    assert one["description"] == "Checks invented facts." and one["body"] == "Read it twice."
    serve.http.put("/skills/invented-fact-check", json={"description": "Checks facts.", "body": "Read it thrice."})
    assert skills.load(home, "invented-fact-check").body == "Read it thrice."
    assert serve.get("/skills/invented-fact-check/check") == {"blocked": None, "warnings": []}
    gone = serve.http.delete("/skills/invented-fact-check").json()
    assert gone["skills"] == [] and "/removed/skills/" in gone["moved_to"]


def test_a_sign_in_from_an_app_on_another_computer(serve, home, monkeypatch):
    """Ombro on a server: the app catches the browser's answer on its own computer and hands it
    over; serve checks it and finishes the sign-in."""
    import time as _time

    from ombro import oauth
    from test_oauth import CFG, MCP, World, browse

    world = World()
    real = oauth.connect
    monkeypatch.setattr(oauth, "connect", lambda *a, **k: real(*a, transport=world.transport(), **k))
    server_cmd.add_server(home, "notes", server_cmd.server_entry(url=MCP))

    assert serve.http.post("/tools/servers/notes/sign-in/answer", json={"url": "x"}).status_code == 404
    started = serve.http.post("/tools/servers/notes/sign-in", json={"hand_over": True, "redirect_port": 5555}).json()
    assert started["state"] == "waiting"
    back = browse(world, started["url"])
    assert back.startswith("http://127.0.0.1:5555/callback?")
    bad = serve.http.post("/tools/servers/notes/sign-in/answer", json={"url": back.replace("state=", "state=x")})
    assert bad.status_code == 400 and "isn't the answer" in bad.json()["error"]
    assert serve.http.post("/tools/servers/notes/sign-in/answer", json={"url": "nothing"}).status_code == 400
    assert serve.http.post("/tools/servers/notes/sign-in/answer", json={"url": back}).json() == {"state": "answered"}
    for _ in range(100):
        row = next(x for x in serve.get("/tools")["servers"] if x["server"] == "notes")
        if not row["signing_in"]:
            break
        _time.sleep(0.05)
    assert not row["signing_in"] and not row["problem"]
    assert oauth.store_path(home, "notes").is_file() and CFG["url"] == MCP
    # The app with no port of its own (a browser): the address is pasted, the same way.
    assert serve.http.post("/tools/servers/notes/sign-in", json={"hand_over": True, "redirect_port": "x"}).status_code == 400
