# Ombro

A small, local harness for briefing a crew of AI roles and getting bounded,
reported work back.

Ombro assumes models will try to do too much, so it puts deterministic limits
around them. You brief Ombro Lead, which turns what you want into a spec;
you start a job from it, walk away, and read a report later. The Lead plans
the job and briefs the crew's roles. Every job ends in a report, whether it
finished, hit a limit or failed.

**Status: early.** This is the headless core, native tools, interviews,
memory and chat: the home directory, roles and crews, a model connection,
the set-up, role and task interviews, the Lead loop, the guardrail gate, the
result contract, the journal, reports, web search and fetch, workspace files,
secrets, the job index, standing notes, memory search, `ombro chat`,
schedules, outside services through MCP with permissions and approvals, and
Python scripts in the operating system's sandbox, with folders you share.

## Install

Python 3.11 or later.

```bash
uv tool install .
```

or, for development:

```bash
uv sync
uv run pytest
```

## Quick start

```bash
ombro init                         # creates ~/.config/ombro, connects a model, then the set-up interview
```

`init` first asks your name, used only to greet you (Enter skips it). Then it
asks for an OpenAI-compatible endpoint (DeepSeek by default) and its API key,
asks the endpoint which models the key can use, and offers them as a choice
for your default (an endpoint that does not list its models is asked for a
name). Every listed model goes into `models.md`, so roles can use any of
them. It then makes one test call. The key is typed into a hidden prompt and
stored in `secrets/keys.env` with owner-only permissions. It is never sent to a
model or written to a journal or report.

Once a model answers, the Lead holds the set-up interview: who you are, how
you work and how you want reports. It proposes `preferences/instructions.md`
and a "Working with you" section of its `soul.md` (its character is kept),
and writes them only if you accept. Then it offers to keep, rename or add a
crew, to add the first roles (each asked its model when there is a choice),
and to brief a first task.

```bash
ombro role add ["Marketing Assistant"] # the Lead interviews you and proposes the role's files
ombro brief "find three bakeries"      # the Lead interviews you and saves a spec; offers to start it
ombro specs                            # saved specs, by name
ombro start --spec find-bakeries       # start a saved spec (by name or by path)
ombro start                            # brief a task, then start it as soon as you accept the spec
ombro start --spec my-spec.md --detach   # keeps going after the terminal closes
ombro jobs                             # list jobs
ombro status [job_id]                  # limits and usage
ombro journal [job_id] [--follow]      # the event log, readable; --follow shows new events as they come
ombro report [job]                     # the final report (any part of the job's name; the latest by default)
ombro chat [--resume [title]]          # talk with the Lead; /brief, /remember, /new, /chats inside
ombro chats                            # saved chats by title
ombro summary                          # who you are, the crew, what is running and waiting, and jobs not yet seen
ombro preferences [edit]               # your name, theme, default crew, editor and outward preset
ombro stop <job_id>                    # stop a job; it writes its report first
ombro open [job_id]                    # open a job's workspace folder
ombro limits                           # what jobs and interviews may use
ombro limits set generous              # change the allowance level
ombro keys                             # list keys (masked) and what uses them
ombro keys set FIRECRAWL_API_KEY       # set one; typed into a hidden prompt
ombro schedule                         # schedules, their next and last jobs, and the scheduler entry
ombro schedule add <name> --spec <spec> --cron "30 7 * * 1-5"
ombro schedule show | enable | disable | remove <name>
ombro schedule install | uninstall     # the one system scheduler entry; asks first
ombro tick [--dry-run]                 # start what is due (the scheduler calls this each minute)
ombro approvals                        # what waits for your approval, across jobs
ombro approvals show ab12-3            # one in full: tool, arguments, role, reason
ombro approvals approve ab12-1 ab12-3  # run the named ones (each at most once); asks first
ombro approvals decline ab12-2         # drop it for good
ombro approvals edit ab12-3            # change its arguments; it waits for approval again
ombro tools [server]                   # tools and MCP servers; a server's tools and their permission
ombro tools web.search --provider duckduckgo --max-calls 30   # change a built-in tool
ombro server add <name>                # add an MCP server to tools.md; asks for any secret hidden
ombro server remove <name>
ombro folder                           # shared folders, and whether each can be backed up
ombro folder add ~/Documents/Brand     # share one; asks read or write
ombro folder remove brand
ombro models                           # model connections, their models, the default, and who uses each
ombro models default | refresh | add   # choose the default; ask each endpoint for its models; connect another
ombro model [lead | <crew>/<role>] [name]   # which model the Lead or a role uses
ombro keys remove FIRECRAWL_API_KEY
ombro role list [--crew <crew>]        # the Lead, then each crew's roles
ombro role add [title] --crew <crew>   # a role joins one crew
ombro role edit | remove <crew>/<role> # or the role's own name, where only one crew has it
ombro role edit lead                   # the one Lead's files
ombro crew list | add <name> | use <name>   # a new crew starts empty; `use` sets the default crew
ombro crew add legal --title "Legal Team"   # the title shown; without one, "Legal Crew"
ombro start --spec <spec> --crew ops   # --crew gives the crews when the spec names none
ombro start --spec <spec> --role marketing-researcher   # who does the work, this job only
```

Every command that lists or shows something takes `--json`, for an interface
to read: `jobs`, `status`, `report` (with its deliverables as `file://`
links), `journal` (with `--follow`, one event a line), `keys` (names and
whether each is set, never a value), `specs`, `role list`, `crew list`,
`limits`, `schedule`, `folder`, `models`, `summary` and `preferences`.

Roles and crews have a display title as well as a folder name. The role
interview asks for the title ("Marketing Assistant") and makes the folder
name from it (`marketing-assistant`); a role or crew without a `title:`
shows its name tidied. Everything shown to you uses titles, and a crew's
title works wherever its name does (`ombro crew use "Marketing Team"`).

