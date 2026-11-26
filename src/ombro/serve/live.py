"""Live sessions in `ombro serve`, and the hub that sends what they say to the windows.

A session is one conversation: a chat, or a flow such as set-up. It
runs on its own worker thread with its own ServeUI, the counterpart of the
terminal's TuiUI:

- show(event) adds the event to the session's log with a sequence number and
  sends it out;
- ask(field) sends a question with an id and blocks until it is answered;
- edit(paths) sends edit_files and blocks until the window says they are saved;
- interrupt makes the next show or ask raise, as Ctrl+C does in the terminal;
- a message sent as the answer to a chat question it doesn't answer sets the
  question aside: ask raises SetAside, and the chat takes the message next.

The session lives in serve, not in a window: a window that closes and comes
back subscribes with the last sequence number it saw and gets what it missed,
the open question included.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
from pathlib import Path
from typing import Any, Callable

from ombro import commands as core_commands
from ombro.flows import Flows
from ombro.home import Home
from ombro.session import Session, for_window
from ombro.ui import UI, Event, Failure, Field, Note, SetAside, parse_answer, sets_aside

LOG_KEEP = 2000  # messages a session keeps for windows that reconnect


class Interrupted(KeyboardInterrupt):
    pass


class Busy(Exception):
    """The session is already working on something."""


class Hub:
    """Every open socket, and what each has asked to hear. Threads publish; the event loop sends."""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set["Client"] = set()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def publish(self, message: dict[str, Any]) -> None:
        """Send to every client that wants it. Safe from any thread."""
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fanout, message)
        except RuntimeError:  # the loop is closing
            pass

    def _fanout(self, message: dict[str, Any]) -> None:
        for client in list(self.clients):
            client.offer(message)


class Client:
    """One socket. Hears every message that isn't a session's, and the sessions it subscribed to
    (or all of them), each from the sequence number it gave."""

    def __init__(self):
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.all_sessions = False
        self.seen: dict[str, int] = {}  # session id: last sequence number sent

    def offer(self, message: dict[str, Any]) -> None:
        sid = message.get("session")
        if sid is None:
            self.queue.put_nowait(message)
            return
        if not self.all_sessions and sid not in self.seen:
            return
        if message.get("seq", 0) <= self.seen.get(sid, 0):
            return  # already sent in a replay
        self.seen[sid] = message["seq"]
        self.queue.put_nowait(message)


class ServeUI(UI):
    """ombro.ui.UI for a session in serve. Called from the session's worker thread."""

    def __init__(self, live: "LiveSession"):
        self.live = live
        self.stop_requested = False

    def _check(self) -> None:
        if self.live.closed:
            raise Interrupted()
        if self.stop_requested:
            self.stop_requested = False
            raise Interrupted()

    def show(self, event: Event) -> None:
        self._check()
        self.live.emit({"type": "event", "event": for_window(event).to_dict()})

    def _wait(self, kind: str, payload: dict[str, Any]) -> Any:
        self._check()
        q = Question(secrets.token_hex(6), kind, payload)
        self.live.open(q)
        while not q.done.wait(0.25):
            if self.live.closed:
                q.error = Interrupted()
                break
        self.live.close_question(q)
        if q.error:
            raise q.error
        return q.value

    def ask(self, f: Field) -> Any:
        return self._wait("question", {"field": f.to_dict(), "prompt": field_prompt(f)})

    def edit(self, paths: list[Path]) -> None:
        self._wait("edit_files", {"paths": [str(p) for p in paths]})


def field_prompt(f: Field) -> str:
    from ombro.ui import field_prompt as prompt

    return prompt(f)


class Question:
    def __init__(self, qid: str, kind: str, payload: dict[str, Any]):
        self.id, self.kind, self.payload = qid, kind, payload
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None

    @property
    def field(self) -> Field | None:
        if self.kind != "question":
            return None
        from ombro.ui import Option

        d = dict(self.payload["field"])
        d["options"] = [Option(o["key"], o["label"], tuple(o.get("aliases") or ())) for o in d.get("options") or []]
        return Field(**d)


