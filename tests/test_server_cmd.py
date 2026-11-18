"""`ombro server add | remove`. All values are invented; the only server started is Ombro's fake one."""

from __future__ import annotations

import getpass
import sys

import pytest

from conftest import write_role
from ombro import cli, keys, mdfile
from ombro.server_cmd import key_name, pin_warning, server_entry, ServerError
from ombro.settings import load_tool_settings

SECRET = "invented-secret-value-123"


class Answers:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        return self.answers.pop(0)


@pytest.fixture
def typed(monkeypatch):
    """What the user types: `input` answers, and hidden answers for secrets."""
    def setup(*answers, hidden=()):
        a, h = Answers(*answers), Answers(*hidden)
        monkeypatch.setattr("builtins.input", a)
        monkeypatch.setattr(getpass, "getpass", h)
        return a, h
    return setup


def test_a_local_server_is_added_with_its_secret_kept_out_of_tools_md(home, typed, capsys):
    mdfile.write(home.tools, {"tools": {"web.search": {"provider": "fake"}}}, "My notes on tools.\n")
    _, hidden = typed("y", hidden=(SECRET,))
    assert cli.main(["server", "add", "notes", "--command", "uvx invented-notes-mcp==1.2.3 --read-only",
                     "--secret", "NOTES_TOKEN", "--env", "NOTES_REGION=eu", "--class", "internal"]) == 0
    out = capsys.readouterr().out
    meta, body = mdfile.read(home.tools)
    assert body.strip() == "My notes on tools." and meta["tools"] == {"web.search": {"provider": "fake"}}
    assert meta["servers"]["notes"] == {
        "command": "uvx", "args": ["invented-notes-mcp==1.2.3", "--read-only"],
        "env": {"NOTES_REGION": "eu", "NOTES_TOKEN": "${NOTES_NOTES_TOKEN}"}, "class": "internal",
    }
    assert keys.get_key(home, "NOTES_NOTES_TOKEN") == SECRET
    assert SECRET not in home.tools.read_text() and SECRET not in out
    assert "kept in secrets/keys.env as NOTES_NOTES_TOKEN" in hidden.prompts[0]
    assert "Warning" not in out and "Added notes" in out
    assert "notes" in load_tool_settings(home).servers


def test_the_added_server_starts_and_lists_its_tools(home, typed, capsys):
    typed("y")
    assert cli.main(["server", "add", "fake", "--command", f"{sys.executable} -m ombro.fake_mcp",
                     "--no-prompt"]) == 0
    capsys.readouterr()
    assert cli.main(["tools", "fake"]) == 0
    assert "mcp.fake.post_message" in capsys.readouterr().out


def test_a_remote_server_keeps_its_token_in_a_header(home, typed, capsys):
    typed("y", hidden=(SECRET,))
    assert cli.main(["server", "add", "tracker", "--url", "https://mcp.tracker.invalid/mcp",
                     "--secret", "Authorization=TRACKER_TOKEN"]) == 0
    entry = mdfile.read(home.tools)[0]["servers"]["tracker"]
    assert entry == {"url": "https://mcp.tracker.invalid/mcp", "headers": {"Authorization": "Bearer ${TRACKER_TOKEN}"}}
    assert keys.get_key(home, "TRACKER_TOKEN") == SECRET
    assert "ombro connect tracker" in capsys.readouterr().out


def test_the_interactive_add_asks_for_what_is_missing(home, typed, capsys):
    answers, _ = typed("l", "npx invented-mcp", "API_KEY", "y", hidden=(SECRET,))
    assert cli.main(["server", "add", "widgets"]) == 0
    out = capsys.readouterr().out
    assert "invented-mcp is not pinned to an exact version" in out
    assert mdfile.read(home.tools)[0]["servers"]["widgets"]["env"] == {"API_KEY": "${WIDGETS_API_KEY}"}
    assert keys.get_key(home, "WIDGETS_API_KEY") == SECRET


def test_nothing_is_written_unless_confirmed(home, typed):
    before = home.tools.read_text()
    typed("n", hidden=(SECRET,))
    assert cli.main(["server", "add", "notes", "--command", "uvx invented==1.0", "--secret", "TOKEN"]) == 1
    assert home.tools.read_text() == before and keys.get_key(home, "NOTES_TOKEN") is None


def test_an_existing_key_can_be_kept(home, typed):
    keys.set_key(home, "SHARED_TOKEN", SECRET)
    typed("y", hidden=("",))
    assert cli.main(["server", "add", "notes", "--command", "uvx invented==1.0", "--secret",
                     "TOKEN=SHARED_TOKEN"]) == 0
    assert mdfile.read(home.tools)[0]["servers"]["notes"]["env"] == {"TOKEN": "${SHARED_TOKEN}"}
    assert keys.get_key(home, "SHARED_TOKEN") == SECRET


@pytest.mark.parametrize("argv, message", [
    (["server", "add", "Bad Name", "--command", "x"], "Server names use"),
    (["server", "add", "notes", "--command", "uvx x==1 --token ${TOKEN}", "--yes"], "process list"),
    (["server", "add", "notes", "--url", "ftp://x.invalid", "--yes"], "must start with https://"),
    (["server", "add", "notes", "--command", "x", "--url", "https://x.invalid", "--yes"], "either a command"),
])
def test_bad_servers_are_refused(home, typed, capsys, argv, message):
    typed()
    before = home.tools.read_text()
    assert cli.main(argv) == 2
    assert message in capsys.readouterr().err
    assert home.tools.read_text() == before


def test_a_name_already_in_use_is_refused(home, typed, capsys):
    typed("y")
    assert cli.main(["server", "add", "notes", "--command", "uvx invented==1.0", "--no-prompt"]) == 0
    assert cli.main(["server", "add", "notes", "--command", "uvx other==1.0", "--no-prompt"]) == 2
    assert "already a server named 'notes'" in capsys.readouterr().err


def test_remove_takes_its_tool_settings_and_says_what_is_left(home, typed, capsys):
    typed("y", hidden=(SECRET,))
    cli.main(["server", "add", "notes", "--command", "uvx invented==1.0", "--secret", "TOKEN"])
    meta, body = mdfile.read(home.tools)
    meta["tools"] = {**(meta.get("tools") or {}), "mcp.notes.write_page": {"class": "internal"}}
    mdfile.write(home.tools, meta, body)
    write_role(home, "scribe", tools=["mcp.notes.write_page"])
    capsys.readouterr()
    typed("y")
    assert cli.main(["server", "remove", "notes"]) == 0
    out = capsys.readouterr().out
    assert "default/scribe" in out and "NOTES_TOKEN" in out
    meta = mdfile.read(home.tools)[0]
    assert "notes" not in (meta.get("servers") or {}) and "mcp.notes.write_page" not in meta["tools"]
    assert "and NOTES_TOKEN from secrets/keys.env" in out
    assert keys.get_key(home, "NOTES_TOKEN") is None


def test_pin_warning_and_entries():
    assert pin_warning("uvx", ["--with", "mcp==1.30.0", "mcp-google-sheets==0.6.3", "--include-tools", "a,b"]) is None
    assert "mcp" in pin_warning("uvx", ["--with", "mcp", "server==1.0"])
    assert "x@latest" in pin_warning("npx", ["-y", "x@latest"])
    assert pin_warning("/usr/local/bin/my-server", ["--flag"]) is None
    assert key_name("my-notes", "api-key") == "MY_NOTES_API_KEY"
    with pytest.raises(ServerError, match="internal or external"):
        server_entry(url="https://x.invalid", server_class="read-only")
