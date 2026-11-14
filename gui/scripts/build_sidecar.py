"""Freeze `ombro serve` with PyInstaller as the app's sidecar.

Run from gui/ with `npm run sidecar` (which is `uv run --group build python
scripts/build_sidecar.py`). It writes src-tauri/binaries/ombro-serve-<target>,
the name Tauri looks for, where <target> is the Rust host triple, e.g.
aarch64-apple-darwin or x86_64-unknown-linux-gnu.

The whole core goes in, not just what serve imports today, so the size is
close to what later stages will ship and anything that won't freeze shows up
now. The terminal interface (ombro.tui and Textual) stays out.

PyInstaller can't cross-compile: build on each platform for its own binary.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

GUI = Path(__file__).resolve().parent.parent
REPO = GUI.parent
BUILD = GUI / "build" / "sidecar"
OUT = GUI / "src-tauri" / "binaries"

ENTRY = '''from ombro.serve import main

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
    started = time.monotonic()
    subprocess.run(cmd, check=True, cwd=REPO)
    built = BUILD / "dist" / ("ombro-serve.exe" if sys.platform == "win32" else "ombro-serve")
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"ombro-serve-{triple}{built.suffix}"
    shutil.copy2(built, dest)
    size = dest.stat().st_size / 1e6
    print(f"{dest.relative_to(GUI)}: {size:.1f} MB, built in {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
