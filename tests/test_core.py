"""Home directory, roles, settings, the result contract and the journal."""

from __future__ import annotations

import os
import stat

import pytest

from conftest import write_role
from ombro import contract, journal, mdfile, prompts
from ombro.home import Home, ensure_home
from ombro.roles import RoleError, load_crew, load_role, skill_texts
from ombro.settings import load_limits, load_models, parse_duration


def test_home_honours_ombro_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OMBRO_HOME", str(tmp_path / "elsewhere"))
    assert Home().root == (tmp_path / "elsewhere").resolve()


def test_fresh_home_layout(tmp_path):
    h = Home(tmp_path / "h")
    ensure_home(h)
    for rel in (
        ".env", "limits.md", "models.md", "tools.md",
        "preferences/config.md", "preferences/instructions.md", "skills", "chats",
        "crews/default/crew.md", "crews/default/roles/lead/config.md", "crews/default/roles/lead/soul.md",
        "crews/default/memory/index.md", "crews/default/memory/notes.md",
        "crews/default/schedules", "crews/default/specs", "crews/default/runs",
    ):
        assert (h.root / rel).exists(), rel
    assert stat.S_IMODE(os.stat(h.env).st_mode) == 0o600
    assert h.crews() == ["default"]
    # Every settings file parses as front matter.
    for rel in ("limits.md", "models.md", "tools.md", "preferences/config.md", "crews/default/crew.md"):
        mdfile.read(h.root / rel)


def test_ensure_home_never_overwrites(tmp_path):
    h = Home(tmp_path / "h")
    ensure_home(h)
    h.limits.write_text("---\nbudget_usd: 0.25\n---\nMine.\n")
    assert ensure_home(h) == []
    assert load_limits(h).budget_usd == 0.25


def test_default_install_loads(tmp_path):
    h = Home(tmp_path / "h")
    ensure_home(h)
    limits = load_limits(h)
    lead, roles = load_crew(h, "default", limits)
    assert lead.kind == "lead" and roles == {}
    models = load_models(h)
    assert models["cheap"].model == models["default"].model
    assert models["cheap"].name == "cheap"


def test_role_loading_and_kind_defaults(home):
    write_role(home, "tidier", kind="helper", description="Tidies things.", tools=["web.search"], skills=["neat"])
    limits = load_limits(home)
    role = load_role(home.roles_dir("default") / "tidier", limits)
    assert role.kind == "helper"
    assert role.model == "cheap"
    assert role.max_steps == limits.max_steps["helper"]
    assert role.tools == ["web.search"]
    researcher = load_role(home.roles_dir("default") / "researcher", limits)
    assert researcher.model == "default" and researcher.max_steps == 10


def test_role_with_no_tools_line_gets_no_tools(home):
    role = load_role(home.roles_dir("default") / "researcher", load_limits(home))
    assert role.tools == []


@pytest.mark.parametrize(
    "change, message",
    [
        ({"kind": "boss"}, "kind must be one of"),
        ({"name": "someone-else"}, "does not match"),
        ({"description": ""}, "description is required"),
        ({"max_steps": 0}, "max_steps"),
        ({"tools": "web.search"}, "list of names"),
    ],
)
def test_bad_role_config_is_refused(home, change, message):
    folder = write_role(home, "broken")
    meta, _ = mdfile.read(folder / "config.md")
    mdfile.write(folder / "config.md", {**meta, **change})
    with pytest.raises(RoleError, match=message):
        load_role(home.roles_dir("default") / "broken", load_limits(home))


def test_missing_soul_is_refused(home):
    folder = write_role(home, "soulless")
    (folder / "soul.md").unlink()
    with pytest.raises(RoleError, match="soul.md"):
        load_role(folder, load_limits(home))


def test_crew_needs_one_lead(home):
    write_role(home, "usurper", kind="lead")
    with pytest.raises(RoleError, match="only the role in roles/lead"):
        load_crew(home, "default", load_limits(home))


def test_skills_are_included_in_the_role_prompt(home):
    (home.skills_dir / "neat").mkdir()
    (home.skills_dir / "neat" / "skill.md").write_text("---\n---\nAlways fold the corners.\n<!-- not for the model -->\n")
    write_role(home, "tidier", skills=["neat", "missing"])
    role = load_role(home.roles_dir("default") / "tidier", load_limits(home))
    found, missing = skill_texts(home, role.skills)
    assert missing == ["missing"]
    system = prompts.role_system(role, found, [])
    assert "Always fold the corners." in system
    assert "not for the model" not in system
    assert "no tools" in system


def test_durations():
    assert parse_duration("90s") == 90
    assert parse_duration("30m") == 1800
    assert parse_duration("2h") == 7200
    assert parse_duration(45) == 45


def test_spec_overrides_merge_with_defaults(home):
    limits = load_limits(home, {"budget_usd": 0.5, "tool_max_calls": {"web.search": 3}})
    assert limits.budget_usd == 0.5
    assert limits.max_calls_for("web.search") == 3
    assert limits.max_calls_for("web.fetch") == limits.tool_max_calls["default"]


# -- result contract ---------------------------------------------------------------

VALID = {"summary": "Did it.", "deliverables": [], "open_questions": [], "did_not_do": [], "requests": []}


def test_valid_result():
    import json

    result, errors = contract.validate_text(json.dumps(VALID))
    assert errors == [] and result == VALID


def test_fenced_and_wrapped_json_is_accepted():
    import json

    assert contract.validate_text("```json\n" + json.dumps(VALID) + "\n```")[0] == VALID
    assert contract.validate_text("Result: " + json.dumps(VALID))[0] == VALID


@pytest.mark.parametrize(
    "text",
    [
        "just prose",
        '{"summary": "x"}',
        '{"summary": "", "deliverables": [], "open_questions": [], "did_not_do": [], "requests": []}',
        '{"summary": "x", "deliverables": [{"name": "a"}], "open_questions": [], "did_not_do": [], "requests": []}',
        '{"summary": "x", "deliverables": [], "open_questions": [], "did_not_do": [], "requests": [], "extra": 1}',
        "[1, 2]",
        "",
    ],
)
def test_invalid_results(text):
    result, errors = contract.validate_text(text)
    assert result is None and errors


def test_journal_reads_past_a_torn_line(tmp_path):
    j = journal.Journal(tmp_path / "journal.jsonl")
    j.write("lead", "briefed", "researcher: invented task")
    with (tmp_path / "journal.jsonl").open("a") as f:
        f.write('{"ts": "2026-11-11T22:14:03Z", "role": "le')
    events = journal.read(tmp_path / "journal.jsonl")
    assert len(events) == 1
    assert set(events[0]) >= {"ts", "role", "action", "note"}
