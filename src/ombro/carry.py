"""What a job carries to other sites through the browser, once it has read private material.

A job (or a chat) that has only read the web is not held back here at all. Once it has read
private material (a shared folder, memory, or anything through an MCP, API or Composio tool:
Notion, email, a spreadsheet) every site it types into or opens is someone else's, so:

- a budget of text typed into, or submitted to, each site (BUDGET characters a site, per job);
- an address it opens may have no more than PATH_MAX characters of path, as well as the query
  cap it always has, and no long encoded-looking segment (base64, hex);
- before typing, submitting or opening an address, the text is checked against the private
  material the job has read: a long run of its words, or an encoded copy, is held back, and
  the role is told why.

Only then does the user decide: a held form waits in Approvals (in chat, the user is asked).
Text typed and addresses opened are recorded, for the report.

The material is the same the engine already marks as read (its taint);
this keeps the private part of it, as word runs, to check against.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import threading
import unicodedata
from typing import Any
from urllib.parse import parse_qsl, unquote, unquote_plus, urlsplit

from ombro import material

BUDGET = 300  # characters typed into or submitted to one site, per job, once it holds private material
PATH_MAX = 120  # characters of an address's path, once the job holds private material
ENCODED_MIN = 32  # an unbroken encoded-looking segment this long, in an address, is refused
RUN_WORDS = 6  # this many words in a row from private material is a run
TOKEN_MIN = 12  # a single unspaced token this long (an id, a key, an account number) counts on its own
KEEP_MAX = 4_000_000  # characters of private material kept to check against, per job

# Native tools whose results are the user's own material. Every outward tool (MCP, API, Composio) is too.
PRIVATE_TOOLS = {"memory.search", "memory.read", "memory.list", "folders.read", "folders.list"}
WORD = re.compile(r"[^\W_]+")
B64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
HEX = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")
SEGMENT = re.compile(r"[A-Za-z0-9+/=_.~-]{%d,}" % ENCODED_MIN)
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "ltd", "plc", "me", "edu", "nhs", "sch", "police", "mod"}


def is_private(tool: str, *, outward: bool = False, args: dict | None = None, shared: bool = False) -> bool:
    """Whether a tool's result is the user's own material, not the web's."""
    if tool.startswith(("browser.", "web.")):
        return False
    if outward or tool in PRIVATE_TOOLS:
        return True
    path = str((args or {}).get("path") or "") if isinstance(args, dict) else ""
    if tool in ("workspace.read", "workspace.list") and path.lstrip("./").startswith("shared/"):
        return True
    return tool == "code.run" and shared  # a script may print what's in a shared folder


def site_of(url: str) -> str:
    """The site an address belongs to, as a budget counts it: shop.example.co.uk -> example.co.uk."""
    host = (urlsplit(url).hostname or url).lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _words(text: str) -> list[str]:
    return WORD.findall(unicodedata.normalize("NFKC", text).casefold())


def _long_token(w: str) -> bool:
    return len(w) >= TOKEN_MIN and (any(c.isdigit() for c in w) or len(w) >= 20)


def looks_encoded(segment: str) -> bool:
    """A long unbroken run that reads as base64 or hex rather than words: a slug's words are
    broken by - or _, and an encoding mixes digits and letters (or both cases) throughout."""
    for piece in re.split(r"[-_.~]", segment):
        if len(piece) < 24:
            continue
        digits = any(c.isdigit() for c in piece)
        letters = any(c.isalpha() for c in piece)
        upper = sum(c.isupper() for c in piece)
        if (digits and letters) or "+" in piece or "/" in piece or 0.2 <= upper / len(piece) <= 0.8:
            return True
        if re.fullmatch(r"[0-9a-fA-F]+", piece) and len(piece) >= ENCODED_MIN:
            return True
    return False


def _decodings(text: str, depth: int = 2) -> list[str]:
    """The text as it is, percent-decoded, and every base64 or hex run in it decoded, a level or two deep."""
    out = [text]
    for t in {unquote(text), unquote_plus(text)} - {text}:
        out.append(t)
    if depth:
        for t in list(out):
            for m in B64.finditer(t):
                raw = m.group(0)
                for alt in (None, b"-_"):
                    try:
                        data = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=alt, validate=alt is None)
                    except (binascii.Error, ValueError):
                        continue
                    s = data.decode("utf-8", errors="ignore")
                    if s and sum(c.isprintable() or c.isspace() for c in s) >= 0.8 * len(s):
                        out += _decodings(s, depth - 1)
            for m in HEX.finditer(t):
                s = bytes.fromhex(m.group(0)).decode("utf-8", errors="ignore")
                if s:
                    out += _decodings(s, depth - 1)
    return out


