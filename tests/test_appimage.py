"""Ombro from an AppImage: jobs and the scheduler's entry run the .AppImage file (with --ombro),
not the sidecar in the AppImage's mount, which goes when the app quits."""

from __future__ import annotations

import sys

import pytest

from ombro import appimage, jobs, scheduler


@pytest.fixture
def in_appimage(monkeypatch, tmp_path):
    """Frozen, on Linux, started by an AppImage's runtime ($APPIMAGE names the file)."""
    file = tmp_path / "Applications" / "Ombro_0.2.0_amd64.AppImage"
    file.parent.mkdir()
    file.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", "/tmp/.mount_OmbroAbc/usr/bin/ombro-serve")
    monkeypatch.setenv("APPIMAGE", str(file))
    return file


def test_jobs_start_through_the_appimage_file(in_appimage):
    assert appimage.path() == str(in_appimage)
    assert jobs.ombro_argv() == [str(in_appimage), "--ombro"]


def test_otherwise_jobs_start_as_before(monkeypatch, in_appimage):
    monkeypatch.delenv("APPIMAGE")
    assert jobs.ombro_argv() == ["/tmp/.mount_OmbroAbc/usr/bin/ombro-serve"]  # frozen, not an AppImage
    monkeypatch.setenv("APPIMAGE", str(in_appimage.parent / "gone.AppImage"))
    assert appimage.path() is None  # a name that isn't a file
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setenv("APPIMAGE", str(in_appimage))
    assert jobs.ombro_argv() == [sys.executable, "-m", "ombro"]


def test_the_scheduler_entry_runs_the_appimage_file(home, no_system_scheduler, in_appimage):
    no_system_scheduler.platform = "linux"
    inst = scheduler.Installer(home, platform="linux", run=no_system_scheduler.run, python=sys.executable)
    assert inst.program() == [str(in_appimage), "--ombro", "tick"]
    inst.install()
    cur = inst.installed()
    assert cur.python == str(in_appimage) and not scheduler.broken(cur)


def test_a_moved_appimage_breaks_the_entry_and_its_next_start_puts_it_right(
    home, no_system_scheduler, in_appimage, monkeypatch
):
    no_system_scheduler.platform = "linux"
    old = scheduler.Installer(home, platform="linux", run=no_system_scheduler.run, python=sys.executable)
    old.install()
    moved = in_appimage.parent.parent / "Ombro.AppImage"
    in_appimage.rename(moved)
    monkeypatch.setenv("APPIMAGE", str(moved))
    assert scheduler.broken(old.installed())  # the entry names a file that's gone
    new = scheduler.Installer(home, platform="linux", run=no_system_scheduler.run, python=sys.executable)
    assert scheduler.ensure(home, new)
    assert new.installed().python == str(moved) and not scheduler.broken(new.installed())


def test_the_app_installs_the_entry_itself_from_an_appimage(home, monkeypatch, in_appimage):
    from ombro import home as home_mod

    monkeypatch.setattr(home_mod, "default_root", lambda: home.root)
    assert scheduler.may_install_itself(home, sys.executable, platform="linux")
    # The mount alone (no AppImage file known) still isn't enough.
    assert not scheduler.may_install_itself(home, sys.executable, platform="linux", appimage=None)
