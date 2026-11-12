"""Role, crew and limits commands (section 13). Invented data; no network."""

from __future__ import annotations

import sys

import pytest

from conftest import write_role
from ombro import cli, mdfile
from ombro.settings import load_limits, load_preferences


def test_role_list_shows_kind_tools_and_broken_roles(home, capsys):
    write_role(home, "scout", kind="helper", tools=["web.search"], description="Finds invented things.")
    (home.roles_dir("default") / "broken").mkdir()
    assert cli.main(["role", "list"]) == 0
    out = capsys.readouterr().out
    assert "scout" in out and "helper" in out and "web.search" in out and "Finds invented things." in out
    assert "researcher" in out and "lead" in out
    assert "broken" in out and "missing config.md" in out


def test_role_remove_asks_then_moves_the_folder(home, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda p: "n")
    assert cli.main(["role", "remove", "researcher"]) == 1
    assert (home.roles_dir("default") / "researcher").is_dir()
    monkeypatch.setattr("builtins.input", lambda p: "y")
    assert cli.main(["role", "remove", "researcher"]) == 0
    assert not (home.roles_dir("default") / "researcher").exists()
    assert (home.crew_dir("default") / "removed" / "researcher" / "soul.md").is_file()
    # A second role of the same name removed later does not overwrite the first.
    write_role(home, "researcher")
    assert cli.main(["role", "remove", "researcher"]) == 0
    assert len(list((home.crew_dir("default") / "removed").iterdir())) == 2
    # The crew still loads without it.
    from ombro.roles import crew_roles

    assert "researcher" not in crew_roles(home, "default", load_limits(home))


def test_the_lead_cannot_be_removed(home, capsys):
    assert cli.main(["role", "remove", "lead"]) == 2
    assert (home.lead_dir / "config.md").is_file()


def test_role_edit_opens_the_editor_and_checks_the_result(home, tmp_path, capsys):
    script = tmp_path / "fake-editor.py"
    script.write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path(sys.argv[1]); p.write_text(p.read_text().replace('kind: specialist', 'kind: wizard'))\n"
    )
    mdfile.write(home.preferences_config, {"default_crew": "default", "editor": f"{sys.executable} {script}"})
    assert cli.main(["role", "edit", "researcher"]) == 1
    out = capsys.readouterr().out
    assert "kind must be one of" in out


def test_role_edit_uses_editor_env(home, monkeypatch, tmp_path, capsys):
    log = tmp_path / "opened.txt"
    script = tmp_path / "record.py"
    script.write_text(f"import sys\nopen({str(log)!r}, 'w').write(' '.join(sys.argv[1:]))\n")
    monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")
    monkeypatch.delenv("VISUAL", raising=False)
    assert cli.main(["role", "edit", "researcher"]) == 0
    opened = log.read_text()
    assert "researcher/config.md" in opened and "researcher/soul.md" in opened
    assert "is valid" in capsys.readouterr().out


def test_role_add_refuses_an_existing_name(home, capsys):
    from ombro import keys

    keys.set_key(home, "TEST_MODEL_KEY", "invented-key-for-the-check")
    assert cli.main(["role", "add", "researcher"]) == 2
    assert "already has a role named researcher" in capsys.readouterr().err


def test_crew_add_list_and_use(home, capsys):
    assert cli.main(["crew", "add", "podcast", "--description", "Invented podcast work."]) == 0
    # A new crew has no Lead of its own: the one Lead briefs its roles.
    assert home.roles_dir("podcast").is_dir() and not (home.roles_dir("podcast") / "lead").exists()
    assert (home.crew_dir("podcast") / "memory" / "notes.md").is_file()
    assert cli.main(["crew", "add", "podcast"]) == 2
    assert cli.main(["crew", "add", "Not Valid"]) == 2
    capsys.readouterr()
    assert cli.main(["crew", "list"]) == 0
    out = capsys.readouterr().out
    assert "* default" in out and "  podcast" in out and "Invented podcast work." in out
    assert cli.main(["crew", "use", "podcast"]) == 0
    assert load_preferences(home).default_crew == "podcast"
    assert "editor" in mdfile.read(home.preferences_config)[0]  # other preferences kept
    assert cli.main(["role", "list"]) == 0 and "Crew podcast:\n  (no roles yet)" in capsys.readouterr().out
    assert cli.main(["crew", "use", "nowhere"]) == 2


def test_limits_shows_and_sets_the_allowance(home, capsys):
    mdfile.write(home.limits, {"budget_usd": 1.0, "max_tokens": 400000, "max_rounds": 8, "loop_threshold": 3})
    assert cli.main(["limits"]) == 0
    out = capsys.readouterr().out
    assert "set by hand: max_tokens, max_rounds" in out and "budget_usd" in out
    assert cli.main(["limits", "set", "generous"]) == 0
    out = capsys.readouterr().out
    assert "Removed values set by hand" in out and "1,500,000" in out
    meta, _ = mdfile.read(home.limits)
    assert meta["allowance"] == "generous" and "max_tokens" not in meta and "budget_usd" not in meta
    assert meta["loop_threshold"] == 3  # other settings kept
    assert load_limits(home).max_tokens == 1_500_000
    assert cli.main(["limits", "set", "lavish"]) == 2


def test_run_accepts_the_name_of_a_saved_spec(home):
    from ombro.cli import _spec_path

    spec = home.specs_dir / "invented.md"
    spec.write_text("# Invented\n")
    assert _spec_path(home, "invented") == spec
    with pytest.raises(cli.CliError):
        _spec_path(home, "missing")


def test_specs_lists_saved_specs_by_name(home, capsys):
    assert cli.main(["specs"]) == 0 and "No specs saved" in capsys.readouterr().out
    folder = home.specs_dir
    (folder / "teapot-survey.md").write_text("---\nname: teapot-survey\n---\n\n# Three invented teapot makers\n")
    (folder / "teapot-survey.previous.md").write_text("# Old\n")
    assert cli.main(["specs"]) == 0
    out = capsys.readouterr().out
    assert "teapot-survey" in out and "Three invented teapot makers" in out and "previous" not in out
    with pytest.raises(cli.CliError, match="Saved specs: teapot-survey"):
        cli._spec_path(home, "nope")
