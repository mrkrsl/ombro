"""`ombro tools add --mcp` and `ombro tools remove`: MCP servers in tools.md, without editing the file by hand.

A server is either local (a command Ombro starts) or remote (an address). Any
secret it needs is typed into a hidden prompt, stored in secrets/keys.env, and
written into tools.md only as ${NAME}, in `env` (local) or `headers` (remote),
never on the command line. Nothing is written until the user confirms what
will be added. The checks are the same ones a job makes when it loads tools.md.

The building blocks (`server_entry`, `pin_warning`, `add_server`,
`remove_server`, `tool_claims`, `confirm_internal`) take plain values, so an
interface can ask the same questions in its own way.

What a server says of its tools is only its word: a tool it marks
`openWorldHint: false` (changes things, reaches no one outside) counts as
external until the user has seen the claim and confirmed it, once, for that
server. The confirmed tools are kept as `confirmed_internal:` on its entry,
and dropped if its command or address changes.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Callable

from ombro import keys, mdfile
from ombro.home import Home
from ombro.settings import SERVER_CLASSES, SERVER_NAME, SetupError, ToolSettings
from ombro.ui import UI, ConsoleUI, Field, Option

RUNNERS = ("uvx", "npx", "pipx", "bunx", "pnpx")
# Runner options that take no value, so the word after them is a package.
FLAGS = ("-y", "--yes", "-q", "--quiet", "--offline", "--isolated", "--no-cache", "--refresh")
_PINNED = re.compile(r"(==|@)\d")
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ServerError(SetupError):
    pass


def _cli():
    from ombro import cli

    return cli


def key_name(server: str, var: str) -> str:
    """The name in keys.env for a server's secret: SERVER_VAR in capitals."""
    return re.sub(r"[^A-Z0-9_]", "_", f"{server}_{var}".upper())


def pin_warning(command: str, args: list[str]) -> str | None:
    """A server started by a package runner should name an exact version, and so should a
    library it is run with (`--with mcp==1.30.0`): the runner resolves them afresh each time."""
    if command.rsplit("/", 1)[-1] not in RUNNERS:
        return None
    loose = []
    for i, a in enumerate(args):
        if a.startswith("-"):
            continue
        prev = args[i - 1] if i else ""
        if prev.startswith("-") and prev not in ("--with", "-w", "--from", *FLAGS) and "=" not in prev:
            continue  # the value of some other option
        if "@latest" in a or not _PINNED.search(a):
            loose.append(a)
    if not loose:
        return None
    return (f"{', '.join(loose)} {'is' if len(loose) == 1 else 'are'} not pinned to an exact version "
            f"(such as name==1.2.3 or name@1.2.3). {command} fetches the newest each time it starts, so the "
            "server can change or break without warning.")


def server_entry(*, command: str | None = None, url: str | None = None, env: dict[str, str] | None = None,
                 headers: dict[str, str] | None = None, server_class: str | None = None) -> dict[str, Any]:
    """The tools.md entry for one server. `command` is the whole command line, split as a shell would."""
    if bool(command) == bool(url):
        raise ServerError("give either a command (a local server Ombro starts) or a url (a remote server)")
    entry: dict[str, Any] = {}
    if command:
        try:
            argv = shlex.split(command)
        except ValueError as e:
            raise ServerError(f"the command could not be read: {e}") from None
        if not argv:
            raise ServerError("the command is empty")
        refs = sorted({m for a in argv for m in _REF.findall(a)})
        if refs:
            raise ServerError(f"the command puts {', '.join(refs)} on its command line, where other users of "
                              "this machine can read it in the process list; give it as a secret for `env` instead")
        entry["command"] = argv[0]
        if argv[1:]:
            entry["args"] = argv[1:]
        if env:
            entry["env"] = dict(env)
    else:
        entry["url"] = url
        if headers:
            entry["headers"] = dict(headers)
    if server_class:
        if server_class not in SERVER_CLASSES:
            raise ServerError("a server's class is internal or external (it covers its tools that are not read-only)")
        entry["class"] = server_class
    return entry


def _read_tools(home: Home) -> tuple[dict[str, Any], str]:
    if not home.tools.is_file():
        raise ServerError(f"{home.tools} is missing; run `ombro init`")
    return mdfile.read(home.tools)


