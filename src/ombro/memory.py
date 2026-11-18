"""Crew memory: the job index, standing notes, and searching past reports.

Cost per job stays constant however long Ombro has been in use: a job's Lead
is given notes.md and the most recent index entries, each within a cap in
characters, and nothing else. Everything older is reached by search.

- index.md: one line per job, written by the harness, never the model.
- notes.md: two parts. "From you" is the user's and is never changed by the
  model. "From jobs" is the Lead's, rewritten at the end of a job. Both count
  towards one cap, and a write over it is refused.
- search: keyword scoring over the sections of past reports. Chat transcripts
  are never searched or read.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ombro import mdfile, jobs
from ombro.home import NOTES_BODY, Home
from ombro.settings import Limits

FROM_YOU = "## From you"
FROM_JOBS = "## From jobs"
OLD_FROM_JOBS = "## From runs"  # notes written before jobs had that name
NOTES_TITLE = "# Notes\n\n"
REMEMBER_MAX_CHARS = 500


class NotesError(ValueError):
    """A notes write the harness refuses. The message says why."""


def notes_path(home: Home, crew: str) -> Path:
    return home.crew_dir(crew) / "memory" / "notes.md"


def index_path(home: Home, crew: str) -> Path:
    return home.crew_dir(crew) / "memory" / "index.md"


def _rel(home: Home, path: Path) -> str:
    return home.rel(path)


# ---------------------------------------------------------------------------
# notes.md


@dataclass
class Notes:
    """notes.md in its parts. `from_you` is kept exactly as found, so a rewrite
    of the Lead's part can never change a character of the user's."""

    preamble: str = NOTES_TITLE
    from_you: str = "\n"
    from_jobs: str = ""

    def render(self) -> str:
        you = self.from_you
        if you and not you.endswith("\n"):
            you += "\n"
        return f"{self.preamble}{FROM_YOU}\n{you}{FROM_JOBS}\n{self.from_jobs}"

    @property
    def size(self) -> int:
        return len(self.render())

    def your_entries(self) -> list[str]:
        return [ln for ln in self.from_you.splitlines() if ln.strip()]

    def job_lines(self) -> list[str]:
        return [ln.strip() for ln in self.from_jobs.splitlines() if ln.strip()]


def parse_notes(text: str) -> Notes:
    """Split notes.md into its parts. A file whose headings are missing or out
    of order (say, after a hand edit) is treated as all the user's own."""
    lines = text.splitlines(keepends=True)
    you = [i for i, ln in enumerate(lines) if ln.strip().lower() == FROM_YOU.lower()]
    job = [i for i, ln in enumerate(lines) if ln.strip().lower() in (FROM_JOBS.lower(), OLD_FROM_JOBS.lower())]
    if len(you) == 1 and len(job) == 1 and you[0] < job[0]:
        y, r = you[0], job[0]
        return Notes("".join(lines[:y]), "".join(lines[y + 1 : r]), "".join(lines[r + 1 :]))
    body = text.strip("\n")
    if body.startswith("# Notes"):
        body = body[len("# Notes") :].strip("\n")
    return Notes(NOTES_TITLE, ("\n" + body + "\n\n") if body else "\n", "")


def read_notes(home: Home, crew: str) -> Notes:
    path = notes_path(home, crew)
    if not path.is_file():
        return parse_notes(NOTES_BODY)
    return parse_notes(path.read_text(encoding="utf-8"))


def _write_notes(home: Home, crew: str, notes: Notes) -> None:
    from ombro.interview import keep_previous

    path = notes_path(home, crew)
    keep_previous(path)
    mdfile.write_atomic(path, notes.render())


def clean_from_jobs(text: str) -> str:
    """The Lead's part as it is written: no headings, ends with one newline."""
    text = str(text or "").replace("\r\n", "\n").strip("\n")
    for ln in text.splitlines():
        if re.match(r"^\s{0,3}#{1,6}\s", ln) or ln.strip().lower() in ("from you", "from jobs"):
            raise NotesError(
                "headings are not allowed in your part of notes.md; write lessons as a Markdown list. "
                "The harness keeps the headings and the user's part itself"
            )
    return ("\n" + text.strip() + "\n") if text.strip() else ""