Set `OMBRO_HOME` to use a home directory other than `~/.config/ombro`.
Example roles and specs are in [`examples/`](examples); a spec can also be
any Markdown file you write yourself.

## The terminal interface

`ombro` on its own, in a terminal, opens the terminal interface: a chat with
the Lead, full screen, with the shell restored when you leave. It opens with
a header (your crew as a tree, what is running, scheduled and waiting) that
scrolls away as the conversation grows. Type to chat; `/` lists the
commands; `/brief` starts a task interview and `/chat` goes back; `/theme`
switches between light and dark. Ctrl+C stops what is in progress; with
nothing in progress and nothing typed, press it twice to leave, or type
`/quit`. `theme: auto | light | dark` in `preferences/config.md` sets the
colours; `auto` asks the terminal for its background, then the system.

On a home with no model or key, it opens into set-up: your name, the model and
its key (typed into a hidden field), then the set-up interview. `/setup` goes
through it again.

The `/` commands do what their `ombro` commands do:

- `/brief`, `/chat`, `/new`, `/remember`: as in `ombro chat`. When a brief is
  confirmed, it offers to start the spec at once, in the background.
- `/start [spec]`: start a saved spec again, with no interview (no name: choose
  from the saved specs, newest first).
- `/jobs`, `/report [job]` (the summary, deliverables and decisions, with every
  file the job made as a link; `/report <job> full` adds Ombro's record),
  `/stop <job_id>`, `/open [job_id]`.
- `/approvals`, `/approvals show | approve | decline <ref>...`,
  `/approvals resolve <ref> sent | not-sent`; decisions work while a job is going.
- `/role`, `/role add [title] [--crew <crew>]`, `/role edit <name>`;
  `/crew`, `/crew add <name> [title]`, `/crew use <name>`.
- `/limits`, `/limits set [level]`: one allowance level for all of Ombro.
- `/model` (who uses which model, and changing one); `/models`,
  `/models default | refresh | add`.
- `/keys`, `/keys set <NAME>` (hidden); `/folder`, `/folder add <path>`,
  `/folder remove <name>`; `/schedule`, `/schedule enable | disable <name>`;
  `/tools`, `/tools <tool>` (change a built-in tool's provider or calls, such
  as `/tools web.search`), `/tools add [name]`, `/tools remove <name>`; `/preferences`,
  `/preferences edit`; `/theme`, `/help`, `/quit`.

Enter sends; Shift+Return (or Option+Return, Ctrl+J, or `\` then Return)
starts a new line. While the Lead is working a small glyph turns beneath the
conversation. `/new` clears the screen and starts a fresh chat.

A job started here shows its progress as it goes (who was briefed, who
returned). When any job ends while the interface is open, scheduled jobs
included, a notice says how it ended, links its deliverables, says what is
waiting and how to open the report; on opening, it says what finished and what
is waiting since you were last here. The interface is a layer over the
commands and files: everything it does, the command line does too. A few
things are still command-line only: `ombro approvals edit`, `ombro schedule add`,
`ombro keys remove`.

## Interviews

The Lead interviews you in three places: set-up (`ombro init`), adding a role
(`ombro role add`) and briefing a task (`ombro brief`, or `ombro start` with no
spec). The three are kept apart: set-up records only who you are and how
you work, for every task; a role describes a kind of work it can be briefed
for again and again; a brief covers one task. In each the Lead asks pointed
questions, reflects back what it understood, and proposes the exact files it
would write. Each question shows how far through you are:

```
[●●●○○  3 of 5 covered  ·  still to cover: what would be a waste, non-goals  ·  about halfway]
```

When it has enough, it proposes the files. The harness checks the proposal,
shows it, and asks:

```
[a]ccept, ask for a [c]hange, [e]dit by hand, or [q]uit?
```

Nothing is written until you accept. A change request goes back to the Lead;
`e` opens the draft in your editor (`editor` in `preferences/config.md`, or
`$EDITOR`), checks your version, and shows it again. `/quit` or Ctrl-C at
any point writes nothing. When a file is replaced, its old text is kept next
to it as `name.previous.md`.

- **Roles.** The Lead may propose only tools from the tool registry and
  skills from `skills/` (or `skills: all`); anything else is refused. The harness checks the role
  the same way a job loads it, and warns when a role has no workspace tools
  (so it can only work from its brief).
- **Task briefs.** The Lead must find out what done looks like, who uses the
  result and how, the binding constraint, what would make it a waste, and the
  non-goals. A spec with any of these empty is refused. If answers are
  missing or contradict each other, the Lead lists the gaps and you can answer
  them; if you end the brief instead (`/end`), `ombro start` records an
  `insufficient_brief` job whose report names the gaps, and no role is briefed.
- **Standards are not asked about.** Accuracy, honesty and citing sources
  are Ombro's fixed rules for every Lead, role and interview, so the Lead
  never asks whether you want them.
- **Keys** are never asked for. Anything you type is scrubbed of the values in
  `secrets/keys.env` and of common key shapes before it reaches the model or the transcript.
- Each interview has its own token cap (see Limits) and is saved as a
  transcript in `interviews/`. A job started from an interview
  keeps a copy as `interview.md`, and its report shows the interview's tokens
  separately from the job's.

## One Lead, many crews

There is one Lead, above every crew. It is the role you talk to in chat and
interviews, and the one that briefs roles in jobs. A crew is a named group of
roles with its own memory: a team of skills (legal, marketing, ops) or a
separate project or client (marketing for one project, marketing for
another), whichever suits you. A fresh install has the Lead and a `default`
crew with one role in it, `general-assistant`, a generalist for everyday
tasks that no other role fits.

A spec names the crews its job may use (`crews: [marketing-a, ops]`); a spec
with no `crews:` line uses the default crew. The brief interview proposes the
crews, asks which one where two do similar work, and shows them before you
accept. In a job with more than one crew, roles are named `crew/role`
(`marketing-a/writer`); the Lead may use a role's own name where only one of
the crews has it. Jobs, specs, schedules and interview transcripts belong to
no crew.

A spec can also say who does the work (`roles: [marketing-researcher]`),
which the brief interview records when you name a role. The Lead then briefs
those roles for the work itself, and others only for side tasks such as a
check; the report says who the spec named and who else was briefed. Without
`roles:`, the Lead chooses from the roles' descriptions.

## How a job works

1. Ombro loads the Lead, the roles of the crews the spec names, the limits,
   the spec and those crews' memory (see Memory).
2. The Lead decides whom to brief and with what (`brief`), one role at a time.
3. The role works within its own step limit and hands in a structured result
   with `submit_result`.
4. The harness validates the result and records it in the journal. An invalid
   result is retried once with a stricter instruction, and then the brief is
   marked failed and the Lead is told. A reply cut off at the output limit
   (`max_output_tokens`, 32,000 by default, thinking included for models that
   think) is not counted as invalid: the role is told to write less at once,
   and three in one brief end it. Where a model's endpoint allows fewer output
   tokens, Ombro learns its maximum from the endpoint, uses it, and the report
   suggests a `max_output_tokens` line for that model in `models.md`. Near a
   token cap the reply allowance shrinks to what is left.
5. The Lead briefs again, searches past reports (`memory.search`), or calls
   `finish`.
6. On finish, or when any limit is reached, delegation stops. The Lead
   rewrites its part of the notes of each crew whose roles it briefed, one
   call per crew, then writes the report
   in a separate call with no tools. If even that is impossible, the harness
   writes the report from the journal.
7. The harness adds one line for the job to the `memory/index.md` of each
   crew the spec names.

A job is named for its spec, then when it started:
`teapot-survey-20261113-1301`. Commands that take a job take any part of its
name (`ombro report teapot`), or the latest job by default.

The report reads as a report: the task's title and a status line in words,
then a short summary, the deliverables (in the workspace or a shared folder),
decisions for you, and what was and was not done. Ombro's own record follows
after a divider: the job's facts, the briefs, outward actions, code, shared
folders, flagged material, the changes to each crew's notes, and usage. The
terminal interface shows reports and the Lead's words as Markdown.

## Memory

Each crew has its own memory, and a job sees only the memory of the crews its
spec names. What a job costs stays the same however many jobs came before
it; it grows only with the crews a spec names.

- **`memory/index.md`**: one line per job that named the crew, written by the harness: date, job
  ID, outcome, one-line brief and the path to its report. Every job gets
  one, including stopped, failed and insufficient briefs.
- **`memory/notes.md`**: standing lessons in two parts. **From you** holds
  your own dated entries (added by hand or with `/remember` in chat); no
  model ever changes or drops them. **From jobs** is the Lead's: at the end
  of each job that briefed a role, it rewrites this part from what the roles
  returned, or keeps it as it is. The whole file has a cap of 10,000
  characters. A rewrite over the cap is refused and the Lead is told how much
  room it has; a second refusal leaves the notes as they were. The report's
  "Changes to notes.md" lists what was added and removed, worked out by the
  harness. The old file is kept as `notes.previous.md`.
- **At the start of a job** the Lead is given the notes.md of each crew the
  spec names and the 20 most recent index entries across those crews, each
  at most 200 characters. Nothing else.
- **`memory.search`** (the Lead only) finds passages in the past reports of
  jobs that used the spec's crews,
  reports by keyword, up to 4,000 characters in all, and is capped at 8 calls
  a job. It does not use a round.

The figures are in `limits.md`, in characters, so they mean the same on
every model: `memory_notes_max_chars`, `memory_index_entries`,
`memory_index_entry_chars`, `memory_search_max_chars`.

## Skills

A skill is a reusable method, kept in one library that every crew shares, in
the open Agent Skills format: `skills/<name>/SKILL.md`, with `name` (lower
case and hyphens, the same as the folder) and `description` (what it does and
when to use it) in the front matter, then the method in Markdown. An optional
`references/` folder holds longer material the method points to. An older
`skill.md` is read the same way.

- **Who uses which.** A role's `config.md` lists its skills
  (`skills: [kettle-survey]`), or `skills: all` for the whole library. A role
  with no `skills:` line has none, so a new skill reaches no role until you
  give it: `ombro skills give <skill> <crew/role>`, `/skills give`, the role
  interview, or editing `config.md`.
- **Read on demand.** A role's prompt lists only the name and description of
  each skill it may use; it reads the method with `skills.read` when it needs
  it. The Lead sees the whole library and each role's skills, so a brief can
  say which skill to read. The listing in any prompt is capped at
  `skills_list_chars` (4,000 characters), and a read at `skill_read_chars`
  (20,000), so a growing library does not grow the cost of a job. The report
  says which skills each role read.
- **Models propose; you decide.** After a completed job in which a role
  returned a result, the Lead may propose one skill (a new one, or a better
  version of one), or none, which is the usual answer. A proposal waits for
  your approval as a `skills.propose` action: `ombro approvals show <ref>` gives the
  whole text, or the diff against today's version, the roles it is for, and
  any signs of injected text in it; `approve` writes it and gives it to those
  roles, `decline` drops it, and `edit` opens the text in your editor. At most
  10 proposals wait at once (`skill_proposals_waiting`); a model-written
  skill is capped at `skill_max_chars` (10,000 characters).
- **In chat**, ask the Lead to remember how to do something and it offers a
  skill, showing it in full, or as a diff; nothing is written without your yes.
- **Nothing is lost.** Replacing a skill keeps the old version in
  `skills/Archive/<name>/<date-time>/`. Each skill records where it came from
  (`metadata: source:`); one with no source is yours.
- A skill never adds a tool, and its `scripts/` folder is not run.

- **Importing.** `ombro skills add <folder>` (or `/skills add <folder>`)
  brings in a skill you found elsewhere. A skill from outside is someone
  else's instructions, so Ombro checks its format, shows you the whole text
  (or the diff against yours), lists any signs of injected text and what it
  will not copy, and adds it only after your yes. It copies `SKILL.md` and
  `references/` (text files up to 1 MB), never `scripts/` or anything else,
  and records it as imported. No role uses it until you give it.

`ombro skills` lists the approved skills and who uses each, then the
proposals waiting for your approval; `ombro skills show <name>` and
`edit <name>` show and edit one.

## Chat

`ombro chat` is a conversation with the Lead, with two modes; the current one
is always shown in the prompt (`[Chat] You:` or `[Brief] You:`).

- **Chat** sees every crew: it is the same one Lead. It can search and read
  reports, specs, notes, indexes and workspace files, and use web search and
  fetch. It knows which folders you have shared, lists and reads them all as
  `shared/<name>/...`, and can write a file in one shared for writing: it
  shows you what it will write, and nothing changes until you say yes. It
  never deletes; a file that should go is set aside (below), also after your
  yes. It cannot brief roles, start jobs, approve what waits for approval or use outward
  tools; those calls are refused. Chat transcripts, journals, keys and
  settings cannot be read.
- **Brief** is the task interview. When the Lead hears a task it offers to
  brief it, and never switches on its own. Accept, or type `/brief [@crew]
  [text]` at any point: the interview starts from what you said in the chat
  (your own messages, most recent first, up to 4,000 characters) and the
  Lead's one-line summary if you accepted its offer. It saves a spec, which
  you start with `ombro start --spec <name>`. `/chat` goes back with nothing written.
- **Saved specs.** Ask to restart a task you briefed before and the Lead
  finds its spec and asks, in one question, whether to start it: yes, no, or
  "change it". It can say who does the work this time (for this job only, as
  `ombro start --spec <name> --role <role>` does), leaving the saved spec as it
  is. "change it", or asking for a change to the task itself, opens the saved
  spec: the interview starts from its text, asks only about the change, and
  shows it as a diff (the old text is kept as `<name>.previous.md`).
- **`/new`** saves the chat and starts a fresh one, with its own token cap
  and usage from zero. Nothing from the old one carries over.
- **Titles.** After the first exchange a small model call names the chat in
  a few words (a confirmed brief gives it the spec's title; `/rename <title>`
  sets your own, which is kept). The file is named to match:
  `chats/teapot-makers-20261113-1417.md`.
- **Carry on.** `/chats` lists past chats by title and reopens one: the
  conversation is shown again and you carry on where you left off
  (tool results from before are not kept). `ombro chats` and
  `ombro chat --resume <part of a title>` do the same on the command line.
- **`/remember [@crew] <text>`** adds a dated line to a crew's notes.md
  ("From you"); the default crew unless you name one, or `ombro chat --crew
  <name>` sets it for the session. The Lead can offer to remember something
  and propose a wording. Nothing is written until you confirm the wording. If
  it would pass the cap, you are shown the sizes and your entries and asked
  what to trim.
- **Bounded.** A chat has its own token cap (`chat_max_tokens`, set by the
  allowance), per-tool call caps (`chat_tool_max_calls`) and a cap on model
  calls for one message. Usage is shown after each reply. Once the
  conversation passes `chat_history_chars` (30,000), the oldest turns are no
  longer sent to the model.
- **Saved.** Each chat is saved in `chats/`. Transcripts are never loaded into
  a job and never searched; only what you chose to remember carries into jobs.

In chat and interviews, a line that starts with `"""` begins a block of
several lines, ended by a line that ends with `"""`, so a pasted paragraph
is one answer.

## Schedules

A schedule is a file in `schedules/<name>.md`:

```yaml
---
name: morning-brief
cron: "30 7 * * 1-5"          # local time: minute hour day month weekday
spec: specs/morning-brief.md  # a saved spec, which names the crews; scheduled jobs never interview
enabled: true
limits: {allowance: conservative, run_timeout: 15m}   # optional; in tokens
grace: 10m                     # optional; default schedule_grace in limits.md
---
```

The files are the source of truth. `ombro schedule install` adds one system
scheduler entry (a launch agent on macOS, a crontab line elsewhere) that jobs
`ombro tick` every minute; nothing is installed without your confirmation,
and `ombro schedule add` offers it when it is missing. Editing a schedule
needs no reinstalling: the next tick reads it, and the change takes effect
from the schedule's next slot.

- **Starting.** Tick starts each due slot once, detached, exactly as
  `ombro start --detach` does, under limits fixed at the start and recorded in
  job.md. job.md and the report name the schedule and the slot. The limits
  are limits.md, then the spec's `limits:`, then the schedule's. A scheduled
  job must have a token cap, so `unlimited` is refused.
- **Late and missed.** A slot is started if a tick sees it within its grace
  period. A slot missed because the machine was off, asleep or logged out is
  never started late: it is noted in the `index.md` of each crew its spec
  names, one entry per gap:

  ```
  - 2026-11-10 · schedule:morning-brief@2026-11-10T07:30..2026-11-12T07:30 · missed · 3 slots: the machine was off or asleep (no tick from 09 Nov 23:00 to 12 Nov 09:14) · schedules/morning-brief.md
  ```
- **Overlap.** If a schedule's previous job is still going, or
  `schedule_max_running` scheduled jobs are going, or this tick has already
  started `schedule_max_starts`, the slot waits while it is within grace, then
  is skipped and noted with the reason.
- **Never twice.** Ticks take a lock, and a slot is looked up by its
  wall-clock time in the jobs and the index before it starts. When clocks go
  back, the repeated hour's slots start once; when they go forward, a slot in
  the skipped hour starts at the first minute after the change.
- **Never silent.** A due slot that cannot start (a missing spec, invalid
  limits, no model) becomes a `failed` job with a report and an index entry.
  A schedule file with a problem is shown by `ombro schedule` and logged once.
- **Logs.** `scheduler/tick.log` has one line per tick and one per start or
  note. `scheduler/scheduler.log` catches anything the system scheduler sees
  on stderr. `scheduler/tick.md` records the last tick, which is how a
  missed slot is told from one that was not due; it is safe to delete.

## Tools

A role gets only the tools its `config.md` lists:

| Tool | Does |
|---|---|
| `web.search` | Web search through a provider. Returns titles, URLs and snippets. |
| `web.fetch` | Reads one page as text or Markdown through a provider. |
| `workspace.read`, `workspace.write`, `workspace.list` | Text files in the job's `workspace/` and `scratch/` folders, and in folders shared with the job. |
| `workspace.place` | Copies a workspace file outside the job: at once into a folder shared for writing, waiting for your approval anywhere else. |
| `code.run` | Runs a Python script the role writes, in the operating system's sandbox. See Code and shared folders. |
| `memory.search` | The Lead only: passages from past reports of jobs that used the spec's crews. |

Workspace paths start with `workspace/` (deliverables) or `scratch/` (working
notes). Absolute paths, `..`, and symlinks that lead outside those folders
are refused, and nothing is written through a symlink. A deliverable a role
names by path is listed in the report with its full path. Only files in
`workspace/` are listed as deliverables; working files in `scratch/` and
short inline notes are listed apart, and workspace files no role named are
listed too.

Roles ask for a tool by what it does, never by vendor. `tools.md` says which
provider backs each one:

| Provider | Tools | Notes |
|---|---|---|
| `firecrawl` | search, fetch | Needs `FIRECRAWL_API_KEY`. |
| `duckduckgo` | search | No key. Unofficial and rate-limited, so best effort. |
| `direct` | fetch | No key. A plain download. Local and private network addresses are refused. |
| `fake` | search, fetch | Invented results. Records every request in the job's `fake-calls.jsonl`. |

```yaml
---
tools:
  web.search: {provider: firecrawl, max_calls: 20}
  web.fetch: {provider: firecrawl, max_calls: 30}
  workspace.write: {max_calls: 40}
providers:
  firecrawl: {key: FIRECRAWL_API_KEY}
---
```

Without `max_calls`, a job may make 20 searches and 30 fetches through
Firecrawl, which bills each call, and 60 searches through DuckDuckGo and 100
direct fetches, which are free. `/tools web.search` changes either.

`max_calls` caps a tool's calls per job, which is also how a service that
bills in its own units, such as Firecrawl credits, is kept in bounds. Like
limits, tool settings are fixed when a job starts and recorded in `job.md`.

Every secret lives in one owner-only folder, `secrets/`: single keys in
`keys.env`, sign-ins for remote MCP servers in `oauth/`, and key files (such
as a service account's) by name. Keys are read at the moment of use, so a key
set with `ombro keys set` is picked up on the next call, even in a job already
going. Nothing in `secrets/` ever reaches a model, a journal or a report. A
`.env` from an earlier version is moved into `secrets/keys.env` the first
time Ombro runs, and Ombro says so.

## Code and shared folders

**Code.** A role that names `code.run` can write a Python script and run it,
for work on files: renaming, sorting, converting, editing in bulk, counting
and checking. Ombro does not try to judge code by reading it, because a
list of "dangerous commands" is easy to get round. Instead every script runs
in the operating system's own sandbox, which limits what any code can reach:

- **macOS:** the system sandbox (`sandbox-exec`), with a profile that
  denies everything it does not name.
- **Linux:** bubblewrap (`bwrap`; install it with your package manager, for
  example `sudo apt install bubblewrap`). The script starts in an empty
  file system with no network, holding only what is bound into it.
- Anywhere the sandbox cannot be set up, `code.run` is left out of the job
  and the report says why. Code never runs unconfined.

A script can read and write the job's `workspace/` and `scratch/`, and the
folders shared with the job as granted. It can read what Python needs to
start, and nothing else: not the rest of the Ombro home (secrets, settings,
roles, memory, other jobs, this job's journal, `job.md` or report), not the
rest of your files, and not the network. It runs on the Python Ombro runs
on, isolated, with the standard library only. It cannot start other
programs (on Linux it can start programs already inside its sandbox, which
still has no network). Its environment holds no keys. It may change what is
in `workspace/`, `scratch/` and a writable shared folder, but never the
folders themselves; if one is ever not a real folder where the job started,
the tools refuse it.

Each script is limited by `code_timeout` (60s, never more than the job has
left), CPU time, `code_max_memory` (1 GB; on macOS by watching the script),
5 MB for any one file, and `code_output_chars` (8,000 characters of output
shown to the role: the start and end, with the middle cut). `code_max_total`
(2 GB) caps everything in a job's `workspace/` and `scratch/`: past it, no
more scripts run in that job. `code.run` has
10 calls a job unless `tools.md` says otherwise. The role is told the exit
status, the output, the time, any limit that stopped it, and which files
were created, changed or removed, worked out by the harness. Each script and
its output are kept in the job's `code/` folder, which scripts cannot write
to. The journal has a `code_run` event for each, and the report a Code
section.

**Shared folders.** A job reaches only its own `workspace/` and `scratch/`
unless you share a folder. Grants live in `folders.md`, by name:

```yaml
---
folders:
  brand:  {path: /Volumes/Studio/Brand, access: read}
  photos: {path: ~/Pictures/2026 trip, access: write}
declined: [~/Projects/site]
---
```

- **Only you grant.** `ombro folder add <path>` asks read or write. When
  `ombro brief` (or `ombro start` without a spec) starts in a folder that is
  not shared, Ombro asks once whether to share it; a "no" is remembered in
  `declined`. In the brief interview the Lead asks which folders the task
  needs; a folder not yet shared is put to you when you accept the spec.
  A model can suggest a folder but never shares one.
- **What can be shared:** folders inside your home folder, and mounted
  drives and server shares (`/Volumes/...` on macOS; `/mnt`, `/media` and
  `/run/media` on Linux). Never the root, system folders, your home folder
  itself, hidden folders, `~/Library`, or the Ombro home.
- **A spec chooses.** Grants apply to every crew, and a spec names the ones
  its job uses (`folders: [brand, photos]`, or `folders: {photos: read}` to
  lower write to read). A spec cannot add a folder that is not shared; one
  that is no longer shared is left out and the report says so. Folders are
  fixed when a job starts and recorded in `job.md`. Jobs never stop to ask.
- **Inside a grant** roles work as you would: `shared/<name>/...` through
  the workspace tools and in scripts. Changes there are internal and run
  without approval; the grant is the permission, and presets do not change it.
  The Lead in chat reaches every grant too, writing only after your yes.
- **Nothing is deleted.** No role, the Lead included, deletes a file in a
  shared folder. To get rid of one, `workspace.set_aside` (given to every role
  that can write) moves it into `For deletion/`, or into `Archive/` when a
  newer file replaces it, at the top of the same folder, with a line in that
  folder's `README.md` saying why. The report lists what was set aside. Only
  you empty either folder.
- **Replacing keeps the old version.** The first time a job replaces a file in
  a shared folder, its old version is copied into `Archive/` with a note (once
  per file per job). In chat, every replacement keeps the old version. Scripts
  that replace files are covered by the backup clone; scripts are told never
  to delete, though that is not yet enforced.
- **Backup.** Before a job that can write to a shared folder starts, Ombro
  clones it into the job's `backup/` if the disk allows (APFS on macOS,
  reflinks on Linux). A clone is near-instant and takes almost no space until
  files change. A server share usually cannot be cloned: it stays writable,
  `ombro folder` says no backup will be taken, and the server's own
  backups are the remedy. The report lists every file created, changed and
  removed, from a before-and-after comparison by the harness.
- **Somewhere not shared.** To save a file in a folder that is not shared
  for writing, a role writes it in `workspace/` and calls `workspace.place`.
  The copy waits for your approval with a copy of the file as it was; `ombro
  approvals approve` checks the destination again, and never replaces a file
  unless the action says `overwrite: true`. Approving a placement does not
  share the folder. Reading a folder that is not shared is refused, and the
  report lists the paths refused, so you can share the folder before the
  next job.

## Untrusted material

Anything a model reads that came from neither you nor Ombro (web pages, MCP
results, file contents, script output, what roles return) is **material**,
and may try to steer the model. Ombro's defence does not depend on the model
noticing:

- What a role can do (its tools, folders, crews, permissions and limits) is
  fixed before it reads anything. Material cannot add to it.
- Material reaches a model fenced, between `⟪material <id> from <source>⟫`
  and `⟪end material <id>⟫`, with a random id for each call; text inside that
  copies a fence is neutralised. The fixed rules say fenced text is assessed,
  never obeyed.
- Once a role has read material in a brief, its external calls in that brief
  wait for your approval even under `auto`, unless the spec allows that tool
  by name. The report says which waited for this reason.
- `web.fetch` refuses an address with more than `fetch_max_query` (200)
  characters after `?`, so a page cannot have a role send data out in a link.
- Secrets never enter a prompt, code has no network, and nothing is deleted.
- Common signs of injected instructions ("ignore previous instructions",
  text addressed to an AI, hidden characters, requests to send or delete
  files) are flagged in the report's Material section, with where they came
  from. A flag never stops a job; it tells you what to check. In chat, Ombro
  says so in the conversation at once.

## Outside services: MCP, permissions and approvals

Ombro builds no integrations of its own. Every tool that reaches an outside
service (a sheet, a calendar, a social account, a CRM, a design tool, Zapier)
comes from an MCP server listed in `tools.md`:

```yaml
---
servers:
  sheets:
    command: uvx                          # a local server, which handles its own sign-in
    args: [--with, "mcp==1.30.0", "mcp-google-sheets==0.6.3", --include-tools, "get_sheet_data,update_cells"]
    env: {SERVICE_ACCOUNT_PATH: "${SHEETS_KEY_FILE}", DRIVE_FOLDER_ID: "<a folder shared with the account>"}
    start_timeout: 120s                   # the first start downloads the package
  notes:
    url: https://mcp.example.invalid/mcp  # a remote server you sign in to: `ombro connect notes`
    class: internal
  remote:
    url: https://other.example.invalid/mcp  # a remote server with a fixed token instead
    headers: {Authorization: "Bearer ${REMOTE_TOKEN}"}
tools:
  mcp.sheets.update_cells: {max_calls: 10}
  mcp.sheets.share_spreadsheet: {permission: refuse}
---
```

`ombro server add <name>` writes an entry like these for you, so tools.md need
not be edited by hand. It asks whether the server is local or remote, takes its
command or address, and asks for each secret in a hidden prompt: the value goes
into `secrets/keys.env` and tools.md gets only `${NAME}`, in `env` for a local
server or `headers` for a remote one. It warns when a package runner's server
or library is not pinned to an exact version, refuses a `${NAME}` on the
command line, checks the entry as a job would, and writes nothing until you
confirm. The same as flags, for scripts and interfaces:

```bash
ombro server add notes --command "uvx invented-notes-mcp==1.2.3" --secret NOTES_TOKEN --class internal
ombro server add tracker --url https://mcp.tracker.invalid/mcp --secret Authorization=TRACKER_TOKEN
ombro server remove notes    # its tool settings too; says which roles named its tools; keys are kept
```

`${NAME}` is read from `secrets/keys.env` when the server starts and is never logged.
A server Ombro starts gets only `PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_ALL`,
`LC_CTYPE` and `TMPDIR` from Ombro's own environment, plus its `env`, so keys kept in
your shell never reach it; `pass_env: [NAME, ...]` on a server passes others it needs.
Put a `${NAME}` in `env`, not in `command` or `args`: other users of the machine can read
a command line in the process list, and Ombro warns if one is there.
A server's tools are named `mcp.<server>.<tool>`; `ombro tools <server>`
lists them. A role can use only the ones its `tools:` list names, so a large
server costs only what a role uses. Ombro speaks MCP itself (tools only),
rather than through the SDK, in both eras of the protocol: it asks in the
2026-07-28 way first and falls back to the 2025-06-18 handshake for a server
that does not understand it. `ombro tools <server>` says which one a server
speaks. Where the 2026-07-28 standard says to send a request again after a
broken answer, Ombro does so only for listing tools: a tool call that may
have happened is never sent twice (it becomes uncertain instead).

**Signing in to a remote server.** A remote server that asks for sign-in is
connected once, in your browser:

```
ombro connect notes          # opens the browser; sign in and allow access
ombro keys                   # lists keys, and each remote server's sign-in
ombro disconnect notes       # forgets it, and asks the service to revoke it
```

Ombro follows MCP's authorization standard: it finds the server's sign-in
service from the server itself, registers itself as a native app (or uses
a `client_id` you give the server in `tools.md`), and signs in with PKCE,
binding the token to that server's address. The sign-in is kept in
`secrets/oauth/<server>.json`, readable only by you, and renews itself. A
job never opens a browser: if a sign-in has lapsed, that server's tools are
left out, the report says to connect again, and actions waiting for approval keep waiting.

**Pin what a server runs.** A package runner such as `uvx` downloads and
runs the server's code, so name an exact version (`==0.6.3`, never
`@latest`). It also resolves the server's own dependencies afresh, so pin
those where the server needs it: `mcp-google-sheets` 0.6.3 asks for any
`mcp>=1.8`, gets 2.x and fails to start, hence `--with "mcp==1.30.0"`. If a
server can limit the tools it offers (here `--include-tools`), offer only
those your roles use. When a server fails to start, Ombro shows the last
lines it printed; in a job, all of it is in the job's `mcp-<server>.log`.

**Classes.** Each tool is classed by who it reaches, as you would judge a
trusted employee's work:

- `read-only` changes nothing.
- `internal` changes things in your organisation's own tools: documents,
  sheets, databases, CRMs, Notion, Linear, files and drafts, including
  overwriting and trashing, and even when colleagues see or are notified of
  the change. It is done, then reported, like an employee who comes back
  with the finished spreadsheet. Internal is not the same as harmless: it
  runs without approval because the role is trusted, and backups and
  version history are the remedy if it goes wrong.
- `external` reaches people outside your organisation, or the public, or
  spends money: email sent, messages to customers, social posts,
  publishing to the web, public records, purchases. A tool that can reach
  either side, such as sending email or calendar invitations, counts as
  external, because it cannot tell who is inside.

The boundary is the organisation, not visibility: a Notion page is internal
even though Notion can publish pages; publishing one to the web would be
external. The class is a default you can change: a team chat whose tool
could reach outside counts as external, and `class: internal` makes it
internal if you know it stays inside.

A tool's class comes from, each overriding the one before: the server's
markings (`readOnlyHint` makes it read-only, `openWorldHint: false` makes it
internal), `class:` on the server in `tools.md` (for all its tools that are
not read-only), then `class:` on the tool. A tool that changes something and is not
classed counts as `external`, the safe default. Most servers do not mark
tools this way, so a sheets or documents server usually needs one line:

```yaml
servers:
  sheets:
    command: uvx
    class: internal      # its tools that are not read-only are internal
```

`ombro tools <server>` shows each tool's class, where it came from, and the
line to add where a tool is external only by default.

**Permissions.** Every outward call is allowed (it runs, and is recorded),
held for approval (it waits under Approvals) or refused:

| `outward` | read-only | internal | external |
|---|---|---|---|
| `manual` | allow | stage | stage |
| `balanced` (default) | allow | allow | stage |
| `auto` | allow | allow | allow |

Set your default with `outward:` in `preferences/config.md`. A spec
overrides it, with a preset and, if wanted, particular tools:

```yaml
outward: {preset: balanced, allow: [mcp.mail.send_message], stage: [mcp.sheets.update_cells]}
```

The task interview asks what may happen without you whenever the crew has
external tools, and writes the answer into the spec. A crew whose tools are
only read-only and internal needs no answer. `permission: refuse` in
`tools.md` beats everything, for every class, read-only included: a refused
tool is not offered to any role. Permissions are fixed when a job starts and
recorded in `job.md`; scheduled jobs follow them like any other.

**Approvals.** A call held for approval does not happen. The harness writes
it to the job's `outbox/` as one file (tool, arguments, the role and its
reason, the time, a status), tells the role it waits for approval, and never
holds an identical call twice in a job. Each one counts towards the tool's
`max_calls`. The report's Outward actions section lists work done, sends
allowed, and everything that waited for approval, from the harness's own record.

You can decide while the job is still going. Holding an action and every decision take
the job's outbox lock, so the two never cross. An action you approve during
the job is carried out by the approval, not by the job: the role is not told,
and nothing it returns reaches the job. The report shows each action that waited
as it stood when the report was written (waiting, or decided by you), and
decisions made after that are added at the end.

You decide on named actions only; there is no "approve all":

- `ombro approvals` lists what is waiting, across jobs, by reference (`ab12-3`).
- `ombro approvals approve ab12-1 ab12-3` shows each in full, asks once, and
  runs them. `decline` drops them for good. `edit` changes an action's
  arguments (in your editor, or `--arguments '<json>'`); they are checked
  against the tool's rules and the action waits for approval again. You may
  also edit `arguments` in the file by hand; it is shown as edited and
  checked before it runs.
- An action runs at most once. Its status is written before and after the
  call, under a lock. If Ombro stops mid-call, or the server does not
  answer, the action becomes `uncertain`: it may or may not have happened,
  and it is never retried. Settle it with `ombro approvals resolve <ref> sent`
  or `not-sent`.
- Outcomes are recorded in the action's file, the job's journal, job.md and
  an "Approvals" section added to the report.
- Every `ombro approvals` command takes `--json` (as do the other listing commands), and `approve` and `decline` take
  `--yes`, for an interface that has already shown and selected the actions.

`ombro-fake-mcp` is a fake MCP server, with one tool of each class, that
writes what it would have done to a file. The tests use it, and so can a dry
run (see `examples/`).

## Guardrails

Limits are enforced by code, never by asking the model. Every model call and
tool call passes through one gate that checks all limits before the call and
records the usage after it.

One word in `limits.md` sets how far jobs, chats and interviews may go, for the whole of
Ombro (`ombro limits set <level>`, or `/limits` in the terminal interface):

| `allowance` | job tokens | rounds | job time | each interview | each chat |
|---|---|---|---|---|---|
| `conservative` | 300,000 | 4 | 15m | 50,000 | 100,000 |
| `moderate` (default) | 800,000 | 8 | 30m | 100,000 | 200,000 |
| `generous` | 2,000,000 | 16 | 1h | 250,000 | 1,000,000 |
| `unlimited` | no cap | 50 | 4h | no cap | no cap |

Any of these can be set by hand in `limits.md` or a spec's `limits:` block,
which wins over the level. Even `unlimited` keeps every other cap:

| Limit | Scope |
|---|---|
| `max_tokens` | job; total tokens |
| `max_rounds` | job; Lead delegation rounds |
| `job_timeout` | job; wall clock |
| `max_steps` | each brief; model calls a role may make |
| `tool_max_calls` | each tool, per job (`max_calls` in `tools.md`) |
| `max_output_tokens` | each model call |
| `outward` | each outward call: allowed, held for approval or refused (see above) |
| `loop_threshold` | the same tool with the same arguments repeated |
| `code_timeout`, `code_max_memory`, `code_output_chars` | each script `code.run` runs |
| `interview_max_tokens`, `interview_max_turns` | each interview |
| `chat_max_tokens`, `chat_tool_max_calls`, `chat_calls_per_message` | each chat |

Limits and usage are in tokens; Ombro reports no costs. Settings from earlier
versions (`budget_usd`, prices in `models.md`, `cost_per_call` in `tools.md`,
`display_currency` in preferences) are accepted and ignored. A job's usage is
kept under `usage:` in its `job.md`; older jobs that call it `spend:` are still
read.

Tokens, rounds, time and a stop request end the whole job. Narrower limits
end only what they bound, and the job goes on:

- A role's `max_steps` or loop detection ends that brief. The Lead is told and
  can brief again more narrowly or finish. The report records which limit
  ended which brief.
- A tool that has used its `max_calls` refuses further calls for the rest of
  the job. The role is told and carries on without it.
- The Lead's own step limit, and the Lead repeating the same call, still stop
  the job.

A share of the token limit (`report_reserve`) is held back so the final
report can always be written. The Lead's rewrite of notes.md also comes out
of the reserve, and is skipped if it would leave too little for the report. Limits are fixed when a job starts and recorded
in its `job.md`. Interviews run under their own gate and cap, so an interview
never uses a job's allowance.

If a job's process dies, the next `ombro` command that notices writes a report
from the journal and marks the job `failed`.

## Files

Everything variable lives in the home directory, as Markdown with YAML front
matter, except the journal, which is JSON Lines:

```
~/.config/ombro/
  limits.md  models.md  tools.md  folders.md
  secrets/keys.env  secrets/oauth/  secrets/<key file>   owner-only
  preferences/config.md  preferences/instructions.md
  skills/<name>/SKILL.md, references/  the skills library; skills/Archive/ keeps old versions
  chats/<time>.md                      chat transcripts (never loaded or searched)
  scheduler/tick.md, tick.log, scheduler.log   the last tick and the scheduler's logs
  lead/config.md, soul.md              the one Lead, above every crew
  specs/<name>.md                      saved specs; each names its crews
  schedules/<name>.md
  interviews/<time>-<kind>.md
  removed/schedules/<name>.md          schedules removed with `ombro schedule remove`
  jobs/<job_id>/job.md, spec.md, interview.md, journal.jsonl, report.md, workspace/, scratch/, outbox/,
                code/ (scripts and their output), backup/ (clones of writable shared folders)
  crews/<crew>/
    crew.md
    roles/<role>/config.md, soul.md
    memory/index.md, notes.md
    removed/<role>/                    roles removed with `ombro role remove`
```

Jobs were once called runs. A home from then keeps its jobs: on start,
Ombro moves its `runs/` folder to `jobs/`, and each `run.md` to `job.md`.

A home made by an earlier Ombro, with a Lead inside each crew, is refused
with a message: move it aside (keeping `secrets/` if you want your keys) and
run `ombro init` for a new one.

A role is a folder with `config.md` (name, kind, description, model, tools,
skills, max_steps) and `soul.md` (its prompt). `kind` is `specialist` or
`helper` in a crew, and `lead` only for the one Lead in `lead/`; helpers
default to the `cheap` model and fewer steps.

## Testing without a model

`models.md` accepts `api: fake` with a `script:` JSON file of canned replies,
and `tools.md` accepts `provider: fake` for search and fetch, and the fake
MCP server for outward tools. The test suite uses the same fakes, scripted answers for interviews, and mock HTTP
transports for the real providers, so every guardrail, interview and tool is
tested without a network connection or a paid call. Schedules are tested with a fake
clock and a fake scheduler installer; no test touches launchctl or crontab.
Code is tested in the real sandbox of the system the tests run on, with
invented files in temporary folders; the network test only tries to reach a
listener on the machine's own loopback address. The other system's sandbox
tests are skipped.
