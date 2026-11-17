"""Pairing: which devices may use an Ombro server, and the short codes that let a new one in.

When `ombro serve` runs as a service, every request needs a device token. A device gets one by
pairing, in one of two ways:

- **Over SSH**, the app runs `ombro pair --device <name>` on the server, which prints a new
  device's token as JSON. Getting into the machine with your SSH key is the proof.
- **With a code**, shown on the server, typed into the app and sent to `POST /pair`. A code
  works once. The first code, made while no device is paired, has no expiry and is printed each
  time serve starts, so there's no rush between installing the server and setting up the app.
  Later codes come from `ombro pair` and last a day.

Everything is kept in secrets/devices.json (owner-only). Device tokens are kept only as hashes,
so the file can't be used to connect. The code is kept as it is, since serve prints it at each
start. Changes take a file lock, so `ombro pair` and a running serve never lose each other's
writes; serve reads the file again whenever it changes, so `ombro pair --forget-all` takes effect
at once.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from ombro.home import Home

# No 0/O, 1/I/L or U, so a code read aloud or off a log can't be mistyped.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_GROUPS = 3
CODE_GROUP = 3
LATER_CODE_SECONDS = 24 * 3600
MAX_WRONG = 5
LOCKOUT_SECONDS = 60
NAME_LIMIT = 60


class PairingError(Exception):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_code() -> str:
    groups = ("".join(secrets.choice(ALPHABET) for _ in range(CODE_GROUP)) for _ in range(CODE_GROUPS))
    return "-".join(groups)


def normal_code(text: str) -> str:
    """A code as typed: any case, with or without dashes or spaces."""
    return re.sub(r"[\s\-]", "", text).upper()


def clean_name(name: str) -> str:
    name = " ".join(str(name).split())[:NAME_LIMIT]
    return name or "A device"


@dataclass
class Device:
    id: str
    name: str
    paired: float

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "paired": self.paired}


class Devices:
    """The devices paired with one home, and its pairing code."""

    def __init__(self, home: Home, *, clock: Callable[[], float] = time.time):
        self.home = home
        self.clock = clock
        self._seen: tuple[int, int] | None = None  # the file's (mtime_ns, size) when last read
        self._hashes: set[str] = set()
        self._wrong: list[float] = []  # times of recent wrong codes, for the lockout

    @property
    def path(self) -> Path:
        return self.home.secrets / "devices.json"

    @property
    def _lock_path(self) -> Path:
        return self.home.secrets / "devices.lock"

    # -- the file ----------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"devices": [], "code": None}
        except (OSError, ValueError) as e:
            raise PairingError(f"{self.path} can't be read: {e}") from None
        if not isinstance(data, dict):
            raise PairingError(f"{self.path} is not what Ombro wrote")
        data.setdefault("devices", [])
        data.setdefault("code", None)
        return data

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, self.path)

    @contextmanager
    def _change(self) -> Iterator[dict[str, Any]]:
        self.home.ensure_secrets()
        with open(self._lock_path, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = self._read()
                yield data
                self._write(data)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        self._seen = None

    # -- devices -----------------------------------------------------------------

    def list(self) -> list[Device]:
        return [Device(d["id"], d.get("name", ""), d.get("paired", 0)) for d in self._read()["devices"]]

    def add(self, name: str) -> tuple[Device, str]:
        """A new device and its token. The token is shown this once and kept only as a hash."""
        token = secrets.token_urlsafe(32)
        device = Device(secrets.token_hex(4), clean_name(name), self.clock())
        with self._change() as data:
            data["devices"].append({**device.to_dict(), "token": _hash(token)})
        return device, token

    def forget(self, ref: str) -> list[Device]:
        """Forget the devices with this id or name. Returns those forgotten."""
        with self._change() as data:
            gone = [d for d in data["devices"] if ref in (d["id"], d.get("name"))]
            data["devices"] = [d for d in data["devices"] if d not in gone]
        return [Device(d["id"], d.get("name", ""), d.get("paired", 0)) for d in gone]

    def forget_all(self) -> int:
        """Forget every device, and any code, so a fresh first code is made at the next start."""
        with self._change() as data:
            n = len(data["devices"])
            data["devices"] = []
            data["code"] = None
        return n

    def valid(self, token: str) -> bool:
        """Whether a token belongs to a paired device. Reads the file again only when it changed."""
        if not token:
            return False
        try:
            st = self.path.stat()
            seen = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            seen = (0, 0)
        if seen != self._seen:
            try:
                self._hashes = {d["token"] for d in self._read()["devices"] if d.get("token")}
            except PairingError:
                self._hashes = set()
            self._seen = seen
        wanted = _hash(token)
        return any(secrets.compare_digest(wanted, h) for h in self._hashes)

    # -- codes -------------------------------------------------------------------

    def _live_code(self, data: dict[str, Any]) -> dict[str, Any] | None:
        code = data.get("code")
        if not isinstance(code, dict) or not code.get("code"):
            return None
        expires = code.get("expires")
        if expires is not None and self.clock() >= expires:
            return None
        return code

    def first_code(self) -> str | None:
        """While no device is paired: the standing first code, made if there isn't one. Once a
        device has paired, None: later codes come only from `ombro pair`."""
        with self._change() as data:
            if data["devices"]:
                return None
            code = self._live_code(data)
            if code is None:
                code = {"code": new_code(), "made": self.clock(), "expires": None}
                data["code"] = code
            return code["code"]

    def later_code(self) -> tuple[str, float]:
        """A new code that lasts a day, in place of any code there was. Returns it and its expiry."""
        expires = self.clock() + LATER_CODE_SECONDS
        code = new_code()
        with self._change() as data:
            data["code"] = {"code": code, "made": self.clock(), "expires": expires}
        return code, expires

    def locked_out(self) -> bool:
        now = self.clock()
        self._wrong = [t for t in self._wrong if now - t < LOCKOUT_SECONDS]
        return len(self._wrong) >= MAX_WRONG

    def pair(self, code: str, name: str) -> tuple[Device, str]:
        """Swap a code for a new device and its token. A code works once. After MAX_WRONG wrong
        codes in a minute, every code is refused for a minute, right ones included."""
        if self.locked_out():
            raise PairingError("Too many wrong codes. Wait a minute, then try again.")
        token = secrets.token_urlsafe(32)
        device = Device(secrets.token_hex(4), clean_name(name), self.clock())
        with self._change() as data:
            live = self._live_code(data)
            given = normal_code(code).encode()
            if live is None or not secrets.compare_digest(given, normal_code(live["code"]).encode()):
                self._wrong.append(self.clock())
                raise PairingError("That code isn't right, or it has run out. "
                                   "Run `ombro pair` on the server for a new one.")
            data["code"] = None
            data["devices"].append({**device.to_dict(), "token": _hash(token)})
        return device, token
