"""Freeze `ombro serve` with PyInstaller as the app's sidecar.

Run from gui/ with `npm run sidecar` (which is `uv run --group build python
scripts/build_sidecar.py`). It writes src-tauri/binaries/ombro-serve-<target>,
the name Tauri looks for, where <target> is the Rust host triple, e.g.
aarch64-apple-darwin or x86_64-unknown-linux-gnu.

The binary is the whole `ombro` command line without the terminal interface
(ombro.tui and Textual stay out). The app runs it as `ombro-serve serve
--handshake`, and serve runs it again for each job it starts.

PyInstaller can't cross-compile: build on each platform for its own binary.

On macOS, with APPLE_SIGNING_IDENTITY set (scripts/release_macos.sh sets it),
PyInstaller signs every library it bundles with that Developer ID and the
hardened runtime, so they still load once the app is notarised.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

GUI = Path(__file__).resolve().parent.parent
REPO = GUI.parent
BUILD = GUI / "build" / "sidecar"
OUT = GUI / "src-tauri" / "binaries"

# The whole command line, not serve alone: a job serve starts runs as `<this binary> _exec <job>`
# (jobs.ombro_argv), so the frozen binary must be able to run it.
ENTRY = '''from ombro.cli import main

raise SystemExit(main())
'''


def target() -> str:
    return subprocess.run(["rustc", "--print", "host-tuple"], capture_output=True, text=True, check=True).stdout.strip()


def core_modules() -> list[str]:
    import pkgutil

    import ombro

    names = ["ombro", *(m.name for m in pkgutil.walk_packages(ombro.__path__, "ombro."))]
    return [n for n in names if n != "ombro.tui" and not n.startswith("ombro.tui.")]


def main() -> int:
    triple = target()
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    entry = BUILD / "ombro_serve.py"
    entry.write_text(ENTRY)
    cmd = [
        sys.executable, "-m", "PyInstaller", str(entry),
        "--onefile", "--name", "ombro-serve", "--noconfirm", "--clean", "--log-level", "WARN",
        "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
        "--paths", str(REPO / "src"),
        *[a for m in core_modules() for a in ("--hidden-import", m)],
        # uvicorn picks its loop, HTTP and WebSocket implementations by name at run time.
        "--collect-submodules", "uvicorn",
        "--exclude-module", "ombro.tui", "--exclude-module", "textual",
    ]
    identity = os.environ.get("APPLE_SIGNING_IDENTITY")
    if sys.platform == "darwin" and identity:
        entitlements = GUI / "src-tauri" / "entitlements.plist"
        cmd += ["--codesign-identity", identity, "--osx-entitlements-file", str(entitlements)]
    started = time.monotonic()
    subprocess.run(cmd, check=True, cwd=REPO)
    built = BUILD / "dist" / ("ombro-serve.exe" if sys.platform == "win32" else "ombro-serve")
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"ombro-serve-{triple}{built.suffix}"
    shutil.copy2(built, dest)
    size = dest.stat().st_size / 1e6
    print(f"{dest.relative_to(GUI)}: {size:.1f} MB, built in {time.monotonic() - started:.0f}s")
    bundle_python()
    return 0


# -- the sandbox's Python ---------------------------------------------------------------
#
# code.run starts a real Python inside the sandbox (ombro.sandbox), with -I -S: the standard
# library only. A frozen app has no Python of its own, and a Mac may have none at all, so the app
# carries one: the standalone build this one runs on (uv's python-build-standalone), trimmed.

PYTHON_OUT = GUI / "src-tauri" / "python"
# Not needed to run a script with the standard library: the Tk GUI kit, tests, headers, docs.
PRUNE = ["include", "share", "BUILD", "lib/pkgconfig", "lib/itcl*", "lib/tcl*", "lib/tk*", "lib/thread*",
         "lib/libtcl*", "lib/libtk*", "lib/python3.*/test", "lib/python3.*/idlelib", "lib/python3.*/tkinter",
         "lib/python3.*/turtledemo", "lib/python3.*/ensurepip", "lib/python3.*/site-packages",
         "lib/python3.*/config-*", "lib/python3.*/lib-dynload/_tkinter*", "lib/python3.*/lib-dynload/*test*",
         "bin/idle*", "bin/pip*", "bin/pydoc*", "bin/*-config"]


def bundle_python() -> None:
    src = Path(sys.base_prefix).resolve()
    if not (src / "bin").is_dir():
        raise SystemExit(f"{src} doesn't look like a standalone Python (no bin/); build with a uv-managed Python")
    shutil.rmtree(PYTHON_OUT, ignore_errors=True)
    shutil.copytree(src, PYTHON_OUT, symlinks=True)
    for pattern in PRUNE:
        for p in PYTHON_OUT.glob(pattern):
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    for p in list(PYTHON_OUT.rglob("__pycache__")):
        shutil.rmtree(p, ignore_errors=True)
    identity = os.environ.get("APPLE_SIGNING_IDENTITY")
    if sys.platform == "darwin" and identity:
        # Every binary in it is signed with the Developer ID and the hardened runtime, or
        # notarisation refuses the app. Libraries first, then the programs that load them.
        machos = [p for p in PYTHON_OUT.rglob("*") if p.is_file() and not p.is_symlink() and _is_macho(p)]
        machos.sort(key=lambda p: p.parent.name == "bin")
        for p in machos:
            subprocess.run(["codesign", "--force", "--timestamp", "--options", "runtime", "--sign", identity, str(p)],
                           check=True, capture_output=True)
    size = sum(p.stat().st_size for p in PYTHON_OUT.rglob("*") if p.is_file() and not p.is_symlink()) / 1e6
    print(f"{PYTHON_OUT.relative_to(GUI)}: Python {sys.version.split()[0]}, {size:.1f} MB")


def _is_macho(p: Path) -> bool:
    with p.open("rb") as f:
        magic = f.read(4)
    return magic in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf")


if __name__ == "__main__":
    raise SystemExit(main())
