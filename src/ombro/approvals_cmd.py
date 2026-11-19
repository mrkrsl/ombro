"""`ombro approvals` and `ombro tools`: actions waiting for approval, and the outward tools behind them.

Decisions are made on named actions only. There is no "approve all". The
command line shows each named action in full and asks one question; an
interface that has already shown and selected them passes --yes. Every
command can print JSON (--json) for an interface to read.
"""

from __future__ import annotations

import json
import subprocess
import sys
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


def _show(a: outbox.Action, home: Home | None = None) -> None:
    m = a.meta
    age, days = _age(m.get("staged_at"))
    print(f"{a.ref}   {_status_label(a).upper()}")
    print(f"  Tool      {a.tool} ({m.get('class', '?')})")
    print(f"  Role      {m.get('role', '?')}")
    print(f"  Reason    {m.get('reason') or '(none given)'}")
    print(f"  Waiting   since {m.get('staged_at')} ({age}), from job {a.job_id}")
    if days >= OLD_DAYS and a.status == "pending":
        print(f"  Note      waiting for more than {OLD_DAYS} days; check it still makes sense")
    if a.edited:
        print("  Edited    yes: the arguments were changed while it waited")
    for key, label in (("note", "Note"), ("last_error", "Last try"), ("error", "Error"), ("result", "Result")):
        if m.get(key):
            print(f"  {label:<9} {' '.join(str(m[key]).split())[:500]}")
    if a.tool == outbox.PLACE_TOOL and isinstance(a.arguments, dict):
        import os

        print(f"  Copies    {m.get('source', '?')} (the copy kept with this action: {m.get('file', '?')})")
        if home is not None:
            # Where it would really go, spelt as on disk, so an odd spelling cannot hide the place.
            try:
                final, _ = outbox.placement_check(home, a)
                print(f"  Goes to   {final}")
            except outbox.OutboxError as e:
                print(f"  Refused   {e}")
        to = str(a.arguments.get("to") or "")
        if to and os.path.lexists(os.path.expanduser(to)):
            print(f"  Replaces  {to} already exists; " + ("approving replaces it" if a.arguments.get("overwrite")
                                                          else "approving is refused unless overwrite is true"))
    if a.tool == outbox.SKILL_TOOL and home is not None:
        _show_skill(a, home)
        print(f"  File      {a.path}")
        return
    print("  Arguments")
    text = yaml.safe_dump(a.arguments if a.arguments is not None else {}, sort_keys=False, allow_unicode=True,
                          default_flow_style=False).rstrip()
    for line in text.splitlines() or ["{}"]:
        print(f"    {line}")
    print(f"  File      {a.path}")


def _show_skill(a: outbox.Action, home: Home) -> None:
    """A skill proposal: the roles it is for, any signs of injected text, and the whole text,
    or the diff against today's version."""
    try:
        v = outbox.skill_view(home, a)
    except outbox.OutboxError as e:
        print(f"  Problem   {e}")
        return
    print(f"  Skill     {v['name']} ({'a new version' if v['live'] else 'new'})")
    print(f"  For       {', '.join(v['for']) or 'no role named'}; approving adds it to their skills: lines")
    if v.get("adds"):
        print(f"  Adds      {v['adds']}")
    if v.get("jobs"):
        print(f"  Jobs      earlier jobs of its kind: {', '.join(v['jobs'])}")
    print("  Declining it means the Lead won't suggest it again.")
    if v["flags"]:
        print(f"  Warning   signs of injected text in it: {', '.join(v['flags'])}. Read it closely.")
    if v["changed_since"] and a.status == "pending":
        print("  Changed   the live skill has changed since this was proposed; approving is refused until you "
              f"save it again with `ombro approvals edit {a.ref}`")
    if v["live"]:
        print("  Diff against today's version:")
        body = v.get("diff") or "(no difference)"
    else:
        print("  The proposed SKILL.md:")
        body = v["text"]
    for line in body.rstrip().splitlines():
        print(f"    {line}")


def _find_all(home: Home, refs: list[str]) -> list[outbox.Action]:
    out: list[outbox.Action] = []
    for r in refs:
        a = outbox.find(home, r)
        if any(x.path == a.path for x in out):
            raise outbox.OutboxError(f"{a.ref} is named twice")
        out.append(a)
    return out


