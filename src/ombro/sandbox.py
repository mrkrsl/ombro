"""Running one Python script inside the operating system's own sandbox.

Ombro never tries to judge code by reading it. It limits what any code can
reach: the job's workspace/ and scratch/, the shared folders the job was
given, and what Python needs to start. No network, no other programs, no
secrets. If the sandbox cannot be set up, nothing runs.

- macOS: the system sandbox (`sandbox-exec`), with a profile that denies
  everything not named.
- Linux: bubblewrap (`bwrap`), with an empty root, no network, and only the
  folders named bound into it. Other programs are simply not there.

Limits for each script: wall-clock time, CPU time, memory, the size of any
one file written, and how much output is kept. On macOS memory is watched
and the script stopped when it passes the cap; on Linux the kernel enforces
an address-space limit.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

OUTPUT_KEEP_BYTES = 1_000_000  # what the harness keeps of a script's output
FILE_MAX_BYTES = 5_000_000  # any one file a script writes
POLL_SECONDS = 0.05
MEMORY_POLL_SECONDS = 0.25
OPEN_FILES = 256


# Ubuntu 24.04 and later let a program make Linux user namespaces, but take away its rights inside
# them, unless an AppArmor profile allows it. bubblewrap then fails (for example "loopback: Failed
# RTM_NEWADDR"). This profile, loaded once on the machine (the host, when Ombro is in Docker), lets
# /usr/bin/bwrap use them properly; nothing else changes.
APPARMOR_PROFILE = """# Lets bubblewrap, the sandbox Ombro runs job code in, use Linux user namespaces (Ubuntu 24.04+).
abi <abi/4.0>,
include <tunables/global>

profile ombro-bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/ombro-bwrap>
}
"""
APPARMOR_PATH = "/etc/apparmor.d/ombro-bwrap"
APPARMOR_FIX = (f"ombro sandbox --apparmor | sudo tee {APPARMOR_PATH} >/dev/null && "
                f"sudo apparmor_parser -r {APPARMOR_PATH}")
APPARMOR_FIX_DOCKER = (f"docker compose exec -T ombro ombro sandbox --apparmor | sudo tee {APPARMOR_PATH} >/dev/null "
                       f"&& sudo apparmor_parser -r {APPARMOR_PATH}, then docker compose restart")


def userns_restricted() -> bool:
    """Whether this Linux machine restricts user namespaces through AppArmor (Ubuntu 24.04+)."""
    try:
        return Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns").read_text().strip() == "1"
    except OSError:
        return False


class SandboxUnavailable(Exception):
    """The sandbox cannot be set up here, so no code may run. The message says why."""


@dataclass
class Box:
    """What one script may reach. Every path is a real (resolved) path."""

    job_dir: Path  # the working folder; its own files stay unreadable
    script: Path  # the script, readable but not writable
    write: list[Path] = field(default_factory=list)  # read and write: workspace/, scratch/, writable shared folders
    read: list[Path] = field(default_factory=list)  # read only: read-only shared folders
    mounts: dict[Path, Path] = field(default_factory=dict)  # Linux: where a shared folder appears -> its real path
    temp: Path | None = None  # TMPDIR and HOME for the script


@dataclass
class Outcome:
    exit_code: int | None
    seconds: float
    output: bytes  # the first OUTPUT_KEEP_BYTES of stdout and stderr together
    output_total: int  # bytes the script printed in all
    limit: str | None = None  # code_timeout, code_max_memory, cpu, stopped


# ---------------------------------------------------------------------------
# The interpreter


def interpreter() -> tuple[Path, Path]:
    """The real Python Ombro runs on, and its installation folder. Started with -I -S,
    so it sees the standard library only: no site-packages, no PYTHON* settings.

    The desktop app is frozen and has no Python of its own, so it carries one for this
    (gui/scripts/build_sidecar.py): on macOS in Ombro.app/Contents/Resources/python."""
    bundled = bundled_python()
    if bundled is not None:
        return Path(os.path.realpath(bundled)), bundled.parent.parent.resolve()
    exe = Path(os.path.realpath(sys.executable))
    prefix = Path(os.path.realpath(sys.base_prefix))
    return exe, prefix


def bundled_python() -> Path | None:
    """The app's own Python, when Ombro runs frozen inside the app; else None."""
    if not getattr(sys, "frozen", False):
        return None
    here = Path(os.path.realpath(sys.executable)).parent  # Contents/MacOS, or the AppImage's bin
    for root in (here.parent / "Resources" / "python", here.parent / "lib" / "ombro" / "python", here / "python"):
        exe = root / "bin" / "python3"
        if exe.exists():
            return exe
    return None


