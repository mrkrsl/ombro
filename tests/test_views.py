"""Views as plain data, `--json`, `ombro summary`, `ombro preferences`, the journal
follower, /new in chat and "Start it now?". All data is invented; no network."""

from __future__ import annotations

import json
from types import SimpleNamespace

from conftest import LEAD_REPORT, write_role
from ombro import cli, fake, keys, jobs, views
from ombro.fake import ScriptedClient
from ombro.journal import Follower


def _a_job_with_a_deliverable(job, home):
    write_role(home, "writer", tools=["workspace.write"])
    status = job(
        [
            fake.brief("writer", "Write an invented note."),
            fake.call("workspace__write", {"path": "workspace/note.md", "content": "Invented.\n"}),
            fake.result("Wrote it.", deliverables=[{"name": "The note", "path": "workspace/note.md"}]),
            fake.finish(),
            fake.keep_notes(),
            LEAD_REPORT,
        ]
    )
    assert status == "completed"
    return job.job_dir


def test_every_view_is_plain_data(job, home):
    job_dir = _a_job_with_a_deliverable(job, home)
    for data in (
        views.summary(home),
        views.crews(home),
        views.job_list(home),
        views.status(job_dir),
        views.report(home, job_dir),
        views.journal_events(job_dir),
        views.key_list(home),
        views.schedule_list(home),
        views.spec_list(home),
        views.folder_list(home),
        views.limit_values(home),
        views.preferences(home),
    ):
        json.dumps(data)


def test_a_report_view_has_its_deliverables_as_links(job, home):
    job_dir = _a_job_with_a_deliverable(job, home)
    r = views.report(home, job_dir)
    (d,) = [x for x in r["deliverables"] if x["kind"] == "deliverable"]
    assert d["name"] == "The note" and d["role"] == "writer" and d["exists"]
    assert d["url"].startswith("file://") and d["path"].endswith("workspace/note.md")
    assert r["report"] and r["status"] == "completed"
    assert d["text"] == "Invented.\n"  # a Markdown file carries its text, to read in the app


def test_a_large_or_non_markdown_file_carries_no_text(job, home):
    job_dir = _a_job_with_a_deliverable(job, home)
    note = job_dir / "workspace" / "note.md"
    note.write_text("x" * (views.MARKDOWN_SHOWN + 1), encoding="utf-8")
    (d,) = [x for x in views.report(home, job_dir)["deliverables"] if x["kind"] == "deliverable"]
    assert "text" not in d


def test_the_summary_counts_what_is_going_on(home):
    from ombro.settings import set_preference

    set_preference(home, "name", "Ada")
    before = views.summary(home)  # no key for the model yet: set-up isn't finished
    assert before["counts"]["crews_ready"] == 0 and before["model"] is None
    from ombro import keys

    keys.set_key(home, "TEST_MODEL_KEY", "sk-test-INVENTED")
    s = views.summary(home)
    assert s["name"] == "Ada" and s["lead"]["title"] == "Lead"
    assert [c["title"] for c in s["crews"]] == ["Default Crew"]
    assert [r["title"] for r in s["crews"][0]["roles"]] == ["Researcher"]  # the test home's role
    assert s["counts"] == {
        "crews": 1,
        "crews_ready": 1,
        "active_jobs": 0,
        "scheduled_jobs": 0,
        "waiting": 0,
        "unseen_jobs": 0,
    }
    cli.main(["crew", "add", "legal", "--title", "Legal Team"])  # no roles yet: not ready
    assert views.summary(home)["counts"]["crews_ready"] == 1


def test_the_default_crew_comes_first_then_by_title(home):
    cli.main(["crew", "add", "zeta", "--title", "Alpha Team"])
    cli.main(["crew", "add", "beta"])
    assert [c["title"] for c in views.crews(home)["crews"]] == ["Default Crew", "Alpha Team", "Beta Crew"]


def test_the_keys_view_never_holds_a_value(home):
    keys.set_key(home, "TEST_MODEL_KEY", "sk-test-INVENTED-0123456789")
    data = views.key_list(home)
    assert {
        "name": "TEST_MODEL_KEY",
        "set": True,
        "source": "keys.env",
        "used_by": ["model default", "model cheap"],
    } in data["keys"]
    text = json.dumps(data)
    assert "INVENTED" not in text and "6789" not in text


def test_commands_print_json(job, home, capsys):
    job_dir = _a_job_with_a_deliverable(job, home)
    for argv in (
        ["jobs", "--json"],
        ["status", job_dir.name, "--json"],
        ["report", "--json"],
        ["journal", "--json"],
        ["keys", "--json"],
        ["specs", "--json"],
        ["role", "list", "--json"],
        ["crew", "list", "--json"],
        ["limits", "--json"],
        ["schedule", "list", "--json"],
        ["folders", "list", "--json"],
        ["summary", "--json"],
        ["preferences", "--json"],
    ):
        capsys.readouterr()
        assert cli.main(argv) == 0, argv
        json.loads(capsys.readouterr().out)
    cli.main(["jobs", "--json"])
    (row,) = json.loads(capsys.readouterr().out)
    assert row["job_id"] == job_dir.name and row["total_tokens"] > 0


def test_summary_and_preferences_as_text(home, capsys):
    from ombro import keys

    keys.set_key(home, "TEST_MODEL_KEY", "sk-test-INVENTED")  # set up, so the crew is ready
    assert cli.main(["summary"]) == 0
    out = capsys.readouterr().out
    assert "Lead\n└ Default Crew  (default)\n   └ Researcher" in out and "1 crew ready to work" in out
    assert cli.main(["preferences"]) == 0
    assert "Theme         auto" in capsys.readouterr().out


