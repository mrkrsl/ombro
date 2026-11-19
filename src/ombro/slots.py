"""How many model calls may be in flight at once to one provider, across every Ombro process in a
home (jobs, scheduled jobs, chats, the app's server). A provider that counts in-flight requests
(OpenRouter reserves credit for each one) or limits them refuses the extra calls, and a scheduled job
failed on it (T10). Each provider has a few slot files under the home's run/slots/; a call holds an
exclusive lock on one for as long as it is in flight. Locks go when the process ends, however it ends.
"""

from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

try:
    import fcntl
except ImportError:  # not on this system: calls are not limited
    fcntl = None  # type: ignore[assignment]

DEFAULT_PER_PROVIDER = 4
POLL = 0.25


class NoSlot(Exception):
    """Every slot for the provider stayed busy until the call's time ran out."""


class ProviderSlots:
    def __init__(self, folder: Path, host: str, size: int = DEFAULT_PER_PROVIDER, *,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.folder = Path(folder) / (re.sub(r"[^A-Za-z0-9.-]+", "_", host.lower()) or "provider")
        self.size = max(1, int(size))
        self.clock = clock
        self.sleep = sleep

    def _try(self) -> int | None:
        self.folder.mkdir(parents=True, exist_ok=True)
        for i in range(self.size):
            fd = os.open(self.folder / f"slot-{i}.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            return fd
        return None

    @contextmanager
    def hold(self, deadline: float) -> Iterator[float]:
        """Wait for a free slot (until `deadline`, on the monotonic clock) and hold it. Yields the seconds spent
        waiting."""
        if fcntl is None:
            yield 0.0
            return
        began = self.clock()
        while True:
            try:
                fd = self._try()
            except OSError:  # the home can't hold the slot files: carry on unlimited
                yield 0.0
                return
            if fd is not None:
                break
            if self.clock() >= deadline:
                raise NoSlot
            self.sleep(POLL)
        try:
            yield self.clock() - began
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def for_provider(home, host: str, size: int | None = None) -> ProviderSlots:
    return ProviderSlots(Path(home.root) / "run" / "slots", host, size or DEFAULT_PER_PROVIDER)
