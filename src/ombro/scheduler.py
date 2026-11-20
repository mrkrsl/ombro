"""The single system scheduler entry that calls `ombro tick` once a minute.

macOS: a launch agent, ~/Library/LaunchAgents/local.ombro.tick.plist, loaded
with `launchctl bootstrap gui/<uid>`. It runs only while you are logged in;
slots while logged out, off or asleep are noted as missed.

Linux and other systems with cron: one line in your crontab, marked
`# ombro-tick`, so it can be found and removed again.

The entry calls the Python that installed it, as `python -m ombro tick`, or
the desktop app's own binary, as `<binary> tick` (an AppImage's file, as
`<file> --ombro tick`), with OMBRO_HOME set to this home. The app installs it itself (`ensure`), for the usual home, when it runs
from an Applications folder; the Scheduled page offers it otherwise. Tick writes its own
log to scheduler/tick.log; anything the scheduler catches on stderr (a crash)
goes to scheduler/scheduler.log.

On the command line, nothing here runs unless the user confirms. Tests build an Installer with a
temporary folder and a fake command runner.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ombro.home import Home

LABEL = "local.ombro.tick"
CRON_MARK = "# ombro-tick"
INTERVAL = 60

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class Plan:
    kind: str  # "launchd" or "cron"
    path: Path | None  # the file written (launchd), or None (cron)
    text: str  # the plist, or the crontab line
    commands: list[list[str]] = field(default_factory=list)
    replaces: str | None = None  # what is installed now, if anything


@dataclass
class Installed:
    kind: str
    detail: str  # what it runs
    home: str | None
    python: str | None
    loaded: bool | None = None  # launchd only


class Installer:
    def __init__(
        self,
        home: Home,
        *,
        platform: str = sys.platform,
        agents_dir: Path | None = None,
        run: Runner = subprocess.run,
        python: str = sys.executable,
        uid: int | None = None,
        frozen: bool | None = None,
        appimage: str | None = "",
    ):
        self.home = home
        self.kind = "launchd" if platform == "darwin" else "cron"
        self.agents_dir = agents_dir or Path.home() / "Library" / "LaunchAgents"
        self.run = run
        self.python = str(Path(python).absolute())
        self.uid = os.getuid() if uid is None else uid
        # The desktop app's binary is the whole command line; it has no -m.
        self.frozen = getattr(sys, "frozen", False) if frozen is None else frozen
        # Run from an AppImage, the entry runs the .AppImage file: the binary is in its mount,
        # which goes when the app quits. "" means look ($APPIMAGE); None means not one.
        from ombro import appimage as appimage_mod

        self.appimage = appimage_mod.path() if appimage == "" else appimage

    # -- what would be written ---------------------------------------------

    @property
    def plist_path(self) -> Path:
        return self.agents_dir / f"{LABEL}.plist"

    @property
    def error_log(self) -> Path:
        return self.home.scheduler_dir / "scheduler.log"

    def program(self) -> list[str]:
        from ombro.appimage import FLAG

        if self.appimage:
            return [self.appimage, FLAG, "tick"]
        return [self.python, "tick"] if self.frozen else [self.python, "-m", "ombro", "tick"]

    def plist(self) -> str:
        data = {
            "Label": LABEL,
            "ProgramArguments": self.program(),
            "EnvironmentVariables": {
                **self.home.process_env(),
                "PATH": f"{Path(self.python).parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            },
            "StartInterval": INTERVAL,
            "RunAtLoad": True,
            # Jobs started by tick live in their own session; never let launchd
            # clean them up when tick itself exits.
            "AbandonProcessGroup": True,
            "StandardOutPath": "/dev/null",
            "StandardErrorPath": str(self.error_log),
        }
        return plistlib.dumps(data).decode("utf-8")

    def cron_line(self) -> str:
        """The crontab line. Paths are shell-quoted, and `%`, which crontab turns into a
        line break, is escaped. A path holding a line break cannot be written at all."""
        paths = [*self.program(), str(self.home.root), str(self.error_log)]
        if any(c in p for p in paths for c in "\r\n"):
            raise OSError("a path has a line break in it, so it cannot go in a crontab line")

        def q(text: str) -> str:
            return shlex.quote(text).replace("%", "\\%")

        cmd = " ".join(q(p) for p in self.program())
        env = " ".join(f"{k}={q(v)}" for k, v in self.home.process_env().items())
        return f"* * * * * {env} {cmd} >/dev/null 2>>{q(str(self.error_log))} {CRON_MARK}"

    def plan(self) -> Plan:
        now = self.installed()
        replaces = f"{now.kind}: {now.detail}" if now else None
        if self.kind == "launchd":
            cmds = [["launchctl", "bootout", f"gui/{self.uid}/{LABEL}"]] if now else []
            cmds.append(["launchctl", "bootstrap", f"gui/{self.uid}", str(self.plist_path)])
            return Plan("launchd", self.plist_path, self.plist(), cmds, replaces)
        return Plan("cron", None, self.cron_line(), [["crontab", "-"]], replaces)

    # -- what is there now -------------------------------------------------

    def _crontab(self) -> list[str]:
        r = self.run(["crontab", "-l"], capture_output=True, text=True)
        if r.returncode != 0:
            return []  # "no crontab for <user>"
        return r.stdout.splitlines()

    def installed(self) -> Installed | None:
        if self.kind == "launchd":
            if not self.plist_path.is_file():
                return None
            try:
                data: dict[str, Any] = plistlib.loads(self.plist_path.read_bytes())
            except (OSError, plistlib.InvalidFileException, ValueError):
                return Installed("launchd", f"{self.plist_path} (unreadable)", None, None)
            args = data.get("ProgramArguments") or []
            env = data.get("EnvironmentVariables") or {}
            r = self.run(["launchctl", "print", f"gui/{self.uid}/{LABEL}"], capture_output=True, text=True)
            return Installed(
                "launchd", " ".join(args), env.get("OMBRO_HOME"), args[0] if args else None, loaded=r.returncode == 0
            )
        for line in self._crontab():
            if line.rstrip().endswith(CRON_MARK):
                parts = shlex.split(line.split(CRON_MARK)[0].replace("\\%", "%"))
                home = next((p.split("=", 1)[1] for p in parts if p.startswith("OMBRO_HOME=")), None)
                python = next((p for p in parts[5:] if not p.startswith("OMBRO_HOME=")), None)
                return Installed("cron", line.split(CRON_MARK)[0].strip(), home, python)
        return None

    # -- changes (only after the user confirms) ----------------------------

    def install(self) -> Plan:
        plan = self.plan()
        self.home.scheduler_dir.mkdir(parents=True, exist_ok=True)
        if self.kind == "launchd":
            if plan.replaces:
                self.run(plan.commands[0], capture_output=True, text=True)  # not loaded is fine
            self.agents_dir.mkdir(parents=True, exist_ok=True)
            self.plist_path.write_text(plan.text, encoding="utf-8")
            r = self.run(plan.commands[-1], capture_output=True, text=True)
            if r.returncode != 0:
                raise OSError(f"launchctl bootstrap failed: {(r.stderr or r.stdout).strip()}")
            return plan
        lines = [ln for ln in self._crontab() if not ln.rstrip().endswith(CRON_MARK)]
        self._write_crontab(lines + [plan.text])
        return plan

    def uninstall(self) -> bool:
        if self.kind == "launchd":
            if not self.plist_path.is_file():
                return False
            self.run(["launchctl", "bootout", f"gui/{self.uid}/{LABEL}"], capture_output=True, text=True)
            self.plist_path.unlink()
            return True
        lines = self._crontab()
        kept = [ln for ln in lines if not ln.rstrip().endswith(CRON_MARK)]
        if len(kept) == len(lines):
            return False
        self._write_crontab(kept)
        return True

    def uninstall_commands(self) -> list[list[str]]:
        if self.kind == "launchd":
            return [["launchctl", "bootout", f"gui/{self.uid}/{LABEL}"]]
        return [["crontab", "-"]]

    def _write_crontab(self, lines: list[str]) -> None:
        text = "\n".join(lines) + ("\n" if lines else "")
        r = self.run(["crontab", "-"], input=text, capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError(f"crontab failed: {(r.stderr or r.stdout).strip()}")


def check(home: Home, inst: Installer | None = None) -> dict[str, Any]:
    """Whether scheduled jobs in this home can run: `ok`, and when not, a line saying why for the
    Scheduled page, and whether Install now would put it right."""
    inst = inst or make_installer(home)
    try:
        cur = inst.installed()
        inst.plan()
    except OSError as e:
        return {"ok": False, "message": f"The scheduler service can't be installed here: {e}.", "can_install": False}
    if cur is None:
        return {
            "ok": False,
            "message": "The scheduler service is not installed, so scheduled jobs don't run.",
            "can_install": True,
        }
    if cur.home and Path(cur.home).resolve() != home.root:
        return {
            "ok": False,
            "message": f"The scheduler service runs scheduled jobs for another Ombro home, {cur.home}, not this one.",
            "can_install": True,
        }
    if broken(cur):
        return {"ok": False, "message": "The scheduler service is installed but isn't working.", "can_install": True}
    return {"ok": True, "message": "", "can_install": False}


def broken(cur: Installed) -> bool:
    """Installed, but it can't run: macOS hasn't loaded it, or what it runs has gone (the app moved)."""
    return cur.loaded is False or (cur.python is not None and not Path(cur.python).exists())


def may_install_itself(
    home: Home, executable: str = sys.executable, platform: str = sys.platform, appimage: str | None = ""
) -> bool:
    """The app installs the service without asking only for the usual home (not a demo or test home,
    which would take it from the real one) and only from where the app is installed, so it never points
    at a build folder, a copy macOS has moved aside to run, or an AppImage's mount (gone when it quits):
    an Applications folder on macOS; on Linux /usr/bin (the .deb or .rpm), or an .AppImage file, which
    the entry runs itself. If the file moves, the entry is broken, and the app's next start puts it
    right (serve runs `ensure`). `appimage`: "" looks at $APPIMAGE."""
    from ombro import appimage as appimage_mod
    from ombro.home import default_root

    if not getattr(sys, "frozen", False) or home.root != default_root() or not home.exists():
        return False
    exe = str(Path(executable).absolute())
    if platform.startswith("linux"):
        found = appimage_mod.path() if appimage == "" else appimage
        return exe.startswith("/usr/bin/") or bool(found)
    return "/AppTranslocation/" not in exe and (
        exe.startswith("/Applications/") or exe.startswith(str(Path.home() / "Applications") + "/")
    )


def ensure(home: Home, inst: Installer | None = None) -> bool:
    """Install the service if it is missing or broken for this home; True if it did. One that runs
    another home's jobs is left alone (that home's owner installed it); the page says so."""
    inst = inst or make_installer(home)
    try:
        cur = inst.installed()
        if cur is not None and not ((not cur.home or Path(cur.home).resolve() == home.root) and broken(cur)):
            return False
        inst.install()
        return True
    except OSError:
        return False


def schedule_turned_on(home: Home, ticking: bool = False) -> dict[str, Any]:
    """What follows a schedule being added or turned on, wherever it was done (the Scheduled page,
    the chat): the app installs the scheduler service itself where it may, then says, as `check` does,
    whether scheduled jobs can run. `ticking`: this process starts them itself (serve --tick)."""
    from ombro import service

    if ticking or service.serve_ticks(home):
        return {"ok": True, "message": "", "can_install": False}
    if may_install_itself(home):
        ensure(home)
    return check(home)


def make_installer(home: Home) -> Installer:
    """The installer for this machine. Tests replace this function."""
    return Installer(home)