def test_the_follower_waits_for_a_line_to_be_finished(tmp_path):
    path = tmp_path / "journal.jsonl"
    f = Follower(path)
    assert f.new_events() == []
    path.write_text('{"action": "one"}\n{"action": "tw')
    assert [e["action"] for e in f.new_events()] == ["one"]
    with path.open("a") as fh:
        fh.write('o"}\nnot json\n{"action": "three"}\n')
    assert [e["action"] for e in f.new_events()] == ["two", "three"]
    assert f.new_events() == []


def test_journal_follow_prints_until_the_job_ends(job, home, capsys, monkeypatch):
    job_dir = _a_job_with_a_deliverable(job, home)
    jobs.update_job(job_dir, status="running", pid=None)
    ticks = []

    def sleep(_):  # the job ends while it is being followed
        ticks.append(1)
        jobs.update_job(job_dir, status="completed")

    monkeypatch.setattr(jobs, "is_dead", lambda meta: False)
    args = SimpleNamespace(all=False, json=True)
    assert cli._follow_journal(job_dir, args, sleep=sleep) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert ticks and any(e["action"] == "briefed" for e in lines)
    assert not any(e["action"] == "model_call" for e in lines)


def test_new_starts_a_fresh_chat(home):
    from ombro.chat import Chat
    from test_ui import ScriptedUI

    client = ScriptedClient([fake.say("First answer."), fake.say("Second answer.")])
    ui = ScriptedUI([])
    chat = Chat(home, ui=ui, client_factory=lambda s: client, titles=False)
    assert chat.handle("Something invented.")
    first = chat.path
    assert chat.handle("/new")
    assert chat.path != first and first.is_file() and chat.exchanges == []
    assert chat.handle("Another thing.")
    assert "Something invented." not in json.dumps(client.calls[-1]["messages"])
    assert chat.gate.snapshot()["model_calls"] == 1  # a fresh cap


def test_a_confirmed_brief_can_be_started_at_once(home, monkeypatch, capsys):
    from ombro.interview import Outcome, Proposal

    outcome = Outcome(
        "confirmed",
        proposal=Proposal(files=[SimpleNamespace(path=home.specs_dir / "x.md")], data={"name": "invented-spec"}),
    )
    monkeypatch.setattr(cli, "_require_model", lambda h: None)
    monkeypatch.setattr(cli, "_brief_interview", lambda *a, **k: outcome)
    monkeypatch.setattr(cli, "_confirm", lambda label: label.startswith("Start it now?"))
    started = []
    real_main = cli.main
    monkeypatch.setattr(cli, "main", lambda argv: started.append(argv) or 0)
    assert real_main(["brief"]) == 0
    assert started == [["start", "--spec", "invented-spec", "--detach"]]


# -- seen_at and the job.md lock -----------------------------------------------------


def test_showing_a_report_marks_the_job_seen_once(job, home, capsys):
    job_dir = _a_job_with_a_deliverable(job, home)
    assert views.since_last(home)["finished"] == 1
    assert [r["job_id"] for r in views.summary(home)["unseen"]] == [job_dir.name]
    assert cli.main(["report", job_dir.name]) == 0
    first = jobs.read_job(job_dir)[0]["seen_at"]
    assert first and views.since_last(home) == {"finished": 0, "waiting": 0, "jobs": []}
    assert jobs.mark_seen(job_dir) is False and jobs.read_job(job_dir)[0]["seen_at"] == first


def test_a_running_run_is_never_marked_seen(job, home):
    job_dir = _a_job_with_a_deliverable(job, home)
    jobs.update_job(job_dir, status="running")
    assert jobs.mark_seen(job_dir) is False and not jobs.read_job(job_dir)[0].get("seen_at")


def test_report_json_also_marks_it_seen(job, home, capsys):
    job_dir = _a_job_with_a_deliverable(job, home)
    assert cli.main(["report", "--json"]) == 0
    assert jobs.read_job(job_dir)[0]["seen_at"]


def test_no_change_to_job_md_is_lost(job, home):
    """The job saving its usage and a decision recording its counts, from two processes at once."""
    import multiprocessing

    job_dir = _a_job_with_a_deliverable(job, home)
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_bump, args=(str(job_dir), f"field_{i}")) for i in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    meta = jobs.read_job(job_dir)[0]
    assert [meta.get(f"field_{i}") for i in range(4)] == [50] * 4


def _bump(job_dir: str, field: str) -> None:
    from pathlib import Path

    from ombro import jobs as r

    for i in range(1, 51):
        r.update_job(Path(job_dir), **{field: i})


def test_a_job_reaped_after_it_stops_counting_as_active_still_changes_the_counts(home):
    """A dead job leaves the active jobs a poll before it is marked failed and given its report.
    That second step must count as a change too, or the menu bar never alerts for it."""
    from conftest import SPEC
    from ombro.session import JobWatcher
    from ombro.settings import load_limits, load_tool_settings

    job_dir = jobs.create_job(
        home, ["default"], {}, SPEC, load_limits(home, None, load_tool_settings(home)), source="test"
    )
    jobs.update_job(job_dir, pid=999_999_9, status="running")  # its process has gone
    watcher = JobWatcher(home)
    watcher.active = {job_dir.name}
    _, changed = watcher.poll(mark_seen=False)
    assert changed and views.summary(home)["counts"]["unseen_jobs"] == 0  # ended, but no report yet
    _, changed = watcher.poll(mark_seen=False)
    assert not changed
    assert jobs.reap_dead_jobs(home) == [job_dir]  # the scheduler's tick does this
    _, changed = watcher.poll(mark_seen=False)
    assert changed and views.summary(home)["counts"]["unseen_jobs"] == 1
