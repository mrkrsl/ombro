"""Composio: the user's own Composio account as a source of tools (Google Docs, Gmail, Sheets…).

The user connects services (toolkits) in their Composio dashboard, and gives Ombro their project's
API key. Ombro keeps the key in secrets/keys.env and the connection in tools.md:

    composio:
      key: COMPOSIO_API_KEY    # its name in keys.env; never the value
      class: external          # internal or external: what its other tools that change things count as

Each toolkit with an active connected account is offered to every crew member, as one tool per
Composio tool: composio.<toolkit>.<tool>, such as composio.googledocs.CREATE_DOCUMENT. Like an MCP
server's tools, they are listed by name in tools__load and sent in full only once loaded. A tool
Composio marks read-only (or whose name says it only looks things up, where Composio says nothing)
is a lookup. A tool whose name sends to other people, deletes or spends is external and waits
in Approvals; any other change in the organisation's own document tools (Sheets, Docs, Drive,
Notion and the like, OWN_TOOLKITS) or to a draft is internal, so a brief that asks for a sheet gets
it made and formatted without asking. Anything else follows `class:` above.

Ombro talks to Composio's REST API (v3.1) directly. Usage, limits and costs are the user's, on
their Composio account: when Composio says no (a limit, a quota, a lost connection), the tool's
error says so plainly, and the role carries on without it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import httpx

from ombro.home import Home
from ombro.settings import SERVER_CLASSES, SetupError
from ombro.tools import Tool, ToolContext, ToolError

BASE = "https://backend.composio.dev/api/v3.1"
KEY_NAME = "COMPOSIO_API_KEY"
DASHBOARD = "https://dashboard.composio.dev"
KEYS_PAGE = "https://dashboard.composio.dev/~/project/settings/api-keys"  # a project's API Keys tab
# Where "Connect more in Composio" leads: the Toolkits page of whichever project the user has open.
TOOLKITS_PAGE = "https://dashboard.composio.dev/~/project/toolkits"
TIMEOUT = 60.0  # a tool run
LIST_TIMEOUT = 15.0  # listing accounts and tools
CACHE_SECONDS = 300.0
REPLY_CHARS = 20_000
MAX_PAGES = 10
_FIELDS = {"key", "class"}
_TOOLKIT = re.compile(r"[^a-z0-9_-]+")
# The first word of a tool's name, when Composio gives no marking, that says it only looks things up.
READ_VERBS = {"GET", "LIST", "SEARCH", "FETCH", "FIND", "READ", "RETRIEVE", "QUERY", "LOOKUP", "DESCRIBE",
              "COUNT", "VIEW"}
# Toolkits that are the organisation's own documents, sheets, pages and boards. Making and editing
# things in them is the work a brief asks for (internal), unless the name is risky (see classify).
OWN_TOOLKITS = frozenset({"googlesheets", "googledocs", "googledrive", "googleslides", "googletasks", "notion",
                          "airtable", "linear", "asana", "trello", "clickup", "jira", "confluence", "coda",
                          "excel", "onedrive", "sharepoint", "dropbox", "box", "monday"})
TRANSPORT: httpx.BaseTransport | None = None  # tests put a fake Composio here; nothing else sets it


class ComposioNotReached(ToolError):
    """Nothing was done: Composio could not be reached, or asked to wait. Safe to try again."""


class ComposioCallLost(ToolError):
    """The request went, but no answer came back. It may have happened; never retried."""


class ComposioRefused(ToolError):
    """Composio answered no. `status` is its HTTP status."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Settings


