"""Make a demo Ombro home to try the desktop app with: everything invented, nothing sent anywhere.

    uv run python gui/scripts/demo_home.py [path] [--fresh] [--open]

The home goes to ~/ombro-demo unless a path is given; --fresh replaces one that is
there. --open then starts the built app on it (macOS):

    open --env OMBRO_HOME=~/ombro-demo gui/src-tauri/target/release/bundle/macos/Ombro.app

What is in it: a live job to try (the first message in a new chat is offered as work; choose
Do it live), four crews with roles, two skills, three model providers (only the canned one is
used), built-in tools with providers, a web API,
two MCP servers (one local, one remote), two keys (dummy values), a shared folder,
saved specs, a skill folder to add (next to the home, in <path>-skill), three chats, four finished jobs
(one that made a file and proposes a skill, one with two actions waiting for approval, one that failed), two
schedules, and two saved specs that don't finish (token-limit-demo and timeout-demo), to try the restart and
resume offers.

The model is Ombro's fake one: it answers chat with canned replies and never
reaches the internet. Jobs it runs come from the scripts below. A brief interview
can't finish with canned replies.

Your own home (~/.config/ombro) is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT = Path.home() / "ombro-demo"
APP = Path(__file__).resolve().parent.parent / "src-tauri" / "target" / "release" / "bundle" / "macos" / "Ombro.app"

CHAT_REPLIES = [
    "This is the demo profile, so my answers are canned. Everything you see here is invented.",
    "Good question. In a real profile I'd answer properly; here I can only show you how a chat looks.",
    "Here's a longer reply, to show Markdown:\n\n- **Bold** and *italic*\n- A list\n- [A link](https://example.com)\n\n"
    "And a short paragraph after it.",
]


def live_demo() -> list[dict]:
    """A live job to try: the first message in a new chat is offered as work (say Do it live). The
    chat and the job read one script, so each reply waits for the call it belongs to (`needs`)."""
    from ombro import fake

    task = ("List three invented bakeries in Lowtown, with one line on each. Done when the list is in "
            "the report.")
    report = ("## What was done\nThe General Assistant found three invented sourdough bakeries in Lowtown:\n\n"
              "- **Crumb & Co.**, Harbour Street: a small counter, best before ten.\n"
              "- **The Rising**, by the market: rye and spelt as well.\n"
              "- **Old Oven**, near the station: open on Sundays.\n\n"
              "## What was not done and why\nNothing else was asked. All three are invented.\n\n"
              "## Decisions for you\nNone.")
    return [
        {**fake.call("offer_work", {"summary": "Three bakeries in Lowtown", "title": "Bakeries in Lowtown",
                                    "task": task}), "needs": "offer_work"},
        {**fake.say("Bakeries in Lowtown"), "needs": "prompt:Give this conversation a title"},
        {**fake.call("ask_you", {"question": "Sourdough only, or any bread?",
                                 "options": ["Sourdough", "Any bread"]}), "needs": "ask_you", "sleep": 1},
        {**fake.brief("general-assistant", "List three invented bakeries in Lowtown, sourdough only, "
                      "one line each."), "needs": "brief", "sleep": 1},
        {**fake.result("Three invented sourdough bakeries: Crumb & Co., The Rising and Old Oven."),
         "needs": "workspace__write", "sleep": 2},
        {**fake.finish("Three invented sourdough bakeries listed."), "needs": "finish"},
        {**fake.keep_notes(), "needs": "keep_notes"},
        {**fake.say(report), "needs": "prompt:writing the parts of the final report"},
    ]


TOKEN_DEMO_TOKENS = 4_000  # under what the Lead's first call needs (about 4k), and over half of it: "more" gets it going


def demo_unfinished_specs(home) -> None:
    """Two saved specs that don't finish, to try the restart and resume offers: /start one in a chat."""
    from ombro import mdfile

    home.specs_dir.mkdir(parents=True, exist_ok=True)
    mdfile.write(home.specs_dir / "token-limit-demo.md",
                 {"name": "token-limit-demo", "crews": ["ops"], "outward": "balanced",
                  "limits": {"max_tokens": TOKEN_DEMO_TOKENS}},
                 "# Count the invented biscuits\n\nCount the invented biscuits in the tin. Its token limit is "
                 "far too small on purpose, so it stops before it starts.\n")
    mdfile.write(home.specs_dir / "timeout-demo.md",
                 {"name": "timeout-demo", "crews": ["ops"], "outward": "balanced", "limits": {"job_timeout": "5s"}},
                 "# Sort the invented receipts\n\nSort the invented receipts by date. This invented task is slow on "
                 "purpose: its time limit is five seconds, so it runs out of time.\n")