def add_server(home: Home, name: str, entry: dict[str, Any]) -> None:
    """Add one server to tools.md, keeping everything else. Checked as a job checks tools.md."""
    if not SERVER_NAME.match(name):
        raise ServerError("server names use lower-case letters, digits, - and _ (up to 31)")
    meta, body = _read_tools(home)
    servers = dict(meta.get("servers") or {})
    if name in servers:
        raise ServerError(f"there is already a server named {name!r} in tools.md")
    if name in (meta.get("apis") or {}):
        raise ServerError(f"there is already an API named {name!r} in tools.md")
    servers[name] = entry
    new = {**meta, "servers": servers}
    ToolSettings.from_dict(new, str(home.tools))  # raises SetupError if anything is wrong
    mdfile.write(home.tools, new, body)


def update_server(home: Home, name: str, entry: dict[str, Any]) -> None:
    """Replace one server's entry in tools.md, keeping its per-tool settings and everything else.
    Checked as a job checks tools.md. A new command or address is another server, as far as anyone
    knows: what you confirmed of the old one's tools is dropped."""
    meta, body = _read_tools(home)
    servers = dict(meta.get("servers") or {})
    if name not in servers:
        raise ServerError(f"no server named {name!r} in tools.md")
    old = servers[name] or {}
    if any((old.get(k) or None) != (entry.get(k) or None) for k in ("command", "args", "url")):
        entry = {k: v for k, v in entry.items() if k != "confirmed_internal"}
    servers[name] = entry
    new = {**meta, "servers": servers}
    ToolSettings.from_dict(new, str(home.tools))
    mdfile.write(home.tools, new, body)


def remove_server(home: Home, name: str) -> list[str]:
    """Remove a server and its per-tool settings from tools.md. Returns the tool settings removed."""
    meta, body = _read_tools(home)
    servers = dict(meta.get("servers") or {})
    if name not in servers:
        raise ServerError(f"no server named {name!r} in tools.md")
    del servers[name]
    tools = dict(meta.get("tools") or {})
    dropped = [t for t in tools if t.startswith(f"mcp.{name}.")]
    for t in dropped:
        del tools[t]
    new = {**meta, "servers": servers, "tools": tools}
    if not servers:
        del new["servers"]
    mdfile.write(home.tools, new, body)
    return dropped


def tool_claims(home: Home, name: str) -> list[dict[str, Any]]:
    """Each of a server's tools, what the server says of it, and the class it has now. Starts the
    server (a remote one may need signing in first). Raises McpError if it can't be listed."""
    from ombro import outward
    from ombro.mcp import McpClient, ServerConfig
    from ombro.settings import load_tool_settings

    settings = load_tool_settings(home)
    cfg = settings.servers.get(name)
    if cfg is None:
        raise ServerError(f"no server named {name!r} in tools.md")
    with McpClient(home, ServerConfig.from_dict(name, cfg)) as c:
        listed = c.list_tools()
    rows = []
    for t in listed:
        a = t.annotations or {}
        claim = "read-only" if a.get("readOnlyHint") is True else "internal" if outward.claims_internal(a) else None
        effect, source = outward.tool_class(settings, f"mcp.{name}.{t.name}", a)
        rows.append({"tool": t.name, "description": " ".join(str(t.description or "").split()),
                     "claims": claim, "class": effect, "class_from": source})
    return rows


def unconfirmed(rows: list[dict[str, Any]]) -> list[str]:
    """The tools the server says stay inside, which count as external until the user confirms it."""
    return [r["tool"] for r in rows if r["class_from"] == "unconfirmed"]


def confirm_internal(home: Home, name: str, tools: list[str]) -> list[str]:
    """Keep the user's yes to what the server says of these tools (that they stay inside the
    organisation), with the server's settings in tools.md. Returns all the server's confirmed tools."""
    meta, body = _read_tools(home)
    servers = dict(meta.get("servers") or {})
    if name not in servers:
        raise ServerError(f"no server named {name!r} in tools.md")
    entry = dict(servers[name] or {})
    confirmed = sorted({*(entry.get("confirmed_internal") or []), *(str(t) for t in tools if str(t).strip())})
    if confirmed:
        entry["confirmed_internal"] = confirmed
    servers[name] = entry
    new = {**meta, "servers": servers}
    ToolSettings.from_dict(new, str(home.tools))
    mdfile.write(home.tools, new, body)
    return confirmed


def claims_text(name: str, rows: list[dict[str, Any]]) -> list[str]:
    """What the server says of the tools still to confirm, in words, for a terminal."""
    waiting = [r for r in rows if r["class_from"] == "unconfirmed"]
    out = [f"{len(waiting)} of {name}'s tools say they change things but stay inside your organisation, "
           "reaching no one outside:"]
    out += [f"  - mcp.{name}.{r['tool']}" + (f": {r['description'][:120]}" if r["description"] else "")
            for r in waiting]
    out.append("Ombro can't check what a server says about itself. Until you confirm it, these count as "
               "external: each use waits for your approval. Once confirmed, they run without asking, as "
               "internal changes do (deleting or sharing still asks once a job has read outside material).")
    return out