def with_from_jobs(notes: Notes, text: str) -> Notes:
    return Notes(notes.preamble, notes.from_you, clean_from_jobs(text))


def replace_from_jobs(home: Home, crew: str, text: str, cap: int) -> tuple[Notes, Notes]:
    """Replace the Lead's part. Returns (old, new). Raises NotesError over the cap."""
    old = read_notes(home, crew)
    new = with_from_jobs(old, text)
    if new.size > cap:
        room = cap - with_from_jobs(old, "").size
        raise NotesError(
            f"refused: notes.md would be {new.size:,} characters, over the cap of {cap:,}. "
            f"The user's part and the headings take {cap - room:,}, so your part must be at most "
            f"{max(room, 0):,} characters (yours was {len(new.from_jobs):,}). Condense it and write again"
        )
    _write_notes(home, crew, new)
    return old, new


def notes_diff(old: Notes, new: Notes) -> tuple[list[str], list[str]]:
    """(added, removed) lines of the Lead's part, worked out by the harness."""
    before, after = old.job_lines(), new.job_lines()
    added = [ln for ln in after if ln not in before]
    removed = [ln for ln in before if ln not in after]
    return added, removed


def remember_entry(text: str, today: date | None = None) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > REMEMBER_MAX_CHARS:
        text = text[: REMEMBER_MAX_CHARS - 1] + "…"
    return f"- {(today or date.today()).isoformat()}: {text}"


def with_your_entry(notes: Notes, entry: str) -> Notes:
    existing = notes.from_you.strip("\n")
    you = "\n" + (existing + "\n" if existing else "") + entry + "\n\n"
    return Notes(notes.preamble, you, notes.from_jobs)


def without_your_entries(notes: Notes, numbers: list[int]) -> Notes:
    """Drop the user's own entries by number (1-based), at the user's request."""
    entries = notes.your_entries()
    drop = {entries[n - 1] for n in numbers if 1 <= n <= len(entries)}
    kept = [ln for ln in notes.from_you.splitlines() if not (ln.strip() and ln in drop)]
    body = "\n".join(kept).strip("\n")
    return Notes(notes.preamble, ("\n" + body + "\n\n") if body else "\n", notes.from_jobs)


def add_your_entry(home: Home, crew: str, entry: str, cap: int) -> Notes:
    """Add a confirmed remember. Raises NotesError (with the sizes) over the cap."""
    new = with_your_entry(read_notes(home, crew), entry)
    if new.size > cap:
        raise NotesError(f"notes.md would be {new.size:,} characters, over the cap of {cap:,}")
    _write_notes(home, crew, new)
    return new


def save_notes(home: Home, crew: str, notes: Notes) -> None:
    """Write notes the user chose (for example after trimming their own entries)."""
    _write_notes(home, crew, notes)


# ---------------------------------------------------------------------------
# index.md


def index_entry(home: Home, job_dir: Path, max_chars: int = 200) -> str:
    meta, _ = jobs.read_job(job_dir)
    when = str(meta.get("finished_at") or meta.get("started_at") or meta.get("created_at") or "")[:10]
    job_id = meta.get("job_id", Path(job_dir).name)
    status = meta.get("status", "?")
    report = _rel(home, Path(job_dir) / "report.md")
    brief = " ".join(str(meta.get("brief") or "(no brief)").split())
    fixed = f"- {when} · {job_id} · {status} ·  · {report}"
    room = max_chars - len(fixed)
    if room < 8:
        line = f"- {when} · {job_id} · {status} · {brief} · {report}"
        return line[: max_chars - 1] + "…"
    if len(brief) > room:
        brief = brief[: room - 1] + "…"
    return f"- {when} · {job_id} · {status} · {brief} · {report}"