def check(raw: Any, where: str = "tools.md") -> dict[str, Any] | None:
    """The `composio:` section, checked as a job checks tools.md."""
    from ombro import keys

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SetupError(f"{where}: `composio` must be a mapping, such as {{key: {KEY_NAME}}}")
    bad = set(raw) - _FIELDS
    if bad:
        raise SetupError(f"{where}: composio has unknown setting(s): {', '.join(sorted(bad))}")
    key = str(raw.get("key") or KEY_NAME)
    if not keys.valid_name(key):
        raise SetupError(f"{where}: the key for composio must be a name in keys.env, such as {KEY_NAME}, "
                         "never the key itself")
    cls = str(raw.get("class") or "external")
    if cls not in SERVER_CLASSES:
        raise SetupError(f"{where}: class for composio must be internal or external (it covers its tools "
                         "that are not read-only)")
    return {"key": key, "class": cls}


# Composio's names for the toolkits people use most, for when it gives only the slug.
TITLES = {"gmail": "Gmail", "googledocs": "Google Docs", "googlesheets": "Google Sheets",
          "googledrive": "Google Drive", "googlecalendar": "Google Calendar", "googleslides": "Google Slides",
          "googlemeet": "Google Meet", "googletasks": "Google Tasks", "github": "GitHub", "gitlab": "GitLab",
          "slack": "Slack", "notion": "Notion", "linear": "Linear", "outlook": "Outlook", "hubspot": "HubSpot",
          "airtable": "Airtable", "dropbox": "Dropbox", "trello": "Trello", "asana": "Asana", "jira": "Jira",
          "discord": "Discord", "youtube": "YouTube", "linkedin": "LinkedIn", "twitter": "X (Twitter)",
          "microsoft_teams": "Microsoft Teams", "one_drive": "OneDrive", "calendly": "Calendly", "zoom": "Zoom"}


def title_for(slug: str, said: str | None = None) -> str:
    """A toolkit's name for people: Composio's own, unless that is just its slug again."""
    said = str(said or "").strip()
    if said and said.lower() != slug.lower():
        return said
    return TITLES.get(slug.lower()) or slug.replace("_", " ").title()


def toolkit_name(slug: str) -> str:
    """A toolkit's slug as it is used in a tool's name: googledocs, google_maps."""
    return _TOOLKIT.sub("-", str(slug or "").lower()).strip("-")[:31] or "toolkit"


def short_name(toolkit: str, slug: str) -> str:
    """A Composio tool's slug without its toolkit's prefix: GOOGLEDOCS_CREATE_DOCUMENT -> CREATE_DOCUMENT."""
    prefix = toolkit.upper().replace("-", "_") + "_"
    return slug[len(prefix):] if slug.upper().startswith(prefix) and len(slug) > len(prefix) else slug


def split_name(name: str) -> tuple[str, str] | None:
    parts = name.split(".", 2)
    if len(parts) == 3 and parts[0] == "composio" and parts[1] and parts[2]:
        return parts[1], parts[2]
    return None


# ---------------------------------------------------------------------------
# Talking to Composio


def _problem(r: httpx.Response) -> str:
    """Composio's own words for what went wrong, from either shape of its error body."""
    try:
        body = r.json()
    except ValueError:
        return (r.text or "").strip()[:300]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            text = str(err.get("message") or "")
            if err.get("suggested_fix"):
                text += f" ({err['suggested_fix']})"
            return text.strip()[:500]
        if isinstance(err, str) and err:
            return err[:500]
        if body.get("message"):
            return str(body["message"])[:500]
    return json.dumps(body)[:300]


def _refusal(status: int, said: str, doing: str) -> str:
    """What a refusal from Composio means, in plain words."""
    said = f" Composio said: {said}" if said else ""
    if status == 401:
        return f"Composio refused the API key while {doing}. Check it in Settings › Tools.{said}"
    if status == 402:
        return f"Your Composio account has run out of allowance, so {doing} was not done.{said}"
    if status == 403:
        return f"Composio's connection doesn't allow {doing}: it may need more permissions in Composio.{said}"
    if status == 404:
        return f"Composio couldn't find what was asked for while {doing} (a tool, or a connected account).{said}"
    if status == 410:
        return f"Composio no longer offers this tool.{said}"
    if status == 422:
        return f"The connection in Composio isn't usable right now; reconnect it in the Composio dashboard.{said}"
    if status == 429:
        return f"Composio's limit (or the service's own) was reached while {doing}. Try again later.{said}"
    if status >= 500:
        return f"Composio, or the service behind it, had a problem while {doing} ({status}).{said}"
    return f"Composio answered {status} while {doing}.{said}"


