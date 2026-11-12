"""`ombro server add | remove`: MCP servers in tools.md, without editing the file by hand.

A server is either local (a command Ombro starts) or remote (an address). Any
secret it needs is typed into a hidden prompt, stored in secrets/keys.env, and
written into tools.md only as ${NAME}, in `env` (local) or `headers` (remote),
never on the command line. Nothing is written until the user confirms what
will be added. The checks are the same ones a run makes when it loads tools.md.

The building blocks (`server_entry`, `pin_warning`, `add_server`,
`remove_server`) take plain values, so an interface can ask the same
questions in its own way.
"""

from __future__ import annotations

import getpass
import re
import shlex
from typing import Any, Callable

from ombro import keys, mdfile
from ombro.home import Home
from ombro.settings import SERVER_CLASSES, SERVER_NAME, SetupError, ToolSettings

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
    """Add one server to tools.md, keeping everything else. Checked as a run checks tools.md."""
    if not SERVER_NAME.match(name):
        raise ServerError("server names use lower-case letters, digits, - and _ (up to 31)")
    meta, body = _read_tools(home)
    servers = dict(meta.get("servers") or {})
    if name in servers:
        raise ServerError(f"there is already a server named {name!r} in tools.md")
    servers[name] = entry
    new = {**meta, "servers": servers}
    ToolSettings.from_dict(new, str(home.tools))  # raises SetupError if anything is wrong
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
               ask_secret: Callable[[str], str] | None = None) -> int:
    cli = _cli()
    home = cli._home()
    ask = ask or (lambda prompt: input(prompt))
    ask_secret = ask_secret or (lambda prompt: getpass.getpass(prompt))
    if args.action == "remove":
        return _remove(home, args, ask)
    return _add(home, args, ask, ask_secret)


def _add(home: Home, args, ask, ask_secret) -> int:
    cli = _cli()
    name = (args.name or "").strip() or ask("Server name (lower-case, e.g. sheets): ").strip()
    if not SERVER_NAME.match(name or ""):
        raise cli.CliError("Server names use lower-case letters, digits, - and _ (up to 31).")
    try:
        existing = ToolSettings.from_dict(_read_tools(home)[0], str(home.tools)).servers
    except SetupError as e:
        raise cli.CliError(str(e)) from None
    if name in existing:
        raise cli.CliError(f"There is already a server named {name!r} in tools.md. Remove it first with "
                           f"`ombro server remove {name}`, or edit {home.tools}.")
    command, url = args.command, args.url
    if not command and not url:
        kind = ask("Is it local (a command Ombro starts) or remote (an address)? [l/r]: ").strip().lower()
        if kind in ("l", "local"):
            command = ask("Command, with an exact version (e.g. uvx some-mcp-server==1.2.3): ").strip()
        elif kind in ("r", "remote"):
            url = ask("Address (https://…): ").strip()
        else:
            print("Nothing added.")
            return 1
    try:
        server_entry(command=command, url=url)  # the command and address are checked before anything is asked
    except SetupError as e:
        raise cli.CliError(str(e)) from None
    env = dict(_pair(e, "--env") for e in args.env or [])
    headers = dict(_pair(h, "--header") for h in args.header or [])
    secrets = list(args.secret or [])
    if not args.secret and command and not args.env and not args.no_prompt:
        more = ask("Variables it needs that hold a secret (names, space-separated; Enter for none): ").split()
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
        value = ask_secret(f"Value for {var}, kept in secrets/keys.env as {key}{hint}: ").strip()
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

    print(f"\nTo add to tools.md under `servers:`:\n")
    print("\n".join("  " + ln for ln in mdfile.dump({name: entry}).strip().strip("-").strip().splitlines()))
    if to_set:
        print(f"\nKeys to store in secrets/keys.env (hidden): {', '.join(to_set)}")
    warning = pin_warning(entry["command"], entry.get("args") or []) if command else None
    if warning:
        print(f"\nWarning: {warning}")
    if url and not url.startswith("https://"):
        print("\nWarning: the address is not https, so what is sent to it can be read on the way.")
    if not args.server_class:
        print("\nIts tools are classed by the server's own markings; a tool that changes something and is not "
              "marked counts as external\n(it waits in the outbox). After adding, `ombro tools "
              f"{name}` shows each tool's class.")
    if not args.yes and ask("\nAdd it? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing added.")
        return 1
    for key, value in to_set.items():
        keys.set_key(home, key, value)
    try:
        add_server(home, name, entry)
    except SetupError as e:
        raise cli.CliError(str(e)) from None
    print(f"Added {name} to {home.tools}.")
    if url:
        print(f"If it asks you to sign in, connect it once with `ombro connect {name}`. Then see its tools with "
              f"`ombro tools {name}`.")
    else:
        print(f"See its tools with `ombro tools {name}` (this starts the server, which may download it first).")
    print(f"A role uses a tool by naming it in its config.md `tools:` list, as mcp.{name}.<tool>.")
    return 0


def _remove(home: Home, args, ask) -> int:
    cli = _cli()
    name = (args.name or "").strip()
    if not name:
        raise cli.CliError("Give the server's name, e.g. `ombro server remove sheets`.")
    try:
        servers = ToolSettings.from_dict(_read_tools(home)[0], str(home.tools)).servers
    except SetupError as e:
        raise cli.CliError(str(e)) from None
    if name not in servers:
        raise cli.CliError(f"No server named {name!r} in tools.md. Servers: {', '.join(servers) or 'none'}")
    users = roles_using(home, name)
    if users:
        print(f"These roles name its tools and will no longer be offered them: {', '.join(users)}.")
    if not args.yes and ask(f"Remove {name} and its tool settings from tools.md? [y/N] ").strip().lower() \
            not in ("y", "yes"):
        print("Nothing changed.")
        return 1
    dropped = remove_server(home, name)
    print(f"Removed {name}" + (f" and the settings for {len(dropped)} of its tools" if dropped else "")
          + f" from {home.tools}.")
    refs = sorted(set(_REF.findall(str(servers[name]))))
    if refs:
        print(f"Its keys are still in secrets/keys.env ({', '.join(refs)}); remove any you no longer need with "
              "`ombro keys remove NAME`.")
    from ombro import oauth

    if servers[name].get("url") and oauth.status(home, name) != "not signed in":
        print(f"Its sign-in is still kept; forget it with `ombro disconnect {name}`.")
    return 0
