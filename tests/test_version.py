"""One version: the core's, in src/ombro/__init__.py, which the wheel takes too (pyproject.toml).
The app's manifests carry copies of it, and must match, or the app and its sidecar disagree."""

import json
import re
import tomllib
from pathlib import Path

import pytest

import ombro

ROOT = Path(__file__).resolve().parent.parent


def test_the_app_carries_the_core_version():
    gui = ROOT / "gui"
    package = json.loads((gui / "package.json").read_text())["version"]
    tauri = json.loads((gui / "src-tauri" / "tauri.conf.json").read_text())["version"]
    cargo = tomllib.loads((gui / "src-tauri" / "Cargo.toml").read_text())["package"]["version"]
    assert package == tauri == cargo == ombro.__version__


def test_the_wheel_takes_its_version_from_the_package():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" in project["project"]["dynamic"]
    assert project["tool"]["hatch"]["version"]["path"] == "src/ombro/__init__.py"
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-.]?\w+)?", ombro.__version__)


def _bump_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("bump_version", ROOT / "scripts" / "bump_version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_lockfiles_carry_the_core_version_too():
    lock = json.loads((ROOT / "gui" / "package-lock.json").read_text())
    assert lock["version"] == lock["packages"][""]["version"] == ombro.__version__
    cargo_lock = tomllib.loads((ROOT / "gui" / "src-tauri" / "Cargo.lock").read_text())
    assert [p["version"] for p in cargo_lock["package"] if p["name"] == "ombro-gui"] == [ombro.__version__]


def test_the_bump_script_changes_every_copy_and_nothing_else(tmp_path):
    bump = _bump_script()
    for name, _, _ in bump.PLACES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text((ROOT / name).read_text())
    before = {name: (tmp_path / name).read_text() for name, _, _ in bump.PLACES}

    assert sorted(bump.bump("9.8.7-rc1", tmp_path)) == sorted(name for name, _, _ in bump.PLACES)
    for name, _, _ in bump.PLACES:
        # Only the version changed: put the old one back and the file is as it was.
        assert (tmp_path / name).read_text().replace("9.8.7-rc1", ombro.__version__) == before[name]
    assert json.loads((tmp_path / "gui" / "package-lock.json").read_text())["packages"][""]["version"] == "9.8.7-rc1"
    assert tomllib.loads((tmp_path / "gui" / "src-tauri" / "Cargo.toml").read_text())["package"]["version"] == "9.8.7-rc1"
    cargo_lock = tomllib.loads((tmp_path / "gui" / "src-tauri" / "Cargo.lock").read_text())
    assert [p["version"] for p in cargo_lock["package"] if p["name"] == "ombro-gui"] == ["9.8.7-rc1"]
    assert '__version__ = "9.8.7-rc1"' in (tmp_path / "src" / "ombro" / "__init__.py").read_text()
    assert bump.bump("9.8.7-rc1", tmp_path) == []


def test_the_bump_script_refuses_a_bad_version(tmp_path):
    with pytest.raises(SystemExit):
        _bump_script().bump("v0.2", tmp_path)
