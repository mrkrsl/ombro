"""Views as plain data, `--json`, `ombro summary`, `ombro preferences`, the journal
follower, /new in chat and "Run it now?". All data is invented; no network."""

from __future__ import annotations

import json
from types import SimpleNamespace

from conftest import LEAD_REPORT, write_role
from ombro import cli, fake, keys, runs, views
from ombro.fake import ScriptedClient
from ombro.journal import Follower


def _a_run_with_a_deliverable(run, home):
    write_role(home, "writer", tools=["workspace.write"])
    status = run([
        fake.brief("writer", "Write an invented note."),
        fake.call("workspace__write", {"path": "workspace/note.md", "content": "Invented.\n"}),
        fake.result("Wrote it.", deliverables=[{"name": "The note", "path": "workspace/note.md"}]),
        fake.finish(),
        fake.keep_notes(),
        LEAD_REPORT,
    ])
    assert status == "completed"
    return run.run_dir


def test_every_view_is_plain_data(run, home):
    run_dir = _a_run_with_a_deliverable(run, home)
    for data in (views.summary(home), views.crews(home), views.run_list(home), views.status(run_dir),
                 views.report(home, run_dir), views.journal_events(run_dir), views.key_list(home),
                 views.schedule_list(home), views.spec_list(home), views.folder_list(home),
                 views.limit_values(home), views.preferences(home)):
        json.dumps(data)


def test_a_report_view_has_its_deliverables_as_links(run, home):
    run_dir = _a_run_with_a_deliverable(run, home)
    r = views.report(home, run_dir)
    (d,) = [x for x in r["deliverables"] if x["kind"] == "deliverable"]
    assert d["name"] == "The note" and d["role"] == "writer" and d["exists"]
    assert d["url"].startswith("file://") and d["path"].endswith("workspace/note.md")
    assert r["report"] and r["status"] == "completed"


def test_the_summary_counts_what_is_going_on(home):
    from ombro.settings import set_preference

    set_preference(home, "name", "Ada")
    s = views.summary(home)
    assert s["name"] == "Ada" and s["lead"]["title"] == "Lead"
    assert [c["title"] for c in s["crews"]] == ["Default Crew"]
    assert [r["title"] for r in s["crews"][0]["roles"]] == ["Researcher"]  # the test home's role
    assert s["counts"] == {"crews": 1, "crews_ready": 1, "active_runs": 0, "scheduled_runs": 0, "waiting": 0,
                           "unseen_runs": 0}
    cli.main(["crew", "add", "legal", "--title", "Legal Team"])  # no roles yet: not ready
    assert views.summary(home)["counts"]["crews_ready"] == 1


def test_the_default_crew_comes_first_then_by_title(home):
    cli.main(["crew", "add", "zeta", "--title", "Alpha Team"])
    cli.main(["crew", "add", "beta"])
    assert [c["title"] for c in views.crews(home)["crews"]] == ["Default Crew", "Alpha Team", "Beta Crew"]


def test_the_keys_view_never_holds_a_value(home):
    keys.set_key(home, "TEST_MODEL_KEY", "sk-test-INVENTED-0123456789")
    data = views.key_list(home)
    assert {"name": "TEST_MODEL_KEY", "set": True, "source": "keys.env", "used_by": ["model default", "model cheap"]} \
        in data["keys"]
    text = json.dumps(data)
    assert "INVENTED" not in text and "6789" not in text


def test_commands_print_json(run, home, capsys):
    run_dir = _a_run_with_a_deliverable(run, home)
    for argv in (["runs", "--json"], ["status", run_dir.name, "--json"], ["report", "--json"],
                 ["journal", "--json"], ["keys", "--json"], ["specs", "--json"], ["role", "list", "--json"],
                 ["crew", "list", "--json"], ["limits", "--json"], ["schedule", "list", "--json"],
                 ["folders", "list", "--json"], ["summary", "--json"], ["preferences", "--json"]):
        capsys.readouterr()
        assert cli.main(argv) == 0, argv
        json.loads(capsys.readouterr().out)
    cli.main(["runs", "--json"])
    (row,) = json.loads(capsys.readouterr().out)
    assert row["run_id"] == run_dir.name and row["total_tokens"] > 0


