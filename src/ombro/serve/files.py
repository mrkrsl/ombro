"""Files over the API, for an app that isn't on the same machine as serve ("Save to this Mac").

- `GET /files?path=…` sends one file's bytes.
- `GET /files/list?path=…` lists the files in a folder (a job's workspace, say), each with its own
  path, so the app can fetch them one by one and keep the folder's shape.
- `PUT /files?path=…` writes a file back. It's only for a draft an interview has handed out for
  editing by hand (an open `edit_files` question), so the app can return what you changed.

Only these can be read: files in the jobs and chats folders, in a folder shared in folders.md, and
any draft out for editing. Paths are resolved first, so a link pointing elsewhere is refused.

Each answer says where the app should keep its copy, as `X-Ombro-Save-As` (or `save_as` in a
listing): a relative path under the app's ~/Downloads/Ombro folder, such as `<job id>/workspace/x.md`.
"""

from __future__ import annotations

import asyncio
import os
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

if TYPE_CHECKING:
    from ombro.serve.app import Serve

MAX_LIST = 500
MAX_PUT_BYTES = 20 * 1024 * 1024


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def editing_paths(s: "Serve") -> list[Path]:
    """The drafts out for editing by hand in any open session."""
    out = []
    for live in list(s.sessions.by_id.values()):
        q = live.question
        if q is not None and q.kind == "edit_files" and not live.closed:
            out += [Path(p).resolve() for p in q.payload.get("paths") or []]
    return out


def roots(s: "Serve") -> list[tuple[str, Path]]:
    """Where files may be read from, each with the name its copies go under."""
    from ombro import folders

    home = s.home
    out = [("jobs", home.jobs_dir.resolve()), ("Chats", home.chats_dir.resolve())]
    try:
        grants, _ = folders.load(home)
    except Exception:  # noqa: BLE001 - a broken folders.md shares nothing
        grants = {}
    for g in grants.values():
        out.append((f"shared/{g.name}", Path(g.path).expanduser().resolve()))
    return out


def save_as(s: "Serve", path: Path) -> str:
    """Where the app keeps its copy, under ~/Downloads/Ombro: `<job id>/…` for a job's files."""
    for name, root in roots(s):
        if path == root or path.is_relative_to(root):
            rel = path.relative_to(root).as_posix()
            return rel if name == "jobs" else f"{name}/{rel}"
    return f"Drafts/{path.parent.name}-{path.name}"


def readable(s: "Serve", raw: str | None) -> Path:
    if not raw or not raw.strip():
        raise Refused(400, "`path` is needed")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = s.home.at(raw)  # jobs/<id>/… as prompts and indexes name it
    path = path.resolve()
    if path in editing_paths(s):
        return path
    for _, root in roots(s):
        if path == root or path.is_relative_to(root):
            return path
    raise Refused(403, "Ombro only shares files from jobs, chats and shared folders")


def _send(s: "Serve", raw: str | None) -> Response:
    path = readable(s, raw)
    if not path.is_file():
        raise Refused(404, f"{path.name} isn't there any more")
    rel = save_as(s, path)
    return FileResponse(path, filename=path.name, headers={"X-Ombro-Save-As": urllib.parse.quote(rel)})


def _list(s: "Serve", raw: str | None) -> dict:
    folder = readable(s, raw)
    if not folder.is_dir():
        raise Refused(404, f"{folder.name} isn't a folder")
    files = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = Path(dirpath, name)
            if p.is_symlink():
                continue  # it may point anywhere; never follow it out
            files.append({"path": str(p), "save_as": save_as(s, p.resolve()), "size": p.stat().st_size})
            if len(files) > MAX_LIST:
                raise Refused(413, f"{folder.name} has more than {MAX_LIST} files; save them on the server instead")
    return {"folder": str(folder), "save_as": save_as(s, folder), "files": files}


def _put(s: "Serve", raw: str | None, data: bytes) -> dict:
    if not raw:
        raise Refused(400, "`path` is needed")
    path = Path(raw).expanduser().resolve()
    if path not in editing_paths(s):
        raise Refused(403, "only a draft out for editing can be written back")
    tmp = path.with_name(f".{path.name}.upload")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return {"path": str(path), "size": len(data)}


def file_routes(s: "Serve") -> list[Route]:
    async def get_file(r: Request) -> Response:
        try:
            return await asyncio.to_thread(_send, s, r.query_params.get("path"))
        except Refused as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)

    async def list_files(r: Request) -> Response:
        try:
            return JSONResponse(await asyncio.to_thread(_list, s, r.query_params.get("path")))
        except Refused as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)

    async def put_file(r: Request) -> Response:
        data = await r.body()
        if len(data) > MAX_PUT_BYTES:
            return JSONResponse({"error": "that file is too big to send back"}, status_code=413)
        try:
            return JSONResponse(await asyncio.to_thread(_put, s, r.query_params.get("path"), data))
        except Refused as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)

    return [Route("/files", get_file, methods=["GET"]), Route("/files", put_file, methods=["PUT"]),
            Route("/files/list", list_files, methods=["GET"])]
