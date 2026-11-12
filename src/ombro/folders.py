"""Shared folders: places outside a run that the user has granted, by name.

A run's own workspace/ and scratch/ need no grant. Everything else on the
machine does. Grants live in folders.md, are made only by the user (a model
can suggest a folder, but the harness asks and writes), and apply to every
crew; a spec names the ones its run uses and may lower write to read.

What can be granted is a fixed rule: a folder inside the user's home folder,
or on a mounted drive or server share, never the root, system folders, the
home folder itself, a hidden folder, ~/Library, or anything holding or held
by the Ombro home.

Before a run that can write to a shared folder starts, the folder is cloned
into the run's backup/ where the disk allows it (APFS on macOS, reflinks on
Linux). The run's changes are worked out by comparing the folder before and
after.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ombro import mdfile
from ombro.home import Home
from ombro.settings import SetupError

ACCESS = ("read", "write")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")
MANIFEST_MAX_FILES = 20_000  # a folder with more files is not compared before and after
CHANGES_LISTED = 200

FOLDERS_BODY = """\
Folders on this machine that runs may use, by name. A run's own workspace/
and scratch/ need no grant; everything else does. Grants apply to every
crew, and a spec names the ones its run uses (`folders: [brand, photos]`),
and may lower write to read (`folders: {photos: read}`).

- access: read (roles can read and list it) or write (they can also change,
  add and remove files, as you could). Changes are listed in the report.
- Before a run that can write to a folder starts, Ombro clones it into the
  run's backup/ folder if the disk allows (same disk as the Ombro home, on
  APFS or a file system with reflinks). Server shares usually cannot be
  cloned; their own backups and version history are the remedy.
- declined: folders you said no to when Ombro offered to share them. Ombro
  does not ask about them again.