PY_FLAGS = ["-I", "-S", "-B", "-X", "utf8"]


def environment(box: Box) -> dict[str, str]:
    """An empty environment apart from a temporary folder and the locale. No keys."""
    tmp = str(box.temp or box.job_dir / "scratch")
    return {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "TMPDIR": tmp, "HOME": tmp, "PATH": ""}


# ---------------------------------------------------------------------------
# macOS


def _sb(path: Path | str) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(box: Box, exe: Path, prefix: Path) -> str:
    system_reads = ["/System", "/usr/lib", "/usr/share", "/private/var/db/timezone", "/Library/Apple/usr/lib"]
    lines = [
        "(version 1)",
        "(deny default)",
        # Only the interpreter itself may be started; nothing it starts can run.
        f"(allow process-exec (literal {_sb(exe)}))",
        # Names and sizes of files can be looked up; their contents cannot be read.
        "(allow file-read-metadata)",
        '(allow file-read-data (literal "/"))',
        f"(allow file-read-data (literal {_sb(box.job_dir)}))",
        "(allow file-read* " + " ".join(f"(subpath {_sb(p)})" for p in [prefix, *system_reads]) + ")",
        '(allow file-read* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))',
        '(allow file-write-data (literal "/dev/null"))',
        f"(allow file-read* (literal {_sb(box.script)}))",
        "(allow sysctl-read)",
    ]
    if box.read:
        lines.append("(allow file-read* " + " ".join(f"(subpath {_sb(p)})" for p in box.read) + ")")
    if box.write:
        lines.append("(allow file-read* file-write* " + " ".join(f"(subpath {_sb(p)})" for p in box.write) + ")")
        # A subpath includes the folder itself. A script may change what is inside
        # each folder, never the folder: not rename, remove or replace it (for
        # example with a symlink), nor change its own mode. Later rules win.
        lines.append("(deny file-write* " + " ".join(f"(literal {_sb(p)})" for p in box.write) + ")")
    return "\n".join(lines) + "\n"


def macos_command(box: Box, profile_path: Path) -> list[str]:
    exe, _ = interpreter()
    return ["/usr/bin/sandbox-exec", "-f", str(profile_path), str(exe), *PY_FLAGS, str(box.script)]


# ---------------------------------------------------------------------------
# Linux


def _linux_system_binds(exe: Path, prefix: Path) -> list[tuple[str, str]]:
    """What Python needs to start, read-only. A system Python (/usr) is bound by
    its library folders and the one binary, so no other program is present."""
    binds: list[tuple[str, str]] = []
    for p in ("/usr/lib", "/usr/lib64", "/lib", "/lib64", "/usr/share/zoneinfo", "/etc/ld.so.cache",
              "/etc/localtime"):
        binds.append(("--ro-bind-try", p))
    if str(prefix) in ("/usr", "/usr/local", "/"):
        for sub in ("lib", "lib64"):
            binds.append(("--ro-bind-try", str(prefix / sub)))
        binds.append(("--ro-bind", str(exe)))
    else:
        binds.append(("--ro-bind", str(prefix)))
    return binds


def linux_command(box: Box) -> list[str]:
    exe, prefix = interpreter()
    bwrap = shutil.which("bwrap") or "bwrap"
    cmd = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
           "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for flag, p in _linux_system_binds(exe, prefix):
        cmd += [flag, p, p]
    cmd += ["--dir", str(box.job_dir)]
    for p in box.write:
        cmd += ["--bind", str(p), str(p)]
    for p in box.read:
        cmd += ["--ro-bind", str(p), str(p)]
    for where, real in box.mounts.items():
        cmd += ["--bind" if real in box.write else "--ro-bind", str(real), str(where)]
    cmd += ["--ro-bind", str(box.script), str(box.script), "--chdir", str(box.job_dir),
            str(exe), *PY_FLAGS, str(box.script)]
    return cmd


# ---------------------------------------------------------------------------
# Availability


def kind() -> str | None:
    system = platform.system()
    return {"Darwin": "macos", "Linux": "linux"}.get(system)


_probe: dict[str, str | None] = {}


