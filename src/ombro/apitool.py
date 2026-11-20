"""Web APIs as tools: any API the user adds by its name, address and key.

An API lives in tools.md under `apis:`:

    podcasts:
      url: https://api.podcastlist.example/v2
      key: PODCASTS_API_KEY        # its name in keys.env; never the value
      send_key: bearer             # bearer, header:<Name> or query:<param>
      about: Our podcast list.     # what it's for: the Lead decides from this when it helps
      how_to: GET /episodes?q=…    # optional: its paths, from its docs
      class: external              # read-only, internal or external: what its changes are
      get_looks_up: true           # do its GET requests only look things up? (true when not said)
      max_calls: 30

Every crew member is offered it. A role gets `api.<name>.get` (GET only: a lookup) and
`api.<name>.request` (any method: a change of the API's class, so it follows the outward presets and
may wait in Approvals). An API whose class is read-only has one tool, `api.<name>.request`, that takes
any method as a lookup: some APIs search with POST.

Some APIs act on a GET (a bot's /sendMessage?text=…, a webhook, a trigger), so a GET isn't always a
lookup. The user says, when adding an API, whether its GETs only look things
up; if not, it has no `get`, and every GET goes through `request` with the API's class. Even when they
do, `get` won't run a path that names a sending verb (send, post, notify, trigger, webhook…) or a query
longer than the fetch cap (fetch_max_query, as web.fetch and the browser): those go through `request`
as a GET, so they take the API's class and, once the brief has read material, wait for approval.

The guards: a role names a path, never an address, and the call can only go to the API's own host
and path prefix; redirects are not followed; Ombro adds the key, which no model sees, and wipes it
from what comes back. Any address the user gives is allowed, on the internet or their own network.
"""

from __future__ import annotations

import json
import posixpath
import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx

from ombro.home import Home
from ombro.settings import SERVER_NAME, TOOL_CLASSES, SetupError
from ombro.tools import Tool, ToolContext, ToolError, ToolInputError

DEFAULT_CALLS = 30
ABOUT_MAX = 600
HOW_TO_MAX = 2_000
REPLY_CHARS = 20_000
TIMEOUT = 30.0
LOOKUP = ("GET",)
CHANGE = ("POST", "PUT", "PATCH", "DELETE")
_FIELDS = {"url", "key", "send_key", "about", "how_to", "class", "get_looks_up", "max_calls"}
# Words in a GET's path that say it sends or changes something, so it isn't run as a lookup.
SENDING_WORDS = frozenset(
    {
        "send",
        "post",
        "notify",
        "trigger",
        "webhook",
        "webhooks",
        "hook",
        "hooks",
        "create",
        "delete",
        "update",
        "remove",
        "publish",
        "submit",
        "push",
        "invite",
        "share",
        "transfer",
        "pay",
        "charge",
        "refund",
        "cancel",
        "subscribe",
        "unsubscribe",
        "reply",
        "forward",
        "upload",
        "insert",
        "edit",
        "modify",
        "destroy",
        "purge",
        "write",
        "add",
        "set",
        "put",
        "patch",
        "execute",
        "exec",
        "run",
        "dispatch",
        "emit",
        "broadcast",
        "sms",
        "mark",
        "approve",
        "reject",
        "enable",
        "disable",
        "start",
        "stop",
        "restart",
        "reset",
        "deploy",
        "revoke",
    }
)
# Query values that often name what a GET does: ?action=send, ?method=chat.postMessage.
_ACTION_KEYS = frozenset({"action", "method", "op", "operation", "cmd", "command", "do", "task", "func", "function"})

_SEND = re.compile(r"^(bearer|header:[A-Za-z0-9][A-Za-z0-9-]*|query:[A-Za-z0-9_.-]+)$")
TRANSPORT: httpx.BaseTransport | None = None  # tests put a fake API here; nothing else sets it


class ApiNotReached(ToolError):
    """Nothing was sent: the address could not be reached. Safe to try again."""


class ApiCallLost(ToolError):
    """The request went, but no answer came back. It may have happened; never retried."""


# ---------------------------------------------------------------------------
# Settings


