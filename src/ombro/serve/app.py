"""The Starlette app behind `ombro serve`: the guard, the views, sessions, actions, the socket
and the poller (plan 3.3, 3.4).

Every handler that reads or writes the home runs on a worker thread, so the event
loop only ever sends and receives.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import signal
from pathlib import Path
from typing import Any, Awaitable, Callable

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

from ombro import __version__, jobs, outbox, views
from ombro.home import Home
from ombro.serve import DEV_ORIGIN, HOST
from ombro.serve.live import Busy, Client, Hub, Sessions
from ombro.session import JobWatcher, StartError, full_line, record_job_end_in_chat, setup_needed, start_saved_spec
from ombro.settings import SetupError
from ombro.ui import JobEnded, JobProgress, Note

APP_ORIGINS = ("tauri://localhost", "http://tauri.localhost")
SUBPROTOCOL = "ombro"
# Raised when the app and a server would no longer understand each other. The app reads it from
# /hello and refuses, with a clear message, a server too old or too new for it.
PROTOCOL = 1
# Paths a service answers without a device token: swapping a pairing code for one.
OPEN_PATHS = ("/pair",)


def origins(dev: bool = False) -> list[str]:
    return [*APP_ORIGINS, DEV_ORIGIN] if dev else list(APP_ORIGINS)


def hello() -> dict[str, Any]:
    """The first message on every socket."""
    event = Note(f"Ombro {__version__} is running.", level="done")
    return {"type": "event", "session": None, "seq": 1, "event": event.to_dict()}


class Guard:
    """Refuses any request without a token `check` accepts, or from an origin that isn't the app's.
    A path in `open_paths` needs no token (the origin check still applies)."""

    def __init__(self, app: ASGIApp, check: Callable[[str], bool], allowed: list[str],
                 open_paths: tuple[str, ...] = ()):
        self.app, self.check, self.allowed, self.open_paths = app, check, allowed, open_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        origin = headers.get("origin")
        if origin is not None and origin not in self.allowed:
            return await self._refuse(scope, receive, send)
        if scope["type"] == "http":
            if scope["path"] in self.open_paths:
                return await self.app(scope, receive, send)
            given = headers.get("authorization", "").removeprefix("Bearer ")
        else:
            given = next((p for p in scope.get("subprotocols", []) if p != SUBPROTOCOL), "")
        if not self.check(given):
            return await self._refuse(scope, receive, send)
        await self.app(scope, receive, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await WebSocket(scope, receive, send).close(code=1008)
        else:
            await Response(status_code=403)(scope, receive, send)


POLL_SECONDS = 2.0


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _body(request: Request) -> dict[str, Any]:
    if not (await request.body()).strip():
        return {}
    try:
        data = await request.json()
    except ValueError:
        raise HttpError(400, "the body must be JSON") from None
    if not isinstance(data, dict):
        raise HttpError(400, "the body must be a JSON object")
    return data


def _need(data: dict[str, Any], key: str, kind: type = str) -> Any:
    value = data.get(key)
    if not isinstance(value, kind) or (kind is str and not value.strip()):
        raise HttpError(400, f"`{key}` is needed")
    return value


def find_approval(home: Home, ref: str) -> outbox.Action:
    """An approval by its reference; a reference that names nothing is not found (404), one that is
    malformed or ambiguous is a bad request (400)."""
    try:
        return outbox.find(home, ref)
    except outbox.OutboxError as e:
        if str(e).startswith("no "):
            raise LookupError(str(e)) from None
        raise


def find_schedule(home: Home, name: str):
    from ombro import schedules

    try:
        return schedules.find_schedule(home, name)
    except SetupError as e:
        raise LookupError(str(e)) from None


class Serve:
    """What serve keeps while it runs: the home, the live sessions, the open sockets and the job watcher."""

    def __init__(self, home: Home, *, client_factory: Callable | None = None, titles: bool = True,
                 poll_seconds: float = POLL_SECONDS, tick: bool = False):
        self.home = home
        self.ticking = tick  # this serve starts scheduled jobs itself (--tick)
        self._ticker: asyncio.Task | None = None
        self.hub = Hub()
        self.sessions = Sessions(home, self.hub, client_factory=client_factory, titles=titles,
                                 watch_job=self.watch_job)
        self.sessions.on_change = self.counts_soon
        self.watcher = JobWatcher(home)
        self.owners: dict[str, str] = {}  # job id: the session that started it
        self.poll_seconds = poll_seconds
        self.last_counts: dict[str, Any] | None = None
        self._poller: asyncio.Task | None = None
        self._scheduler: asyncio.Task | None = None  # installing the scheduler service at start
        self.signing: dict[str, dict[str, Any]] = {}  # tool server: its sign-in from the app, while it lasts
        self.handovers: dict[str, Any] = {}  # tool server: its sign-in waiting for the app to hand over the answer

    # -- jobs -------------------------------------------------------------------

    def watch_job(self, job_id: str, owner: str | None) -> None:
        if owner:
            self.owners[job_id] = owner
        try:
            self.watcher.follow(job_id)
        except LookupError:
            pass

    def poll(self) -> None:
        """One look at every job: progress and endings out as `job` messages (and into the session
        that started the job), and the counts when they change. Runs on a worker thread."""
        try:
            self.watcher.follow_all()
        except Exception:  # noqa: BLE001 - a job being written
            pass
        polled = self.watcher.poll(mark_seen=False)
        if polled is None:
            return
        events, changed = polled
        for e in events:
            status = "running" if isinstance(e, JobProgress) else getattr(e, "status", "")
            line = full_line(e) if isinstance(e, JobProgress) else e.text  # the side panel shows it whole
            self.hub.publish({"type": "job", "job_id": e.job_id, "name": e.name, "status": status,
                              "line": line, "event": e.to_dict()})
            owner = self.owners.get(e.job_id)
            if owner and owner in self.sessions.by_id and not self.sessions.by_id[owner].closed:
                self.sessions.by_id[owner].emit({"type": "event", "event": e.to_dict()})
            if isinstance(e, JobEnded):
                # Its outcome becomes part of the chat that started it: the open one, or its file.
                open_chats = [live for live in self.sessions.by_id.values() if not live.closed]
                if not any(live.session.job_ended(e) for live in open_chats):
                    record_job_end_in_chat(self.home, e)
            if isinstance(e, JobEnded):
                self.owners.pop(e.job_id, None)
        if changed or self.last_counts is None:
            self.send_counts()

    def counts(self) -> dict[str, Any]:
        c = views.summary(self.home)["counts"]
        return {"type": "counts", "active": c["active_jobs"], "scheduled": c["scheduled_jobs"],
                "waiting": c["waiting"], "unseen": c["unseen_jobs"], "crews_ready": c["crews_ready"]}

    def send_counts(self, force: bool = False) -> None:
        try:
            counts = self.counts()
        except Exception:  # noqa: BLE001 - the home is being changed; the next poll sends them
            return
        if force or counts != self.last_counts:
            self.last_counts = counts
            self.hub.publish(counts)

    def counts_soon(self) -> None:
        """Something a session did may have changed the counts (an approval, a new crew)."""
        self.send_counts()

    async def run_poller(self) -> None:
        self.watcher.start()
        while True:
            try:
                await asyncio.to_thread(self.poll)
            except Exception:  # noqa: BLE001 - never let the poller die
                pass
            await asyncio.sleep(self.poll_seconds)

    def ensure_scheduler(self) -> None:
        """Scheduling is part of the app: the packaged app installs the scheduler service itself, or
        puts it right, when it starts and when a schedule is added or turned on."""
        from ombro import scheduler

        if not self.ticking and scheduler.may_install_itself(self.home):
            scheduler.ensure(self.home)

    def tick_once(self) -> None:
        """What the system scheduler's `ombro tick` does, from inside serve. The tick lock keeps it
        from starting a job twice if the system scheduler runs too."""
        from ombro import schedules

        if self.home.exists():
            schedules.tick(self.home)

    async def run_ticker(self) -> None:
        """With --tick: a tick at the start of every minute, as cron would."""
        import time

        while True:
            await asyncio.sleep(60 - time.time() % 60 + 0.5)
            try:
                await asyncio.to_thread(self.tick_once)
            except Exception:  # noqa: BLE001 - never let the ticker die; tick logs its own problems
                pass

    async def startup(self) -> None:
        self.hub.bind(asyncio.get_running_loop())
        self._poller = asyncio.create_task(self.run_poller())
        self._scheduler = asyncio.create_task(asyncio.to_thread(self.ensure_scheduler))
        if self.ticking:
            self._ticker = asyncio.create_task(self.run_ticker())

    async def shutdown(self) -> None:
        if self._ticker:
            self._ticker.cancel()
        if self._poller:
            self._poller.cancel()
        await asyncio.to_thread(self.sessions.close_all)


def _views(s: Serve) -> list[Route]:
    """GET: what each screen shows. Each is a function of the request, run on a worker thread."""
    home = s.home
    from ombro import commands as core_commands

    def job_dir(r: Request) -> Path:
        return jobs.find_job(home, r.path_params["job_id"])

    def scheduler_check() -> Any:
        from ombro import scheduler

        if s.ticking:  # serve starts scheduled jobs itself, so no system service is needed
            return {"ok": True, "message": "", "can_install": False}
        return scheduler.check(home)

    def skills_view(r: Request) -> Any:
        from ombro.skills_cmd import library_view

        return library_view(home)

    table: dict[str, Callable[[Request], Any]] = {
        "/summary": lambda r: views.summary(home),
        "/setup": lambda r: {"needed": setup_needed(home)},
        "/commands": lambda r: [c.to_dict() for c in core_commands.COMMANDS],
        "/chats": lambda r: views.chat_list(home),
        "/chats/{name}": lambda r: views.chat(home, r.path_params["name"]),
        "/jobs": lambda r: views.job_list(home),
        "/jobs/{job_id}": lambda r: views.status(job_dir(r)),
        "/jobs/{job_id}/report": lambda r: views.report(home, job_dir(r)),
        "/jobs/{job_id}/brief": lambda r: views.brief(job_dir(r)),
        "/approvals": lambda r: views.approval_list(home),
        "/approvals/{ref}": lambda r: (find_approval(home, r.path_params["ref"]),
                                       views.approval(home, r.path_params["ref"]))[1],
        "/schedules": lambda r: views.schedule_list(home),
        "/scheduler": lambda r: scheduler_check(),
        "/specs": lambda r: views.spec_list(home),
        "/crews": lambda r: views.crews(home),
        "/roles/{crew}/{role}": lambda r: views.role(home, r.path_params["crew"], r.path_params["role"]),
        "/preferences": lambda r: views.preferences(home),
        "/models": lambda r: views.model_list(home),
        "/limits": lambda r: views.limit_values(home),
        "/keys": lambda r: views.key_list(home),
        "/folders": lambda r: views.folder_list(home),
        "/tools": lambda r: views.tool_list(home, signing=s.signing),
        "/browser": lambda r: views.browser_view(home),
        "/skills": skills_view,
        "/sessions": lambda r: [live.describe() for live in s.sessions.by_id.values()],
        "/sessions/{sid}": lambda r: s.sessions.get(r.path_params["sid"]).describe(),
    }
    return [Route(path, _handler(fn), methods=["GET"]) for path, fn in table.items()]


def _handler(fn: Callable[[Request], Any], *, status: int = 200, with_body: bool = False,
             after: Callable[[], None] | None = None) -> Callable[[Request], Awaitable[Response]]:
    async def handle(request: Request) -> Response:
        try:
            data = await _body(request) if with_body else None
            result = await asyncio.to_thread(fn, request, data) if with_body else await asyncio.to_thread(fn, request)
        except HttpError as e:
            return _error(e.status, str(e))
        except Busy as e:
            return _error(409, str(e))
        except (LookupError, jobs.JobNotFound) as e:
            return _error(404, str(e).strip("'\"") or "not found")
        except (ValueError, SetupError, StartError, outbox.OutboxError) as e:
            return _error(400, str(e))
        except Exception as e:  # noqa: BLE001 - reported, never a crash of serve
            from ombro.folders import FolderError
            from ombro.skills import SkillError

            if isinstance(e, (FolderError, SkillError)):
                return _error(400, str(e))
            return _error(500, f"{type(e).__name__}: {e}")
        if after:
            await asyncio.to_thread(after)
        return JSONResponse(result if result is not None else {"ok": True}, status_code=status)

    return handle


def _session_routes(s: Serve) -> list[Route]:
    home = s.home

    def _attached(data: dict[str, Any]) -> list[Path]:
        from ombro.serve.files import Refused, attached_path

        try:
            return [attached_path(s, p) for p in data.get("attachments") or []]
        except Refused as e:
            raise HttpError(e.status, str(e)) from e

    def create(r: Request, data: dict[str, Any]) -> Any:
        """A new chat, a chat reopened by name, or a flow (setup, role.add)."""
        flow = data.get("flow")
        if flow and flow not in ("setup", "role.add"):
            raise HttpError(400, f"no flow called {flow!r}; flows: setup, role.add")
        live = s.sessions.new(flow or "chat")
        if data.get("chat"):
            name = str(data["chat"])
            path = home.chats_dir / f"{name}.md"
            if "/" in name or not path.is_file():
                s.sessions.remove(live.id)
                raise LookupError(f"no chat called {name!r}")
            live.reopen(path)
        elif data.get("discuss"):  # Discuss this brief: a new chat about the brief a job ran from
            try:
                job_dir = jobs.find_job(home, str(data["discuss"]))
            except LookupError:
                s.sessions.remove(live.id)
                raise
            live.start(lambda: live.session.ensure_chat().discuss_brief(job_dir))
        elif flow == "setup":
            live.start(lambda: live.flows.setup() and None)
        elif flow == "role.add":
            words = ["add", *str(data.get("title") or "").split()]
            if data.get("crew"):
                words += ["--crew", str(data["crew"])]
            live.start(lambda: live.flows.cmd_role(words, " ".join(words)))
        return {"session": live.describe()}

    def text_input(r: Request, data: dict[str, Any]) -> Any:
        live = s.sessions.get(r.path_params["sid"])
        return live.input(_need(data, "text"), _attached(data))

    def answer(r: Request, data: dict[str, Any]) -> Any:
        live = s.sessions.get(r.path_params["sid"])
        problem = live.answer(_need(data, "id"), data.get("value"), _attached(data))
        return {"ok": problem is None, "problem": problem}

    def interrupt(r: Request) -> Any:
        s.sessions.get(r.path_params["sid"]).interrupt()
        return {"ok": True}

    def delete(r: Request) -> Any:
        s.sessions.get(r.path_params["sid"])
        s.sessions.remove(r.path_params["sid"])
        return {"ok": True}

    return [
        Route("/sessions", _handler(create, status=201, with_body=True), methods=["POST"]),
        Route("/sessions/{sid}/input", _handler(text_input, with_body=True), methods=["POST"]),
        Route("/sessions/{sid}/answer", _handler(answer, with_body=True), methods=["POST"]),
        Route("/sessions/{sid}/interrupt", _handler(interrupt), methods=["POST"]),
        Route("/sessions/{sid}", _handler(delete), methods=["DELETE"]),
    ]


PREFERENCE_KEYS = {"name": str, "theme": str, "default_crew": str, "editor": str, "background_feed": bool,
                   "notifications": bool, "instructions": str, "outward": str}


def _action_routes(s: Serve) -> list[Route]:
    """POST, PUT, DELETE: each calls the same core function the terminal or command line calls.
    The window has already asked the user, so none of these asks again."""
    home = s.home

    def start_job(r: Request, data: dict[str, Any]) -> Any:
        roles = data.get("roles") or []
        if not isinstance(roles, list):
            raise HttpError(400, "`roles` must be a list")
        job_id = start_saved_spec(home, _need(data, "spec"), [str(x) for x in roles])
        s.watch_job(job_id, None)
        return {"job_id": job_id, "name": jobs.job_name(job_id)}

    def stop_job(r: Request) -> Any:
        job_dir = jobs.find_job(home, r.path_params["job_id"])
        meta, _ = jobs.read_job(job_dir)
        if meta.get("status") != "running":
            raise HttpError(409, f"job {job_dir.name} is not running (status {meta.get('status')})")
        pid = meta.get("pid")
        if not pid or not jobs.pid_alive(int(pid)):
            jobs.reap_dead_jobs(home)
            return {"stopping": False, "note": "it had no live process; it is marked failed and its report written"}
        os.kill(int(pid), signal.SIGTERM)
        return {"stopping": True}

    def decide(approve: bool) -> Callable[[Request, dict[str, Any]], Any]:
        def run(r: Request, data: dict[str, Any]) -> Any:
            refs = _need(data, "refs", list)
            items = [find_approval(home, str(x)) for x in refs]  # all found before any is acted on
            done = []
            for a in items:
                try:
                    a2 = outbox.approve(home, a) if approve else outbox.decline(home, a)
                    done.append({"ref": a2.ref, "status": a2.status})
                except outbox.OutboxError as e:
                    done.append({"ref": a.ref, "status": a.status, "error": str(e)})
            return {"results": done}
        return run

    def resolve(r: Request, data: dict[str, Any]) -> Any:
        a = outbox.resolve(home, find_approval(home, r.path_params["ref"]), bool(_need(data, "sent", bool)))
        return {"ref": a.ref, "status": a.status}

    def schedule(enabled: bool) -> Callable[[Request], Any]:
        def run(r: Request) -> Any:
            from ombro import schedules

            sch = find_schedule(home, r.path_params["name"])
            schedules.set_enabled(sch.path, enabled)
            if enabled:
                s.ensure_scheduler()
            return {"name": sch.name, "enabled": enabled}
        return run

    def add_schedule(r: Request, data: dict[str, Any]) -> Any:
        """A new schedule: a saved spec, when (cron), and a name. Off until it is enabled, unless asked."""
        from ombro import schedules
        from ombro.settings import SetupError

        name, spec, cron_text = _need(data, "name"), _need(data, "spec"), _need(data, "cron")
        try:
            schedules.add_schedule(home, name, spec, cron_text, bool(data.get("enabled", True)))
        except SetupError as e:
            raise HttpError(400, str(e)) from None
        s.ensure_scheduler()
        return views.schedule_list(home)

    def install_scheduler(r: Request) -> Any:
        """Install now, on the Scheduled page: the service for this home, replacing any other."""
        from ombro import scheduler

        try:
            scheduler.make_installer(home).install()
        except OSError as e:
            raise HttpError(400, str(e)) from None
        return scheduler.check(home)

    def edit_schedule(r: Request, data: dict[str, Any]) -> Any:
        from ombro import schedules

        sch = find_schedule(home, r.path_params["name"])
        schedules.set_timing(sch.path, _need(data, "cron"))
        return views.schedule_list(home)

    def delete_schedule(r: Request) -> Any:
        from ombro import schedules

        sch = find_schedule(home, r.path_params["name"])
        return {"moved_to": str(schedules.remove_schedule(home, sch)), "schedules": views.schedule_list(home)}

    def seen(r: Request) -> Any:
        return {"seen": jobs.mark_seen(jobs.find_job(home, r.path_params["job_id"]))}

    def seen_all(r: Request) -> Any:
        """The Jobs page has been looked at: every finished job counts as seen."""
        return {"seen": sum(jobs.mark_seen(d) for d in jobs.unseen_jobs(home))}

    def clear(r: Request) -> Any:
        """Hide a finished job from the Jobs page; its folder stays."""
        try:
            return {"cleared": jobs.clear_job(jobs.find_job(home, r.path_params["job_id"]))}
        except jobs.StillRunning as e:
            raise HttpError(409, str(e)) from None

    def clear_all(r: Request) -> Any:
        return {"cleared": jobs.clear_finished(home)}

    def restore(r: Request) -> Any:
        return {"restored": jobs.restore_job(jobs.find_job(home, r.path_params["job_id"]))}

    def add_crew(r: Request, data: dict[str, Any]) -> Any:
        from ombro.home import ensure_crew
        from ombro.roles import NAME

        name = _need(data, "name")
        if not NAME.match(name) or home.crew_dir(name).exists():
            raise HttpError(400, "crew names use lower-case letters, digits, - and _, and must be new")
        ensure_crew(home, name, str(data.get("description") or ""), str(data.get("title") or ""))
        return views.crews(home)

    def retitle_crew(r: Request, data: dict[str, Any]) -> Any:
        from ombro.home import set_crew_title

        name = r.path_params["name"]
        if name not in home.crews():
            raise LookupError(f"no crew called {name!r}")
        set_crew_title(home, name, _need(data, "title"))
        return views.crews(home)

    def preferences(r: Request, data: dict[str, Any]) -> Any:
        from ombro.outward import PRESETS
        from ombro.settings import THEMES, set_instructions, set_outward, set_preference

        for key, value in data.items():
            if key not in PREFERENCE_KEYS or not isinstance(value, PREFERENCE_KEYS[key]):
                raise HttpError(400, f"`{key}` can't be set here; these can: {', '.join(PREFERENCE_KEYS)}")
            if key == "theme" and value not in THEMES:
                raise HttpError(400, f"theme is one of {', '.join(THEMES)}")
            if key == "default_crew" and value not in home.crews():
                raise HttpError(400, f"there's no crew called {value!r}")
            if key == "outward" and value not in PRESETS:
                raise HttpError(400, f"the approval mode is one of {', '.join(PRESETS)}")
        for key, value in data.items():
            if key == "instructions":
                set_instructions(home, value)  # the profile, in its own file
            elif key == "outward":
                set_outward(home, value)
            else:
                set_preference(home, key, " ".join(value.split())[:60] if key == "name" else value)
        return views.preferences(home)

    def limits(r: Request, data: dict[str, Any]) -> Any:
        from ombro.settings import set_allowance

        dropped = set_allowance(home, _need(data, "allowance"))
        return {**views.limit_values(home), "removed_by_hand": dropped}

    def set_browser(r: Request, data: dict[str, Any]) -> Any:
        from ombro import tool_cmd

        tool_cmd.set_browser(home, _need(data, "choice"))
        return views.browser_view(home)

    def default_model(r: Request, data: dict[str, Any]) -> Any:
        from ombro import models

        models.set_default(home, _need(data, "name"))
        return views.model_list(home)

    def set_key(r: Request, data: dict[str, Any]) -> Any:
        from ombro import keys

        keys.set_key(home, r.path_params["name"], _need(data, "value"))
        return {"name": r.path_params["name"], "set": True}  # never the value

    def remove_key(r: Request) -> Any:
        from ombro import keys

        return {"name": r.path_params["name"], "removed": keys.remove_key(home, r.path_params["name"])}

    def add_folder(r: Request, data: dict[str, Any]) -> Any:
        from ombro import folders

        path = Path(os.path.expanduser(_need(data, "path")))
        if not path.is_absolute():
            raise HttpError(400, "the folder must be a full path")
        real = folders.check_place(path, home)
        grants, _ = folders.load(home)
        if folders.find(grants, real) is not None:
            raise HttpError(409, f"{real} is already shared")
        access = str(data.get("access") or "read")
        folders.add(home, real, str(data.get("name") or folders.suggest_name(real, set(grants))), access)
        return views.folder_list(home)

    def remove_folder(r: Request) -> Any:
        from ombro import folders

        folders.remove(home, r.path_params["name"])
        return views.folder_list(home)

    def give_skill(r: Request, data: dict[str, Any]) -> Any:
        from ombro import skills

        return {"done": skills.give(home, r.path_params["name"], _need(data, "role"))}

    def save_soul(r: Request, data: dict[str, Any]) -> Any:
        from ombro import mdfile

        crew, name = r.path_params["crew"], r.path_params["role"]
        folder = home.lead_dir if name == "lead" else home.roles_dir(crew) / name
        path = folder / "soul.md"
        if "/" in name or not path.is_file():
            raise LookupError(f"no role {crew}/{name}")
        meta, body = mdfile.read(path)  # its front matter and guidance comments stay
        mdfile.write(path, meta, mdfile.with_comments(body, _need(data, "text"))) if meta else \
            mdfile.write_atomic(path, mdfile.with_comments(body, _need(data, "text")))
        return views.role(home, crew, name)

    after = s.counts_soon
    return [
        Route("/jobs/start", _handler(start_job, status=201, with_body=True, after=after), methods=["POST"]),
        Route("/jobs/{job_id}/stop", _handler(stop_job), methods=["POST"]),
        Route("/approvals/approve", _handler(decide(True), with_body=True, after=after), methods=["POST"]),
        Route("/approvals/decline", _handler(decide(False), with_body=True, after=after), methods=["POST"]),
        Route("/approvals/{ref}/resolve", _handler(resolve, with_body=True, after=after), methods=["POST"]),
        Route("/schedules", _handler(add_schedule, status=201, with_body=True, after=after), methods=["POST"]),
        Route("/schedules/{name}", _handler(edit_schedule, with_body=True), methods=["PUT"]),
        Route("/scheduler/install", _handler(install_scheduler), methods=["POST"]),
        Route("/schedules/{name}", _handler(delete_schedule, after=after), methods=["DELETE"]),
        Route("/jobs/seen", _handler(seen_all, after=after), methods=["POST"]),
        Route("/jobs/{job_id}/seen", _handler(seen, after=after), methods=["POST"]),
        Route("/jobs/clear", _handler(clear_all, after=after), methods=["POST"]),
        Route("/jobs/{job_id}/clear", _handler(clear, after=after), methods=["POST"]),
        Route("/jobs/{job_id}/restore", _handler(restore, after=after), methods=["POST"]),
        Route("/schedules/{name}/enable", _handler(schedule(True), after=after), methods=["POST"]),
        Route("/schedules/{name}/disable", _handler(schedule(False), after=after), methods=["POST"]),
        Route("/crews", _handler(add_crew, status=201, with_body=True, after=after), methods=["POST"]),
        Route("/crews/{name}", _handler(retitle_crew, with_body=True), methods=["PUT"]),
        Route("/preferences", _handler(preferences, with_body=True), methods=["PUT"]),
        Route("/limits", _handler(limits, with_body=True), methods=["PUT"]),
        Route("/browser", _handler(set_browser, with_body=True), methods=["PUT"]),
        Route("/models/default", _handler(default_model, with_body=True), methods=["PUT"]),
        Route("/keys/{name}", _handler(set_key, with_body=True), methods=["PUT"]),
        Route("/keys/{name}", _handler(remove_key), methods=["DELETE"]),
        Route("/folders", _handler(add_folder, status=201, with_body=True), methods=["POST"]),
        Route("/folders/{name}", _handler(remove_folder), methods=["DELETE"]),
        Route("/skills/{name}/give", _handler(give_skill, with_body=True), methods=["POST"]),
        Route("/roles/{crew}/{role}/soul", _handler(save_soul, with_body=True), methods=["PUT"]),
    ]


# Lines from bringing the home up to date as the server started (home.tidy_layout), which the app
# shows once, as notices.
NOTES: list[str] = []


async def _hello(request: Request) -> JSONResponse:
    return JSONResponse({"version": __version__, "protocol": PROTOCOL, "notes": NOTES})


def _pair_route(devices) -> Route:
    """POST /pair {"code", "name"}: a pairing code for a new device's token. The only route a
    service answers without a token; wrong codes count towards a lockout (pairing.py)."""
    from ombro.pairing import PairingError

    async def pair(request: Request) -> Response:
        try:
            data = await _body(request)
            code = _need(data, "code")
            name = data.get("name") if isinstance(data.get("name"), str) else ""
        except HttpError as e:
            return _error(e.status, str(e))
        try:
            device, token = await asyncio.to_thread(devices.pair, code, name)
        except PairingError as e:
            status = 429 if devices.locked_out() else 403
            return _error(status, str(e))
        return JSONResponse({"token": token, "device": device.to_dict(), "version": __version__,
                             "protocol": PROTOCOL}, status_code=201)

    return Route("/pair", pair, methods=["POST"])


def _socket(s: Serve):
    async def events(ws: WebSocket) -> None:
        """Server to client: hello, the counts, then every message for this client. Client to
        server: `subscribe` with {"sessions": {id: last seq seen}} or {"all": true}."""
        await ws.accept(subprotocol=SUBPROTOCOL)
        client = Client()
        s.hub.clients.add(client)
        try:
            await ws.send_json(hello())
            if s.last_counts:
                await ws.send_json(s.last_counts)
            sender = asyncio.create_task(_send(ws, client))
            try:
                while True:
                    message = await ws.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text"):
                        _subscribe(s, client, message["text"])
            finally:
                sender.cancel()
        finally:
            s.hub.clients.discard(client)

    return events


async def _send(ws: WebSocket, client: Client) -> None:
    while True:
        message = await client.queue.get()
        await ws.send_json(message)


def _subscribe(s: Serve, client: Client, text: str) -> None:
    """Replay what each session said after the sequence number given, then send it live. Runs on the
    event loop, so nothing published meanwhile can slip between the replay and the live messages."""
    import json

    try:
        data = json.loads(text)
    except ValueError:
        return
    if not isinstance(data, dict) or data.get("type") != "subscribe":
        return
    wanted = data.get("sessions") or {}
    if data.get("all"):
        client.all_sessions = True
        wanted = {sid: wanted.get(sid, 0) for sid in s.sessions.by_id} if isinstance(wanted, dict) else {}
    if not isinstance(wanted, dict):
        return
    for sid, seq in wanted.items():
        live = s.sessions.by_id.get(str(sid))
        if live is None:
            client.queue.put_nowait({"type": "session", "session": str(sid), "state": "ended", "seq": 0})
            continue
        client.seen.setdefault(live.id, int(seq or 0))
        for m in live.since(int(seq or 0)):
            client.offer(m)


def build_app(token: str | None, dev: bool = False, *, home: Home | None = None, client_factory: Callable | None = None,
              titles: bool = True, poll_seconds: float = POLL_SECONDS, devices: Any = None,
              hosts: list[str] | None = None, tick: bool = False) -> Starlette:
    """For the app: one `token`, made for this launch. For a service: `devices` (pairing.Devices),
    whose paired tokens are accepted, `POST /pair` to pair a new one, and the `hosts` it answers to."""
    from ombro.serve.files import file_routes
    from ombro.serve.settings_routes import settings_routes

    allowed = origins(dev)
    s = Serve(home or Home(), client_factory=client_factory, titles=titles, poll_seconds=poll_seconds, tick=tick)
    if devices is not None:
        check, open_paths = devices.valid, OPEN_PATHS
    elif token:
        def check(given: str) -> bool:
            return secrets.compare_digest(given.encode(), token.encode())
        open_paths = ()
    else:
        raise ValueError("serve needs a token or paired devices")
    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=hosts or ([HOST, "localhost"] if dev else [HOST])),
        # Answers the app's preflight; never a wildcard.
        Middleware(CORSMiddleware, allow_origins=allowed, allow_methods=["GET", "POST", "PUT", "DELETE"],
                   allow_headers=["authorization", "content-type"], expose_headers=["x-ombro-save-as"]),
        Middleware(Guard, check=check, allowed=allowed, open_paths=open_paths),
    ]
    routes = [Route("/hello", _hello), WebSocketRoute("/events", _socket(s)),
              *([_pair_route(devices)] if devices is not None else []),
              *file_routes(s), *_action_routes(s), *settings_routes(s), *_session_routes(s), *_views(s)]
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        await s.startup()
        try:
            yield
        finally:
            await s.shutdown()

    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
    app.state.serve = s
    return app
