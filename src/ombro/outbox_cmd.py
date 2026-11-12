"""`ombro outbox` and `ombro tools`: staged actions, and the outward tools behind them.

Decisions are made on named actions only. There is no "approve all". The
command line shows each named action in full and asks one question; an
interface that has already shown and selected them passes --yes. Every
command can print JSON (--json) for an interface to read.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ombro import outbox, outward
from ombro.home import Home
from ombro.settings import SetupError, load_tool_settings

OLD_DAYS = 7


def _cli():
    from ombro import cli

    return cli


def _age(ts: Any) -> tuple[str, float]:
    try:
        when = datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return "?", 0.0
    secs = max(0.0, time.time() - when)
    if secs < 3600:
        text = f"{int(secs // 60)}m ago"
    elif secs < 86400:
        text = f"{int(secs // 3600)}h ago"
    else:
        text = f"{int(secs // 86400)}d ago"
    return text, secs / 86400


def _status_label(a: outbox.Action) -> str:
    label = {"pending": "waiting", "executed": "done"}.get(a.status, a.status)
    if a.status == "pending" and a.edited:
        label += ", edited"
    return label


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _show(a: outbox.Action) -> None:
    m = a.meta
    age, days = _age(m.get("staged_at"))
    print(f"{a.ref}   {_status_label(a).upper()}")
    print(f"  Tool      {a.tool} ({m.get('class', '?')})")
    print(f"  Role      {m.get('role', '?')}")
    print(f"  Reason    {m.get('reason') or '(none given)'}")
    print(f"  Staged    {m.get('staged_at')} ({age}) in run {a.run_id}")
    if days >= OLD_DAYS and a.status == "pending":
        print(f"  Note      staged more than {OLD_DAYS} days ago; check it still makes sense")
    if a.edited:
        print("  Edited    yes: the arguments were changed after staging")
    for key, label in (("note", "Note"), ("last_error", "Last try"), ("error", "Error"), ("result", "Result")):
        if m.get(key):
            print(f"  {label:<9} {' '.join(str(m[key]).split())[:500]}")
    print("  Arguments")
    text = yaml.safe_dump(a.arguments if a.arguments is not None else {}, sort_keys=False, allow_unicode=True,
                          default_flow_style=False).rstrip()
    for line in text.splitlines() or ["{}"]:
        print(f"    {line}")
    print(f"  File      {a.path}")


def _find_all(home: Home, refs: list[str]) -> list[outbox.Action]:
    out: list[outbox.Action] = []
    for r in refs:
        a = outbox.find(home, r)
        if any(x.path == a.path for x in out):
            raise outbox.OutboxError(f"{a.ref} is named twice")
        out.append(a)
    return out


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        print()
        return False


def cmd_outbox(args) -> int:
    cli = _cli()
    home = cli._home()
    cli._reap(home)
    action = args.action or "list"
    refs = args.refs or []
    try:
        if action == "list":
            return _list(home, args)
        if action == "show":
            if len(refs) != 1:
                raise cli.CliError("Name one action, e.g. `ombro outbox show ab12-3`.")
            a = outbox.find(home, refs[0])
            outbox.settle(a.run_dir)
            a = outbox.load(a.path)
            _print_json(a.as_dict()) if args.json else _show(a)
            return 0
        if action in ("approve", "decline"):
            return _decide(home, action, refs, args)
        if action == "edit":
            return _edit(home, refs, args)
        if action == "resolve":
            return _resolve(home, refs, args)
    except outbox.OutboxError as e:
        raise cli.CliError(str(e)) from None
    raise cli.CliError(f"Unknown outbox action {action!r}.")


def _list(home: Home, args) -> int:
    crew = _cli()._crew(home, args.crew) if args.crew else None
    if args.all:
        from ombro import runs

        items = []
        for _, d in runs.all_runs(home, crew):
            outbox.settle(d)
            items += outbox.actions(d)
    else:
        items = outbox.waiting(home, crew)
    if args.json:
        _print_json([a.as_dict() for a in items])
        return 0
    if not items:
        print("Nothing is waiting in the outbox." if not args.all else "Nothing has been staged.")
        return 0
    what = "staged" if args.all else "waiting for a decision"
    print(f"{len(items)} action(s) {what}:\n")
    print(f"  {'REF':<10} {'STATUS':<16} {'TOOL':<32} {'STAGED':<9} WHAT")
    for a in items:
        age, _ = _age(a.meta.get("staged_at"))
        print(f"  {a.ref:<10} {_status_label(a):<16} {a.tool[:32]:<32} {age:<9} {a.summary(70)}")
    print("\nSee one with `ombro outbox show <ref>`; then `ombro outbox approve <ref> ...` or "
          "`ombro outbox decline <ref> ...`.")
    if any(a.status == "uncertain" for a in items):
        print("An uncertain action may or may not have happened; settle it with "
              "`ombro outbox resolve <ref> sent` or `not-sent`.")
    return 0


def _decide(home: Home, action: str, refs: list[str], args) -> int:
    cli = _cli()
    if not refs:
        raise cli.CliError(f"Name the actions to {action}, e.g. `ombro outbox {action} ab12-1 ab12-3`. "
                           "There is no 'all'.")
    items = _find_all(home, refs)
    for a in items:
        outbox.settle(a.run_dir)
    items = [outbox.load(a.path) for a in items]
    not_pending = [a for a in items if a.status != "pending"]
    if not_pending:
        raise cli.CliError("; ".join(f"{a.ref} is {_status_label(a)}" for a in not_pending)
                           + f", so nothing was {action}d")
    if not args.json:
        for a in items:
            _show(a)
            print()
    if not args.yes:
        verb = "Run" if action == "approve" else "Decline"
        tail = "" if action == "approve" else " They will never run."
        if not _confirm(f"{verb} {'this action' if len(items) == 1 else f'these {len(items)} actions'}?{tail} [y/N] "):
            print("Nothing was done.")
            return 1
    results, code = [], 0
    for a in items:
        try:
            done = outbox.approve(home, a) if action == "approve" else outbox.decline(home, a)
        except outbox.OutboxError as e:
            code = 1
            results.append({"ref": a.ref, "status": outbox.load(a.path).status, "error": str(e)})
            if not args.json:
                print(f"{a.ref}: {e}")
            continue
        results.append(done.as_dict())
        if done.status in ("failed", "uncertain"):
            code = 1
        if not args.json:
            print(f"{done.ref}: {_outcome(done)}")
    if args.json:
        _print_json(results)
    return code


def _outcome(a: outbox.Action) -> str:
    m = a.meta
    if a.status == "executed":
        return "done." + (f" {' '.join(str(m.get('result')).split())[:300]}" if m.get("result") else "")
    if a.status == "declined":
        return "declined; it will never run."
    if a.status == "failed":
        return f"failed: {' '.join(str(m.get('error') or '').split())[:300]}"
    if a.status == "uncertain":
        return (f"uncertain: {' '.join(str(m.get('note') or '').split())[:300]} Settle it with "
                f"`ombro outbox resolve {a.ref} sent` or `not-sent`.")
    return a.status


def _edit(home: Home, refs: list[str], args) -> int:
    cli = _cli()
    if len(refs) != 1:
        raise cli.CliError("Name one action, e.g. `ombro outbox edit ab12-3`.")
    a = outbox.find(home, refs[0])
    if args.arguments is not None:
        try:
            new = json.loads(args.arguments)
        except ValueError as e:
            raise cli.CliError(f"--arguments must be a JSON object: {e}") from None
    else:
        from ombro.interview import editor_command

        cmd = editor_command(home)
        if not cmd:
            raise cli.CliError(f"No editor is set (`editor` in preferences/config.md, or $EDITOR). Edit "
                               f"`arguments` in {a.path} by hand, or pass --arguments '<json>'.")
        current = yaml.safe_dump(a.arguments if a.arguments is not None else {}, sort_keys=False,
                                 allow_unicode=True, default_flow_style=False)
        header = (f"# Arguments for {a.ref} ({a.tool}). Change them and save; Ombro checks them before\n"
                  "# saving. The action then waits for your approval. Lines starting with # are ignored.\n")
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(header + current)
            tmp = Path(f.name)
        try:
            subprocess.run([*cmd, str(tmp)], check=False)
            text = tmp.read_text(encoding="utf-8")
        except OSError as e:
            raise cli.CliError(f"Could not start the editor {cmd[0]!r}: {e}") from None
        finally:
            tmp.unlink(missing_ok=True)
        try:
            new = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise cli.CliError(f"The edit is not valid YAML, so nothing was saved: {e}") from None
        if new is None:
            new = {}
    if new == a.arguments:
        print("No change; nothing was saved.")
        return 0
    done = outbox.edit(home, a, new)
    if args.json:
        _print_json(done.as_dict())
        return 0
    _show(done)
    print(f"\nSaved. It is still waiting: approve it with `ombro outbox approve {done.ref}`.")
    return 0


def _resolve(home: Home, refs: list[str], args) -> int:
    cli = _cli()
    if len(refs) != 2 or refs[1] not in ("sent", "not-sent"):
        raise cli.CliError("Use `ombro outbox resolve <ref> sent` (it happened) or `not-sent` (it did not).")
    a = outbox.find(home, refs[0])
    outbox.settle(a.run_dir)
    done = outbox.resolve(home, outbox.load(a.path), refs[1] == "sent")
    if args.json:
        _print_json(done.as_dict())
    elif done.status == "executed":
        print(f"{done.ref}: marked as done.")
    else:
        print(f"{done.ref}: marked as not done; it is waiting again. Approve or decline it as usual.")
    return 0


# ---------------------------------------------------------------------------
# ombro tools


def cmd_tools(args) -> int:
    cli = _cli()
    home = cli._home()
    settings = load_tool_settings(home)
    try:
        pref = outward.resolve(home, {})
    except SetupError as e:
        raise cli.CliError(str(e)) from None
    if args.server:
        return _server_tools(home, settings, pref, args)
    from ombro.settings import PROVIDER_TOOLS

    rows = {"native": [], "servers": []}
    for name in ("web.search", "web.fetch", "workspace.read", "workspace.write", "workspace.list"):
        cfg = settings.tools.get(name, {})
        rows["native"].append({"tool": name, "provider": settings.provider_for(name) if name in PROVIDER_TOOLS else None,
                               "max_calls": cfg.get("max_calls")})
    for name, cfg in settings.servers.items():
        rows["servers"].append({"server": name, "command": cfg.get("command"), "args": cfg.get("args") or [],
                                "url": cfg.get("url")})
    if args.json:
        _print_json({**rows, "outward": pref})
        return 0
    print("Native tools:")
    for r in rows["native"]:
        extra = f"  provider {r['provider']}" if r["provider"] else ""
        extra += f"  max {r['max_calls']} calls" if r["max_calls"] else ""
        print(f"  {r['tool']:<18}{extra}")
    print("\nMCP servers (tools.md `servers:`):")
    if not rows["servers"]:
        print("  none yet. Add one under `servers:` in tools.md, then see its tools with `ombro tools <server>`.")
    for r in rows["servers"]:
        how = r["url"] or " ".join([r["command"] or "", *map(str, r["args"])])
        if r["url"]:
            from ombro import oauth

            how += f"  ({oauth.status(home, r['server'])})"
        print(f"  {r['server']:<18}{how}")
    print(f"\nOutward permissions by default: {outward.describe(pref)}.")
    print("  " + "; ".join(f"{p}: {', '.join(f'{c} {v}' for c, v in outward.PRESETS[p].items())}"
                         for p in outward.PRESETS))
    return 0


def _server_tools(home: Home, settings, pref: dict, args) -> int:
    cli = _cli()
    from ombro.mcp import McpClient, McpError, ServerConfig

    name = args.server
    if name not in settings.servers:
        raise cli.CliError(f"No server named {name!r} in tools.md. Servers: {', '.join(settings.servers) or 'none'}")
    try:
        with McpClient(home, ServerConfig.from_dict(name, settings.servers[name])) as c:
            listed = c.list_tools()
    except McpError as e:
        raise cli.CliError(f"Could not list the tools of {name}: {e}") from None
    rows = []
    for t in listed:
        full = f"mcp.{name}.{t.name}"
        effect, source = outward.tool_class(settings, full, t.annotations)
        rows.append({"tool": full, "class": effect, "class_from": source,
                     "permission": outward.decide(pref, full, effect, settings), "description": t.description})
    if args.json:
        _print_json(rows)
        return 0
    print(f"Tools of {name} ({len(rows)}), with the permission each gets under {pref['preset']}:\n")
    print(f"  {'TOOL':<44} {'CLASS':<9} {'FROM':<8} {'PERMISSION':<10} DESCRIPTION")
    for r in rows:
        desc = " ".join(r["description"].split())[:50]
        print(f"  {r['tool']:<44} {r['class']:<9} {r['class_from']:<8} {r['permission']:<10} {desc}")
    print("\nClasses: read changes nothing; work is documents, sheets, databases, files and drafts; external "
          "reaches people\nor the public, or spends money. FROM: marking (the server's own claim), server or "
          "tool (set in tools.md),\nor default (a tool that changes something and is not classed counts as "
          "external).")
    defaulted = [r["tool"] for r in rows if r["class_from"] == "default"]
    marked = [r for r in rows if r["class_from"] == "marking" and r["class"] != "read"]
    if defaulted:
        print(f"\n{len(defaulted)} tool(s) are external only by default, because the server does not say "
              "otherwise. Leave any that\nreach people or the public (email, invitations, posts). For one that "
              f"only does work, add under `tools:` in tools.md:\n    {defaulted[0]}: {{class: work}}")
        if not marked:
            print("If every tool of this server that is not read only does work, one line on its entry under "
                  "`servers:` covers them all:\n    class: work")
        else:
            print("This server marks some tools itself, so its tools are of mixed kinds: set them one by one, "
                  "not with `class:` on the server.")
    print("\nGive a role a tool by naming it in its config.md `tools:` list.")
    return 0
