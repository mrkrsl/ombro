"""Files you attach to a chat or a brief: dragged onto the window, chosen with the paperclip, or a
path dragged into the terminal.

Every attached file is kept under the Files folder (`~/Ombro/Files/<date>/`), so it outlives the
chat. What the Lead gets depends on the kind of file:

- text (`.txt`, `.md`, `.csv`, `.json`, code and the like): its text, in the message, up to a cap;
- PDF and Word (`.pdf`, `.docx`): the text read out of them, the same way;
- anything else (images, spreadsheets, archives): a note that it was attached, and where it is.

A brief or a live job made from that chat is given the files: they are copied into its
`workspace/inputs/`, and the brief lists them, so the crew reads them with its workspace tools
(which read PDF and Word as text too).
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from ombro.home import Home

MAX_BYTES = 25 * 1024 * 1024  # one attached file
TEXT_CHARS = 40_000  # of one file's text, in the message to the Lead
INPUTS = "inputs"  # in a job's workspace

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".xml", ".html",
    ".htm", ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".rb", ".go", ".rs", ".java", ".kt", ".swift", ".c",
    ".h", ".cpp", ".hpp", ".cs", ".php", ".sh", ".zsh", ".bash", ".sql", ".r", ".ini", ".cfg", ".conf", ".log",
    ".tex", ".rst", ".org", ".srt", ".vtt", ".ics", ".vcf", ".env.example",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tiff", ".bmp", ".svg"}


class AttachError(Exception):
    """The file could not be attached; the message says why, for the user."""


@dataclass
class Read:
    """What could be read out of a file: its text (None if it has none), whether it was cut, and its kind."""

    kind: str  # text, pdf, word, image or other
    text: str | None
    cut: bool = False
    problem: str = ""


def files_dir(home: Home) -> Path:
    return home.files / ("Files" if home.separate else "files")


def clean_name(name: str) -> str:
    """A safe file name from what the app or the terminal sent: no folders, no hidden files."""
    name = unicodedata.normalize("NFC", Path(str(name or "").replace("\\", "/")).name).strip()
    name = re.sub(r"[\x00-\x1f/:]", "-", name).lstrip(".").strip()
    return name[:120] or "attached-file"


def save(home: Home, name: str, data: bytes, *, now: datetime | None = None) -> Path:
    """Keep an attached file under Files/<date>/, never over another of the same name."""
    if len(data) > MAX_BYTES:
        raise AttachError(f"{clean_name(name)} is over {MAX_BYTES // (1024 * 1024)} MB, too large to attach")
    folder = files_dir(home) / (now or datetime.now()).strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    name = clean_name(name)
    path, n = folder / name, 2
    while path.exists():
        path = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def save_path(home: Home, source: str | Path) -> Path:
    """A file attached by its path (dragged into the terminal): copied into Files like an upload."""
    src = Path(str(source).strip().strip("'\"")).expanduser()
    if not src.is_file():
        raise AttachError(f"{src} isn't a file Ombro can find")
    if src.stat().st_size > MAX_BYTES:
        raise AttachError(f"{src.name} is over {MAX_BYTES // (1024 * 1024)} MB, too large to attach")
    return save(home, src.name, src.read_bytes())


_DRAGGED = re.compile(r"""'((?:~|/)[^']+)'|"((?:~|/)[^"]+)"|(?<!\S)((?:~/|/)(?:\\.|[^\s])+)""")


def dragged_paths(text: str) -> tuple[str, list[Path]]:
    """Files dragged into the terminal: the paths a terminal types (quoted, or with spaces escaped)
    that name a file here. Returns the message with each path cut to the file's name, and the files."""
    found: list[Path] = []
    spans: list[tuple[int, int]] = []
    for m in _DRAGGED.finditer(text):
        raw = m.group(1) or m.group(2) or re.sub(r"\\(.)", r"\1", m.group(3))
        path = Path(raw).expanduser()
        try:
            if path.is_file():
                found.append(path)
                spans.append(m.span())
        except OSError:
            continue
    for (start, end), path in reversed(list(zip(spans, found))):
        text = text[:start] + path.name + text[end:]  # the message names the file, not where it was
    return " ".join(text.split()), found


SEEABLE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
           ".webp": "image/webp"}  # what the providers take as an image in a message
IMAGE_MAX_BYTES = 5 * 1024 * 1024  # Anthropic's limit for one image, the lowest of them


