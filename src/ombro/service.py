"""`ombro serve install | remove | status`: run the server on its own, now and after a restart.

Linux: a systemd user unit, ~/.config/systemd/user/ombro.service, enabled with `systemctl --user`.
A user unit stops when you log out unless lingering is on, so install also runs
`loginctl enable-linger`; where that isn't allowed, it says what to ask an administrator.

macOS: a launch agent, ~/Library/LaunchAgents/local.ombro.serve.plist, kept alive by launchd. It
runs while you are logged in.

Either way the service runs `ombro serve --tick`, so it starts scheduled jobs itself and no cron
entry is needed. Its output (including the first pairing code) goes to the journal on Linux
(`journalctl --user -u ombro`) and to scheduler/serve.log on macOS.

Nothing is written unless the user confirms (or passes --yes). Tests build a ServiceInstaller with
temporary folders and a fake command runner.
"""

from __future__ import annotations

import os
import re
import plistlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ombro.home import Home

LABEL = "local.ombro.serve"
UNIT = "ombro.service"

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class ServicePlan:
    kind: str  # "systemd" or "launchd"
    path: Path
    text: str
    commands: list[list[str]] = field(default_factory=list)
    exists: bool = False


class ServiceInstaller:
    def __init__(
        self,
        home: Home,
        *,
        platform: str = sys.platform,
        run: Runner = subprocess.run,
        python: str = sys.executable,
        uid: int | None = None,
        units_dir: Path | None = None,
        agents_dir: Path | None = None,
        user: str | None = None,
        has_systemd: bool | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        import time

        self.home = home
        self.sleep = sleep or time.sleep
        self.kind = "launchd" if platform == "darwin" else "systemd"
        self.run = run
        self.python = str(Path(python).absolute())
        self.uid = os.getuid() if uid is None else uid
        self.units_dir = units_dir or Path.home() / ".config" / "systemd" / "user"
        self.agents_dir = agents_dir or Path.home() / "Library" / "LaunchAgents"
        self.user = user or os.environ.get("USER") or ""
        self.has_systemd = (shutil.which("systemctl") is not None) if has_systemd is None else has_systemd

    @property
    def path(self) -> Path:
        return self.agents_dir / f"{LABEL}.plist" if self.kind == "launchd" else self.units_dir / UNIT

    @property
    def log(self) -> Path:
        return self.home.scheduler_dir / "serve.log"

    def program(self, port: int, host: str, allow: list[str]) -> list[str]:
        argv = [self.python, "-m", "ombro", "serve", "--tick", "--port", str(port), "--host", host]
        for name in allow:
            argv += ["--allow-host", name]
        return argv

    def env(self) -> dict[str, str]:
        return {**self.home.process_env(), "PATH": f"{Path(self.python).parent}:/usr/local/bin:/usr/bin:/bin"}

    def unit(self, argv: list[str]) -> str:
        """The systemd unit. Values are quoted the way systemd reads them."""

        def q(text: str) -> str:
            if any(c in text for c in "\r\n"):
                raise OSError("a path has a line break in it, so it cannot go in a systemd unit")
            return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'

        env = "\n".join(f"Environment={q(f'{k}={v}')}" for k, v in self.env().items())
        return (
            "[Unit]\nDescription=Ombro server\nAfter=network-online.target\nWants=network-online.target\n\n"
            f"[Service]\nExecStart={' '.join(q(a) for a in argv)}\n{env}\n"
            "Restart=on-failure\nRestartSec=5\n# Jobs it starts run in their own session; stopping the\n"
            "# server must not stop them.\nKillMode=process\n\n[Install]\nWantedBy=default.target\n"
        )

    def plist(self, argv: list[str]) -> str:
        return plistlib.dumps(
            {
                "Label": LABEL,
                "ProgramArguments": argv,
                "EnvironmentVariables": self.env(),
                "RunAtLoad": True,
                "KeepAlive": True,
                "AbandonProcessGroup": True,
                "StandardOutPath": str(self.log),
                "StandardErrorPath": str(self.log),
            }
        ).decode("utf-8")

    def plan(self, port: int, host: str, allow: list[str]) -> ServicePlan:
        argv = self.program(port, host, allow)
        exists = self.path.is_file()
        if self.kind == "launchd":
            cmds = [["launchctl", "bootout", f"gui/{self.uid}/{LABEL}"]] if exists else []
            cmds.append(["launchctl", "bootstrap", f"gui/{self.uid}", str(self.path)])
            return ServicePlan("launchd", self.path, self.plist(argv), cmds, exists)
        if not self.has_systemd:
            raise OSError(
                "this machine has no systemd. Run `ombro serve --tick` under whatever keeps "
                "services running here, or use the Docker image"
            )
        cmds = [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", UNIT],
            ["systemctl", "--user", "restart", UNIT],
        ]
        if self.user:
            cmds.append(["loginctl", "enable-linger", self.user])
        return ServicePlan("systemd", self.path, self.unit(argv), cmds, exists)

    def install(self, plan: ServicePlan) -> list[str]:
        """Write and start it. Returns warnings (lingering that couldn't be turned on)."""
        warnings: list[str] = []
        self.home.scheduler_dir.mkdir(parents=True, exist_ok=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if plan.kind == "launchd" and plan.exists:
            self.run(plan.commands[0], capture_output=True, text=True)  # not loaded is fine
            # launchd unloads the old one in the background; loading the new one before it has
            # gone fails. Wait for it, up to five seconds.
            for _ in range(20):
                if (
                    self.run(
                        ["launchctl", "print", f"gui/{self.uid}/{LABEL}"], capture_output=True, text=True
                    ).returncode
                    != 0
                ):
                    break
                self.sleep(0.25)
        self.path.write_text(plan.text, encoding="utf-8")
        for cmd in plan.commands[1 if plan.kind == "launchd" and plan.exists else 0 :]:
            r = self.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                continue
            why = (r.stderr or r.stdout or "").strip()
            if cmd[0] == "loginctl":
                warnings.append(
                    f"Lingering couldn't be turned on ({why or 'refused'}), so the server stops when "
                    f"you log out. An administrator can run: sudo loginctl enable-linger {self.user}"
                )
                continue
            raise OSError(f"{' '.join(cmd)} failed: {why}")
        return warnings

    def remove(self) -> bool:
        if not self.path.is_file():
            return False
        if self.kind == "launchd":
            self.run(["launchctl", "bootout", f"gui/{self.uid}/{LABEL}"], capture_output=True, text=True)
        else:
            self.run(["systemctl", "--user", "disable", "--now", UNIT], capture_output=True, text=True)
        self.path.unlink()
        if self.kind == "systemd":
            self.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
        return True

    def running(self) -> bool | None:
        if not self.path.is_file():
            return None
        if self.kind == "launchd":
            cmd = ["launchctl", "print", f"gui/{self.uid}/{LABEL}"]
        else:
            cmd = ["systemctl", "--user", "is-active", "--quiet", UNIT]
        return self.run(cmd, capture_output=True, text=True).returncode == 0

    def log_hint(self) -> str:
        return f"journalctl --user -u {UNIT.removesuffix('.service')}" if self.kind == "systemd" else str(self.log)


def sandbox_step(
    *, ask: Callable[[str], bool] | None, run: Runner = subprocess.run, say: Callable[[str], None] = print
) -> bool | None:
    """On Linux, after installing: can code a job writes run here? Some Ubuntu versions (24.04)
    need an AppArmor profile for bubblewrap, once; offer to load it (sudo asks for a password). With no
    `ask` (--yes), only say how. Returns whether the sandbox works, or None off Linux."""
    from ombro import sandbox

    if sandbox.kind() != "linux":
        return None
    reason = sandbox.unavailable_reason()
    if reason is None:
        return True
    if not sandbox.userns_restricted():
        say(f"\nCode a job writes can't run here yet: {reason}. Everything else works.")
        return False
    say(
        "\nThis machine (Ubuntu 24.04, say) limits the Linux namespaces Ombro's sandbox for "
        "job code (bubblewrap) needs. A small AppArmor profile allows them for bubblewrap only:\n"
    )
    say(f"  {sandbox.APPARMOR_FIX}\n")
    say("Without it, everything works except jobs that run code they write.")
    if ask is None or not ask("Allow it now? sudo will ask for your password."):
        say("Not done. Run the command above whenever you like; `ombro sandbox` checks it.")
        return False
    wrote = run(
        ["sudo", "tee", sandbox.APPARMOR_PATH], input=sandbox.APPARMOR_PROFILE, text=True, stdout=subprocess.DEVNULL
    )
    loaded = run(["sudo", "apparmor_parser", "-r", sandbox.APPARMOR_PATH]) if wrote.returncode == 0 else wrote
    if loaded.returncode != 0:
        say("That didn't work, so nothing changed. Run the command above as an administrator.")
        return False
    sandbox._probe.clear()
    reason = sandbox.unavailable_reason()
    say(
        "Done: code a job writes can run here, in the sandbox."
        if reason is None
        else f"The profile is loaded, but code still can't run: {reason}"
    )
    return reason is None


def serve_ticks(home: Home) -> str | None:
    """Whether an Ombro server starts this home's scheduled jobs itself (`ombro serve --tick`), so
    no system scheduler entry is needed: the service `ombro serve install` wrote for this home, or
    Ombro's Docker image, whose server always runs with --tick. Returns which, or None."""
    if Path("/.dockerenv").exists():
        return "the Ombro server in this container"
    try:
        inst = make_installer(home)
        path = inst.path
        if not path.is_file():
            return None
        if inst.kind == "launchd":
            data = plistlib.loads(path.read_bytes())
            argv, served = (
                data.get("ProgramArguments") or [],
                (data.get("EnvironmentVariables") or {}).get("OMBRO_HOME"),
            )
        else:
            text = path.read_text(errors="replace")
            argv = text.split()
            found = re.search(r'^Environment="OMBRO_HOME=(.*)"$', text, re.M)
            served = found.group(1).replace('\\"', '"').replace("\\\\", "\\") if found else None
        if served is None or not any("--tick" in a for a in argv):
            return None
        if Path(served).expanduser().resolve() != Path(home.root).resolve():
            return None
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return f"the Ombro server's service ({path})"


def make_installer(home: Home) -> ServiceInstaller:
    """The installer for this machine. Tests replace this function."""
    return ServiceInstaller(home)


def cmd(args) -> int:
    from ombro import cli
    from ombro.pairing import Devices
    from ombro.serve import HOST, SERVICE_PORT, is_private, service_lines

    home = Home()
    inst = make_installer(home)
    if args.action == "status":
        state = inst.running()
        if state is None:
            print("The Ombro server isn't installed as a service. Install it with `ombro serve install`.")
            return 1
        print(f"The Ombro server is installed ({inst.path}) and {'running' if state else 'not running'}.")
        print(f"Its output: {inst.log_hint()}")
        devices = Devices(home).list()
        print(f"Paired devices: {', '.join(d.name for d in devices) if devices else 'none yet'}.")
        return 0 if state else 1
    if args.action == "remove":
        if not inst.path.is_file():
            print("The Ombro server isn't installed as a service.")
            return 0
        if not args.yes and not cli._confirm(f"Stop the Ombro server and remove {inst.path}?"):
            print("Nothing removed.")
            return 1
        inst.remove()
        print("Removed. Paired devices are kept; `ombro pair --forget-all` forgets them.")
        return 0

    port = SERVICE_PORT if args.port is None else args.port
    host = args.host or HOST
    try:
        plan = inst.plan(port, host, args.allow_host)
    except OSError as e:
        raise cli.CliError(f"The server can't be installed as a service: {e}.") from None
    where = "systemd (a user service)" if plan.kind == "systemd" else "launchd (a launch agent)"
    print(f"This sets up {where}, so the Ombro server keeps running and starts again after a restart:\n")
    print(f"  runs      {shlex.join(inst.program(port, host, args.allow_host))}")
    print(f"  listens   {host}:{port}")
    print(f"  home      {home.root}")
    print(f"  file      {plan.path}{' (replaced)' if plan.exists else ''}")
    print(f"  output    {inst.log_hint()}")
    print("\nIt also starts scheduled jobs, so no cron entry is needed.")
    if plan.kind == "systemd":
        print("Lingering is turned on, so it keeps running when you log out.")
    else:
        print("macOS runs it while you are logged in.")
    if not is_private(host) and host not in ("0.0.0.0", "::"):
        print(f"\nWarning: {host} may be reachable from the internet. Use SSH or Tailscale instead.")
    print("\nCommands: " + "; ".join(" ".join(c) for c in plan.commands))
    if not args.yes and not cli._confirm("Install it?"):
        print("Nothing installed.")
        return 1
    try:
        warnings = inst.install(plan)
    except OSError as e:
        raise cli.CliError(str(e)) from None
    print("\nInstalled and started. `ombro serve status` checks it; `ombro serve remove` takes it away.")
    for w in warnings:
        print(f"\n{w}")
    sandbox_step(ask=None if args.yes else cli._confirm)
    code = Devices(home).first_code()
    if code:
        for line in service_lines(host, port, code)[1:]:
            print(line)
    else:
        print("\nA device is already paired. For another, run `ombro pair` for a new code.")
    return 0
