"""Simple file actions in chat (move, copy, rename, fill in) are file operations, never a rewrite and
never a job (T20). All invented."""

from __future__ import annotations

import pytest

from ombro import fake, folders
from test_chat import ChatSession

REPORT = "# Competitor matrix report\n\n" + "".join(f"- Row {i}: invented finding\n" for i in range(80)) \
    + "\nNo sheet exists yet.\n"


@pytest.fixture
def user(tmp_path, monkeypatch):
    me = tmp_path / "user"
    (me / "Documents" / "Work" / "Framework").mkdir(parents=True)
    (me / "Documents" / "Work" / "notes.md").write_text("Invented notes.\n")
    (me / "Documents" / "Brand").mkdir(parents=True)
    (me / "Documents" / "Brand" / "guide.md").write_text("Invented brand guide.\n")
    monkeypatch.setenv("HOME", str(me))
    return me.resolve()


@pytest.fixture
def chat(home, user):
    folders.add(home, user / "Documents" / "Work", "work", "write")
    folders.add(home, user / "Documents" / "Brand", "brand", "read")
    job = home.jobs_dir / "matrix-20261119-1119" / "workspace"
    job.mkdir(parents=True)
    (job / "Competitor matrix report.md").write_text(REPORT)
    return ChatSession(home)


def test_a_finished_report_is_copied_into_a_folder_as_it_is(chat, user):
    chat([
        fake.call("folders__copy", {"from": "jobs/matrix-20261119-1119/workspace/Competitor matrix report.md",
                                    "to": "shared/work/Framework/"}),
        fake.say("It's in Framework/."),
    ], ["Put the report in Framework", "y"])
    (reply,) = chat.tool_replies()
    saved = user / "Documents" / "Work" / "Framework" / "Competitor matrix report.md"
    assert saved.read_text() == REPORT and reply["moved"] is False
    assert "I'd like to copy" in chat.output
    assert not list(chat.home.jobs_dir.glob("competitor-matrix-report*"))  # no job, no offer


def test_then_a_line_is_corrected_with_an_edit(chat, user):
    saved = user / "Documents" / "Work" / "Framework" / "report.md"
    saved.write_text(REPORT)
    chat([
        fake.call("folders__edit", {"path": "shared/work/Framework/report.md", "old": "No sheet exists yet.",
                                    "new": "The sheet: https://docs.example.invalid/sheet"}),
        fake.say("Corrected."),
    ], ["Fix the sheet line", "y"])
    (reply,) = chat.tool_replies()
    assert reply["replaced"] == 1
    assert "The sheet: https://docs.example.invalid/sheet" in saved.read_text()
    assert "No sheet exists yet." not in saved.read_text()
    assert "No sheet exists yet." in (user / "Documents" / "Work" / "Archive" / "report.md").read_text()


def test_a_file_is_renamed_within_a_writable_folder(chat, user):
    chat([
        fake.call("folders__copy", {"from": "shared/work/notes.md", "to": "shared/work/Framework/old-notes.md",
                                    "move": True}),
        fake.say("Moved."),
    ], ["Move my notes into Framework as old-notes.md", "y"])
    assert not (user / "Documents" / "Work" / "notes.md").exists()
    assert (user / "Documents" / "Work" / "Framework" / "old-notes.md").read_text() == "Invented notes.\n"


def test_read_only_files_are_copied_never_moved_and_nothing_lands_in_a_read_only_folder(chat, user):
    chat([
        fake.call("folders__copy", {"from": "shared/brand/guide.md", "to": "shared/work/guide.md", "move": True}),
        fake.call("folders__copy", {"from": "shared/work/notes.md", "to": "shared/brand/notes.md"}),
        fake.say("Couldn't."),
    ], ["Move the guide"])
    moved, into_read_only = chat.tool_replies()
    assert "can be copied instead" in moved["error"]
    assert "error" in into_read_only
    assert (user / "Documents" / "Brand" / "guide.md").exists()
    assert not (user / "Documents" / "Brand" / "notes.md").exists()


def test_an_existing_file_is_not_replaced_unasked(chat, user):
    (user / "Documents" / "Work" / "Framework" / "Competitor matrix report.md").write_text("Mark's own.\n")
    chat([
        fake.call("folders__copy", {"from": "jobs/matrix-20261119-1119/workspace/Competitor matrix report.md",
                                    "to": "shared/work/Framework/"}),
        fake.say("It's there already."),
    ], ["Put the report in Framework"])
    (reply,) = chat.tool_replies()
    assert "exists already" in reply["error"]
    assert (user / "Documents" / "Work" / "Framework" / "Competitor matrix report.md").read_text() == "Mark's own.\n"


def test_a_refused_copy_changes_nothing(chat, user):
    chat([
        fake.call("folders__copy", {"from": "jobs/matrix-20261119-1119/workspace/Competitor matrix report.md",
                                    "to": "shared/work/Framework/"}),
        fake.say("Left it."),
    ], ["Put the report in Framework", "n"])
    (reply,) = chat.tool_replies()
    assert "declined" in reply
    assert not list((user / "Documents" / "Work" / "Framework").iterdir())


def test_chat_settings_files_cannot_be_copied_out(chat, user):
    chat([
        fake.call("folders__copy", {"from": "secrets/keys.env", "to": "shared/work/keys.env"}),
        fake.say("No."),
    ], ["Copy my keys"])
    (reply,) = chat.tool_replies()
    assert "error" in reply and not (user / "Documents" / "Work" / "keys.env").exists()


def test_the_lead_is_told_file_actions_are_not_rewrites(chat):
    chat([fake.say("Hello.")], ["Hi"])
    system = chat.client.calls[0]["messages"][0]["content"]
    assert "folders__copy does it as it is" in system
    names = {t["function"]["name"] for t in chat.client.calls[0]["tools"]}
    assert {"folders__copy", "folders__edit"} <= names