@contextmanager
def index_lock(home: Home, crew: str):
    """index.md is read, extended and replaced; jobs and ticks may do that at once."""
    path = index_path(home, crew)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # pragma: no cover - not POSIX
        yield
        return
    with open(path.parent / ".index.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _append_index_line(home: Home, crew: str, key: str, line: str) -> bool:
    """Add one line unless an entry with this key (` · key · `) is already there."""
    with index_lock(home, crew):
        path = index_path(home, crew)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if any(f" · {key} · " in ln for ln in text.splitlines()):
            return False
        if not text.strip():
            text = "# Index\n"
        if not text.endswith("\n"):
            text += "\n"
        mdfile.write_atomic(path, text + line + "\n")
        return True


def add_index_entry(home: Home, job_dir: Path) -> bool:
    """One entry per job in each crew it named, written by the harness.
    Returns False if every one was already there."""
    meta, _ = jobs.read_job(job_dir)
    job_id = meta.get("job_id", Path(job_dir).name)
    limit = (meta.get("limits") or {}).get("memory_index_entry_chars") or 200
    line = index_entry(home, job_dir, int(limit))
    added = False
    for crew in jobs.job_crews(meta):
        if crew in home.crews():
            added = _append_index_line(home, crew, job_id, line) or added
    return added


SLOT = r"\d{4}-\d\d-\d\dT\d\d:\d\d"
SLOT_KEY = re.compile(rf" · schedule:([a-z0-9][a-z0-9_-]*)@({SLOT})(?:\.\.({SLOT}))? · ")


def schedule_key(name: str, first: str, last: str | None = None) -> str:
    return f"schedule:{name}@{first}" + (f"..{last}" if last and last != first else "")


def add_schedule_entry(
    home: Home, crew: str, name: str, slots: list[str], status: str, reason: str, max_chars: int = 200
) -> bool:
    """A scheduled slot that did not start: there is no job directory, so the entry
    names the slot (or the first and last of a gap) and the schedule file."""
    first, last = slots[0], slots[-1]
    if len(slots) == 1:
        what = f"{first[11:]}: {reason}"
    else:
        what = f"{len(slots)} slots: {reason}"
    what = " ".join(what.split())
    key = schedule_key(name, first, last)
    path = f"schedules/{name}.md"
    fixed = f"- {first[:10]} · {key} · {status} ·  · {path}"
    room = max(20, max_chars - len(fixed))
    if len(what) > room:
        what = what[: room - 1] + "…"
    return _append_index_line(home, crew, key, f"- {first[:10]} · {key} · {status} · {what} · {path}")


def noted_slots(home: Home, crew: str) -> list[tuple[str, str, str]]:
    """(schedule, first slot, last slot) for every missed or skipped entry in a crew's index."""
    path = index_path(home, crew)
    if not path.is_file():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = SLOT_KEY.search(ln)
        if m:
            out.append((m.group(1), m.group(2), m.group(3) or m.group(2)))
    return out


def schedule_entries(home: Home, crew: str, name: str) -> list[str]:
    path = index_path(home, crew)
    if not path.is_file():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if (m := SLOT_KEY.search(ln)) and m.group(1) == name]