Only folders in your home folder, or on mounted drives and server shares
(/Volumes/... on macOS; /mnt, /media, /run/media on Linux), can be shared.
Never the home folder itself, hidden folders, ~/Library, or the Ombro home.
Manage grants with `ombro folders`.
"""


class FolderError(Exception):
    """A folder cannot be shared, or a folders.md entry is wrong. Shown as it is."""


@dataclass(frozen=True)
class Grant:
    name: str
    path: str  # as written in folders.md
    access: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "access": self.access}


# ---------------------------------------------------------------------------
# What can be granted


def mount_roots() -> list[Path]:
    if platform.system() == "Darwin":
        return [Path("/Volumes")]
    return [Path("/mnt"), Path("/media"), Path("/run/media")]


def user_home() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def expand(path: str | Path) -> Path:
    return Path(os.path.expanduser(str(path)))


def check_place(path: str | Path, home: Home, *, must_exist: bool = True) -> Path:
    """The real path of a folder that may be shared, or FolderError saying why not.
    With must_exist=False the folder need not exist yet (a placement's parent)."""
    raw = expand(path)
    if not raw.is_absolute():
        raise FolderError(f"{path} is not a full path; give one such as ~/Documents/Brand")
    real = raw.resolve()
    if must_exist and not real.is_dir():
        raise FolderError(f"{path} is not a folder" + ("" if real.exists() else " (it does not exist)"))
    if real == Path("/"):
        raise FolderError("the root folder can never be shared")
    ombro = home.root.resolve()
    if real == ombro or real.is_relative_to(ombro):
        raise FolderError(f"{path} is inside Ombro's own home, which holds its settings and secrets")
    if ombro.is_relative_to(real):
        raise FolderError(f"{path} contains Ombro's own home, which holds its settings and secrets")
    me = user_home()
    base = None
    if real != me and real.is_relative_to(me):
        base = me
    else:
        for root in mount_roots():
            r = root.resolve() if root.exists() else root
            if real != r and real.is_relative_to(r):
                base = r
                break
    if base is None:
        if real == me:
            raise FolderError("your whole home folder cannot be shared; share a folder inside it")
        where = ", ".join(str(r) for r in mount_roots())
        raise FolderError(f"{path} is not in your home folder or on a mounted drive ({where}), "
                          "so it cannot be shared")
    parts = real.relative_to(base).parts
    if any(p.startswith(".") for p in parts):
        raise FolderError(f"{path} is a hidden folder or inside one, so it cannot be shared")
    if base == me and parts and parts[0] == "Library":
        raise FolderError(f"{path} is in ~/Library, which holds your applications' data, so it cannot be shared")
    return real


# ---------------------------------------------------------------------------
# folders.md


def load(home: Home) -> tuple[dict[str, Grant], list[str]]:
    """Grants by name, and the folders declined. Raises SetupError if the file is wrong."""
    if not home.folders.is_file():
        return {}, []
    try:
        meta, _ = mdfile.read(home.folders)
    except mdfile.FrontMatterError as e:
        raise SetupError(str(e)) from None
    raw = meta.get("folders") or {}
    if not isinstance(raw, dict):
        raise SetupError(f"{home.folders}: `folders` must be a mapping of names")
    grants: dict[str, Grant] = {}
    for name, cfg in raw.items():
        name = str(name)
        if not NAME.match(name):
            raise SetupError(f"{home.folders}: folder name {name!r} must use lower-case letters, digits, - and _")
        if isinstance(cfg, str):
            cfg = {"path": cfg}
        if not isinstance(cfg, dict) or not cfg.get("path"):
            raise SetupError(f"{home.folders}: {name} needs a path, e.g. {{path: ~/Documents/Brand, access: read}}")
        access = str(cfg.get("access") or "read")
        if access not in ACCESS:
            raise SetupError(f"{home.folders}: access for {name} must be read or write")
        bad = set(cfg) - {"path", "access"}
        if bad:
            raise SetupError(f"{home.folders}: {name} has unknown setting(s): {', '.join(sorted(bad))}")
        grants[name] = Grant(name, str(cfg["path"]), access)
    declined = meta.get("declined") or []
    if not isinstance(declined, list):
        raise SetupError(f"{home.folders}: `declined` must be a list of paths")
    return grants, [str(d) for d in declined]


def save(home: Home, grants: dict[str, Grant], declined: list[str]) -> None:
    meta: dict[str, Any] = {"folders": {n: g.as_dict() for n, g in grants.items()}}
    if declined:
        meta["declined"] = declined
    body = mdfile.read(home.folders)[1] if home.folders.is_file() else FOLDERS_BODY
    mdfile.write(home.folders, meta, body or FOLDERS_BODY)


def _same(a: str | Path, b: str | Path) -> bool:
    try:
        return expand(a).resolve() == expand(b).resolve()
    except OSError:
        return False


def find(grants: dict[str, Grant], path: str | Path) -> Grant | None:
    for g in grants.values():
        if _same(g.path, path):
            return g
    return None


def suggest_name(path: str | Path, taken: set[str] = frozenset()) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", expand(path).name.lower()).strip("-")[:24] or "folder"
    if not base[0].isalnum():
        base = "f" + base
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def written_path(path: str | Path) -> Path:
    """A grant's path as written, made absolute without following any symlink.
    `~/x` is taken from the real home folder, as display_path writes it."""
    text = str(path)
    if text == "~" or text.startswith("~/"):
        return user_home() / text[2:]
    return Path(os.path.abspath(os.path.expanduser(text)))


def display_path(real: Path) -> str:
    """~/x for a folder in the home folder, the full path otherwise."""
    me = user_home()
    return "~/" + real.relative_to(me).as_posix() if real.is_relative_to(me) and real != me else str(real)


def add(home: Home, path: str | Path, name: str, access: str) -> Grant:
    """Grant a folder. Only ever called after the user has said yes."""
    if access not in ACCESS:
        raise FolderError("access must be read or write")
    if not NAME.match(name):
        raise FolderError(f"the name {name!r} must use lower-case letters, digits, - and _ (up to 31)")
    real = check_place(path, home)
    grants, declined = load(home)
    existing = find(grants, real)
    if existing and existing.name != name:
        raise FolderError(f"{display_path(real)} is already shared as {existing.name!r}")
    if name in grants and not _same(grants[name].path, real):
        raise FolderError(f"the name {name!r} is already used for {grants[name].path}")
    grants[name] = Grant(name, display_path(real), access)
    declined = [d for d in declined if not _same(d, real)]
    save(home, grants, declined)
    return grants[name]


def remove(home: Home, name: str) -> Grant:
    grants, declined = load(home)
    if name not in grants:
        raise FolderError(f"no shared folder is named {name!r}")
    g = grants.pop(name)
    save(home, grants, declined)
    return g


def decline(home: Home, path: str | Path) -> None:
    grants, declined = load(home)
    real = expand(path).resolve()
    if not any(_same(d, real) for d in declined):
        declined.append(display_path(real))
    save(home, grants, declined)


def offer_wanted(home: Home, path: str | Path) -> bool:
    """Whether to offer to share this folder: it could be shared, it isn't, and the
    user hasn't declined it before."""
    try:
        real = check_place(path, home)
        grants, declined = load(home)
    except (FolderError, SetupError):
        return False
    if any(real == Path(g.path).expanduser().resolve() or real.is_relative_to(expand(g.path).resolve())
           for g in grants.values()):
        return False
    return not any(_same(d, real) for d in declined)


# ---------------------------------------------------------------------------
# A run's folders


def parse_spec(value: Any) -> dict[str, str | None]:
    """A spec's `folders:` as {name: access or None}. A list names grants; a mapping
    may lower write to read."""
    if value in (None, "", [], {}):
        return {}
    if isinstance(value, str):
        value = [value]
    if isinstance(value, list):
        out: dict[str, str | None] = {}
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise SetupError("`folders:` in a spec lists the names of shared folders, e.g. [brand, photos]")
            out[item.strip()] = None
        return out
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if v not in (None, "read", "write"):
                raise SetupError(f"`folders:` in a spec: {k} may be read or write, not {v!r}")
            out[str(k)] = v
        return out
    raise SetupError("`folders:` in a spec must be a list of names, or a mapping of names to read or write")


def for_run(home: Home, spec_meta: dict[str, Any] | None) -> tuple[list[dict[str, str]], list[str]]:
    """The folders a run gets, fixed at its start, and why any it named were left out."""
    wanted = parse_spec((spec_meta or {}).get("folders"))
    if not wanted:
        return [], []
    try:
        grants, _ = load(home)
    except SetupError as e:
        return [], [f"folders.md could not be read, so no shared folder was given: {e}"]
    out: list[dict[str, str]] = []
    left: list[str] = []
    for name, access in wanted.items():
        g = grants.get(name)
        if g is None:
            left.append(f"the spec names folder {name!r}, which is not shared (`ombro folders add`), so it was "
                        "left out")
            continue
        try:
            real = check_place(g.path, home)
        except FolderError as e:
            left.append(f"folder {name!r} was left out: {e}")
            continue
        if real != written_path(g.path):
            # Somewhere on the way the path now passes through a symlink (perhaps one a
            # script made), so it would lead to a different folder from the one granted.
            left.append(f"folder {name!r} was left out: {g.path} now leads to {display_path(real)} through a "
                        "symlink. Check it, then share the folder again with `ombro folders add`")
            continue
        final = "read" if "read" in (access, g.access) else "write"
        out.append({"name": name, "path": str(real), "access": final})
    return out, left


def granted_names(home: Home) -> list[str]:
    try:
        return sorted(load(home)[0])
    except SetupError:
        return []


# ---------------------------------------------------------------------------
# Backups


def backup_hint(path: str | Path, home: Home) -> str:
    """Whether a run could clone this folder before writing, as a short phrase."""
    try:
        same = expand(path).resolve().stat().st_dev == home.root.resolve().stat().st_dev
    except OSError:
        return "unknown (the folder cannot be reached)"
    if not same:
        return "no: it is on a different disk from Ombro's home, so no backup can be cloned"
    if platform.system() == "Darwin":
        return "yes: cloned before each run that can write to it"
    return "if the file system supports reflinks (btrfs, xfs); otherwise no"


def _clonefile(src: Path, dst: Path) -> None:
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    fn = libc.clonefile
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    if fn(os.fsencode(src), os.fsencode(dst), 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def clone(src: Path, dst: Path) -> None:
    """A copy-on-write copy of src at dst. Raises OSError if the disk cannot do it."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Darwin":
        _clonefile(src, dst)
        return
    proc = subprocess.run(["cp", "-a", "--reflink=always", str(src), str(dst)], capture_output=True, text=True)
    if proc.returncode != 0:
        shutil.rmtree(dst, ignore_errors=True)
        raise OSError(proc.stderr.strip() or "cp could not clone the folder")


def backup(run_dir: Path, folder: dict[str, str]) -> dict[str, Any]:
    """Clone one writable folder into run_dir/backup/<name>. Returns what to record."""
    dst = Path(run_dir) / "backup" / folder["name"]
    try:
        clone(Path(folder["path"]), dst)
    except OSError as e:
        import errno

        why = ("it is on a different disk from Ombro's home" if e.errno == errno.EXDEV
               else "the disk cannot clone files" if e.errno in (errno.ENOTSUP, errno.EOPNOTSUPP)
               else str(e.strerror or e))
        return {"backup": None, "backup_note": f"no backup was taken: {why}"}
    return {"backup": str(dst), "backup_note": "cloned before the run started"}


# ---------------------------------------------------------------------------
# Before and after


def manifest(root: Path, skip: tuple[str, ...] = ()) -> dict[str, tuple[int, int]] | None:
    """Every file under root (not following symlinks) with its size and modified
    time, or None if there are more than MANIFEST_MAX_FILES."""
    out: dict[str, tuple[int, int]] = {}
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return out  # a root that has become a symlink is never followed
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        if rel_dir != "." and any(rel_dir == s or rel_dir.startswith(s + "/") for s in skip):
            dirnames[:] = []
            continue
        for name in filenames:
            p = here / name
            try:
                st = p.lstat()
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            out[rel] = (st.st_size, st.st_mtime_ns)
            if len(out) > MANIFEST_MAX_FILES:
                return None
    return out


def diff(before: dict[str, tuple[int, int]] | None, after: dict[str, tuple[int, int]] | None) -> dict[str, Any]:
    if before is None or after is None:
        return {"too_large": True, "created": [], "changed": [], "removed": []}
    created = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return {"created": created, "changed": changed, "removed": removed}


def count(d: dict[str, Any]) -> int:
    return len(d.get("created") or []) + len(d.get("changed") or []) + len(d.get("removed") or [])