def seeable(path: Path) -> bool:
    """An image a model that sees can be shown: a kind every provider takes, not too large."""
    return path.suffix.lower() in SEEABLE and path.is_file() and path.stat().st_size <= IMAGE_MAX_BYTES


def data_url(path: Path) -> str:
    import base64

    return f"data:{SEEABLE[path.suffix.lower()]};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def with_images(messages: list[dict], images: list[Path]) -> list[dict]:
    """The messages with these images added to the last of the user's, as OpenAI's image parts
    (the Anthropic and Responses clients turn them into their own)."""
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user" and isinstance(out[i].get("content"), str):
            parts = [{"type": "text", "text": out[i]["content"]}]
            parts += [{"type": "image_url", "image_url": {"url": data_url(p)}} for p in images if p.is_file()]
            out[i] = {**out[i], "content": parts}
            break
    return out


def kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "word"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in TEXT_SUFFIXES or not suffix:
        return "text"
    return "other"


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as e:  # noqa: BLE001 - pypdf raises several kinds
            raise AttachError("it is protected by a password") from e
    pages = []
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"[Page {i}]\n{text}")
    return "\n\n".join(pages)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(path: Path) -> str:
    """The text of a Word document, paragraph by paragraph, tables as rows of cells split by |."""
    try:
        with zipfile.ZipFile(path) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise AttachError("it isn't a Word document Ombro can read") from e

    def para(p) -> str:
        out = []
        for el in p.iter():
            if el.tag == f"{W}t" and el.text:
                out.append(el.text)
            elif el.tag == f"{W}tab":
                out.append("\t")
            elif el.tag in (f"{W}br", f"{W}cr"):
                out.append("\n")
        return "".join(out)

    body = root.find(f"{W}body")
    lines: list[str] = []
    for block in list(body) if body is not None else []:
        if block.tag == f"{W}p":
            lines.append(para(block))
        elif block.tag == f"{W}tbl":
            for row in block.iter(f"{W}tr"):
                cells = [" ".join(para(p) for p in cell.iter(f"{W}p")).strip() for cell in row.iter(f"{W}tc")]
                lines.append(" | ".join(cells))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def plain_text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def read(path: Path, limit: int = TEXT_CHARS) -> Read:
    """The text of an attached file, for the Lead, cut at `limit` characters."""
    kind = kind_of(path)
    try:
        if kind == "pdf":
            text = pdf_text(path)
            problem = "" if text.strip() else "it has no text to read (a scan, perhaps)"
        elif kind == "word":
            text, problem = docx_text(path), ""
        elif kind == "text":
            text = plain_text(path)
            problem = "" if text is not None else "it isn't a text file"
            if text is None:
                kind = "other"
        else:
            return Read(kind, None)
    except AttachError as e:
        return Read(kind, None, problem=str(e))
    except Exception as e:  # noqa: BLE001 - a damaged file is reported, never a crash
        return Read(kind, None, problem=f"it couldn't be read ({type(e).__name__})")
    if not text or not text.strip():
        return Read(kind, None, problem=problem)
    return Read(kind, text[:limit], cut=len(text) > limit)


def size_text(n: int) -> str:
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def give_to_job(job_dir: Path, inputs: list[str]) -> list[str]:
    """Copy a brief's attached files into the job's workspace/inputs/. Returns the names given;
    a file that has gone is left out."""
    import shutil

    given = []
    folder = job_dir / "workspace" / INPUTS
    for raw in inputs:
        src = Path(str(raw)).expanduser()
        if not src.is_file():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        dest, n = folder / src.name, 2
        while dest.exists():
            dest = folder / f"{src.stem}-{n}{src.suffix}"
            n += 1
        shutil.copy2(src, dest)
        given.append(dest.name)
    return given


INPUTS_HEADING = "## Files given with this brief"


def without_inputs_section(body: str) -> str:
    """A brief without the list of files a job added to it (a restart or a kept brief lists them again)."""
    at = body.find("\n\n" + INPUTS_HEADING)
    return body[:at].rstrip() + "\n" if at >= 0 else body


def inputs_section(names: list[str]) -> str:
    """The lines added to a job's brief naming the files it was given."""
    listed = "\n".join(f"- workspace/{INPUTS}/{n}" for n in names)
    return (f"\n\n{INPUTS_HEADING}\n\nThe user attached these; read them with workspace.read "
            f"(PDF and Word files are read as text):\n\n{listed}\n")