def recent_index(home: Home, crew: str, n: int, max_chars: int) -> list[str]:
    """The n most recent index entries, newest first, each at most max_chars."""
    path = index_path(home, crew)
    if not path.is_file():
        return []
    entries = [ln.rstrip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
    out = []
    for ln in reversed(entries[-n:] if n else []):
        out.append(ln if len(ln) <= max_chars else ln[: max_chars - 1] + "…")
    return out


def lead_context(home: Home, crews: list[str], limits: Limits) -> tuple[str, str]:
    """What a job's Lead is given from memory: (notes text, index text). Nothing else.
    Each crew's notes, and the most recent index entries across the crews in all."""
    cap = limits.memory_notes_max_chars
    parts = []
    for crew in crews:
        notes = read_notes(home, crew)
        text = notes.render().strip()
        if not notes.your_entries() and not notes.job_lines():
            text = "(empty: no lessons yet)"
        elif len(text) > cap:
            text = text[: cap - 60].rstrip() + f"\n…(cut at the cap of {cap:,} characters)"
        parts.append(f"### Crew {crew}\n\n{text}" if len(crews) > 1 else text)
    n = limits.memory_index_entries
    lines: list[str] = []
    for crew in crews:
        for ln in recent_index(home, crew, n, limits.memory_index_entry_chars):
            if ln not in lines:
                lines.append(ln)
    # Entries start with the date and then the job ID (date and time), so they sort by age.
    lines = sorted(lines, reverse=True)[:n] if n else []
    which = "these crews" if len(crews) > 1 else "this crew"
    return "\n\n".join(parts), ("\n".join(lines) if lines else f"(no earlier jobs in {which})")


# ---------------------------------------------------------------------------
# Search

PASSAGE_CHARS = 800
MAX_RESULTS = 5
SKIP_SECTIONS = ("usage", "cost", "approvals", "outbox", "outward actions", "warnings")
_STOP = set(
    """a about above after again all also am an and any are as at be because been before being below between
    both but by can could did do does doing down during each few for from further had has have having he her
    here hers him his how i if in into is it its just me more most my no nor not now of off on once only or
    other our out over own same she should so some such than that the their them then there these they this
    those through to too under until up very was we were what when where which while who whom why will with
    would you your yours""".split()
)


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]


@dataclass
class Passage:
    crew: str
    job_id: str
    date: str
    status: str
    section: str
    text: str
    path: str


def _chunks(text: str, size: int = PASSAGE_CHARS) -> list[str]:
    out: list[str] = []
    cur = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > size:
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:size])
            para = para[size:]
        if cur and len(cur) + len(para) + 2 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def report_passages(home: Home, crew: str, job_dir: Path) -> list[Passage]:
    """`crew` names the job's crews for the passage, e.g. "marketing, ops"."""
    report = Path(job_dir) / "report.md"
    if not report.is_file():
        return []
    try:
        meta, _ = jobs.read_job(job_dir)
    except (OSError, mdfile.FrontMatterError):
        meta = {}
    when = str(meta.get("finished_at") or meta.get("created_at") or "")[:10]
    status = str(meta.get("status") or "?")
    sections: list[tuple[str, list[str]]] = [("Summary", [])]
    for ln in report.read_text(encoding="utf-8").splitlines():
        if ln.startswith("## "):
            sections.append((ln[3:].strip(), []))
        elif ln.startswith("# "):
            continue
        else:
            sections[-1][1].append(ln[2:] if ln.startswith("> ") else ("" if ln == ">" else ln))
    out = []
    for title, body in sections:
        if title.lower().startswith(SKIP_SECTIONS):
            continue
        for chunk in _chunks("\n".join(body)):
            out.append(Passage(crew, Path(job_dir).name, when, status, title, chunk, _rel(home, report)))
    return out