def _confirm(label: str) -> bool:
    return _cli()._confirm(label)


def cmd_approvals(args) -> int:
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
                raise cli.CliError("Name one action, e.g. `ombro approvals show ab12-3`.")
            a = outbox.find(home, refs[0])
            outbox.settle(a.job_dir)
            a = outbox.load(a.path)
            _print_json(a.as_dict()) if args.json else _show(a, home)
            return 0
        if action in ("approve", "decline"):
            return _decide(home, action, refs, args)
        if action == "edit":
            return _edit(home, refs, args)
        if action == "resolve":
            return _resolve(home, refs, args)
    except outbox.OutboxError as e:
        raise cli.CliError(str(e)) from None
    raise cli.CliError(f"Unknown approvals action {action!r}.")


def _list(home: Home, args) -> int:
    crew = _cli()._crew(home, args.crew) if args.crew else None
    if args.all:
        from ombro import jobs

        items = []
        for d in jobs.all_jobs(home, crew):
            outbox.settle(d)
            items += outbox.actions(d)
    else:
        items = outbox.waiting(home, crew)
    if args.json:
        _print_json([a.as_dict() for a in items])
        return 0
    if not items:
        print("Nothing is waiting for approval." if not args.all else "Nothing has ever waited for approval.")
        return 0
    what = "that waited for approval" if args.all else "waiting for approval"
    print(f"{len(items)} action(s) {what}:\n")
    print(f"  {'REF':<10} {'STATUS':<16} {'TOOL':<32} {'WAITING':<9} WHAT")
    for a in items:
        age, _ = _age(a.meta.get("staged_at"))
        print(f"  {a.ref:<10} {_status_label(a):<16} {a.tool[:32]:<32} {age:<9} {a.summary(70)}")
    print("\nSee one with `ombro approvals show <ref>`; then `ombro approvals approve <ref> ...` or "
          "`ombro approvals decline <ref> ...`.")
    if any(a.status == "uncertain" for a in items):
        print("An uncertain action may or may not have happened; settle it with "
              "`ombro approvals resolve <ref> sent` or `not-sent`.")
    return 0


def _decide(home: Home, action: str, refs: list[str], args) -> int:
    cli = _cli()
    if not refs:
        raise cli.CliError(f"Name the actions to {action}, e.g. `ombro approvals {action} ab12-1 ab12-3`. "
                           "There is no 'all'.")
    items = _find_all(home, refs)
    for a in items:
        outbox.settle(a.job_dir)
    items = [outbox.load(a.path) for a in items]
    not_pending = [a for a in items if a.status != "pending"]
    if not_pending:
        raise cli.CliError("; ".join(f"{a.ref} is {_status_label(a)}" for a in not_pending)
                           + f", so nothing was {action}d")
    if not args.json:
        for a in items:
            _show(a, home)
            print()
    if not args.yes:
        verb = "Run" if action == "approve" else "Decline"
        tail = "" if action == "approve" else " They will never run."
        if not _confirm(f"{verb} {'this action' if len(items) == 1 else f'these {len(items)} actions'}?{tail}"):
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
                f"`ombro approvals resolve {a.ref} sent` or `not-sent`.")
    return a.status


def _edit(home: Home, refs: list[str], args) -> int:
    cli = _cli()
    if len(refs) != 1:
        raise cli.CliError("Name one action, e.g. `ombro approvals edit ab12-3`.")
    a = outbox.find(home, refs[0])
    if a.tool == outbox.SKILL_TOOL and args.arguments is None:
        return _edit_skill(home, a, args)
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
    _show(done, home)
    print(f"\nSaved. It is still waiting: approve it with `ombro approvals approve {done.ref}`.")
    return 0


