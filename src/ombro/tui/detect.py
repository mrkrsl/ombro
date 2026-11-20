"""Light or dark, for `theme: auto`: ask the terminal for its
background colour, then fall back to the macOS appearance, then to light.
Run before the interface takes the screen."""

from __future__ import annotations

import os
import re
import select
import subprocess
import sys
import time

_REPLY = re.compile(rb"\x1b\]11;rgba?:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)")


def luminance_is_dark(r: float, g: float, b: float) -> bool:
    """r, g and b from 0 to 1. Dark when the perceived brightness is below half."""
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 0.5


def parse_background(reply: bytes) -> bool | None:
    """True for a dark background, False for light, None if the reply cannot be read."""
    m = _REPLY.search(reply)
    if not m:
        return None
    parts = []
    for h in m.groups():
        parts.append(int(h, 16) / (16 ** len(h) - 1))
    return luminance_is_dark(*parts)


def terminal_background(timeout: float = 0.2) -> bool | None:
    """Ask the terminal (OSC 11). None when there is no terminal or no answer."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        import termios
        import tty
    except ImportError:
        return None
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return None
    reply = b""
    try:
        tty.setcbreak(fd)
        os.write(sys.stdout.fileno(), b"\x1b]11;?\x1b\\")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                break
            reply += os.read(fd, 256)
            if reply.endswith((b"\x07", b"\x1b\\")):
                break
    except OSError:
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return parse_background(reply)


def macos_dark() -> bool | None:
    if sys.platform != "darwin":
        return None
    try:
        r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0 and r.stdout.strip().lower() == "dark"


def is_dark(theme: str) -> bool:
    """The theme to use for `theme:` in preferences: light, dark, or auto."""
    if theme in ("light", "dark"):
        return theme == "dark"
    for probe in (terminal_background, macos_dark):
        found = probe()
        if found is not None:
            return found
    return False