def confirm_flow(home: Home, name: str, ui: UI, rows: list[dict[str, Any]] | None = None) -> list[str]:
    """Show what the server says of its tools and ask, once, whether to believe it. Returns the tools
    confirmed (none if there was nothing to ask, or the answer was no)."""
    from ombro.mcp import McpError

    if rows is None:
        try:
            rows = tool_claims(home, name)
        except McpError as e:
            ui.say(f"Its tools could not be listed yet ({e}). Later, `ombro tools {name}` shows what each one "
                   "says it does and asks you to confirm it.")
            return []
    waiting = unconfirmed(rows)
    if not waiting:
        return []
    ui.say("\n" + "\n".join(claims_text(name, rows)))
    if not ui.yes("confirm_internal", "Confirm these stay inside your organisation?", False):
        ui.say("They count as external: each use waits for your approval. `ombro tools "
               f"{name}` asks again.")
        return []
    confirm_internal(home, name, waiting)
    ui.say(f"Confirmed: {len(waiting)} tool(s) of {name} count as internal.")
    return waiting


def roles_using(home: Home, name: str) -> list[str]:
    """crew/role for every role whose tools name one of the server's tools."""
    out = []
    for crew in home.crews():
        rdir = home.roles_dir(crew)
        for folder in sorted(p for p in rdir.iterdir() if p.is_dir()) if rdir.is_dir() else []:
            try:
                meta, _ = mdfile.read(folder / "config.md")
            except (OSError, mdfile.FrontMatterError):
                continue
            if any(str(t).startswith(f"mcp.{name}.") for t in meta.get("tools") or []):
                out.append(f"{crew}/{folder.name}")
    return out


# ---------------------------------------------------------------------------
# The command


def _pair(text: str, flag: str) -> tuple[str, str]:
    name, sep, value = text.partition("=")
    if not sep or not name.strip():
        raise ServerError(f"{flag} takes NAME=VALUE, not {text!r}")
    return name.strip(), value


def cmd_server(args, *, ask: Callable[[str], str] | None = None,
               ask_secret: Callable[[str], str] | None = None, ui: UI | None = None) -> int:
    cli = _cli()
    home = cli._home()
    ui = ui or ConsoleUI(ask=ask, ask_secret=ask_secret)
    if args.action == "remove":
        return remove_flow(home, args, ui)
    return add_flow(home, args, ui)


