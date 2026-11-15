"""The Settings pages' actions (plan 2, stage 7): model providers, crews and roles, tools and
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
from ombro.settings import SetupError


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

    # -- model providers (C5) --------------------------------------------------------

    def add_provider(r: Request, data: dict[str, Any]) -> Any:
        from ombro import models

        endpoint = _need(data, "endpoint").strip().rstrip("/")
        key_value = str(data.get("key") or "").strip()
        key_name = (str(data.get("key_name") or "").strip() or models.key_name_for(endpoint)) if key_value else ""
        if key_value:
            if not keys.valid_name(key_name):
                raise HttpError(400, "Key names use capitals, digits and underscores, starting with a letter.")
            keys.set_key(home, key_name, key_value)
        factory = None
        if s.sessions.client_factory is not None:
            factory = lambda spec, h=None: s.sessions.client_factory(spec)  # noqa: E731 - tests' scripted model
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

        removed = models.remove_provider(home, _need(data, "endpoint"))
        return {**views.model_list(home), "removed": removed}

    # -- crews and roles (C4) ----------------------------------------------------------

    def check_crew(r: Request) -> Any:
        from ombro import removing

        return removing.check_crew(home, r.path_params["name"]).to_dict()

    @_removing
    def delete_crew(r: Request) -> Any:
        from ombro import removing

        moved = removing.remove_crew(home, r.path_params["name"])
        return {"moved_to": str(moved), **views.crews(home)}

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
        from ombro import server_cmd

        url = str(data.get("url") or "").strip() or None
        command = str(data.get("command") or "").strip() or None
        secrets = _secret_pairs(data)
        keep = [str(n) for n in data.get("keep") or [] if isinstance(n, str)]
        to_set: dict[str, str] = {}
        refs: dict[str, str] = {}
        for var, value in secrets.items():
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", var):
                raise HttpError(400, f"{var!r} can't be the name of a header or a setting")
            key = server_cmd.key_name(name, var)
            to_set[key] = value
            ref = "${" + key + "}"
            refs[var] = f"Bearer {ref}" if url and var.lower() == "authorization" else ref
        if old:
            had = (old.get("headers") if url else old.get("env")) or {}
            for var in keep:
                if var in had and var not in refs:
                    refs[var] = had[var]
        entry = server_cmd.server_entry(command=command, url=url, env=None if url else refs,
                                        headers=refs if url else None)
        if old:
            for k, v in old.items():  # what the form doesn't show, such as a class or a timeout
                if k not in ("command", "args", "url", "env", "headers"):
                    entry[k] = v
        from ombro.settings import ToolSettings

        ToolSettings.from_dict({"servers": {name: entry}}, "the server")
        return entry, to_set

    def add_server(r: Request, data: dict[str, Any]) -> Any:
        from ombro import server_cmd

        name = re.sub(r"[^a-z0-9_-]+", "-", _need(data, "name").strip().lower()).strip("-")[:31]
        if not name:
            raise HttpError(400, "Give it a name, such as notion or sheets.")
        entry, to_set = _server_entry(name, data)
        for k, v in to_set.items():
            keys.set_key(home, k, v)
        server_cmd.add_server(home, name, entry)
        warning = server_cmd.pin_warning(entry["command"], entry.get("args") or []) if entry.get("command") else None
        return {**tools_view(), "added": name, "warning": warning}

    def edit_server(r: Request, data: dict[str, Any]) -> Any:
        from ombro import server_cmd
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        old = load_tool_settings(home).servers.get(name)
        if old is None:
            raise LookupError(f"no tool called {name!r}")
        entry, to_set = _server_entry(name, data, old)
        for k, v in to_set.items():
            keys.set_key(home, k, v)
        server_cmd.update_server(home, name, entry)
        warning = server_cmd.pin_warning(entry["command"], entry.get("args") or []) if entry.get("command") else None
        return {**tools_view(), "warning": warning}

    def check_server(r: Request) -> Any:
        from ombro import server_cmd
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        if name not in load_tool_settings(home).servers:
            raise LookupError(f"no tool called {name!r}")
        users = server_cmd.roles_using(home, name)
        warnings = [f"{', '.join(users)} use{'s' if len(users) == 1 else ''} its tools and will go without them."] \
            if users else []
        return {"blocked": None, "warnings": warnings}

    def remove_server(r: Request) -> Any:
        from ombro import oauth, server_cmd

        name = r.path_params["name"]
        check_server(r)
        server_cmd.remove_server(home, name)
        if oauth.load(home, name) is not None:
            oauth.disconnect(home, name)
        s.signing.pop(name, None)
        return tools_view()

    def sign_in(r: Request) -> Any:
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

        def open_browser(url: str) -> None:
            opened["url"] = url
            ready.set()

        def run() -> None:
            try:
                oauth.connect(home, name, cfg, open_browser=open_browser, say=lambda _t: None)
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

        s.signing[name] = {"state": "waiting"}
        threading.Thread(target=run, daemon=True, name=f"sign-in-{name}").start()
        ready.wait(30)
        state = s.signing.get(name) or {}
        if "url" in opened and state.get("state") == "waiting":
            return {"url": opened["url"], "state": "waiting"}
        return {"url": None, **state}

    def sign_out(r: Request) -> Any:
        from ombro import oauth

        name = r.path_params["name"]
        s.signing.pop(name, None)
        return {"signed_out": oauth.disconnect(home, name), **tools_view()}

    # -- web APIs (the API tool design) ---------------------------------------------

    def _api_entry(name: str, data: dict[str, Any], old: dict[str, Any] | None = None) -> tuple[dict, str | None]:
        """The tools.md entry from the form, and the key to store, if one was typed. A key left
        empty in an edit keeps the one there; `no_key` removes it."""
        from ombro import apitool

        how = str(data.get("send_key") or "bearer")
        param = str(data.get("key_param") or "").strip()
        if how in ("header", "query"):
            if not param:
                raise HttpError(400, "Say what the header or address value is called, as the API's docs do.")
            how = f"{how}:{param}"
        access = str(data.get("access") or "external")
        max_calls = data.get("max_calls")
        if max_calls is not None and (isinstance(max_calls, bool) or not isinstance(max_calls, int)):
            raise HttpError(400, "`max_calls` is a whole number")
        key_value = str(data.get("key") or "").strip()
        entry: dict[str, Any] = {"url": _need(data, "url").strip(), "send_key": how,
                                 "about": str(data.get("about") or ""), "class": access}
        if str(data.get("how_to") or "").strip():
            entry["how_to"] = str(data["how_to"]).strip()
        if max_calls:
            entry["max_calls"] = max_calls
        if key_value:
            entry["key"] = (old or {}).get("key") or apitool.key_name(name)
        elif old and old.get("key") and data.get("no_key") is not True:
            entry["key"] = old["key"]
        apitool.check_apis({name: entry}, "the API")
        return entry, key_value or None

    def add_api(r: Request, data: dict[str, Any]) -> Any:
        from ombro import apitool

        name = re.sub(r"[^a-z0-9_-]+", "-", _need(data, "name").strip().lower()).strip("-")[:31]
        if not name:
            raise HttpError(400, "Give it a name, such as podcasts.")
        entry, key_value = _api_entry(name, data)
        apitool.add_api(home, name, entry)
        if key_value:
            keys.set_key(home, entry["key"], key_value)
        return {**tools_view(), "added": name}

    def edit_api(r: Request, data: dict[str, Any]) -> Any:
        from ombro import apitool
        from ombro.settings import load_tool_settings

        name = r.path_params["name"]
        old = load_tool_settings(home).apis.get(name)
        if old is None:
            raise LookupError(f"no API called {name!r}")
        entry, key_value = _api_entry(name, data, old)
        apitool.update_api(home, name, entry)
        if key_value:
            keys.set_key(home, entry["key"], key_value)
        return tools_view()

    def check_api(r: Request) -> Any:
        from ombro.settings import load_tool_settings

        if r.path_params["name"] not in load_tool_settings(home).apis:
            raise LookupError(f"no API called {r.path_params['name']!r}")
        return {"blocked": None, "warnings": ["Every crew member stops being offered it. Its key stays on this "
                                              "computer until you remove it."]}

    def remove_api(r: Request) -> Any:
        from ombro import apitool

        apitool.remove_api(home, r.path_params["name"])
        return tools_view()

    # -- skills (C4) -----------------------------------------------------------------

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

        description = _need(data, "description")
        body = _need(data, "body")
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

        name = re.sub(r"[^a-z0-9]+", "-", _need(data, "title").strip().lower()).strip("-")[:skills.NAME_MAX]
        if not name:
            raise HttpError(400, "Give the skill a name.")
        if skills.load(home, name) is not None:
            raise HttpError(409, f"There's already a skill called {name}.")
        return _write_skill(name, data)

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
        Route("/models/providers/check", _handler(check_provider, with_body=True), methods=["POST"]),
        Route("/models/providers/remove", _handler(remove_provider, with_body=True), methods=["POST"]),
        Route("/crews/{name}/check", _handler(check_crew), methods=["GET"]),
        Route("/crews/{name}", _handler(delete_crew, after=after), methods=["DELETE"]),
        Route("/roles/{crew}/{role}/check", _handler(check_role), methods=["GET"]),
        Route("/roles/{crew}/{role}", _handler(delete_role, after=after), methods=["DELETE"]),
        Route("/roles/{crew}/{role}/model", _handler(role_model, with_body=True), methods=["PUT"]),
        Route("/tools/servers", _handler(add_server, status=201, with_body=True), methods=["POST"]),
        Route("/tools/servers/{name}", _handler(edit_server, with_body=True), methods=["PUT"]),
        Route("/tools/servers/{name}/check", _handler(check_server), methods=["GET"]),
        Route("/tools/servers/{name}", _handler(remove_server), methods=["DELETE"]),
        Route("/tools/servers/{name}/sign-in", _handler(sign_in), methods=["POST"]),
        Route("/tools/servers/{name}/sign-in", _handler(sign_out), methods=["DELETE"]),
        Route("/tools/apis", _handler(add_api, status=201, with_body=True), methods=["POST"]),
        Route("/tools/apis/{name}", _handler(edit_api, with_body=True), methods=["PUT"]),
        Route("/tools/apis/{name}/check", _handler(check_api), methods=["GET"]),
        Route("/tools/apis/{name}", _handler(remove_api), methods=["DELETE"]),
        Route("/tools/{tool}", _handler(set_native, with_body=True), methods=["PUT"]),
        Route("/skills", _handler(new_skill, status=201, with_body=True), methods=["POST"]),
        Route("/skills/{name}", _handler(skill_view), methods=["GET"]),
        Route("/skills/{name}", _handler(save_skill, with_body=True), methods=["PUT"]),
        Route("/skills/{name}/check", _handler(check_skill), methods=["GET"]),
        Route("/skills/{name}", _handler(delete_skill), methods=["DELETE"]),
    ]