def _edit_skill(home: Home, a: outbox.Action, args) -> int:
    """Open a proposed skill's text in the editor; Ombro checks it before saving it."""
    cli = _cli()
    from ombro.interview import editor_command

    cmd = editor_command(home)
    if not cmd:
        raise cli.CliError(f"No editor is set (`editor` in preferences/config.md, or $EDITOR). Edit "
                           f"{outbox._kept_skill(a)} by hand, or name the roles with --arguments "
                           "'{\"name\": ..., \"for\": [...]}'.")
    current = outbox.skill_text(a)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(current)
        tmp = Path(f.name)
    try:
        subprocess.run([*cmd, str(tmp)], check=False)
        text = tmp.read_text(encoding="utf-8")
    except OSError as e:
        raise cli.CliError(f"Could not start the editor {cmd[0]!r}: {e}") from None
    finally:
        tmp.unlink(missing_ok=True)
    done = outbox.edit_skill(home, a, text)
    if args.json:
        _print_json(done.as_dict())
        return 0
    _show(done, home)
    print(f"\nSaved. It is still waiting: approve it with `ombro approvals approve {done.ref}`.")
    return 0


def _resolve(home: Home, refs: list[str], args) -> int:
    cli = _cli()
    if len(refs) != 2 or refs[1] not in ("sent", "not-sent"):
        raise cli.CliError("Use `ombro approvals resolve <ref> sent` (it happened) or `not-sent` (it did not).")
    a = outbox.find(home, refs[0])
    outbox.settle(a.job_dir)
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
    if args.server == "composio" and "composio" not in settings.servers:
        return _composio_tools(home, settings, pref, args)
    if args.server:
        return _server_tools(home, settings, pref, args)
    from ombro import sandbox, views

    rows = views.tool_list(home, settings)
    if args.json:
        _print_json({**rows, "outward": pref})
        return 0
    print("Native tools:")
    for r in rows["native"]:
        extra = f"  provider {r['provider']}" if r["provider"] else ""
        extra += f"  max {r['max_calls']} calls" if r["max_calls"] else ""
        if r["tool"] == "code.run":
            extra += ("  sandbox: " + (f"not available ({rows['sandbox']})" if rows["sandbox"] else
                                       {"macos": "macOS system sandbox", "linux": "bubblewrap"}.get(
                                           sandbox.kind() or "", "ready")))
        print(f"  {r['tool']:<18}{extra}")
    print("\nShared folders: " + (", ".join(rows["folders"]) if rows["folders"] else "none")
          + " (`ombro folder`)")
    print("\nMCP servers (tools.md `servers:`):")
    if not rows["servers"]:
        print("  none yet. Add one with `ombro server add <name>` (or under `servers:` in tools.md), then see "
              "its tools with `ombro tools <server>`.")
    for r in rows["servers"]:
        how = r["url"] or " ".join([r["command"] or "", *map(str, r["args"])])
        if r["url"]:
            from ombro import oauth

            how += f"  ({oauth.status(home, r['server'])})"
        print(f"  {r['server']:<18}{how}")
    print("\nWeb APIs (tools.md `apis:`), offered to every crew member:")
    if not rows["apis"]:
        print("  none yet. Add one with `ombro tools add <name>`, or in the desktop app (Settings › Tools).")
    what = {"read-only": "only looks things up", "internal": "changes things in your own tools",
            "external": "can change things or reach outside"}
    for r in rows["apis"]:
        key = "" if not r["key"] else f", key {r['key']}" + ("" if r["key_set"] else " NOT SET")
        print(f"  {r['name']:<18}{r['url']}  ({what.get(r['access'], r['access'])}, max {r['calls']} calls{key})")
    if rows["apis"]:
        print("  Change one with `ombro tools edit <name>`.")
    print(f"\nOutward permissions by default: {outward.describe(pref)}.")
    print("  " + "; ".join(f"{p}: {', '.join(f'{c} {v}' for c, v in outward.PRESETS[p].items())}"
                         for p in outward.PRESETS))
    return 0