def unfinished_demo() -> list[dict]:
    """Replies for the two specs that don't finish, and for the jobs that carry them on. Each needs words
    only its own calls have, so the rest of the demo never takes them; the first that fits is used."""
    from ombro import fake

    return [
        {**fake.say("## Summary\nThe task is finished, building on the earlier run.\n\n## What was done\nI "
                    "picked up where the earlier run stopped and finished it.\n\n## What was not done and why\n"
                    "Nothing."), "needs": "text:carried on from the earlier job"},
        {**fake.finish("Finished, building on the earlier run."), "needs": "text:Carrying on from an earlier run"},
        {"needs": "text:slow on purpose", "sleep": 8, "error": "the model took too long", "timed_out": True},
        {**fake.say("## Summary\nThe biscuits weren't counted: the job hit its token limit first.\n\n## What was "
                    "done\nNothing yet.\n\n## What was not done and why\nThe counting, because its tokens ran "
                    "out."), "needs": "text:Stopped by limit `max_tokens`"},
        {**fake.say("## Summary\nThe receipts weren't sorted: the job ran out of time first.\n\n## What was done\n"
                    "Nothing yet.\n\n## What was not done and why\nThe sorting, because its five seconds ran "
                    "out."), "needs": "text:Stopped by limit `job_timeout`"},
    ]


def role(home, crew: str, name: str, title: str, description: str, *, kind: str = "specialist",
         tools: list[str] | None = None, skills: list[str] | None = None) -> None:
    from ombro import mdfile

    folder = home.roles_dir(crew) / name
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "title": title, "kind": kind, "description": description, "max_steps": 12}
    if tools:
        meta["tools"] = tools
    if skills:
        meta["skills"] = skills
    mdfile.write(folder / "config.md", meta)
    (folder / "soul.md").write_text(f"You are the {title}. You work carefully and say what you did not do.\n")


PROPOSED_SKILL = """---
name: gopher-facts
description: Find and check facts about the pet gopher market before they go in a report.
---
# Gopher facts

A method for the **Marketing Crew**, learned on the invented gopher research job.

## Steps

1. Search for each fact in at least *two* places.
2. Keep the source beside each fact, as a link.
3. Leave out anything only one source says.

## Checklist

| Check | Why |
| --- | --- |
| Two sources | One source can be wrong |
| A date on each | Markets change |

> Invented for the demo: nothing here is real.
"""


def demo_skill_proposal(home) -> None:
    """A skill the Lead proposes, waiting in Approvals, so its formatted preview can be seen.
    Staged once (stage_skill keeps one per text), on the gopher research job."""
    from ombro import jobs, outbox

    found = [d for d in jobs.all_jobs(home) if d.name.startswith("gopher-market-research-")]
    if found:
        outbox.stage_skill(found[0], name="gopher-facts", text=PROPOSED_SKILL, for_roles=["market-researcher"],
                           reason="The research job found a method worth keeping.", base_sha="", flags=[])


def run_job(home, spec: str, script: list) -> str:
    """Run one job to its end with the fake model, as `ombro start --spec` does. Returns its id."""
    (home.root / "script.json").write_text(json.dumps(script))
    env = {**os.environ, "OMBRO_HOME": str(home.root)}
    r = subprocess.run([sys.executable, "-m", "ombro", "start", "--spec", spec], capture_output=True, text=True,
                       env=env, timeout=300, stdin=subprocess.DEVNULL)
    from ombro import jobs

    job_dir = max((d for d in jobs.all_jobs(home) if d.name.startswith(spec + "-")),
                  key=lambda d: d.stat().st_mtime)
    print(f"  job {job_dir.name}: {jobs.read_job(job_dir)[0].get('status')}" + (f" ({r.returncode})" if r.returncode else ""))
    return job_dir.name