class LiveSession:
    """One conversation in serve: its core Session, its log, its worker and its open question."""

    def __init__(self, sessions: "Sessions", kind: str = "chat"):
        self.id = secrets.token_hex(8)
        self.kind = kind  # chat, or the flow it was opened for
        self.sessions = sessions
        self.hub = sessions.hub
        self.lock = threading.Lock()
        self.log: list[dict[str, Any]] = []
        self.seq = 0
        self.state = "idle"
        self.closed = False
        self.question: Question | None = None
        self.worker: threading.Thread | None = None
        self.ui = ServeUI(self)
        self.session = Session(
            sessions.home,
            self.ui,
            client_factory=sessions.client_factory,
            titles=sessions.titles,
            on_started=sessions.watch_job,
        )
        self.flows = Flows(self.session)
        # The chat is on disk (its first message, or a new name with its title): windows list it now,
        # while the Lead is still answering.
        self.session.on_chat_saved = lambda path: self.emit({"type": "chat_saved", "chat": path.stem})

    # -- the log ------------------------------------------------------------------

    def emit(self, message: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.seq += 1
            message = {**message, "session": self.id, "seq": self.seq}
            self.log.append(message)
            del self.log[:-LOG_KEEP]
        self.hub.publish(message)
        return message

    def since(self, seq: int) -> list[dict[str, Any]]:
        with self.lock:
            return [m for m in self.log if m["seq"] > seq]

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.emit({"type": "session", "state": state, "kind": self.kind})

    def describe(self) -> dict[str, Any]:
        chat = self.session.chat
        q = self.question
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "seq": self.seq,
            "chat": chat.path.stem if chat is not None and chat.path.exists() else None,
            "chat_id": chat.id if chat is not None else None,
            "title": chat.title if chat is not None else None,
            "mode": chat.mode if chat is not None else None,
            "jobs": list(self.session.started),
            "question": {"id": q.id, "kind": q.kind, **q.payload} if q else None,
        }

    # -- questions -------------------------------------------------------------------

    def open(self, q: Question) -> None:
        self.question = q
        self.emit({"type": q.kind, "id": q.id, **q.payload})
        self.set_state("waiting")

    def close_question(self, q: Question) -> None:
        if self.question is q:
            self.question = None
        self.emit({"type": "question_closed", "id": q.id})
        if not self.closed:
            self.set_state("working")

    def answer(self, qid: str, value: Any, attached: list[Path] | None = None) -> str | None:
        """Answer the open question (or say edited files are saved). Returns what to say when the
        answer can't be taken; the question then stays open. Files attached with an answer in a
        brief are the chat's, given to the job; the answer names them."""
        q = self.question
        if q is None or q.id != qid:
            raise LookupError("that question is no longer open")
        if q.kind == "question" and sets_aside(q.field, value):
            self.session.send_next(value.strip(), attached)
            q.error = SetAside(q.field)
            q.done.set()
            return None
        if attached and isinstance(value, str):
            value = self.session.attach_to_answer(value, attached)
        if q.kind == "question":
            f = q.field
            value, problem = parse_answer(f, value)
            if problem:
                self.emit({"type": "event", "event": Note(problem).to_dict()})
                return problem
        q.value = value
        q.done.set()
        return None

    # -- work ---------------------------------------------------------------------------

    def start(self, fn: Callable[[], Any]) -> None:
        if self.closed:
            raise LookupError("the session has ended")
        if self.worker is not None and self.worker.is_alive() and self.state != "working":
            self.worker.join(timeout=2)  # it has said idle and is only finishing up
        if self.worker is not None and self.worker.is_alive():
            raise Busy("I'm still working on the last thing. Press Esc to stop it, or wait for it to finish.")
        self.set_state("working")

        def work() -> None:
            ended = False
            try:
                ended = fn() is False
            except KeyboardInterrupt:
                self.session.undo_unanswered()
                if not self.closed:
                    self.emit({"type": "event", "event": Note("Stopped.").to_dict()})
            except Exception as e:  # noqa: BLE001 - shown, never a crash
                if not self.closed:
                    self.emit({"type": "event", "event": Failure(f"Something went wrong: {e}").to_dict()})
            finally:
                self.ui.stop_requested = False
                if ended:
                    self.session.save()
                    self.closed = True
                    self.set_state("ended")
                elif not self.closed:
                    self.set_state("idle")
                self.sessions.changed()

        self.worker = threading.Thread(target=work, name=f"session-{self.id}", daemon=True)
        self.worker.start()

    def input(self, text: str, attached: list[Path] | None = None) -> dict[str, Any]:
        """A message or slash command, as typed, with any files attached. A command that belongs to a
        page of its own is not run: the reply says where the window should go."""
        text = text.strip()
        if not text:
            return {"handled": False}
        where, cmd = core_commands.route(text)
        if where in ("screen", "app"):
            return {
                "handled": False,
                "route": {
                    "where": where,
                    "command": cmd.name,
                    "screen": cmd.screen,
                    "args": text.partition(" ")[2].strip(),
                },
            }
        if where == "chat":
            self.start(lambda: self.session.chat_turn(text, attached))
        else:
            word, _, rest = text.partition(" ")
            name = core_commands.ALIASES.get(word.lower(), word.lower())
            self.start(lambda: self.flows.run(name, rest))
        return {"handled": True}

    def reopen(self, path: Path) -> None:
        self.start(lambda: self.session.ensure_chat().reopen(path))

    def interrupt(self) -> None:
        q = self.question
        if q is not None:
            q.error = Interrupted()
            q.done.set()
            return
        if self.worker is not None and self.worker.is_alive():
            self.ui.stop_requested = True

    def close(self) -> None:
        """The session ends: a question open is withdrawn, and the chat is saved."""
        self.closed = True
        q = self.question
        if q is not None:
            q.error = Interrupted()
            q.done.set()
        worker = self.worker
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=5)
        self.session.save()
        self.set_state("ended")


class Sessions:
    """Every live session, by id."""

    def __init__(
        self,
        home: Home,
        hub: Hub,
        *,
        client_factory: Callable | None = None,
        titles: bool = True,
        watch_job: Callable[[str, str], None] | None = None,
    ):
        self.home = home
        self.hub = hub
        self.client_factory = client_factory
        self.titles = titles
        self._watch = watch_job
        self.by_id: dict[str, LiveSession] = {}
        self.on_change: Callable[[], None] = lambda: None

    def new(self, kind: str = "chat") -> LiveSession:
        live = LiveSession(self, kind)
        self.by_id[live.id] = live
        return live

    def get(self, sid: str) -> LiveSession:
        live = self.by_id.get(sid)
        if live is None:
            raise LookupError(f"no session {sid}")
        return live

    def remove(self, sid: str) -> None:
        live = self.by_id.pop(sid, None)
        if live is not None:
            live.close()

    def close_all(self) -> None:
        for sid in list(self.by_id):
            self.remove(sid)

    def watch_job(self, job_id: str) -> None:
        """A session started a job: follow it, and send its progress to that session too."""
        owner = next((s.id for s in self.by_id.values() if job_id in s.session.started), None)
        if self._watch:
            self._watch(job_id, owner)

    def changed(self) -> None:
        self.on_change()
