"""Home directory, roles, settings, the result contract and the journal."""

from __future__ import annotations

import os
import stat

import pytest

from conftest import write_role
from ombro import contract, journal, mdfile, prompts
from ombro.home import Home, ensure_home
from ombro.roles import RoleError, crew_roles, load_lead, load_role
from ombro.settings import load_limits, load_models, parse_duration


def test_home_honours_ombro_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OMBRO_HOME", str(tmp_path / "elsewhere"))
    assert Home().root == (tmp_path / "elsewhere").resolve()


def test_an_old_home_has_its_runs_moved_to_jobs(home, tmp_path):
    """Jobs were once called runs: their folder, job.md, its job_id and a renamed limit follow."""
    from ombro import jobs, memory
    from ombro.home import migrate_jobs
    from ombro.settings import Limits, load_limits

    job_dir = jobs.create_job(home, ["default"], {}, "# Invented task\n", load_limits(home), source="test")
    meta, _ = jobs.read_job(job_dir)
    meta = {("run_id" if k == "job_id" else k): v for k, v in meta.items()}
    meta["limits"] = {("run_timeout" if k == "job_timeout" else k): v for k, v in meta["limits"].items()}
    old = home.root / ("run" + "s")
    home.jobs_dir.rename(old)
    (old / job_dir.name / "job.md").unlink()
    mdfile.write(old / job_dir.name / ("run" + ".md"), meta, "Written by Ombro while the run is in progress.\n")
    index = memory.index_path(home, "default")
    index.write_text(f"# Index\n- 2026-11-13 · {job_dir.name} · completed · x · run" + f"s/{job_dir.name}/report.md\n")
    home.limits.write_text("---\nallowance: moderate\nrun_timeout: 5m\n---\n")

    assert "moved 1" in migrate_jobs(home)
    assert not old.exists() and (home.jobs_dir / job_dir.name / "job.md").is_file()
    moved, body = jobs.read_job(home.jobs_dir / job_dir.name)
    assert moved["job_id"] == job_dir.name and "run_id" not in moved
    assert Limits.from_dict(moved["limits"]).job_timeout > 0
    assert "the job" in body
    assert f"jobs/{job_dir.name}/report.md" in index.read_text()
    assert load_limits(home).job_timeout == 300  # the old name in limits.md is still read
    assert migrate_jobs(home) is None  # nothing left to move

def test_fresh_home_layout(tmp_path):
    h = Home(tmp_path / "h")
    ensure_home(h)
    for rel in (
        "secrets/keys.env", "limits.md", "models.md", "tools.md",
        "preferences/config.md", "preferences/instructions.md", "skills", "chats",
        "lead/config.md", "lead/soul.md", "specs", "schedules", "jobs", "interviews",
        "crews/default/crew.md", "crews/default/memory/index.md", "crews/default/memory/notes.md",
        "crews/default/roles/general-assistant/config.md", "crews/default/roles/general-assistant/soul.md",
    ):
        assert (h.root / rel).exists(), rel
    assert stat.S_IMODE(os.stat(h.env).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(h.secrets).st_mode) == 0o700
    assert not h.legacy_env.exists()
    assert h.crews() == ["default"]
    # Every settings file parses as front matter.
    for rel in ("limits.md", "models.md", "tools.md", "preferences/config.md", "crews/default/crew.md"):
        mdfile.read(h.root / rel)


def test_ensure_home_never_overwrites(tmp_path):
    h = Home(tmp_path / "h")
    ensure_home(h)
    h.limits.write_text("---\nmax_rounds: 3\n---\nMine.\n")
    assert ensure_home(h) == []
    assert load_limits(h).max_rounds == 3


def test_default_install_loads(tmp_path):
    h = Home(tmp_path / "h")
    ensure_home(h)
    limits = load_limits(h)
    lead, roles = load_lead(h, limits), crew_roles(h, "default", limits)
    assert lead.kind == "lead" and list(roles) == ["general-assistant"]
    assert lead.path == h.lead_dir and not (h.roles_dir("default") / "lead").exists()
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


def test_a_crew_has_no_lead_of_its_own(home):
    write_role(home, "usurper", kind="lead")
    with pytest.raises(RoleError, match="a crew has no Lead of its own"):
        crew_roles(home, "default", load_limits(home))


def test_a_role_prompt_lists_its_skills_by_name_and_description_only(home):
    from ombro import skills

    (home.skills_dir / "neat").mkdir()
    (home.skills_dir / "neat" / "skill.md").write_text("---\n---\nAlways fold the corners.\n<!-- not for the model -->\n")
    write_role(home, "tidier", skills=["neat", "missing"])
    role = load_role(home.roles_dir("default") / "tidier", load_limits(home))
    have, missing = skills.names_for(home, role)
    assert (have, missing) == (["neat"], ["missing"])
    text, _ = skills.listing([skills.load(home, n) for n in have])
    system = prompts.role_system(role, text, [])
    assert "- neat: Always fold the corners." in system  # an old skill.md: its first sentence is its description
    assert "skills.read" in system
    assert "not for the model" not in system
    assert "no tools" in system


def test_durations():
    assert parse_duration("90s") == 90
    assert parse_duration("30m") == 1800
    assert parse_duration("2h") == 7200
    assert parse_duration(45) == 45


def test_spec_overrides_merge_with_defaults(home):
    limits = load_limits(home, {"max_rounds": 2, "tool_max_calls": {"web.search": 3}})
    assert limits.max_rounds == 2
    assert limits.max_calls_for("web.search") == 3
    assert limits.max_calls_for("workspace.list") == limits.tool_max_calls["default"]


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