def search(
    home: Home,
    crews: list[str],
    query: str,
    *,
    job_id: str | None = None,
    exclude: Path | None = None,
    max_chars: int = 4_000,
    max_results: int = MAX_RESULTS,
) -> dict[str, Any]:
    """Score passages of past reports against the query (BM25 over words) and
    return the best, within max_chars of text in all."""
    query = " ".join(str(query or "").split())
    terms = list(dict.fromkeys(tokens(query)))
    if not terms:
        return {"query": query, "results": [], "note": "the query has no searchable words"}
    passages: list[Passage] = []
    reports = 0
    wanted = set(crews)
    for job_dir in jobs.all_jobs(home):
        if exclude is not None and job_dir.resolve() == Path(exclude).resolve():
            continue
        if job_id and not job_dir.name.startswith(job_id):
            continue
        try:
            used = jobs.job_crews(jobs.read_job(job_dir)[0])
        except (OSError, mdfile.FrontMatterError):
            continue
        if not wanted & set(used):
            continue
        found = report_passages(home, ", ".join(used), job_dir)
        reports += bool(found)
        passages += found
    if not passages:
        return {"query": query, "results": [], "reports_searched": 0, "note": "there are no past reports to search"}

    docs = [tokens(f"{p.section} {p.text}") for p in passages]
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in set(d))
    phrase = query.lower() if len(query.split()) > 1 else ""
    scored = []
    for p, d in zip(passages, docs):
        tf = Counter(d)
        score = 0.0
        for t in terms:
            if not tf[t]:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
        if score and phrase and phrase in " ".join(p.text.lower().split()):
            score = score * 1.5 + 1
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda sp: (-sp[0], sp[1].job_id))

    results, used = [], 0
    for _, p in scored[:max_results]:
        room = max_chars - used
        if room < 120:
            break
        text = p.text if len(p.text) <= room else p.text[: room - 1] + "…"
        used += len(text)
        results.append(
            {"crew": p.crew, "job_id": p.job_id, "date": p.date, "outcome": p.status, "section": p.section,
             "report": p.path, "text": text}
        )
    out: dict[str, Any] = {"query": query, "results": results, "reports_searched": reports}
    if not results:
        out["note"] = "no passage in past reports matches; try other words"
    elif len(scored) > len(results):
        out["note"] = f"{len(scored) - len(results)} more passages matched less well; narrow the query or give job_id"
    return out


# ---------------------------------------------------------------------------
# Reading for chat: reports, specs, notes, indexes and deliverables only

READ_DEFAULT_CHARS = 10_000
READ_MAX_CHARS = 15_000


class ReadRefused(ValueError):
    pass


READABLE_TOP = ("crews", "specs", "jobs")


def _allowed(parts: list[str]) -> bool:
    # parts are relative to the home root, e.g. jobs/<id>/report.md
    if len(parts) == 2 and parts[0] == "specs" and parts[1].endswith(".md"):
        return True
    if len(parts) >= 3 and parts[0] == "jobs":
        if len(parts) == 3 and parts[2] in ("report.md", "spec.md"):
            return True
        if parts[2] == "workspace" and len(parts) >= 4:
            return True
        return False
    if len(parts) < 3 or parts[0] != "crews":
        return False
    rest = parts[2:]
    return rest == ["crew.md"] or rest in (["memory", "notes.md"], ["memory", "index.md"])


def resolve_readable(home: Home, path: Any) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise ReadRefused("give a path such as jobs/<job_id>/report.md")
    p = path.strip().replace("\\", "/")
    for base, name in ((str(home.jobs_dir), "jobs"), (str(home.root), "")):
        if p.startswith(base + "/"):
            p = name + p[len(base):]
            break
    parts = [x for x in p.strip("/").split("/") if x not in ("", ".")]
    if ".." in parts or "\x00" in p:
        raise ReadRefused("paths cannot contain `..`")
    if not _allowed(parts):
        raise ReadRefused(
            "only reports, specs, notes, indexes and workspace files can be read "
            "(for example jobs/<job_id>/report.md, specs/<name>.md or crews/<crew>/memory/notes.md); chat "
            "transcripts, journals, keys and settings cannot"
        )
    target = home.at("/".join(parts))
    real = target.resolve()
    if not any(real.is_relative_to(home.at(top).resolve()) for top in READABLE_TOP) or not _allowed(
        list(Path(home.rel(real)).parts) if not Path(home.rel(real)).is_absolute() else []
    ):
        raise ReadRefused(f"{path} leads outside what chat may read (through a symlink), so it is refused")
    return real


