"""One version: the core's, in src/ombro/__init__.py, which the wheel takes too (pyproject.toml).
The app's manifests carry copies of it, and must match, or the app and its sidecar disagree."""

import json
import re
import tomllib
from pathlib import Path

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