class Carry:
    """One per job (and one per chat): the private material it has read, and what it has typed where."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sources: list[str] = []
        self._runs: dict[int, int] = {}  # hash of RUN_WORDS words in a row -> index in sources
        self._tokens: dict[str, int] = {}
        self._kept = 0
        self.typed: dict[str, int] = {}  # site -> characters typed or submitted there

    @property
    def private(self) -> bool:
        return bool(self.sources)

    def saw(self, source: str, value: Any) -> None:
        """A tool's result that is private material: kept, as word runs, to check against."""
        text = material.strings(value)
        with self._lock:
            if source not in self.sources:
                self.sources.append(source)
            i = self.sources.index(source)
            if self._kept >= KEEP_MAX or not text:
                return
            text = text[:KEEP_MAX - self._kept]
            self._kept += len(text)
            words = _words(text)
            for n in range(max(0, len(words) - RUN_WORDS + 1)):
                self._runs.setdefault(hash(" ".join(words[n:n + RUN_WORDS])), i)
            for w in words:
                if _long_token(w):
                    self._tokens.setdefault(w, i)

    def what(self) -> str:
        names = self.sources[:3]
        more = len(self.sources) - len(names)
        return ", ".join(names) + (f" and {more} more" if more > 0 else "")

    # -- checks ---------------------------------------------------------------

    def found(self, text: str) -> str | None:
        """Why `text` carries private material the job has read, or None."""
        for n, t in enumerate(_decodings(text)):
            how = "an encoded copy of" if n and t not in (unquote(text), unquote_plus(text)) else "words from"
            words = _words(t)
            for k in range(max(0, len(words) - RUN_WORDS + 1)):
                i = self._runs.get(hash(" ".join(words[k:k + RUN_WORDS])))
                if i is not None:
                    return f"it carries {how} {self.sources[i]}"
            for w in words:
                if _long_token(w) and w in self._tokens:
                    return f"it carries {how} {self.sources[self._tokens[w]]}"
        return None

    def typing(self, where: str, text: str, count: int | None = None) -> list[str]:
        """Why typing or submitting `text` on site `where` is held back, or []. `count` is what it adds
        to the site's budget (all of `text` unless said: a form's fields typed earlier are counted
        already). Nothing when the job has read no private material."""
        if not self.private or not text:
            return []
        why = []
        problem = self.found(text)
        if problem:
            why.append(problem)
        used, more = self.typed.get(where, 0), len(text) if count is None else count
        if more and used + more > BUDGET:
            why.append(f"it would take what this job has typed into {where} to {used + more} characters, "
                       f"over the {BUDGET} allowed without asking")
        return why

    def spend(self, where: str, n: int) -> None:
        with self._lock:
            self.typed[where] = self.typed.get(where, 0) + max(0, n)

    def address(self, url: str) -> list[str]:
        """Why opening `url` is held back, or []. Nothing when the job has read no private material."""
        if not self.private:
            return []
        parts = urlsplit(url)
        why = []
        if len(parts.path) > PATH_MAX:
            why.append(f"its path is {len(parts.path):,} characters, over the {PATH_MAX} allowed")
        pieces = [unquote(p) for p in parts.path.split("/")]
        pieces += [v for kv in parse_qsl(parts.query, keep_blank_values=True) for v in kv]
        pieces += [unquote(parts.fragment)] if parts.fragment else []
        coded = [m.group(0) for p in pieces for m in SEGMENT.finditer(p) if looks_encoded(m.group(0))]
        if coded:
            why.append(f"it has a long encoded-looking part ({coded[0][:24]}…)")
        problem = self.found(" ".join([unquote_plus(parts.path), unquote_plus(parts.query),
                                       unquote_plus(parts.fragment)]))
        if problem:
            why.append(problem)
        return why

    def held(self, what: str, why: list[str]) -> str:
        """What the role is told when something is held back."""
        return (f"{what}: this job has read private material ({self.what()}), and " + "; ".join(why) + ". Ombro "
                "holds this back so private material is not carried to another site.")


def ask_or_hold(ctx: Any, what: str, items: list[str], why: list[str], **fields: Any) -> bool:
    """When a check is hit: in chat the user is asked (True on their yes); in a job the action is
    held, recorded for the report, and False returned."""
    ask = getattr(ctx, "ask", None)
    if ask is not None and ask(what, [*items, "Why Ombro asks: " + "; ".join(why)]):
        ctx.record("carry_allowed", f"{what}, with the user's yes", why=why, **fields)
        return True
    ctx.record("carry_held", f"{what}: {'; '.join(why)}", why=why, **fields)
    return False