def add_flow(home: Home, args, ui: UI) -> int:
    """Adding an MCP server (`ombro tools add --mcp`): what is missing from `args` is asked through `ui`."""
    cli = _cli()
    name = (args.name or "").strip() or ui.text("name", "Server name (lower-case, e.g. sheets)").strip()
    if not SERVER_NAME.match(name or ""):
        raise cli.CliError("Server names use lower-case letters, digits, - and _ (up to 31).")
    try:
        existing = ToolSettings.from_dict(_read_tools(home)[0], str(home.tools)).servers
    except SetupError as e:
        raise cli.CliError(str(e)) from None
    if name in existing:
        raise cli.CliError(f"There is already a server named {name!r} in tools.md. Remove it first with "
                           f"`ombro tools remove {name}`, or edit {home.tools}.")
    command, url = args.command, args.url
    if not command and not url:
        kind = ui.ask(Field("kind", "Is it local (a command Ombro starts) or remote (an address)?", "choice",
                            options=[Option("l", "local"), Option("r", "remote")], invalid="Type l or r."))
        if kind == "l":
            command = ui.text("command", "Command, with an exact version (e.g. uvx some-mcp-server==1.2.3)",
                              required=True, invalid="A command is needed.").strip()
        elif kind == "r":
            url = ui.text("url", "Address (https://…)", required=True, invalid="An address is needed.").strip()
        else:
            ui.say("Nothing added.")
            return 1
    try:
        server_entry(command=command, url=url)  # the command and address are checked before anything is asked
    except SetupError as e:
        raise cli.CliError(str(e)) from None
    env = dict(_pair(e, "--env") for e in args.env or [])
    headers = dict(_pair(h, "--header") for h in args.header or [])
    secrets = list(args.secret or [])
    if not args.secret and command and not args.env and not args.no_prompt:
        more = ui.text("secrets", "Variables it needs that hold a secret (names, space-separated; Enter for none)"
                       ).split()
        secrets += more
    # Each secret: typed hidden, kept in keys.env, written as ${KEY} in env or headers.
    to_set: dict[str, str] = {}
    for item in secrets:
        var, _, key = item.partition("=")
        var = var.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", var):
            raise cli.CliError(f"{var!r} is not a variable or header name")
        key = key.strip() or key_name(name, var)
        if not keys.valid_name(key):
            raise cli.CliError(f"{key!r} cannot be a key name: use capitals, digits and underscores")
        have = keys.get_key(home, key)
        hint = " (Enter keeps the one already set)" if have else ""
        value = str(ui.ask(Field(f"secret_{key}", f"Value for {var}, kept in secrets/keys.env as {key}{hint}",
                                 "secret")) or "").strip()
        if value:
            to_set[key] = value
        elif not have:
            raise cli.CliError(f"No value for {var}; nothing was added.")
        ref = "${" + key + "}"
        if url:
            headers[var] = f"Bearer {ref}" if var.lower() == "authorization" else ref
        else:
            env[var] = ref
    try:
        entry = server_entry(command=command, url=url, env=env, headers=headers, server_class=args.server_class)
        ToolSettings.from_dict({"servers": {name: entry}}, "the new server")
    except SetupError as e:
        raise cli.CliError(str(e)) from None

    ui.say(f"\nTo add to tools.md under `servers:`:\n")
    ui.say("\n".join("  " + ln for ln in mdfile.dump({name: entry}).strip().strip("-").strip().splitlines()))
    if to_set:
        ui.say(f"\nKeys to store in secrets/keys.env (hidden): {', '.join(to_set)}")
    warning = pin_warning(entry["command"], entry.get("args") or []) if command else None
    if warning:
        ui.say(f"\nWarning: {warning}")
    if url and not url.startswith("https://"):
        ui.say("\nWarning: the address is not https, so what is sent to it can be read on the way.")
    if not args.server_class:
        ui.say("\nIts tools are classed by the server's own markings, but a tool it says stays inside your "
              "organisation counts as\nexternal (it waits for your approval) until you confirm that. After "
              f"adding, `ombro tools {name}` shows each tool's class.")
    ui.say("")
    if not args.yes and not ui.yes("add", "Add it?"):
        ui.say("Nothing added.")
        return 1
    try:
        with keys.setting(home, to_set):  # if it can't be added, its keys don't stay
            add_server(home, name, entry)
    except SetupError as e:
        raise cli.CliError(str(e)) from None
    ui.say(f"Added {name} to {home.tools}.")
    if command and not args.server_class and not args.yes:
        confirm_flow(home, name, ui)  # starts it, to ask what it says of its tools
    if url:
        ui.say(f"If it asks you to sign in, connect it once with `ombro connect {name}`. Then see its tools with "
              f"`ombro tools {name}`.")
    else:
        ui.say(f"See its tools with `ombro tools {name}` (this starts the server, which may download it first).")
    ui.say(f"A role uses a tool by naming it in its config.md `tools:` list, as mcp.{name}.<tool>.")
    return 0


def remove_flow(home: Home, args, ui: UI) -> int:
    """Removing an MCP server (`ombro tools remove`), asking through `ui`."""
    cli = _cli()
    name = (args.name or "").strip()
    if not name:
        raise cli.CliError("Give the server's name, e.g. `ombro tools remove sheets`.")
    try:
        servers = ToolSettings.from_dict(_read_tools(home)[0], str(home.tools)).servers
    except SetupError as e:
        raise cli.CliError(str(e)) from None
    if name not in servers:
        raise cli.CliError(f"No server named {name!r} in tools.md. Servers: {', '.join(servers) or 'none'}")
    users = roles_using(home, name)
    if users:
        ui.say(f"These roles name its tools and will no longer be offered them: {', '.join(users)}.")
    refs = sorted(set(_REF.findall(str(servers[name]))))
    going, kept = keys.still_used(home, refs, f"MCP server {name}")
    also = f" and its key{'s' if len(going) > 1 else ''} ({', '.join(going)})" if going else ""
    if not args.yes and not ui.yes("remove", f"Remove {name} and its tool settings from tools.md{also}?"):
        ui.say("Nothing changed.")
        return 1
    dropped = remove_server(home, name)
    gone = keys.forget_unused(home, refs)
    ui.say(f"Removed {name}" + (f" and the settings for {len(dropped)} of its tools" if dropped else "")
          + f" from {home.tools}" + (f", and {', '.join(gone)} from secrets/keys.env" if gone else "") + ".")
    if kept:
        ui.say(f"Kept {', '.join(kept)} in secrets/keys.env: something else uses {'it' if len(kept) == 1 else 'them'}.")
    from ombro import oauth

    if servers[name].get("url") and oauth.status(home, name) != "not signed in":
        ui.say(f"Its sign-in is still kept; forget it with `ombro disconnect {name}`.")
    return 0