def test_summary_and_preferences_as_text(home, capsys):
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


def test_journal_follow_prints_until_the_run_ends(run, home, capsys, monkeypatch):
    run_dir = _a_run_with_a_deliverable(run, home)
    runs.update_run(run_dir, status="running", pid=None)
    ticks = []

    def sleep(_):  # the run ends while it is being followed
        ticks.append(1)
        runs.update_run(run_dir, status="completed")

    monkeypatch.setattr(runs, "is_dead", lambda meta: False)
    args = SimpleNamespace(all=False, json=True)
    assert cli._follow_journal(run_dir, args, sleep=sleep) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert ticks and any(e["action"] == "briefed" for e in lines)
    assert not any(e["action"] == "model_call" for e in lines)


def test_new_starts_a_fresh_chat(home):
    from ombro.chat import Chat
    from test_ui import ScriptedUI

    client = ScriptedClient([fake.say("First answer."), fake.say("Second answer.")])
    ui = ScriptedUI([])
    chat = Chat(home, ui=ui, client_factory=lambda s: client)
    assert chat.handle("Something invented.")
    first = chat.path
    assert chat.handle("/new")
    assert chat.path != first and first.is_file() and chat.exchanges == []
    assert chat.handle("Another thing.")
    assert "Something invented." not in json.dumps(client.calls[-1]["messages"])
    assert chat.gate.snapshot()["model_calls"] == 1  # a fresh cap


def test_a_confirmed_brief_can_be_run_at_once(home, monkeypatch, capsys):
    from ombro.interview import Outcome, Proposal

    outcome = Outcome("confirmed", proposal=Proposal(files=[SimpleNamespace(path=home.specs_dir / "x.md")],
                                                     data={"name": "invented-spec"}))
    monkeypatch.setattr(cli, "_require_model", lambda h: None)
    monkeypatch.setattr(cli, "_brief_interview", lambda *a, **k: outcome)
    monkeypatch.setattr(cli, "_confirm", lambda label: label.startswith("Run it now?"))
    started = []
    real_main = cli.main
    monkeypatch.setattr(cli, "main", lambda argv: started.append(argv) or 0)
    assert real_main(["brief"]) == 0
    assert started == [["run", "--spec", "invented-spec", "--detach"]]


# -- seen_at and the run.md lock -----------------------------------------------------


def test_showing_a_report_marks_the_run_seen_once(run, home, capsys):
    run_dir = _a_run_with_a_deliverable(run, home)
    assert views.since_last(home)["finished"] == 1
    assert [r["run_id"] for r in views.summary(home)["unseen"]] == [run_dir.name]
    assert cli.main(["report", run_dir.name]) == 0
    first = runs.read_run(run_dir)[0]["seen_at"]
    assert first and views.since_last(home) == {"finished": 0, "waiting": 0, "runs": []}
    assert runs.mark_seen(run_dir) is False and runs.read_run(run_dir)[0]["seen_at"] == first


def test_a_running_run_is_never_marked_seen(run, home):
    run_dir = _a_run_with_a_deliverable(run, home)
    runs.update_run(run_dir, status="running")
    assert runs.mark_seen(run_dir) is False and not runs.read_run(run_dir)[0].get("seen_at")


def test_report_json_also_marks_it_seen(run, home, capsys):
    run_dir = _a_run_with_a_deliverable(run, home)
    assert cli.main(["report", "--json"]) == 0
    assert runs.read_run(run_dir)[0]["seen_at"]


def test_no_change_to_run_md_is_lost(run, home):
    """The run saving its usage and a decision recording its counts, from two processes at once."""
    import multiprocessing

    run_dir = _a_run_with_a_deliverable(run, home)
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_bump, args=(str(run_dir), f"field_{i}")) for i in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    meta = runs.read_run(run_dir)[0]
    assert [meta.get(f"field_{i}") for i in range(4)] == [50] * 4


def _bump(run_dir: str, field: str) -> None:
    from pathlib import Path

    from ombro import runs as r

    for i in range(1, 51):
        r.update_run(Path(run_dir), **{field: i})