# -- allowance levels ----------------------------------------------------------------


def test_default_allowance_is_moderate(home):
    lim = load_limits(home)
    assert lim.allowance == "moderate"
    assert (lim.max_tokens, lim.max_rounds, lim.job_timeout, lim.interview_max_tokens) == (800_000, 8, 1800, 100_000)


def test_allowance_sets_the_level_values_and_hand_values_win(home):
    from ombro import mdfile

    mdfile.write(home.limits, {"allowance": "conservative", "max_rounds": 6})
    lim = load_limits(home)
    assert (lim.max_tokens, lim.max_rounds, lim.interview_max_tokens) == (300_000, 6, 50_000)
    # A spec naming a level replaces the level values below it, and its own hand values win.
    lim = load_limits(home, {"allowance": "generous"})
    assert (lim.max_tokens, lim.max_rounds) == (2_000_000, 16)
    lim = load_limits(home, {"allowance": "generous", "max_tokens": 900_000})
    assert lim.max_tokens == 900_000


def test_old_limits_files_still_load(home):
    from ombro import mdfile

    mdfile.write(home.limits, {"budget_usd": 1.0, "max_tokens": 400000, "max_rounds": 8, "job_timeout": "30m"})
    lim = load_limits(home)
    assert lim.max_tokens == 400_000 and "budget_usd" not in lim.to_dict()


def test_cost_settings_from_earlier_versions_are_accepted_and_ignored(home):
    """Ombro reports tokens only. Prices, per-call costs and a display currency
    left in older files load without error and do nothing."""
    from ombro import jobs
    from ombro.settings import load_preferences, load_tool_settings

    meta, body = mdfile.read(home.limits)
    mdfile.write(home.limits, {**meta, "tool_cost_per_call": {"web.search": 0.01}}, body)
    mdfile.write(home.tools, {"tools": {"web.search": {"provider": "fake", "cost_per_call": 0.002}}})
    models, _ = mdfile.read(home.models)
    models["models"]["default"]["price_per_mtok"] = {"input": 0.3, "output": 1.2}
    mdfile.write(home.models, models)
    prefs, body = mdfile.read(home.preferences_config)
    mdfile.write(home.preferences_config, {**prefs, "display_currency": {"code": "GBP", "rate": 0.79}}, body)

    lim = load_limits(home)
    assert "tool_cost_per_call" not in lim.to_dict()
    assert "cost_per_call" not in load_tool_settings(home).tools["web.search"]
    assert not hasattr(load_models(home)["default"], "price_in")
    assert not hasattr(load_preferences(home), "display_currency")
    # A job.md written before usage was renamed is still read.
    assert jobs.usage({"spend": {"total_tokens": 7}}) == {"total_tokens": 7}
    assert jobs.interview_usage({"interview_spend": {"total_tokens": 3}}) == {"total_tokens": 3}
    assert jobs.usage({"usage": {"total_tokens": 9}, "spend": {"total_tokens": 7}}) == {"total_tokens": 9}


def test_fresh_files_mention_no_costs(tmp_path):
    h = Home(tmp_path / "h")
    ensure_home(h)
    for f in (h.limits, h.models, h.tools, h.preferences_config):
        text = f.read_text()
        assert "price" not in text and "currency" not in text and "cost_per_call" not in text, f


def test_interview_limits_use_their_own_cap(home):
    lim = load_limits(home).for_interview()
    assert lim.max_tokens == 100_000 and lim.max_rounds == 40 and lim.report_reserve == 0


def test_an_old_env_moves_into_the_secrets_folder(tmp_path):
    from ombro import keys

    h = Home(tmp_path / "h")
    h.root.mkdir()
    h.legacy_env.write_text("INVENTED_MODEL_KEY=abc12345\nexport INVENTED_SEARCH_KEY=def67890\n")
    assert keys.get_key(h, "INVENTED_MODEL_KEY") == "abc12345"  # still read before it moves
    msg = keys.migrate(h)
    assert "Moved 2 key(s)" in msg and not h.legacy_env.exists()
    assert keys.read_env(h) == {"INVENTED_MODEL_KEY": "abc12345", "INVENTED_SEARCH_KEY": "def67890"}
    assert stat.S_IMODE(os.stat(h.env).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(h.secrets).st_mode) == 0o700
    assert keys.migrate(h) is None  # nothing left to move


def test_moving_keeps_keys_already_in_the_secrets_folder(tmp_path):
    from ombro import keys

    h = Home(tmp_path / "h")
    h.root.mkdir()
    keys.set_key(h, "INVENTED_A", "new-value-1")
    h.legacy_env.write_text("INVENTED_A=old-value-1\nINVENTED_B=old-value-2\n")
    keys.migrate(h)
    assert keys.read_env(h) == {"INVENTED_A": "new-value-1", "INVENTED_B": "old-value-2"}


def test_any_command_moves_an_old_env_and_says_so(home):
    import subprocess
    import sys

    from ombro import keys

    keys.remove_key(home, "DEEPSEEK_API_KEY")
    home.env.unlink(missing_ok=True)
    home.legacy_env.write_text("INVENTED_KEY=abc12345\n")
    r = subprocess.run([sys.executable, "-m", "ombro", "keys"], capture_output=True, text=True,
                       env={**os.environ, "OMBRO_HOME": str(home.root)})
    assert r.returncode == 0, r.stderr
    assert "Moved 1 key(s)" in r.stderr and "abc12345" not in r.stdout + r.stderr
    assert not home.legacy_env.exists() and keys.read_env(home) == {"INVENTED_KEY": "abc12345"}
