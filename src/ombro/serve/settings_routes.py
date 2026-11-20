"""The Settings pages' actions: model providers, crews and roles, tools and
MCP servers, skills. Each calls the core function the terminal or command line calls.

Deleting has two steps: a GET …/check says what stops it and what to know, then DELETE does
it (refusing, 409, what the check refuses). Secrets come in on writes and never go back out:
replies name a key, never its value.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from starlette.requests import Request
from starlette.routing import Route

from ombro import keys, views
from ombro.serve.app import HttpError, Serve, _handler, _need


def _removing(fn):
    """A RemoveError is a conflict with the state of things (409), not a bad request."""
    from ombro.removing import RemoveError

    def run(*args):
        try:
            return fn(*args)
        except RemoveError as e:
            raise HttpError(409, str(e)) from None

    return run


def _secret_pairs(data: dict[str, Any], key: str = "secrets") -> dict[str, str]:
    """{name: value} from the body; blank values are left out (an edit keeps what is there)."""
    raw = data.get(key) or {}
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        raise HttpError(400, f"`{key}` must be names and values")
    return {k.strip(): v.strip() for k, v in raw.items() if k.strip() and v.strip()}


def settings_routes(s: Serve) -> list[Route]:
    home = s.home
    after = s.counts_soon

    # -- model providers -------------------------------------------------------------

    def add_provider(r: Request, data: dict[str, Any]) -> Any:
        from ombro import models

        endpoint = _need(data, "endpoint").strip().rstrip("/")
        key_value = str(data.get("key") or "").strip()
        key_name = (str(data.get("key_name") or "").strip() or models.key_name_for(endpoint)) if key_value else ""
        if key_value and not keys.valid_name(key_name):
            raise HttpError(400, "Key names use capitals, digits and underscores, starting with a letter.")
        factory = None
        if s.sessions.client_factory is not None:
            factory = lambda spec, h=None: s.sessions.client_factory(spec)  # noqa: E731 - tests' scripted model
        with keys.setting(home, {key_name: key_value} if key_value else {}):  # a refused key isn't kept
            added = models.add_provider(home, endpoint, key_name or None, str(data.get("model") or ""), factory)
        return {**views.model_list(home), "added": added}

    def edit_provider(r: Request, data: dict[str, Any]) -> Any:
        from ombro import models

        endpoint = _need(data, "endpoint")
        current = next((c for c in models.connections(home) if c["endpoint"] == endpoint), None)
        if current is None:
            raise LookupError(f"no provider at {endpoint}")
        new_endpoint = str(data.get("new_endpoint") or "").strip() or None
        key_value = str(data.get("key") or "").strip()
        key_name = None
        if key_value:
            key_name = current["key"] or models.key_name_for(new_endpoint or endpoint)
            keys.set_key(home, key_name, key_value)
        if data.get("no_key") is True:
            key_name = ""
        models.edit_provider(home, endpoint, new_endpoint=new_endpoint, key_name=key_name)
        return views.model_list(home)

    def check_provider(r: Request, data: dict[str, Any]) -> Any:
        from ombro import models

        return models.check_remove_provider(home, _need(data, "endpoint"))

    @_removing
    def remove_provider(r: Request, data: dict[str, Any]) -> Any:
        from ombro import models

        endpoint = _need(data, "endpoint")
        removed = models.remove_provider(home, endpoint)
        if endpoint == models.PLAN:  # the ChatGPT plan: removing it signs out too
            from ombro import chatgpt

            chatgpt.sign_out(home)
            views.CHATGPT_SIGNING.clear()
        return {**views.model_list(home), "removed": removed}

    # -- the ChatGPT plan (Sign in with ChatGPT) -----------------------------------------

    def chatgpt_sign_in(r: Request, data: dict[str, Any]) -> Any:
        """Start signing in with ChatGPT on a thread and hand back the page to open. GET /models
        shows when it has finished (chatgpt.signing_in, problem, welcome)."""
        from ombro import chatgpt, oauth

        signing = views.CHATGPT_SIGNING
        opened: dict[str, str] = {}
        ready = threading.Event()
        receiver = None
        if data.get("hand_over"):
            # The app on another computer catches the browser's answer there and hands it over.
            port = data.get("redirect_port")
            if port is not None and (not isinstance(port, int) or not 0 < port < 65536):
                raise HttpError(400, "`redirect_port` must be a port number")
            receiver = oauth.HandOver(port, path=chatgpt.CALLBACK_PATH)
            s.handovers[":chatgpt"] = receiver

        def open_browser(url: str) -> None:
            opened["url"] = url
            ready.set()

        def run() -> None:
            try:
                done = chatgpt.sign_in(home, open_browser=open_browser, say=lambda _t: None, receiver=receiver,
                                       back_to="the Ombro app")
                signing.update({"signing_in": False, "problem": None if done["plan"] else chatgpt.NOT_ALLOWED,
                                "welcome": done["welcome"]})
            except (oauth.SignInError, Exception) as e:  # noqa: BLE001 - shown on the card
                text = str(e)
                signing.update({"signing_in": False, "problem": text[:1].upper() + text[1:] if text else
                                "The sign-in didn't finish."})
            finally:
                ready.set()
                if s.handovers.get(":chatgpt") is receiver:
                    s.handovers.pop(":chatgpt", None)

        signing.clear()
        signing.update({"signing_in": True})
        threading.Thread(target=run, daemon=True, name="sign-in-chatgpt").start()
        ready.wait(30)
        if "url" in opened and signing.get("signing_in"):
            return {"url": opened["url"], "state": "waiting"}
        return {"url": None, "state": "failed", "problem": signing.get("problem")}

    def chatgpt_answer(r: Request, data: dict[str, Any]) -> Any:
        from ombro import oauth

        receiver = s.handovers.get(":chatgpt")
        if receiver is None:
            raise LookupError("no ChatGPT sign-in is waiting for an answer")
        answer = oauth.answer_from(str(data.get("url") or ""))
        if not answer:
            raise HttpError(400, "That address has no sign-in answer in it. Copy the whole address from "
                                 "the browser's address bar.")
        if not receiver.give(answer):
            raise HttpError(400, "That isn't the answer to this sign-in. Start the sign-in again.")
        return {"state": "answered"}

    def chatgpt_welcomed(r: Request, data: dict[str, Any]) -> Any:
        from ombro import chatgpt

        chatgpt.mark_welcomed(home)
        views.CHATGPT_SIGNING.pop("welcome", None)
        return views.model_list(home)

    def chatgpt_sign_out(r: Request) -> Any:
        from ombro import chatgpt

        done = chatgpt.sign_out(home)
        views.CHATGPT_SIGNING.clear()
        return {**views.model_list(home), **done}

    # -- crews and roles ---------------------------------------------------------------

    def check_crew(r: Request) -> Any:
        from ombro import removing

        return removing.check_crew(home, r.path_params["name"]).to_dict()

    @_removing
    def delete_crew(r: Request) -> Any:
        from ombro import removing

        moved = removing.remove_crew(home, r.path_params["name"])
        return {"moved_to": str(moved), **views.crews(home)}

    def default_crew(r: Request) -> Any:
        from ombro.settings import set_preference

        name = r.path_params["name"]
        if name not in home.crews():
            raise LookupError(f"no crew called {name!r}")
        set_preference(home, "default_crew", name)
        return views.crews(home)

    def check_role(r: Request) -> Any:
        from ombro import removing

        return removing.check_role(home, r.path_params["crew"], r.path_params["role"]).to_dict()

    @_removing
    def delete_role(r: Request) -> Any:
        from ombro import removing

        moved = removing.remove_role(home, r.path_params["crew"], r.path_params["role"])
        return {"moved_to": str(moved), **views.crews(home)}

    def role_model(r: Request, data: dict[str, Any]) -> Any:
        from ombro import models

        crew, role = r.path_params["crew"], r.path_params["role"]
        models.set_role_model(home, "lead" if role == "lead" else f"{crew}/{role}", _need(data, "name"))
        return views.role(home, crew, role)

    def edit_role(r: Request, data: dict[str, Any]) -> Any:
        """The crew member edit page's Save: any of title (a rename), kind, model and soul at once.
        The reply is the member as they are now, under their new name after a rename."""
        from ombro.role_edit import edit_role as edit

        crew, role = r.path_params["crew"], r.path_params["role"]
        given: dict[str, str] = {}
        for key in ("title", "kind", "model", "soul"):
            if key in data and data[key] is not None:
                if not isinstance(data[key], str):
                    raise HttpError(400, f"`{key}` must be text")
                given[key] = data[key]
        if not given:
            raise HttpError(400, "nothing to change: give title, kind, model or soul")
        done = edit(home, crew, role, **given)
        return views.role(home, crew, done.name)

    # -- tools: the built-in providers, and MCP servers --------------------------------

    def tools_view() -> Any:
        return views.tool_list(home, signing=s.signing)

    def set_native(r: Request, data: dict[str, Any]) -> Any:
        from ombro import tool_cmd
        from ombro.settings import PROVIDER_KEYS

        tool = r.path_params["tool"]
        provider = data.get("provider")
        max_calls = data.get("max_calls")
        if provider is not None and not isinstance(provider, str):
            raise HttpError(400, "`provider` is a name")
        if max_calls is not None and (isinstance(max_calls, bool) or not isinstance(max_calls, int)):
            raise HttpError(400, "`max_calls` is a whole number")
        key_value = str(data.get("key") or "").strip()
        key = PROVIDER_KEYS.get(provider or "")
        if key and key_value:
            keys.set_key(home, key, key_value)
        elif key and not keys.get_key(home, key):
            raise HttpError(400, f"{provider.title()} needs a key. Paste it in, and it's kept on this computer.")
        tool_cmd.set_tool(home, tool, provider=provider, max_calls=max_calls)
        return tools_view()

    def _server_entry(name: str, data: dict[str, Any], old: dict[str, Any] | None = None) -> tuple[dict, dict]:
        """The tools.md entry from the form, and the keys to store. A secret left blank in an edit
        keeps the one already there."""
        from ombro import mcp_servers

        url = str(data.get("url") or "").strip() or None
        command = str(data.get("command") or "").strip() or None
        secrets = _secret_pairs(data)
        keep = [str(n) for n in data.get("keep") or [] if isinstance(n, str)]
        to_set: dict[str, str] = {}
        refs: dict[str, str] = {}
        for var, value in secrets.items():
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", var):
                raise HttpError(400, f"{var!r} can't be the name of a header or a setting")
            key = mcp_servers.key_name(name, var)
            to_set[key] = value
            ref = "${" + key + "}"
            refs[var] = f"Bearer {ref}" if url and var.lower() == "authorization" else ref
        if old:
            had = (old.get("headers") if url else old.get("env")) or {}
            for var in keep:
                if var in had and var not in refs:
                    refs[var] = had[var]
        entry = mcp_servers.server_entry(command=command, url=url, env=None if url else refs,
                                        headers=refs if url else None)
        if old:
            for k, v in old.items():  # what the form doesn't show, such as a class or a timeout
                if k not in ("command", "args", "url", "env", "headers"):
                    entry[k] = v
        from ombro.settings import ToolSettings

        ToolSettings.from_dict({"servers": {name: entry}}, "the server")
        return entry, to_set

    def add_server(r: Request, data: dict[str, Any]) -> Any:
        from ombro import mcp_servers

        name = re.sub(r"[^a-z0-9_-]+", "-", _need(data, "name").strip().lower()).strip("-")[:31]
        if not name:
            raise HttpError(400, "Give it a name, such as notion or sheets.")
        entry, to_set = _server_entry(name, data)
        with keys.setting(home, to_set):
            mcp_servers.add_server(home, name, entry)
        warning = mcp_servers.pin_warning(entry["command"], entry.get("args") or []) if entry.get("command") else None
        return {**tools_view(), "added": name, "warning": warning}

    def edit_server(r: Request, data: dict[str, Any]) -> Any:
        from ombro import mcp_servers
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        old = load_tool_settings(home).servers.get(name)
        if old is None:
            raise LookupError(f"no tool called {name!r}")
        entry, to_set = _server_entry(name, data, old)
        with keys.setting(home, to_set):
            mcp_servers.update_server(home, name, entry)
        keys.forget_unused(home, _server_keys(name, old))  # a secret the edit took out
        warning = mcp_servers.pin_warning(entry["command"], entry.get("args") or []) if entry.get("command") else None
        return {**tools_view(), "warning": warning}

    def check_server(r: Request) -> Any:
        from ombro import mcp_servers
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        cfg = load_tool_settings(home).servers.get(name)
        if cfg is None:
            raise LookupError(f"no tool called {name!r}")
        users = mcp_servers.roles_using(home, name)
        warnings = [f"{', '.join(users)} use{'s' if len(users) == 1 else ''} its tools and will go without them."] \
            if users else []
        warnings += _key_notes(_server_keys(name, cfg), f"MCP server {name}")
        return {"blocked": None, "warnings": warnings}

    def remove_server(r: Request) -> Any:
        from ombro import oauth, mcp_servers
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        check_server(r)
        cfg = load_tool_settings(home).servers.get(name) or {}
        mcp_servers.remove_server(home, name)
        keys.forget_unused(home, _server_keys(name, cfg))
        if oauth.load(home, name) is not None:
            oauth.disconnect(home, name)
        s.signing.pop(name, None)
        return tools_view()

    def server_claims(r: Request) -> Any:
        """What each of a server's tools says it does, and which still wait for your confirmation:
        the same as `ombro tools <server>`. Starts the server, which may download it first."""
        from ombro import mcp_servers
        from ombro.mcp import McpError
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        if name not in load_tool_settings(home).servers:
            raise LookupError(f"no tool called {name!r}")
        try:
            rows = mcp_servers.tool_claims(home, name)
        except McpError as e:
            return {"tools": [], "unconfirmed": [], "problem": f"Its tools could not be listed: {e}"}
        return {"tools": rows, "unconfirmed": mcp_servers.unconfirmed(rows), "problem": None}

    def confirm_claims(r: Request, data: dict[str, Any]) -> Any:
        """Your yes to what the server says of these tools (they stay inside your organisation)."""
        from ombro import mcp_servers

        tools = data.get("tools")
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise HttpError(400, "`tools` is a list of the server's tool names")
        claims = server_claims(r)
        if claims["problem"]:
            raise HttpError(400, claims["problem"])
        wanted = [t for t in tools if t in claims["unconfirmed"]]  # only a claim the server makes now
        mcp_servers.confirm_internal(home, r.path_params["name"], wanted)
        return server_claims(r)

    def sign_in(r: Request, data: dict[str, Any]) -> Any:
        """Start the sign-in (the existing OAuth flow) on a thread, and hand back the page to open.
        The flow then waits for the browser; GET /tools shows when it has finished."""
        from ombro import oauth
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        cfg = load_tool_settings(home).servers.get(name)
        if cfg is None:
            raise LookupError(f"no tool called {name!r}")
        if not cfg.get("url"):
            raise HttpError(400, "It runs on this computer, so it needs no sign-in.")
        opened: dict[str, str] = {}
        ready = threading.Event()
        # The app on another computer: its browser comes back to that computer, where the app
        # catches the answer (on `redirect_port`) and hands it over with POST …/sign-in/answer.
        receiver = None
        if data.get("hand_over"):
            port = data.get("redirect_port")
            if port is not None and (not isinstance(port, int) or not 0 < port < 65536):
                raise HttpError(400, "`redirect_port` must be a port number")
            receiver = oauth.HandOver(port)
            s.handovers[name] = receiver

        def open_browser(url: str) -> None:
            opened["url"] = url
            ready.set()

        def run() -> None:
            try:
                oauth.connect(home, name, cfg, open_browser=open_browser, say=lambda _t: None, receiver=receiver,
                              back_to="the Ombro app")
                s.signing[name] = {"state": "done"}
            except oauth.SignInError as e:
                text = str(e)
                if "did not ask for sign-in" in text:
                    s.signing[name] = {"state": "not needed"}
                else:
                    s.signing[name] = {"state": "failed", "problem": text}
            except Exception as e:  # noqa: BLE001 - the address was wrong, or it can't be reached
                s.signing[name] = {"state": "failed", "problem": f"It couldn't be reached: {e}"}
            finally:
                ready.set()
                if s.handovers.get(name) is receiver:
                    s.handovers.pop(name, None)

        s.signing[name] = {"state": "waiting"}
        threading.Thread(target=run, daemon=True, name=f"sign-in-{name}").start()
        ready.wait(30)
        state = s.signing.get(name) or {}
        if "url" in opened and state.get("state") == "waiting":
            return {"url": opened["url"], "state": "waiting"}
        return {"url": None, **state}

    def sign_in_answer(r: Request, data: dict[str, Any]) -> Any:
        """The browser's answer, handed over by the app (or pasted): {"url": the address the
        browser was sent back to}. Only for a sign-in started with `hand_over`."""
        from ombro import oauth

        name = r.path_params["name"]
        receiver = s.handovers.get(name)
        if receiver is None:
            raise LookupError(f"no sign-in to {name} is waiting for an answer")
        answer = oauth.answer_from(str(data.get("url") or ""))
        if not answer:
            raise HttpError(400, "That address has no sign-in answer in it. Copy the whole address from "
                                 "the browser's address bar.")
        if not receiver.give(answer):
            raise HttpError(400, "That isn't the answer to this sign-in. Start the sign-in again.")
        return {"state": "answered"}

    def sign_out(r: Request) -> Any:
        from ombro import oauth

        name = r.path_params["name"]
        s.signing.pop(name, None)
        return {"signed_out": oauth.disconnect(home, name), **tools_view()}

    # -- web APIs -------------------------------------------------------------------

    def _server_keys(name: str, cfg: dict[str, Any]) -> list[str]:
        from ombro.mcp import ServerConfig

        return list(ServerConfig.from_dict(name, cfg).key_refs())

    def _key_notes(names: list[str], leaving: str) -> list[str]:
        """What a delete's confirm says about the keys that go with it, or stay for something else."""
        going, kept = keys.still_used(home, names, leaving)
        notes = []
        if going:
            notes.append(f"Its key{'s' if len(going) > 1 else ''} ({', '.join(going)}) {'are' if len(going) > 1 else 'is'} "
                         "removed from this computer too.")
        if kept:
            notes.append(f"{', '.join(kept)} stay{'s' if len(kept) == 1 else ''}: something else uses "
                         f"{'it' if len(kept) == 1 else 'them'} too.")
        return notes

    def add_api(r: Request, data: dict[str, Any]) -> Any:
        from ombro import apitool

        name = apitool.clean_name(_need(data, "name"))
        apitool.save(home, name, apitool.entry_from(name, data), str(data.get("key") or ""))
        return {**tools_view(), "added": name}

    def edit_api(r: Request, data: dict[str, Any]) -> Any:
        from ombro import apitool
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        old = load_tool_settings(home).apis.get(name)
        if old is None:
            raise LookupError(f"no API called {name!r}")
        apitool.save(home, name, apitool.entry_from(name, data, old), str(data.get("key") or ""), old)
        return tools_view()

    def check_api(r: Request) -> Any:
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        cfg = load_tool_settings(home).apis.get(name)
        if cfg is None:
            raise LookupError(f"no API called {name!r}")
        return {"blocked": None, "warnings": ["Every crew member stops being offered it.",
                                              *_key_notes([cfg.get("key") or ""], f"API {name}")]}

    def remove_api(r: Request) -> Any:
        from ombro import apitool
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        cfg = load_tool_settings(home).apis.get(name) or {}
        apitool.remove_api(home, name)
        keys.forget_unused(home, [cfg.get("key") or ""])
        return tools_view()

    # -- Composio: the user's own account, by its key ----------------------------------

    def connect_composio(r: Request, data: dict[str, Any]) -> Any:
        from ombro import composio

        composio.connect(home, _need(data, "key"))
        return views.tool_list(home, signing=s.signing, fresh=True)

    def refresh_composio(r: Request) -> Any:
        return views.tool_list(home, signing=s.signing, fresh=True)

    def check_composio(r: Request) -> Any:
        from ombro.settings import load_tool_settings

        cfg = load_tool_settings(home).composio
        if cfg is None:
            raise LookupError("Composio isn't connected")
        return {"blocked": None, "warnings": [
            "Every crew member stops being offered the tools from your Composio account. Nothing changes in "
            "Composio itself.", *_key_notes([cfg["key"]], "Composio")]}

    def disconnect_composio(r: Request) -> Any:
        from ombro import composio

        composio.disconnect(home)
        return tools_view()

    # -- skills ----------------------------------------------------------------------

    def skill_view(r: Request) -> Any:
        from ombro import skills

        sk = skills.load(home, r.path_params["name"])
        if sk is None:
            raise LookupError(f"no skill called {r.path_params['name']!r}")
        return {"name": sk.name, "description": sk.description, "body": sk.body, "source": sk.source,
                "path": str(sk.file)}

    def _write_skill(name: str, data: dict[str, Any], old=None) -> Any:
        from datetime import datetime

        from ombro import skills
        from ombro.skills_cmd import library_view

        try:
            pasted, body = skills.pasted(_need(data, "body"))
        except skills.SkillError as e:
            raise HttpError(400, str(e)) from None
        description = str(data.get("description") or "").strip() or " ".join(str(pasted.get("description") or "")
                                                                             .split())
        if not description:
            raise HttpError(400, "Say what it does, and when to use it.")
        if not body.strip():
            raise HttpError(400, "Write the instructions after the front matter.")
        md = dict(old.meta.get("metadata") or {}) if old is not None and isinstance(old.meta.get("metadata"), dict) \
            else {}
        text = skills.compose(name, description, body, md or None)
        skills.check(text, name)
        skills.write(home, name, text, source=md.get("source") or "you",
                     accepted=datetime.now().isoformat(timespec="seconds"))
        return library_view(home)

    def save_skill(r: Request, data: dict[str, Any]) -> Any:
        from ombro import skills

        name = r.path_params["name"]
        old = skills.load(home, name)
        if old is None:
            raise LookupError(f"no skill called {name!r}")
        return _write_skill(name, data, old)

    def new_skill(r: Request, data: dict[str, Any]) -> Any:
        from ombro import skills

        title = str(data.get("title") or "").strip()
        if not title:  # a pasted SKILL.md names itself
            try:
                title = str(skills.pasted(str(data.get("body") or ""))[0].get("name") or "")
            except skills.SkillError:
                title = ""
        name = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")[:skills.NAME_MAX]
        if not name:
            raise HttpError(400, "Give the skill a name.")
        if skills.load(home, name) is not None:
            raise HttpError(409, f"There's already a skill called {name}.")
        return _write_skill(name, data)

    def import_skill(r: Request, data: dict[str, Any]) -> Any:
        """A skill from a folder the app read on its own computer: {folder: its name, files: {path in
        the folder: text}, skipped: [paths not sent], path: where it is, for the record, add: false to
        see it first, true to import it}. The same checks and copying as `ombro skills add`."""
        from ombro import skills
        from ombro.skills_cmd import import_files

        files = data.get("files")
        if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
            raise HttpError(400, "`files` must be paths and their text")
        skipped = [str(x) for x in data.get("skipped") or [] if isinstance(x, str)]
        path = str(data.get("path") or "").strip() or None
        try:
            return import_files(home, str(data.get("folder") or ""), files, skipped=skipped, label=path,
                                add=data.get("add") is True)
        except skills.SkillError as e:
            raise HttpError(400, str(e)[:1].upper() + str(e)[1:]) from None

    def check_skill(r: Request) -> Any:
        from ombro import removing

        return removing.check_skill(home, r.path_params["name"]).to_dict()

    def delete_skill(r: Request) -> Any:
        from ombro import removing
        from ombro.skills_cmd import library_view

        moved = removing.remove_skill(home, r.path_params["name"])
        return {"moved_to": str(moved), **library_view(home)}

    return [
        Route("/models/providers", _handler(add_provider, status=201, with_body=True), methods=["POST"]),
        Route("/models/providers", _handler(edit_provider, with_body=True), methods=["PUT"]),
        Route("/models/chatgpt/sign-in", _handler(chatgpt_sign_in, with_body=True), methods=["POST"]),
        Route("/models/chatgpt/sign-in/answer", _handler(chatgpt_answer, with_body=True), methods=["POST"]),
        Route("/models/chatgpt/sign-in", _handler(chatgpt_sign_out), methods=["DELETE"]),
        Route("/models/chatgpt/welcomed", _handler(chatgpt_welcomed, with_body=True), methods=["POST"]),
        Route("/models/providers/check", _handler(check_provider, with_body=True), methods=["POST"]),
        Route("/models/providers/remove", _handler(remove_provider, with_body=True), methods=["POST"]),
        Route("/crews/{name}/check", _handler(check_crew), methods=["GET"]),
        Route("/crews/{name}/default", _handler(default_crew, after=after), methods=["PUT"]),
        Route("/crews/{name}", _handler(delete_crew, after=after), methods=["DELETE"]),
        Route("/roles/{crew}/{role}/check", _handler(check_role), methods=["GET"]),
        Route("/roles/{crew}/{role}", _handler(delete_role, after=after), methods=["DELETE"]),
        Route("/roles/{crew}/{role}", _handler(edit_role, with_body=True), methods=["PUT"]),
        Route("/roles/{crew}/{role}/model", _handler(role_model, with_body=True), methods=["PUT"]),
        Route("/tools/servers", _handler(add_server, status=201, with_body=True), methods=["POST"]),
        Route("/tools/servers/{name}", _handler(edit_server, with_body=True), methods=["PUT"]),
        Route("/tools/servers/{name}/check", _handler(check_server), methods=["GET"]),
        Route("/tools/servers/{name}", _handler(remove_server), methods=["DELETE"]),
        Route("/tools/servers/{name}/claims", _handler(server_claims), methods=["GET"]),
        Route("/tools/servers/{name}/claims", _handler(confirm_claims, with_body=True), methods=["POST"]),
        Route("/tools/servers/{name}/sign-in", _handler(sign_in, with_body=True), methods=["POST"]),
        Route("/tools/servers/{name}/sign-in/answer", _handler(sign_in_answer, with_body=True), methods=["POST"]),
        Route("/tools/servers/{name}/sign-in", _handler(sign_out), methods=["DELETE"]),
        Route("/tools/apis", _handler(add_api, status=201, with_body=True), methods=["POST"]),
        Route("/tools/apis/{name}", _handler(edit_api, with_body=True), methods=["PUT"]),
        Route("/tools/apis/{name}/check", _handler(check_api), methods=["GET"]),
        Route("/tools/apis/{name}", _handler(remove_api), methods=["DELETE"]),
        Route("/tools/composio", _handler(connect_composio, with_body=True), methods=["PUT"]),
        Route("/tools/composio/refresh", _handler(refresh_composio), methods=["POST"]),
        Route("/tools/composio/check", _handler(check_composio), methods=["GET"]),
        Route("/tools/composio", _handler(disconnect_composio), methods=["DELETE"]),
        Route("/tools/{tool}", _handler(set_native, with_body=True), methods=["PUT"]),
        Route("/skills", _handler(new_skill, status=201, with_body=True), methods=["POST"]),
        Route("/skills/import", _handler(import_skill, with_body=True), methods=["POST"]),
        Route("/skills/{name}", _handler(skill_view), methods=["GET"]),
        Route("/skills/{name}", _handler(save_skill, with_body=True), methods=["PUT"]),
        Route("/skills/{name}/check", _handler(check_skill), methods=["GET"]),
        Route("/skills/{name}", _handler(delete_skill), methods=["DELETE"]),
    ]