def read_file(home: Home, path: Any, offset: int = 0, max_chars: int = READ_DEFAULT_CHARS) -> dict[str, Any]:
    real = resolve_readable(home, path)
    if not real.is_file():
        raise ReadRefused(f"{path} does not exist")
    from ombro import attachments

    if attachments.kind_of(real) in ("pdf", "word"):  # a job's PDF or Word file, read as its text
        got = attachments.read(real, limit=attachments.MAX_BYTES)
        if got.text is None:
            raise ReadRefused(f"{path} can't be read as text" + (f": {got.problem}" if got.problem else ""))
        text = got.text
    else:
        data = real.read_bytes()
        if b"\x00" in data[:4096]:
            raise ReadRefused(f"{path} is not a text file")
        text = data.decode("utf-8", errors="replace")
    max_chars = max(1, min(int(max_chars or READ_DEFAULT_CHARS), READ_MAX_CHARS))
    offset = max(0, int(offset or 0))
    chunk = text[offset : offset + max_chars]
    out: dict[str, Any] = {"path": _rel(home, real), "content": chunk, "total_chars": len(text)}
    if offset + len(chunk) < len(text):
        out["next_offset"] = offset + len(chunk)
    return out


LIST_MAX_JOBS = 40
LIST_MAX_FILES = 100


def listing(home: Home, crew: str | None = None, job_id: str | None = None) -> dict[str, Any]:
    """Crews, a crew's jobs and specs, or one job's files: what chat can then read."""
    from ombro.roles import RoleError, load_role
    from ombro.settings import SetupError, load_limits

    if job_id:
        try:
            job_dir = jobs.find_job(home, job_id)
        except jobs.JobNotFound as e:
            raise ReadRefused(str(e)) from None
        meta, _ = jobs.read_job(job_dir)
        ws = job_dir / "workspace"
        files = sorted(_rel(home, f) for f in ws.rglob("*") if f.is_file() and not f.is_symlink()) \
            if ws.is_dir() and not ws.is_symlink() else []
        return {
            "job_id": job_dir.name,
            "crews": jobs.job_crews(meta),
            "status": meta.get("status"),
            "brief": meta.get("brief"),
            "finished_at": meta.get("finished_at"),
            "report": _rel(home, job_dir / "report.md") if (job_dir / "report.md").is_file() else None,
            "spec": _rel(home, job_dir / "spec.md"),
            "workspace_files": files[:LIST_MAX_FILES],
        }
    if crew:
        if crew not in home.crews():
            raise ReadRefused(f"no crew named {crew!r}; crews: {', '.join(home.crews())}")
        rows = []
        for job_dir in reversed(jobs.all_jobs(home, crew)):
            meta, _ = jobs.read_job(job_dir)
            rows.append({"job_id": job_dir.name, "status": meta.get("status"), "brief": meta.get("brief"),
                         "date": str(meta.get("finished_at") or meta.get("created_at") or "")[:10]})
        specs = []
        for f in sorted(home.specs_dir.glob("*.md")):
            if f.name.endswith(".previous.md"):
                continue
            try:
                named = jobs.spec_crews(home, mdfile.read(f)[0])
            except (OSError, mdfile.FrontMatterError, SetupError):
                continue
            if crew in named:
                specs.append(f.stem)
        return {
            "crew": crew,
            "jobs_newest_first": rows[:LIST_MAX_JOBS],
            "jobs_total": len(rows),
            "specs": specs,
            "notes": _rel(home, notes_path(home, crew)),
            "index": _rel(home, index_path(home, crew)),
        }
    limits = load_limits(home)
    crews = []
    for c in home.crews():
        meta = mdfile.read(home.crew_dir(c) / "crew.md")[0] if (home.crew_dir(c) / "crew.md").is_file() else {}
        roles = []
        rdir = home.roles_dir(c)
        for folder in sorted(p for p in rdir.iterdir() if p.is_dir()) if rdir.is_dir() else []:
            try:
                r = load_role(folder, limits)
                roles.append(f"{r.name}: {r.description}")
            except RoleError:
                roles.append(f"{folder.name}: (could not be loaded)")
        crews.append({"crew": c, "description": meta.get("description", ""), "roles": roles,
                      "jobs": len(jobs.all_jobs(home, c))})
    return {"crews": crews}
