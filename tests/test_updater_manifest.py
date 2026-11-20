"""latest.json, the app updater's manifest: every update a release carries, signed, by platform."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "gui" / "scripts" / "updater_manifest.py"
spec = importlib.util.spec_from_file_location("updater_manifest", SCRIPT)
um = importlib.util.module_from_spec(spec)
spec.loader.exec_module(um)

# The names the release workflow's builds give their files (0.1.0's dry run).
UPDATES = [
    "Ombro_0.2.0_arm64.app.tar.gz",
    "Ombro_0.2.0_amd64.AppImage",
    "Ombro_0.2.0_aarch64.AppImage",
    "Ombro_0.2.0_amd64.deb",
    "Ombro_0.2.0_arm64.deb",
    "Ombro-0.2.0-1.x86_64.rpm",
    "Ombro-0.2.0-1.aarch64.rpm",
]
OTHERS = ["Ombro_0.2.0_arm64.dmg", "ombro-0.2.0-py3-none-any.whl", "ombro-0.2.0.tar.gz", "SHA256SUMS"]


def release(tmp_path, names=UPDATES, signed=True):
    for name in [*names, *OTHERS]:
        (tmp_path / name).write_bytes(b"invented")
        if signed and name in names:
            (tmp_path / f"{name}.sig").write_text(f"invented-signature-of-{name}\n")
    return tmp_path


def test_every_platform_gets_its_file_and_signature(tmp_path):
    m = um.manifest(release(tmp_path), "0.2.0", "v0.2.0", "owner/ombro", now=datetime(2026, 12, 1, tzinfo=UTC))
    assert m["version"] == "0.2.0" and m["pub_date"] == "2026-12-01T00:00:00Z"
    p = m["platforms"]
    assert sorted(p) == sorted([
        "darwin-aarch64", "darwin-aarch64-app",
        "linux-x86_64", "linux-x86_64-appimage", "linux-x86_64-deb", "linux-x86_64-rpm",
        "linux-aarch64", "linux-aarch64-appimage", "linux-aarch64-deb", "linux-aarch64-rpm",
    ])  # fmt: skip
    assert (
        p["darwin-aarch64"]["url"]
        == "https://github.com/owner/ombro/releases/download/v0.2.0/Ombro_0.2.0_arm64.app.tar.gz"
    )
    assert p["darwin-aarch64"]["signature"] == "invented-signature-of-Ombro_0.2.0_arm64.app.tar.gz"
    # A bare linux-{arch} is the AppImage, which updates on any Linux.
    assert p["linux-x86_64"]["url"].endswith("/Ombro_0.2.0_amd64.AppImage")
    assert p["linux-aarch64-rpm"]["url"].endswith("/Ombro-0.2.0-1.aarch64.rpm")


def test_an_unsigned_update_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="has no .*sig"):
        um.manifest(release(tmp_path, signed=False), "0.2.0", "v0.2.0", "owner/ombro")


def test_a_release_with_no_updates_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="no updates"):
        um.manifest(release(tmp_path, names=[]), "0.2.0", "v0.2.0", "owner/ombro")


def test_main_writes_latest_json(tmp_path):
    assert um.main([str(release(tmp_path)), "0.2.0-rc1", "v0.2.0-rc1", "owner/ombro"]) == 0
    written = json.loads((tmp_path / "latest.json").read_text())
    assert written["version"] == "0.2.0-rc1" and "linux-x86_64-deb" in written["platforms"]