def _composio_tools(home: Home, settings, pref: dict, args) -> int:
    """`ombro tools composio`: each Composio tool's class, where it came from, and its permission."""
    cli = _cli()
    from ombro import composio
    from ombro.tools import ToolError

    if not settings.composio:
        raise cli.CliError("Composio isn't connected. Connect your account in the app (Settings › Tools).")
    try:
        rows = composio.tool_classes(home, settings)
    except ToolError as e:
        raise cli.CliError(f"Could not list Composio's tools: {e}") from None
    for r in rows:
        r["permission"] = outward.decide(pref, r["tool"], r["class"], settings)
    if args.json:
        _print_json(rows)
        return 0
    print(f"Composio's tools ({len(rows)}), with the permission each gets under {pref['preset']}:\n")
    print(f"  {'TOOL':<52} {'CLASS':<10} {'FROM':<8} {'PERMISSION':<10} DESCRIPTION")
    for r in rows:
        print(f"  {r['tool']:<52} {r['class']:<10} {r['class_from']:<8} {r['permission']:<10} {r['description'][:50]}")
    print("\nFROM: marking (Composio says it only reads), name (from the words of its name: a reading word "
          "with\nnothing in the name that makes, shares, links or marks something; one that sends, deletes or "
          "spends;\nor a change in your own document tools), tool (set in tools.md), or default (composio's "
          "class in\ntools.md). Change one under `tools:` in tools.md, e.g.\n    "
          f"{rows[0]['tool'] if rows else 'composio.gmail.FETCH_EMAILS'}: {{class: read-only}}")
    return 0


def _server_tools(home: Home, settings, pref: dict, args) -> int:
    cli = _cli()
    from ombro.mcp import McpClient, McpError, ServerConfig

    name = args.server
    if name not in settings.servers:
        raise cli.CliError(f"No server named {name!r} in tools.md. Servers: {', '.join(settings.servers) or 'none'}")
    warning = ServerConfig.from_dict(name, settings.servers[name]).argv_warning()
    if warning and not args.json:
        print(f"Warning: {warning}\n")
    try:
        with McpClient(home, ServerConfig.from_dict(name, settings.servers[name])) as c:
            listed = c.list_tools()
            version = c.version
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
    print(f"Tools of {name} ({len(rows)}; it speaks MCP {version}), with the permission each gets under "
          f"{pref['preset']}:\n")
    print(f"  {'TOOL':<44} {'CLASS':<10} {'FROM':<8} {'PERMISSION':<10} DESCRIPTION")
    for r in rows:
        desc = " ".join(r["description"].split())[:50]
        print(f"  {r['tool']:<44} {r['class']:<10} {r['class_from']:<8} {r['permission']:<10} {desc}")
    print("\nClasses: read-only changes nothing. internal changes things in your organisation's own tools, "
          "even when\ncolleagues see it; it runs without approval because the role is trusted, which is not the "
          "same as harmless.\nexternal reaches people outside your organisation, or the public, or spends "
          "money. FROM: marking (the\nserver's own claim), server or tool (set in tools.md), unconfirmed (the "
          "server says internal, which you\nhaven't confirmed yet, so external) or default (a tool that changes "
          "something and is not classed counts as external).")
    defaulted = [r["tool"] for r in rows if r["class_from"] == "default"]
    marked = [r for r in rows if r["class_from"] == "marking" and r["class"] != "read-only"]
    if defaulted:
        print(f"\n{len(defaulted)} tool(s) are external only by default, because the server does not say "
              "otherwise. Leave any that\ncan reach people outside your organisation (email, invitations, posts). "
              "For one that "
              f"stays inside, add under `tools:` in tools.md:\n    {defaulted[0]}: {{class: internal}}")
        if not marked:
            print("If every tool of this server that is not read-only stays inside, one line on its entry "
                  "under `servers:` covers them all:\n    class: internal")
        else:
            print("This server marks some tools itself, so its tools are of mixed kinds: set them one by one, "
                  "not with `class:` on the server.")
    waiting = [r["tool"].split(".", 2)[2] for r in rows if r["class_from"] == "unconfirmed"]
    if waiting:
        from ombro import server_cmd

        claims = [{"tool": r["tool"].split(".", 2)[2], "description": r["description"], "class_from": r["class_from"]}
                  for r in rows]
        if getattr(args, "confirm", False):
            server_cmd.confirm_internal(home, name, waiting)
            print(f"\nConfirmed: {len(waiting)} tool(s) of {name} count as internal.")
        elif sys.stdin.isatty():
            from ombro.ui import ConsoleUI

            server_cmd.confirm_flow(home, name, ConsoleUI(), claims)
        else:
            print("\n" + "\n".join(server_cmd.claims_text(name, claims)))
            print(f"Confirm them with `ombro tools {name} --confirm`.")
    print("\nGive a role a tool by naming it in its config.md `tools:` list.")
    return 0