def chat(home, name: str, title: str, turns: list[tuple[str, str]], when: str, jobs_started: list[str] = ()) -> str:
    from ombro import jobs, mdfile

    chat_id = f"{when}-{name[:6]}"
    entries = [f"**You (Chat):** {t}" if who == "you" else f"**Lead:** {t}" for who, t in turns]
    meta = {"kind": "chat", "title": title, "titled_by": "model", "default_crew": "default",
            "started_at": f"{when[:4]}-{when[4:6]}-{when[6:8]}T09:00:00Z",
            "updated_at": f"{when[:4]}-{when[4:6]}-{when[6:8]}T09:20:00Z", "ended": "you ended the chat",
            "remembered": [], "specs_saved": [], "id": chat_id, "jobs": list(jobs_started), "refused_tools": [],
            "limits": {"max_tokens": 150000, "tool_max_calls": 30},
            "usage": {"total_tokens": 4200, "input_tokens": 3800, "output_tokens": 400, "model_calls": len(turns) // 2,
                      "tool_calls": 0}}
    body = f"# {title}\n\nReopen this chat with /chats, or `ombro chat --resume`.\n\n" + "\n\n".join(entries) + "\n"
    (home.root / "chats").mkdir(exist_ok=True)
    mdfile.write(home.root / "chats" / f"{name}-{when}.md", meta, body)
    for job_id in jobs_started:
        jobs.update_job(jobs.find_job(home, job_id), chat=chat_id)
    return chat_id


def demo_providers(home) -> None:
    """Two more model providers to see on Settings › General. Nothing uses them unless chosen, and
    their key is a dummy, so they are never called by themselves."""
    from ombro import keys, models

    if not models.provider_names(home, "https://api.deepseek.com"):
        models.add_models(home, "https://api.deepseek.com", "DEEPSEEK_API_KEY", ["deepseek-chat", "deepseek-reasoner"])
        keys.set_key(home, "DEEPSEEK_API_KEY", "demo-not-a-real-key")
    if not models.provider_names(home, "http://localhost:1234/v1"):
        models.add_models(home, "http://localhost:1234/v1", None, ["local-model"])


def demo_api(home) -> None:
    """A web API to see on Settings › Tools, at a placeholder address with a dummy key."""
    from ombro import apitool, keys
    from ombro.settings import load_tool_settings

    if "podcasts" in load_tool_settings(home).apis:
        return
    apitool.add_api(home, "podcasts", {"url": "https://api.example.com/podcasts/v2", "key": "PODCASTS_API_KEY",
                                       "send_key": "bearer", "class": "read-only",
                                       "about": "Our list of podcast episodes, searchable by title and guest.",
                                       "how_to": "GET /episodes?q=… searches episodes by title."})
    keys.set_key(home, "PODCASTS_API_KEY", "demo-not-a-real-key")


def demo_skill_folder(root: Path) -> Path:
    """A skill folder outside the home, to try Settings › Skills › Add from a folder (and
    `ombro skills add`): its SKILL.md, a reference that is copied, and a script that isn't."""
    folder = root.parent / f"{root.name}-skill" / "meeting-notes"
    (folder / "references").mkdir(parents=True, exist_ok=True)
    (folder / "scripts").mkdir(exist_ok=True)
    (folder / "SKILL.md").write_text(
        "---\nname: meeting-notes\ndescription: Write up an invented meeting as short notes, with decisions and "
        "actions first. Use after any meeting you're given notes or a transcript for.\n---\n# Meeting notes\n\n"
        "1. Read the whole transcript before writing anything.\n2. Put decisions first, then actions with an owner "
        "and a date.\n3. Keep the rest to five bullet points.\n4. Follow the layout in references/template.md.\n")
    (folder / "references" / "template.md").write_text("# <Meeting>, <date>\n\n## Decisions\n\n## Actions\n\n"
                                                       "## Notes\n")
    (folder / "scripts" / "tidy.sh").write_text("#!/bin/sh\necho tidy\n")
    return folder


