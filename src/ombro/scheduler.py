"""The single system scheduler entry that calls `ombro tick` once a minute.

macOS: a launch agent, ~/Library/LaunchAgents/local.ombro.tick.plist, loaded
with `launchctl bootstrap gui/<uid>`. It runs only while you are logged in;
slots while logged out, off or asleep are noted as missed.

Linux and other systems with cron: one line in your crontab, marked
`# ombro-tick`, so it can be found and removed again.

The entry calls the Python that ran `ombro schedule install`, as
`python -m ombro tick`, with OMBRO_HOME set to this home. Tick writes its own
log to scheduler/tick.log; anything the scheduler catches on stderr (a crash)
goes to scheduler/scheduler.log.

Nothing here runs unless the user confirms. Tests build an Installer with a
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
    ):
        self.home = home
        self.kind = "launchd" if platform == "darwin" else "cron"
        self.agents_dir = agents_dir or Path.home() / "Library" / "LaunchAgents"
        self.run = run
        self.python = str(Path(python).absolute())
        self.uid = os.getuid() if uid is None else uid

    # -- what would be written ---------------------------------------------

    @property
    def plist_path(self) -> Path:
        return self.agents_dir / f"{LABEL}.plist"

    @property
    def error_log(self) -> Path:
        return self.home.scheduler_dir / "scheduler.log"

    def program(self) -> list[str]:
        return [self.python, "-m", "ombro", "tick"]

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
        return (f"* * * * * {env} {cmd} "
                f">/dev/null 2>>{q(str(self.error_log))} {CRON_MARK}")

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
            return Installed("launchd", " ".join(args), env.get("OMBRO_HOME"), args[0] if args else None,
                             loaded=r.returncode == 0)
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


def make_installer(home: Home) -> Installer:
    """The installer for this machine. Tests replace this function."""
    return Installer(home)
