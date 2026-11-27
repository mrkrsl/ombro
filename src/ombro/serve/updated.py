"""Noticing that Ombro was updated under a running service.

An update replaces Ombro's files but not a server that is already running: the app's own updater
moves the old app away and deletes it, and `brew upgrade`, a .deb or .rpm, or pip do much the same.
The old server carries on from the deleted copy. On macOS it then stops answering on its Tailscale
address, so the app says it can't reach its server.

So the service watches the file it runs from. Once that file is a different one, and has stayed
the same for one more look (an install that is still writing it), the service stops with
EXIT_UPDATED, and launchd or systemd starts the new version. Jobs it started run in their own
session (KillMode=process, AbandonProcessGroup), so they carry on.

A server nobody supervises (run by hand) would not come back, so it only says that it was updated.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Callable

# EX_TEMPFAIL: not a clean exit, so systemd's Restart=on-failure starts it again too.
EXIT_UPDATED = 75
EVERY_SECONDS = 15.0

Stamp = tuple[int, int, int, int]


def program_file() -> Path:
    """The file this Ombro runs from: the AppImage, the frozen binary, or the installed package."""
    from ombro import appimage

    found = appimage.path()
    if found:
        return Path(found)
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    import ombro

    return Path(ombro.__file__)


def stamp(path: Path) -> Stamp | None:
    """Which file is at `path` now, or None while there is none (mid-swap)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def supervised(env: dict[str, str] | None = None) -> bool:
    """Whether launchd or systemd runs this process, and so starts it again when it stops."""
    env = os.environ if env is None else env
    return bool(env.get("INVOCATION_ID")) or env.get("XPC_SERVICE_NAME", "0") not in ("", "0")


class UpdateWatch:
    """Looks at the program file; `check()` is True once it was replaced and has settled."""

    def __init__(self, path: Path | None = None):
        self.path = path or program_file()
        self.first = stamp(self.path)
        self.seen: Stamp | None = None  # the new file, at the last look

    def check(self) -> bool:
        if self.first is None:
            return False
        now = stamp(self.path)
        if now is None or now == self.first:
            self.seen = None
            return False
        settled = now == self.seen
        self.seen = now
        return settled


def watch(
    on_updated: Callable[[], None], every: float = EVERY_SECONDS, stop: threading.Event | None = None
) -> threading.Thread:
    """Calls `on_updated` once, from a background thread, when Ombro has been updated."""
    w = UpdateWatch()
    stop = stop or threading.Event()

    def loop() -> None:
        while not stop.wait(every):
            if w.check():
                on_updated()
                return

    t = threading.Thread(target=loop, name="ombro-update-watch", daemon=True)
    t.start()
    return t