def check_apis(raw: Any, where: str = "tools.md") -> dict[str, dict[str, Any]]:
    """The `apis:` section, checked as a job checks tools.md."""
    from ombro import keys

    if not isinstance(raw, dict):
        raise SetupError(f"{where}: `apis` must be a mapping of names")
    out: dict[str, dict[str, Any]] = {}
    for name, cfg in raw.items():
        name = str(name)
        if not SERVER_NAME.match(name):
            raise SetupError(f"{where}: API name {name!r} must use lower-case letters, digits, - and _")
        if not isinstance(cfg, dict):
            raise SetupError(f"{where}: settings for API {name} must be a mapping")
        bad = set(cfg) - _FIELDS
        if bad:
            raise SetupError(f"{where}: API {name} has unknown setting(s): {', '.join(sorted(bad))}")
        url = str(cfg.get("url") or "").strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise SetupError(f"{where}: the url for API {name} must be a full address starting with https://")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise SetupError(f"{where}: the url for API {name} must be just the address, with no ?, # or user name")
        entry: dict[str, Any] = {"url": url.rstrip("/")}
        if cfg.get("key"):
            if not keys.valid_name(str(cfg["key"])):
                raise SetupError(
                    f"{where}: the key for API {name} must be a name in keys.env, such as "
                    "PODCASTS_API_KEY, never the key itself"
                )
            entry["key"] = str(cfg["key"])
        send = str(cfg.get("send_key") or "bearer")
        if not _SEND.match(send):
            raise SetupError(f"{where}: send_key for API {name} is bearer, header:<Name> or query:<param>")
        entry["send_key"] = send
        about = " ".join(str(cfg.get("about") or "").split())
        if len(about) > ABOUT_MAX:
            raise SetupError(f"{where}: `about` for API {name} is over {ABOUT_MAX} characters")
        entry["about"] = about
        how_to = str(cfg.get("how_to") or "").strip()
        if len(how_to) > HOW_TO_MAX:
            raise SetupError(f"{where}: `how_to` for API {name} is over {HOW_TO_MAX:,} characters")
        if how_to:
            entry["how_to"] = how_to
        cls = str(cfg.get("class") or "external")
        if cls not in TOOL_CLASSES:
            raise SetupError(f"{where}: class for API {name} must be one of {', '.join(TOOL_CLASSES)}")
        entry["class"] = cls
        if "get_looks_up" in cfg:
            if not isinstance(cfg["get_looks_up"], bool):
                raise SetupError(f"{where}: get_looks_up for API {name} is true or false")
            if cls != "read-only":  # a read-only API's every call is a lookup already
                entry["get_looks_up"] = cfg["get_looks_up"]
        if "max_calls" in cfg:
            v = cfg["max_calls"]
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise SetupError(f"{where}: max_calls for API {name} must be a positive whole number")
            entry["max_calls"] = v
        out[name] = entry
    return out


def gets_look_up(cfg: dict[str, Any]) -> bool:
    """Whether the API's GETs only look things up, as the user said. An API added before the question
    was asked counts as yes."""
    return cfg.get("get_looks_up", True) is not False


def tool_names(api: str, cfg: dict[str, Any]) -> list[str]:
    if cfg.get("class") == "read-only" or not gets_look_up(cfg):
        return [f"api.{api}.request"]
    return [f"api.{api}.get", f"api.{api}.request"]