def unavailable_reason() -> str | None:
    """None if scripts can run here; otherwise why not. Checked once per process by
    running Python itself inside the sandbox."""
    k = kind()
    if k is None:
        return f"code.run needs macOS or Linux; this is {platform.system() or 'an unknown system'}"
    if k in _probe:
        return _probe[k]
    reason: str | None = None
    if k == "macos" and not Path("/usr/bin/sandbox-exec").exists():
        reason = "the macOS sandbox (/usr/bin/sandbox-exec) is missing"
    elif k == "linux" and not shutil.which("bwrap"):
        reason = "bubblewrap is not installed; install it (for example `sudo apt install bubblewrap`)"
    if reason is None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="ombro-probe-") as d:
            root = Path(d).resolve()
            (root / "scratch").mkdir()
            script = root / "probe.py"
            script.write_text("print('ok')\n")
            box = Box(job_dir=root, script=script, write=[root / "scratch"])
            try:
                out = run(box, timeout=20, cpu_seconds=20, max_memory=512 * 1024 * 1024)
            except (OSError, SandboxUnavailable) as e:
                reason = f"the sandbox could not be started: {e}"
            else:
                if out.exit_code != 0 or b"ok" not in out.output:
                    detail = out.output.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
                    reason = f"the sandbox could not start Python: {detail[0][:200]}"
                    if k == "linux" and userns_restricted():
                        if Path("/.dockerenv").exists():
                            reason = ("the machine running Docker (Ubuntu 24.04 or later) limits the Linux "
                                      "namespaces the sandbox needs. Allow them for bubblewrap once, on that "
                                      "machine, from the folder with compose.yaml: " + APPARMOR_FIX_DOCKER)
                        else:
                            reason = ("this machine (Ubuntu 24.04 or later) limits the Linux namespaces the "
                                      "sandbox needs. Allow them for bubblewrap once, as an administrator: "
                                      + APPARMOR_FIX)
    _probe[k] = reason
    return reason


# ---------------------------------------------------------------------------
# Running


def _rss_bytes(pid: int) -> int | None:
    try:
        out = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return int(out.stdout.strip()) * 1024
    except ValueError:
        return None


def _limits(cpu_seconds: int, max_memory: int, linux: bool) -> Callable[[], None]:
    def apply() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_MAX_BYTES, FILE_MAX_BYTES))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (OPEN_FILES, OPEN_FILES))
        except (ValueError, OSError):
            pass
        if linux:
            resource.setrlimit(resource.RLIMIT_AS, (max_memory, max_memory))

    return apply


def run(
    box: Box,
    *,
    timeout: float,
    cpu_seconds: int,
    max_memory: int,
    cancelled: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    """Run box.script in the sandbox and wait for it, within the limits. Never raises
    for anything the script does; raises SandboxUnavailable if there is no sandbox."""
    k = kind()
    profile: Path | None = None
    if k == "macos":
        exe, prefix = interpreter()
        import tempfile

        fd, name = tempfile.mkstemp(prefix="ombro-sandbox-", suffix=".sb")
        with os.fdopen(fd, "w") as f:
            f.write(macos_profile(box, exe, prefix))
        profile = Path(name)
        cmd = macos_command(box, profile)
    elif k == "linux":
        cmd = linux_command(box)
    else:
        raise SandboxUnavailable(f"no sandbox on {platform.system()}")
    if box.temp is not None:
        box.temp.mkdir(parents=True, exist_ok=True)
    start = clock()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(box.job_dir),
            env=environment(box),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            preexec_fn=_limits(cpu_seconds, max_memory, linux=(k == "linux")),
        )
    except OSError:
        if profile:
            profile.unlink(missing_ok=True)
        raise
    kept = bytearray()
    total = [0]

    def drain() -> None:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            total[0] += len(chunk)
            room = OUTPUT_KEEP_BYTES - len(kept)
            if room > 0:
                kept.extend(chunk[:room])

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    limit: str | None = None
    next_mem = start
    try:
        while proc.poll() is None:
            now = clock()
            if now - start >= timeout:
                limit = "code_timeout"
            elif cancelled is not None and cancelled():
                limit = "stopped"
            elif k == "macos" and now >= next_mem:
                next_mem = now + MEMORY_POLL_SECONDS
                rss = _rss_bytes(proc.pid)
                if rss is not None and rss > max_memory:
                    limit = "code_max_memory"
            if limit:
                _kill(proc)
                break
            time.sleep(POLL_SECONDS)
        proc.wait()
    finally:
        if proc.poll() is None:
            _kill(proc)
            proc.wait()
        reader.join(timeout=5)
        if profile:
            profile.unlink(missing_ok=True)
    seconds = clock() - start
    code = proc.returncode
    if limit is None and code is not None and code < 0:
        sig = -code
        if sig == signal.SIGXCPU:
            limit = "cpu"
        elif k == "linux" and sig in (signal.SIGKILL, signal.SIGSEGV):
            limit = None  # may be memory; the output says
    if limit is None and k == "linux" and b"MemoryError" in bytes(kept[-4000:]):
        limit = "code_max_memory"
    return Outcome(exit_code=None if limit else code, seconds=seconds, output=bytes(kept),
                   output_total=total[0], limit=limit)


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
