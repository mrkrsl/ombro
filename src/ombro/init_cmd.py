"""`ombro init`, part 1: connect a model. Scripted, because no model answers yet.

Keys are typed into a hidden prompt and written to .env. They never reach a model.
"""

from __future__ import annotations

import getpass
from typing import Callable

from ombro import keys, mdfile
from ombro.client import ModelError, make_client
from ombro.home import Home, ensure_home
from ombro.settings import ModelSpec, SetupError, load_models

ENDPOINTS = [
    {
        "label": "DeepSeek",
        "endpoint": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key": "DEEPSEEK_API_KEY",
        "price": {"input": 0.28, "output": 0.42},
    },
    {"label": "Another OpenAI-compatible endpoint", "endpoint": None, "model": None, "key": None, "price": None},
]
SEARCH_KEY = "FIRECRAWL_API_KEY"
TEST_PROMPT = [{"role": "user", "content": "Reply with the single word OK."}]


def test_connection(home: Home, spec: ModelSpec, client_factory=make_client) -> tuple[bool, str]:
    try:
        reply = client_factory(spec, home).complete(TEST_PROMPT, tools=None, max_output_tokens=16, timeout=60)
    except ModelError as e:
        return False, str(e)
    text = (reply.content or "").strip()
    return True, f"the model replied {text[:40]!r}" if text else "the model replied"


def connected_model(home: Home) -> ModelSpec | None:
    """The default model if it is configured and has its key. (Does not call it.)"""
    try:
        spec = load_models(home).get("default")
    except (SetupError, mdfile.FrontMatterError):
        return None
    if spec is None:
        return None
    if spec.api == "openai" and spec.key and not keys.get_key(home, spec.key):
        return None
    return spec


def _write_default_model(home: Home, entry: dict) -> None:
    meta, body = mdfile.read(home.models)
    models = meta.get("models") if isinstance(meta.get("models"), dict) else {}
    models["default"] = entry
    models.setdefault("cheap", "default")
    meta["models"] = models
    mdfile.write(home.models, meta, body)


def run_init(
    home: Home,
    *,
    ask: Callable[[str], str] = input,
    ask_secret: Callable[[str], str] = getpass.getpass,
    say: Callable[[str], None] = print,
    client_factory=make_client,
) -> bool:
    created = ensure_home(home)
    say(f"Ombro home: {home.root}" + (" (created)" if created else ""))

    spec = connected_model(home)
    if spec is not None:
        ok, detail = test_connection(home, spec, client_factory)
        if ok:
            say(f"A model is already connected: {spec.model} ({detail}).")
            if ask("Connect a different model? [y/N] ").strip().lower() not in ("y", "yes"):
                _offer_search_key(home, ask_secret, say)
                _next_steps(home, say)
                return True
        else:
            say(f"The configured model did not answer: {detail}")

    say("\nConnect a model. Keys are stored in .env with owner-only permissions and are never sent to a model.")
    while True:
        say("Endpoint:")
        for i, e in enumerate(ENDPOINTS, 1):
            say(f"  {i}) {e['label']}" + (f" ({e['endpoint']})" if e["endpoint"] else ""))
        choice = ask("Choose [1]: ").strip() or "1"
        if not choice.isdigit() or not 1 <= int(choice) <= len(ENDPOINTS):
            say("Please choose one of the numbers shown.")
            continue
        preset = ENDPOINTS[int(choice) - 1]

        endpoint = preset["endpoint"]
        if not endpoint:
            endpoint = ask("Endpoint base URL (the part before /chat/completions): ").strip()
            if not endpoint.startswith(("http://", "https://")):
                say("The URL must start with http:// or https://.")
                continue
        model = ask(f"Model name [{preset['model']}]: " if preset["model"] else "Model name: ").strip()
        model = model or preset["model"] or ""
        if not model:
            say("A model name is needed.")
            continue
        key_name = preset["key"]
        if not key_name:
            key_name = ask("Name for its key in .env, e.g. MY_PROVIDER_API_KEY (Enter if it needs no key): ").strip()
            if key_name and not keys.valid_name(key_name):
                say("Key names use capitals, digits and underscores, starting with a letter.")
                continue
        if key_name:
            existing = keys.get_key(home, key_name)
            hint = " (Enter keeps the current key)" if existing else ""
            value = ask_secret(f"API key for {key_name}{hint}: ").strip()
            if value:
                keys.set_key(home, key_name, value)
            elif not existing:
                say("A key is needed for this endpoint.")
                continue

        entry: dict = {"api": "openai", "endpoint": endpoint, "model": model}
        if key_name:
            entry["key"] = key_name
        if preset["price"] and model == preset["model"]:
            entry["price_per_mtok"] = dict(preset["price"])
        _write_default_model(home, entry)

        spec = load_models(home)["default"]
        say(f"Testing {model} at {endpoint} …")
        ok, detail = test_connection(home, spec, client_factory)
        if ok:
            say(f"Connected: {detail}.")
            if not spec.priced:
                say(
                    "Note: models.md has no price for this model, so budget_usd cannot be enforced for it "
                    "(max_tokens still is). Add price_per_mtok in models.md."
                )
            else:
                say(f"Prices in models.md: ${spec.price_in}/M input, ${spec.price_out}/M output tokens. Check them.")
            break
        say(f"The test call failed: {detail}\nLet's try again (Ctrl-C to give up).\n")

    _offer_search_key(home, ask_secret, say)
    _next_steps(home, say)
    return True


def _offer_search_key(home: Home, ask_secret, say) -> None:
    if keys.get_key(home, SEARCH_KEY):
        say(f"Search key {SEARCH_KEY} is set.")
        return
    value = ask_secret(f"Search key for Firecrawl, {SEARCH_KEY} (hidden; Enter to skip): ").strip()
    if value:
        keys.set_key(home, SEARCH_KEY, value)
        say("Search key saved.")


def _next_steps(home: Home, say) -> None:
    roles = home.roles_dir("default")
    say(
        "\nNext:\n"
        f"  1. Add a role: a folder in {roles} with config.md and soul.md.\n"
        "  2. Write a spec: a Markdown file saying what you want done.\n"
        "  3. Run it:  ombro run --spec path/to/spec.md\n"
        "(The set-up interview is not built yet.)"
    )