def build(root: Path) -> None:
    os.environ["OMBRO_HOME"] = str(root)
    from ombro import fake, folders, keys, mdfile, schedules, skills
    from ombro.home import Home, ensure_crew, ensure_home
    from ombro.settings import set_preference

    home = Home(root)
    ensure_home(home)
    set_preference(home, "name", "Mark")
    mdfile.write(home.models, {"models": {"default": {"api": "fake", "script": "script.json",
                                                      "model": "demo-model (canned replies)"},
                                          "cheap": "default"}}, "The demo profile's fake model.")
    demo_providers(home)

    print("crews and roles")
    ensure_crew(home, "marketing", "Research and campaigns.", "Marketing Crew")
    ensure_crew(home, "legal", "Terms, policies and contracts.", "Legal Crew")
    ensure_crew(home, "ops", "Money and the day-to-day.", "Ops Crew")
    role(home, "marketing", "market-researcher", "Market Researcher", "Finds and summarises market facts.",
         tools=["web.search", "web.fetch", "workspace.write"], skills=["source-checking"])
    role(home, "marketing", "marketing-associate", "Marketing Associate", "Drafts posts and campaigns.",
         kind="helper", tools=["mcp.sheets.post_message", "mcp.sheets.add_row", "workspace.write"],
         skills=["brand-voice"])
    role(home, "marketing", "fact-checker", "Fact Checker", "Checks claims against sources.", tools=["web.fetch"])
    role(home, "legal", "legal-researcher", "Legal Researcher", "Looks up terms and their sources.",
         tools=["web.search", "workspace.write"])
    role(home, "legal", "legal-assistant", "Legal Assistant", "Drafts clauses for review.", tools=["workspace.write"])
    role(home, "ops", "finance-assistant", "Finance Assistant", "Keeps the numbers tidy.", tools=["workspace.write"])

    print("skills")
    skills.write(home, "source-checking", "---\nname: source-checking\ndescription: Check each fact against two "
                 "independent sources before it goes in a report.\n---\n\n# Source checking\n\n1. Find two sources.\n"
                 "2. Note where they disagree.\n3. Say which you trust, and why.\n", source="you", accepted="2026-11-01")
    skills.write(home, "brand-voice", "---\nname: brand-voice\ndescription: Write in the house voice, with short "
                 "sentences, plain words, no exclamation marks.\n---\n\n# Brand voice\n\n- Short sentences.\n"
                 "- Plain words.\n- No exclamation marks.\n", source="you", accepted="2026-11-02")

    print("tools, servers and keys")
    mdfile.write(home.tools, {
        "tools": {"web.search": {"provider": "duckduckgo", "max_calls": 20},
                  "web.fetch": {"provider": "firecrawl", "max_calls": 30}},
        "servers": {
            "sheets": {"command": sys.executable, "args": ["-m", "ombro.fake_mcp"],
                            "env": {"OMBRO_FAKE_MCP_RECORD": str(root / "sheets-sent.jsonl")}},
            "notion": {"url": "https://mcp.example.com/notion"},
        }}, "The demo profile's tools. Nothing here reaches a real service.")
    keys.set_key(home, "FIRECRAWL_API_KEY", "demo-not-a-real-key")
    keys.set_key(home, "NOTION_TOKEN", "demo-not-a-real-token")
    demo_api(home)

    print("a shared folder")
    shared = root.parent / f"{root.name}-files"
    shared.mkdir(exist_ok=True)
    (shared / "brand-notes.md").write_text("# Brand notes\n\nInvented notes for the demo.\n")
    try:
        folders.add(home, folders.check_place(shared, home), "demo-files", "read")
    except folders.FolderError as e:
        print(f"  (not shared: {e})")

    print("specs and jobs")
    home.specs_dir.mkdir(parents=True, exist_ok=True)
    specs = {
        "gopher-market-research": ("# Market research for gophers\n\nThree invented facts about the gopher pet market.",
                                   ["marketing"]),
        "reservoir-news": ("# Post the reservoir news\n\nOne post about the invented reservoir, in the news channel.",
                           ["marketing"]),
        "website-terms": ("# Website terms review\n\nCheck the invented website terms for anything missing.", ["legal"]),
        "monthly-figures": ("# Monthly figures\n\nTidy the invented monthly figures into one table.", ["ops"]),
    }
    for name, (body, crews) in specs.items():
        mdfile.write(home.specs_dir / f"{name}.md", {"name": name, "crews": crews, "outward": "balanced"}, body + "\n")
    demo_unfinished_specs(home)

    report = lambda title, summary: fake.say(  # noqa: E731
        f"# {title}\n\n## Summary\n{summary}\n\n- One invented point.\n- Another invented point.\n\n"
        "## What was done\nThe work in the brief.\n\n## What was not done and why\nNothing else was asked.\n\n"
        "## Decisions for you\nNone.")
    research = run_job(home, "gopher-market-research", [
        fake.brief("market-researcher", "Find three invented facts about the gopher pet market."),
        fake.call("workspace__write", {"path": "workspace/gopher-facts.md",
                                       "content": "# Gopher facts\n\n1. Invented.\n2. Invented.\n3. Invented.\n"}),
        fake.result("Wrote three facts.", deliverables=[{"name": "Gopher facts", "path": "workspace/gopher-facts.md"}]),
        fake.finish(), fake.keep_notes(),
        report("Market research for gophers", "The researcher found three **invented** facts and wrote them up.")])
    news = run_job(home, "reservoir-news", [
        fake.brief("marketing-associate", "Post the invented reservoir news."),
        fake.call("mcp__sheets__post_message", {"channel": "news", "text": "The invented reservoir is full.",
                                                    "reason": "The spec asks for one post."}),
        fake.call("mcp__sheets__post_message", {"channel": "social", "text": "Reservoir news, all invented.",
                                                    "reason": "A shorter post for social."}),
        fake.result("Asked to post twice."), fake.finish(), fake.keep_notes(),
        report("Reservoir news", "Two posts are drafted and wait for your approval.")])
    run_job(home, "website-terms", [fake.brief("legal-researcher", "Check the invented terms.")])  # the script runs out: failed
    run_job(home, "monthly-figures", [
        fake.brief("finance-assistant", "Tidy the invented figures."), fake.result("Made the table."), fake.finish(),
        fake.keep_notes(), report("Monthly figures", "The figures are in one table.")])

    demo_skill_proposal(home)

    print("chats")
    chat(home, "market-research-for-gophers", "Market research for gophers", [
        ("you", "Can we look into the market for pet gophers?"),
        ("lead", "Yes. Shall I brief the Marketing Crew to find three facts?"),
        ("you", "Please do."),
        ("lead", "Started. Its progress shows here, and the report when it's done.")], "20261110-0930", [research])
    chat(home, "spotify-campaign", "Spotify campaign", [
        ("you", "What would a small Spotify campaign need?"),
        ("lead", "A clear audience, two or three playlists to target, and a budget cap. Want a brief?")], "20261111-1415")
    chat(home, "reservoir-news", "Reservoir news", [
        ("you", "Post the reservoir news, please."),
        ("lead", "Started. Posting needs your approval, so the posts will wait in Approvals.")], "20261112-0800", [news])

    print("schedules")
    schedules.write_schedule(home, "news-briefing", "0 6 1 1 *", "reservoir-news", True)  # 1 January only
    schedules.write_schedule(home, "monthly-figures", "0 18 * * 4", "monthly-figures", False)

    replies = [fake.say(CHAT_REPLIES[i % len(CHAT_REPLIES)]) for i in range(300)]
    (home.root / "script.json").write_text(json.dumps(unfinished_demo() + live_demo() + replies))
    print(f"done: {root}")


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    root = Path(args[0]).expanduser().resolve() if args else DEFAULT
    if root.exists() and "--fresh" in argv:
        shutil.rmtree(root)
        shutil.rmtree(root.parent / f"{root.name}-files", ignore_errors=True)
    if root.exists():
        print(f"{root} is there already; --fresh makes it again.")
        os.environ["OMBRO_HOME"] = str(root)
        from ombro.home import Home

        demo_providers(Home(root))  # added later; a demo made before then gets them too
        demo_api(Home(root))
        demo_skill_proposal(Home(root))
        demo_unfinished_specs(Home(root))
    else:
        build(root)
    print(f"a skill folder to add: {demo_skill_folder(root)}")
    if "--open" in argv:
        if not APP.exists():
            print(f"No built app at {APP}; build it with `npx tauri build --bundles app` in gui/.")
            return 1
        # One Ombro at a time: quit one that is running (it may be on another home), then start
        # this one. `open -n` would start a second copy beside it, with a second Dock icon.
        subprocess.run(["osascript", "-e", 'quit app id "app.ombro.desktop"'], check=False)
        for _ in range(50):
            if subprocess.run(["pgrep", "-x", "ombro-gui"], capture_output=True).returncode != 0:
                break
            time.sleep(0.1)
        subprocess.run(["open", "--env", f"OMBRO_HOME={root}", str(APP)], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