def key_name(api: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", f"{api}_API_KEY".upper())


# ---------------------------------------------------------------------------
# Changing tools.md (an interface's add, edit and remove)


def clean_name(text: str) -> str:
    """A name for a new API from what was typed: podcasts, my-weather."""
    name = re.sub(r"[^a-z0-9_-]+", "-", str(text or "").strip().lower()).strip("-")[:31]
    if not name:
        raise SetupError("Give it a name, such as podcasts.")
    return name


def entry_from(name: str, form: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    """The tools.md entry from the app's form or the terminal's answers: url, send_key (bearer,
    header or query) with key_param, about, how_to, access (the class), get_looks_up (the answer to
    "Do GET requests to this API only look things up?"; an edit that doesn't say keeps the old one),
    max_calls, and key (its value, if one was typed). A key left empty in an edit keeps the one there;
    `no_key` removes it."""
    how = str(form.get("send_key") or "bearer")
    param = str(form.get("key_param") or "").strip()
    if how in ("header", "query"):
        if not param:
            raise SetupError("Say what the header or address value is called, as the API's docs do.")
        how = f"{how}:{param}"
    access = str(form.get("access") or "external")
    max_calls = form.get("max_calls")
    if max_calls is not None and (isinstance(max_calls, bool) or not isinstance(max_calls, int)):
        raise SetupError("`max_calls` is a whole number")
    url = str(form.get("url") or "").strip()
    if not url:
        raise SetupError("Give the API's address, such as https://api.example.com/v2.")
    entry: dict[str, Any] = {"url": url, "send_key": how, "about": str(form.get("about") or ""), "class": access}
    if str(form.get("how_to") or "").strip():
        entry["how_to"] = str(form["how_to"]).strip()
    if access != "read-only":
        looks = form.get("get_looks_up")
        if looks is None or looks == "":
            looks = gets_look_up(old or {})
        elif isinstance(looks, str):
            if looks.strip().lower() not in ("yes", "no", "true", "false"):
                raise SetupError("Say yes or no: do GET requests to this API only look things up?")
            looks = looks.strip().lower() in ("yes", "true")
        elif not isinstance(looks, bool):
            raise SetupError("`get_looks_up` is true or false")
        entry["get_looks_up"] = looks
    if max_calls:
        entry["max_calls"] = max_calls
    if str(form.get("key") or "").strip():
        entry["key"] = (old or {}).get("key") or key_name(name)
    elif old and old.get("key") and form.get("no_key") is not True:
        entry["key"] = old["key"]
    check_apis({name: entry}, "the API")
    return entry


def save(home: Home, name: str, entry: dict[str, Any], key_value: str = "", old: dict[str, Any] | None = None) -> None:
    """Add (no `old`) or change an API, and keep its key, typed as `key_value`. A key that can't be
    saved with it isn't kept; one the change took out is removed, unless something else uses it."""
    from ombro import keys

    key_value = key_value.strip()
    with keys.setting(home, {entry["key"]: key_value} if key_value and entry.get("key") else {}):
        if old is None:
            add_api(home, name, entry)
        else:
            update_api(home, name, entry)
    if old and old.get("key"):
        keys.forget_unused(home, [old["key"]])


def _read_tools(home: Home) -> tuple[dict[str, Any], str]:
    from ombro import mdfile

    if not home.tools.is_file():
        raise SetupError(f"{home.tools} is missing; run `ombro init`")
    return mdfile.read(home.tools)


def _write(home: Home, meta: dict[str, Any], body: str) -> None:
    from ombro import mdfile
    from ombro.settings import ToolSettings

    ToolSettings.from_dict(meta, str(home.tools))  # raises SetupError if anything is wrong
    mdfile.write(home.tools, meta, body)


def add_api(home: Home, name: str, entry: dict[str, Any]) -> None:
    meta, body = _read_tools(home)
    apis = dict(meta.get("apis") or {})
    servers = meta.get("servers") or {}
    if name in apis or name in servers:
        raise SetupError(f"There's already a tool called {name}.")
    apis[name] = entry
    _write(home, {**meta, "apis": apis}, body)


def update_api(home: Home, name: str, entry: dict[str, Any]) -> None:
    meta, body = _read_tools(home)
    apis = dict(meta.get("apis") or {})
    if name not in apis:
        raise LookupError(f"no API called {name!r}")
    apis[name] = entry
    _write(home, {**meta, "apis": apis}, body)


def remove_api(home: Home, name: str) -> None:
    """Remove an API and its per-tool settings. Its key is the caller's (keys.forget_unused)."""
    meta, body = _read_tools(home)
    apis = dict(meta.get("apis") or {})
    if name not in apis:
        raise LookupError(f"no API called {name!r}")
    del apis[name]
    tools = {t: c for t, c in (meta.get("tools") or {}).items() if not str(t).startswith(f"api.{name}.")}
    new = {**meta, "apis": apis, "tools": tools}
    if not apis:
        del new["apis"]
    if not tools:
        new.pop("tools", None)
    _write(home, new, body)


# ---------------------------------------------------------------------------
# A call


def address(base: str, path: str, query: dict[str, Any] | None = None) -> str:
    """The full address for a path under the API's own. Refused if it would lead anywhere else."""
    path = str(path or "").strip()
    if not path or "://" in path or path.startswith("//") or "\\" in path or "@" in path.split("?", 1)[0]:
        raise ToolInputError("give a path under the API's address, such as /episodes, never a full address")
    raw_path, _, raw_query = path.partition("?")
    if any(seg in ("..", ".") for seg in raw_path.split("/")):
        raise ToolInputError("the path can't use . or .. ; give it from the API's address down")
    b = urlsplit(base)
    joined = posixpath.join(b.path.rstrip("/") or "/", raw_path.lstrip("/"))
    prefix = b.path.rstrip("/")
    if prefix and not (joined == prefix or joined.startswith(prefix + "/")):
        raise ToolInputError("the path leads outside the API's address")
    pairs = parse_qsl(raw_query, keep_blank_values=True)
    for k, v in (query or {}).items():
        pairs.append((str(k), v if isinstance(v, str) else json.dumps(v)))
    return urlunsplit((b.scheme, b.netloc, quote(joined, safe="/:@!$&'()*+,;=-._~%"), urlencode(pairs), ""))


def _reply_text(r: httpx.Response, secret: str | None) -> str:
    try:
        text = json.dumps(r.json(), ensure_ascii=False, separators=(",", ":"))
    except ValueError:
        text = r.text
    if secret:
        text = text.replace(secret, "[key removed]")
    if len(text) > REPLY_CHARS:
        text = text[:REPLY_CHARS] + f"\n… (cut at {REPLY_CHARS:,} characters)"
    return text


def get_check(path: str, query: dict[str, Any] | None, cap: int) -> str | None:
    """Why a GET can't run as a lookup, or None: its path names a sending verb, or its query is over
    the cap, which is how a page would have a role carry data out."""
    raw_path, _, raw_query = str(path or "").partition("?")
    pairs = parse_qsl(raw_query, keep_blank_values=True) + [(str(k), str(v)) for k, v in (query or {}).items()]
    words = _words(raw_path)
    for k, v in pairs:
        if k.strip().lower() in _ACTION_KEYS:
            words |= _words(v)
    found = sorted(words & SENDING_WORDS)
    if found:
        return f"its path says it may send or change something ({', '.join(found)})"
    length = len(urlencode(pairs))
    if length > cap:
        return f"its query is {length:,} characters, over the {cap:,} a lookup may carry"
    return None


def _words(text: str) -> set[str]:
    """/sendMessage -> {send, message}; /chat.post_message -> {chat, post, message}."""
    return {w.lower() for w in re.split(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])", text) if w}


def call(
    home: Home,
    api: str,
    cfg: dict[str, Any],
    method: str,
    path: str,
    query: dict | None = None,
    body: Any = None,
    timeout: float = TIMEOUT,
    sends: bool = False,
) -> dict[str, Any]:
    """Make one request. Raises ApiNotReached (nothing sent), ApiCallLost (sent, no answer) or
    ToolError (the API said no, or its reply could not be read). `sends`: a GET that may have done
    something, so a lost answer is ApiCallLost, never a plain error to try again."""
    from ombro import keys

    method = method.upper()
    url = address(cfg["url"], path, query)
    headers = {"Accept": "application/json, text/plain;q=0.9, */*;q=0.5", "User-Agent": "Ombro"}
    secret = keys.get_key(home, cfg["key"]) if cfg.get("key") else None
    if cfg.get("key") and not secret:
        raise ToolError(f"the key for {api} ({cfg['key']}) isn't set, so it can't be called. Say so in did_not_do.")
    send = cfg.get("send_key") or "bearer"
    if secret:
        if send == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
        elif send.startswith("header:"):
            headers[send.split(":", 1)[1]] = secret
        else:
            sep = "&" if urlsplit(url).query else "?"
            url += sep + urlencode({send.split(":", 1)[1]: secret})
    kwargs: dict[str, Any] = {"headers": headers}
    if body is not None and method != "GET":
        kwargs["json"] = body
    try:
        with httpx.Client(transport=TRANSPORT, follow_redirects=False, timeout=timeout) as client:
            r = client.request(method, url, **kwargs)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise ApiNotReached(f"{api} could not be reached: {type(e).__name__}") from None
    except httpx.TimeoutException:
        if method in LOOKUP and not sends:
            raise ToolError(f"{api} took too long to answer") from None
        raise ApiCallLost(f"{api} took too long to answer") from None
    except httpx.HTTPError as e:
        raise ToolError(f"{api}: {type(e).__name__}") from None
    if 300 <= r.status_code < 400:
        raise ToolError(
            f"{api} answered with a redirect ({r.status_code}), which isn't followed: Ombro only "
            "calls the API's own address"
        )
    text = _reply_text(r, secret)
    if r.status_code >= 400:
        raise ToolError(f"{api} answered {r.status_code}: {text[:2000]}")
    return {"status": r.status_code, "reply": text or "(empty)"}


# ---------------------------------------------------------------------------
# The tools a role is offered


def _fetch_cap() -> int:
    from ombro.native import FETCH_MAX_QUERY

    return FETCH_MAX_QUERY


def _params(methods: tuple[str, ...]) -> dict[str, Any]:
    props: dict[str, Any] = {
        "path": {
            "type": "string",
            "description": "The path under the API's address, e.g. /episodes. It may carry ?query=values.",
        },
        "query": {
            "type": "object",
            "description": "Query values to add, by name.",
            "additionalProperties": {"type": ["string", "number", "boolean"]},
        },
    }
    if methods != LOOKUP:
        props = {
            "method": {"type": "string", "enum": list(methods)},
            **props,
            "body": {"description": "The JSON body to send, if the API takes one."},
        }
    required = ["path"] if methods == LOOKUP else ["method", "path"]
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _describe(api: str, cfg: dict[str, Any], what: str) -> str:
    text = f"{what} {api}, a web API the user gave you (an outside service)."
    if cfg.get("about"):
        text += f" What it's for: {cfg['about']}"
    if cfg.get("how_to"):
        text += f"\nHow to use it, in the user's words:\n{cfg['how_to']}"
    return text


def api_tools(home: Home, apis: dict[str, dict[str, Any]]) -> list[Tool]:
    from ombro.native import _validated
    from ombro.outward import _with_reason

    out: list[Tool] = []
    for api, cfg in apis.items():
        cls = cfg.get("class") or "external"

        def handler(
            args: dict, ctx: ToolContext, api=api, cfg=cfg, cls=cls, fixed: str | None = None
        ) -> dict[str, Any]:
            from ombro.outward import MCP_NOTE

            method = fixed or str(args.get("method") or "GET")
            if fixed == "GET":
                cap = int(getattr(ctx.limits, "fetch_max_query", 0) or _fetch_cap())
                why = get_check(args.get("path", ""), args.get("query"), cap)
                if why:
                    # Ombro's own words, so a plain error (not the API's, which would be material).
                    return {
                        "error": f"not run as a lookup: {why}. A GET like this can send or change things, "
                        f"so make it with api.{api}.request, method GET: that follows what the user said "
                        "this API can do, and may wait for their approval."
                    }
            out = call(
                home,
                api,
                cfg,
                method,
                args.get("path", ""),
                args.get("query"),
                args.get("body"),
                timeout=min(TIMEOUT, ctx.timeout),
                sends=fixed is None and cls != "read-only",
            )
            return {**out, "note": MCP_NOTE}

        if cls == "read-only":
            schema = _params(("GET", *CHANGE))
            out.append(
                _validated(
                    Tool(
                        name=f"api.{api}.request",
                        description=_describe(api, cfg, "Looks things up in"),
                        parameters=schema,
                        handler=handler,
                        outward=True,
                        effect="read-only",
                        input_schema=schema,
                    )
                )
            )
            continue
        looks = gets_look_up(cfg)
        if looks:
            get_schema = _params(LOOKUP)
            out.append(
                _validated(
                    Tool(
                        name=f"api.{api}.get",
                        description=_describe(api, cfg, "Looks things up (GET) in")
                        + f"\nA GET that sends or changes something (a path such as "
                        "/sendMessage, /notify or /webhook) or carries a long query isn't run here: make it with "
                        f"api.{api}.request, method GET.",
                        parameters=get_schema,
                        input_schema=get_schema,
                        outward=True,
                        effect="read-only",
                        handler=lambda args, ctx, h=handler: h(args, ctx, fixed="GET"),
                    )
                )
            )
        change = _params(("GET", *CHANGE))
        params, reason_key = _with_reason(change)
        label = {
            "internal": "Changes things in",
            "external": "Changes things or reaches outside the organisation through",
        }[cls]
        text = _describe(api, cfg, label) + (
            "\nUse it with GET only for a GET that sends or changes something, or "
            f"carries a long query; look things up with api.{api}.get."
            if looks
            else "\nThe user says this API's GET requests can send or change "
            "things, so every call goes here, GET included."
        )
        out.append(
            Tool(
                name=f"api.{api}.request",
                description=text,
                parameters=params,
                handler=handler,
                outward=True,
                effect=cls,
                input_schema=change,
                reason_key=reason_key,
            )
        )
    return out


def run_approved(home: Home, tools_settings: dict[str, Any] | None, tool: str, arguments: dict) -> str:
    """Run an approved request with the API's settings as they were when the job started."""
    from ombro.settings import ToolSettings

    parts = tool.split(".")
    if len(parts) != 3 or parts[0] != "api":
        raise ToolError(f"{tool} is not an API tool")
    settings = ToolSettings.from_dict(tools_settings, "job.md tools")
    cfg = settings.apis.get(parts[1])
    if cfg is None:
        raise ToolError(f"the job has no API called {parts[1]}")
    method = "GET" if parts[2] == "get" else str(arguments.get("method") or "POST")
    out = call(
        home,
        parts[1],
        cfg,
        method,
        arguments.get("path", ""),
        arguments.get("query"),
        arguments.get("body"),
        sends=parts[2] != "get",
    )
    return f"{out['status']}: {out['reply']}"