def _send(key: str, method: str, path: str, *, params: dict | None = None, body: Any = None,
          timeout: float = LIST_TIMEOUT, doing: str = "listing tools", sends: bool = False) -> Any:
    """One request to Composio. Raises ComposioNotReached (nothing done), ComposioCallLost (sent, no
    answer; only for a run, `sends`) or ToolError (Composio said no)."""
    headers = {"x-api-key": key, "Accept": "application/json", "User-Agent": "Ombro"}
    try:
        with httpx.Client(transport=TRANSPORT, follow_redirects=False, timeout=timeout) as client:
            r = client.request(method, BASE + path, params=params, json=body, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise ComposioNotReached(f"Composio could not be reached: {type(e).__name__}") from None
    except httpx.TimeoutException:
        if sends:
            raise ComposioCallLost("Composio took too long to answer") from None
        raise ToolError("Composio took too long to answer") from None
    except httpx.HTTPError as e:
        raise ToolError(f"Composio: {type(e).__name__}") from None
    if r.status_code >= 400:
        text = _refusal(r.status_code, _problem(r).replace(key, "[key removed]"), doing)
        if r.status_code == 429 or r.status_code == 503:
            raise ComposioNotReached(text)  # nothing was done: safe to try again
        raise ComposioRefused(text, r.status_code)
    try:
        return r.json()
    except ValueError:
        raise ToolError(f"Composio's answer while {doing} could not be read") from None


def _pages(key: str, path: str, params: dict[str, Any], doing: str, timeout: float = LIST_TIMEOUT) -> list[dict]:
    out: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        page = _send(key, "GET", path, params={**params, **({"cursor": cursor} if cursor else {})},
                     doing=doing, timeout=timeout)
        out += [i for i in (page.get("items") or []) if isinstance(i, dict)]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return out


def check_key(value: str) -> None:
    """Make sure a key works before keeping it: SetupError with what Composio said, if not."""
    value = value.strip()
    if not value:
        raise SetupError(f"Paste your Composio project API key (ak_…), from Settings › API Keys in your project: {KEYS_PAGE}")
    if value.startswith("uak_"):
        raise SetupError("That's a Composio user key (uak_…). Ombro needs your project's API key, from the "
                         f"project's API Keys page in Composio: {KEYS_PAGE}")
    if value.startswith("ck_"):
        raise SetupError("That's a Composio Connect key (ck_…), from Composio's For You pages. Ombro needs a "
                         "project API key (ak_…): in the Composio dashboard choose Platform, then your project, "
                         f"then Settings › API Keys ({KEYS_PAGE}). Services connected under For You aren't "
                         "visible to a project key, so connect them in the project too.")
    try:
        _send(value, "GET", "/connected_accounts", params={"limit": 1}, doing="checking the key")
    except ToolError as e:
        raise SetupError(str(e)) from None


# Listings, kept a few minutes in this process so the app's Tools page and each chat don't ask
# again every time. Keyed by the key's value, so a new key never sees the old account's.
_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def forget() -> None:
    with _cache_lock:
        _cache.clear()


def _cached(k: tuple, fetch, fresh: bool = False) -> Any:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(k)
    if hit and not fresh and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fetch()
    with _cache_lock:
        _cache[k] = (now, value)
    return value


def _key(home: Home, cfg: dict[str, Any]) -> str:
    from ombro import keys

    value = keys.get_key(home, cfg.get("key") or KEY_NAME)
    if not value:
        raise ToolError(f"the Composio key ({cfg.get('key') or KEY_NAME}) isn't set, so Composio's tools can't "
                        "be used. Connect your Composio account again in Settings › Tools.")
    return value


def toolkits(home: Home, cfg: dict[str, Any], fresh: bool = False) -> list[dict[str, Any]]:
    """The toolkits connected in the user's Composio account, one each: {slug, name (Ombro's tool-name
    part), title (Composio's name for it), status, account_id, user_id, tools (how many)}. An active
    connection wins over one that isn't, and the most recently changed over older ones."""
    key = _key(home, cfg)

    def fetch() -> list[dict[str, Any]]:
        accounts = _pages(key, "/connected_accounts", {"limit": 100, "order_by": "updated_at",
                                                        "order_direction": "desc"}, "listing your connections")
        best: dict[str, dict[str, Any]] = {}
        for a in accounts:
            slug = str((a.get("toolkit") or {}).get("slug") or "")
            if not slug or a.get("is_disabled") or (a.get("auth_config") or {}).get("is_disabled"):
                continue
            status = str(a.get("status") or "").upper()
            row = {"slug": slug, "name": toolkit_name(slug), "title": title_for(slug), "status": status,
                   "account_id": str(a.get("id") or ""), "user_id": a.get("user_id"), "tools": None}
            have = best.get(slug)
            if have is None or (status == "ACTIVE" and have["status"] != "ACTIVE"):
                best[slug] = row
        for row in best.values():  # each toolkit's name and how many tools it has, in one small call
            try:
                page = _send(key, "GET", "/tools", params={"toolkit_slug": row["slug"], "limit": 1},
                             doing=f"listing {row['slug']}'s tools")
            except ToolError:
                continue
            items = page.get("items") or []
            if items and isinstance(items[0], dict):
                row["title"] = title_for(row["slug"], (items[0].get("toolkit") or {}).get("name"))
            if isinstance(page.get("total_items"), int):
                row["tools"] = page["total_items"]
        return sorted(best.values(), key=lambda r: r["title"].lower())

    return _cached(("toolkits", key), fetch, fresh)


def toolkit_tools(home: Home, cfg: dict[str, Any], slug: str, fresh: bool = False) -> list[dict[str, Any]]:
    """Every tool of one toolkit, as Composio lists it (slug, name, description, input_parameters, tags,
    version)."""
    key = _key(home, cfg)
    return _cached(("tools", key, slug), lambda: [
        t for t in _pages(key, "/tools", {"toolkit_slug": slug, "limit": 1000}, f"listing {slug}'s tools")
        if t.get("slug") and not t.get("is_deprecated")], fresh)


def classify(tool: dict[str, Any], short: str, server_class: str, override: str | None = None,
             toolkit: str = "") -> str:
    """read-only, internal or external. In order: tools.md's class for the tool; Composio's read-only
    marking, or (where it says nothing) the first word of its name; a name that sends to other people,
    deletes or spends (outward.RISKY_WORDS) is external; any other change in one of the organisation's
    own document tools (OWN_TOOLKITS), or to a draft, is internal; anything else takes composio's class."""
    from ombro.outward import name_words, risky
    from ombro.settings import TOOL_CLASSES

    if override in TOOL_CLASSES:
        return override
    tags = {str(t).lower() for t in (tool.get("tags") or [])}
    if "readonlyhint" in tags:
        return "read-only"
    marked = tags & {"destructivehint", "openworldhint", "idempotenthint"}
    if not marked and short.split("_", 1)[0].upper() in READ_VERBS:
        return "read-only"
    if risky(short):
        return "external"
    if toolkit in OWN_TOOLKITS or "DRAFT" in name_words(short) or "DRAFTS" in name_words(short):
        return "internal"
    return server_class


def run(home: Home, cfg: dict[str, Any], slug: str, arguments: dict, account: dict[str, Any] | None,
        version: str | None = None, timeout: float = TIMEOUT) -> str:
    """Run one Composio tool for the user's connected account. Returns what it gave back, as text."""
    key = _key(home, cfg)
    body: dict[str, Any] = {"arguments": arguments or {}}
    if account:
        if account.get("account_id"):
            body["connected_account_id"] = account["account_id"]
        if account.get("user_id"):
            body["user_id"] = account["user_id"]
    if version:
        body["version"] = version
    out = _send(key, "POST", f"/tools/execute/{slug}", body=body, timeout=timeout,
                doing=f"running {slug}", sends=True)
    if not isinstance(out, dict):
        raise ToolError("Composio's answer could not be read")
    if out.get("successful") is False or (out.get("error") and not out.get("data")):
        said = str(out.get("error") or "no detail").replace(key, "[key removed]")
        raise ToolError(f"{slug} didn't work. Composio said: {said[:2000]}")
    try:
        text = json.dumps(out.get("data"), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(out.get("data"))
    text = text.replace(key, "[key removed]")
    if len(text) > REPLY_CHARS:
        text = text[:REPLY_CHARS] + f"\n… (cut at {REPLY_CHARS:,} characters)"
    return text or "(empty)"


# ---------------------------------------------------------------------------
# The tools a role is offered


def tools(home: Home, settings: Any) -> tuple[list[Tool], list[str]]:
    """Ombro tools for every active toolkit in the user's Composio account, and a warning for each
    that can't be offered. Nothing, without `composio:` in tools.md."""
    from ombro.outward import DESCRIPTION_MAX_CHARS, MCP_NOTE, _with_reason
    from ombro.tools import wire_name

    cfg = getattr(settings, "composio", None)
    if not cfg:
        return [], []
    warnings: list[str] = []
    try:
        kits = toolkits(home, cfg)
    except ToolError as e:
        return [], [f"Composio's tools are not offered: {e}"]
    out: list[Tool] = []
    seen: set[str] = set()
    for kit in kits:
        if kit["status"] != "ACTIVE":
            warnings.append(f"{kit['title']} (via Composio) isn't offered: its connection is "
                            f"{kit['status'].lower() or 'not active'}. Reconnect it in the Composio dashboard.")
            continue
        try:
            listed = toolkit_tools(home, cfg, kit["slug"])
        except ToolError as e:
            warnings.append(f"{kit['title']} (via Composio) isn't offered: {e}")
            continue
        for t in listed:
            slug = str(t["slug"])
            short = re.sub(r"[^A-Za-z0-9_-]", "_", short_name(kit["name"], slug))
            name = f"composio.{kit['name']}.{short}"
            if wire_name(name) in seen:
                continue  # two names that read the same to a model: keep the first
            seen.add(wire_name(name))
            effect = classify(t, short, cfg.get("class") or "external", settings.tools.get(name, {}).get("class"),
                              kit["name"])
            schema = t.get("input_parameters") if isinstance(t.get("input_parameters"), dict) else {}
            schema = {"type": "object", "properties": {}, **schema}
            if effect == "read-only":
                params, reason_key = schema, None
            else:
                params, reason_key = _with_reason(schema)
            label = {"read-only": "Reads from", "internal": "Changes things in",
                     "external": "Reaches outside the organisation, through"}[effect]
            said = " ".join(str(t.get("description") or t.get("name") or "").split())
            if len(said) > DESCRIPTION_MAX_CHARS:
                said = said[:DESCRIPTION_MAX_CHARS - 1] + "…"
            text = f"{label} {kit['title']}, through Composio (an outside service)." + \
                (f" Composio describes it as: \"{said}\"" if said else "")

            def handler(args: dict, ctx: ToolContext, slug=slug, kit=kit, version=t.get("version")) -> dict[str, Any]:
                result = run(home, cfg, slug, args, kit, version, timeout=min(TIMEOUT, ctx.timeout))
                return {"result": result, "note": MCP_NOTE}

            out.append(Tool(name=name, description=text[:1000], parameters=params, handler=handler, outward=True,
                            effect=effect, input_schema=schema, reason_key=reason_key))
    return out, warnings


def run_approved(home: Home, tools_settings: dict[str, Any] | None, tool: str, arguments: dict) -> str:
    """Run an approved Composio tool with the settings as they were when the job started."""
    from ombro.settings import ToolSettings

    parts = split_name(tool)
    if parts is None:
        raise ToolError(f"{tool} is not a Composio tool")
    settings = ToolSettings.from_dict(tools_settings, "job.md tools")
    cfg = settings.composio
    if not cfg:
        raise ToolError("the job had no Composio account")
    kit = next((k for k in toolkits(home, cfg, fresh=True) if k["name"] == parts[0]), None)
    if kit is None or kit["status"] != "ACTIVE":
        raise ToolError(f"{parts[0]} is no longer connected in your Composio account; reconnect it there, then "
                        "approve again")
    found = next((t for t in toolkit_tools(home, cfg, kit["slug"])
                  if re.sub(r"[^A-Za-z0-9_-]", "_", short_name(kit["name"], str(t["slug"]))) == parts[1]), None)
    if found is None:
        raise ToolError(f"Composio no longer offers {tool}")
    return run(home, cfg, str(found["slug"]), arguments, kit, found.get("version"))


# ---------------------------------------------------------------------------
# Connecting and disconnecting (the app's Connect my Composio account)


def connect(home: Home, key_value: str) -> None:
    """Check the key with Composio, keep it in keys.env, and add `composio:` to tools.md. A key that
    doesn't work is never kept."""
    from ombro import keys
    from ombro.apitool import _read_tools, _write

    key_value = key_value.strip()
    check_key(key_value)
    meta, body = _read_tools(home)
    entry = check(meta.get("composio") or {}) or {"key": KEY_NAME, "class": "external"}
    with keys.setting(home, {entry["key"]: key_value}):
        _write(home, {**meta, "composio": entry}, body)
    forget()


def disconnect(home: Home) -> None:
    """Take `composio:` out of tools.md, with any per-tool settings for its tools, and forget its key
    unless something else uses it. Nothing changes in the Composio account itself."""
    from ombro import keys
    from ombro.apitool import _read_tools, _write

    meta, body = _read_tools(home)
    cfg = meta.get("composio")
    if cfg is None:
        raise LookupError("Composio isn't connected")
    new = {k: v for k, v in meta.items() if k != "composio"}
    tools = {t: c for t, c in (meta.get("tools") or {}).items() if not str(t).startswith("composio.")}
    if tools:
        new["tools"] = tools
    else:
        new.pop("tools", None)
    _write(home, new, body)
    keys.forget_unused(home, [str((cfg or {}).get("key") or KEY_NAME)])
    forget()


def view(home: Home, cfg: dict[str, Any] | None, fresh: bool = False) -> dict[str, Any] | None:
    """What the app's Tools page shows: whether the key is set, any problem, and each toolkit."""
    from ombro import keys

    if not cfg:
        return None
    key_set = bool(keys.get_key(home, cfg.get("key") or KEY_NAME))
    rows: list[dict[str, Any]] = []
    problem = None
    if not key_set:
        problem = "The Composio key is missing. Connect your Composio account again."
    else:
        try:
            kits = toolkits(home, cfg, fresh)
        except ToolError as e:
            kits, problem = [], str(e)
        rows = [{"slug": k["slug"], "title": k["title"], "active": k["status"] == "ACTIVE",
                 "status": k["status"].lower(), "tools": k["tools"]} for k in kits]
    return {"key": cfg.get("key") or KEY_NAME, "key_set": key_set, "access": cfg.get("class") or "external",
            "problem": problem, "toolkits": rows, "dashboard": TOOLKITS_PAGE}

