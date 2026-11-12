# Ombro

A small, local harness for briefing a crew of AI roles and getting bounded,
reported work back.

Ombro assumes models will try to do too much, so it puts deterministic limits
around them. You write a spec, start a run, walk away, and read a report
later. Every run ends in a report, whether it finished, hit a limit or failed.

**Status: early.** This is the headless core, native tools, interviews,
memory and chat: the home directory, roles and crews, a model connection,
the set-up, role and task interviews, the Lead loop, the guardrail gate, the
result contract, the journal, reports, web search and fetch, workspace files,
secrets, the run index, standing notes, memory search, `ombro chat`,
schedules, and outside services through MCP with permissions and an outbox.

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

`init` asks for an OpenAI-compatible endpoint (DeepSeek by default), a model
name and an API key, then makes one test call. The key is typed into a hidden
prompt and stored in `.env` with owner-only permissions. It is never sent to a
model or written to a journal or report.

Once a model answers, the Lead holds the set-up interview: who you are, how
you work and how you want reports. It proposes `preferences/instructions.md`
and its own `soul.md`, and writes them only if you accept. Then it offers to
add the first roles.

```bash
ombro role add [name]                  # the Lead interviews you and proposes the role's files
ombro brief "find three bakeries"      # the Lead interviews you and saves a spec in specs/
ombro specs                            # saved specs, by name
ombro run --spec find-bakeries         # run a saved spec (by name or by path)
ombro run                              # brief a task, then run it as soon as you accept the spec
ombro run --spec my-spec.md --detach   # keeps going after the terminal closes
ombro runs                             # list runs
ombro status [run_id]                  # limits and spend
ombro journal [run_id]                 # the event log, readable
ombro report [run_id]                  # the final report
ombro chat                             # talk with the Lead about past work; /brief, /remember inside
ombro stop <run_id>                    # stop a run; it writes its report first
ombro open [run_id]                    # open a run's workspace folder
ombro limits                           # what runs and interviews may use
ombro limits set generous              # change the allowance level
ombro keys                             # list keys (masked) and what uses them
ombro keys set FIRECRAWL_API_KEY       # set one; typed into a hidden prompt
ombro schedule                         # schedules, their next and last runs, and the scheduler entry
ombro schedule add <name> --spec <spec> --cron "30 7 * * 1-5"
ombro schedule show | enable | disable | remove <name>
ombro schedule install | uninstall     # the one system scheduler entry; asks first
ombro tick [--dry-run]                 # start what is due (the scheduler calls this each minute)
ombro outbox                           # outward actions waiting for you, across runs
ombro outbox show ab12-3               # one in full: tool, arguments, role, reason
ombro outbox approve ab12-1 ab12-3     # run the named ones (each at most once); asks first
ombro outbox decline ab12-2            # drop it for good
ombro outbox edit ab12-3               # change its arguments; it waits for approval again
ombro tools [server]                   # tools and MCP servers; a server's tools and their permission
ombro keys remove FIRECRAWL_API_KEY
ombro role list | edit <name> | remove <name>
ombro crew list | add <name> | use <name>
```

Set `OMBRO_HOME` to use a home directory other than `~/.config/ombro`.
Example roles and specs are in [`examples/`](examples); a spec can also be
any Markdown file you write yourself.

## Interviews

The Lead interviews you in three places: set-up (`ombro init`), adding a role
(`ombro role add`) and briefing a task (`ombro brief`, or `ombro run` with no
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
  skills from `skills/`; anything else is refused. The harness checks the role
  the same way a run loads it, and warns when a role has no workspace tools
  (so it can only work from its brief).
- **Task briefs.** The Lead must find out what done looks like, who uses the
  result and how, the binding constraint, what would make it a waste, and the
  non-goals. A spec with any of these empty is refused. If answers are
  missing or contradict each other, the Lead lists the gaps and you can answer
  them; if you end the brief instead (`/end`), `ombro run` records an
  `insufficient_brief` run whose report names the gaps, and no role is briefed.
- **Standards are not asked about.** Accuracy, honesty and citing sources
  are Ombro's fixed rules for every Lead, role and interview, so the Lead
  never asks whether you want them.
- **Keys** are never asked for. Anything you type is scrubbed of the values in
  `.env` and of common key shapes before it reaches the model or the transcript.
- Each interview has its own token cap (see Limits) and is saved as a
  transcript in `crews/<crew>/interviews/`. A run started from an interview
  keeps a copy as `interview.md`, and its report shows the interview's tokens
  separately from the run's.

## How a run works

1. Ombro loads the crew's roles, the limits, the spec and the crew's memory
   (see Memory).
2. The Lead decides whom to brief and with what (`brief`), one role at a time.
3. The role works within its own step limit and returns a structured result.
4. The harness validates the result and records it in the journal. An invalid
   result is retried once with a stricter instruction, and then the brief is
   marked failed and the Lead is told.
5. The Lead briefs again, searches past reports (`memory.search`), or calls
   `finish`.
6. On finish, or when any limit is reached, delegation stops. The Lead
   rewrites its part of the crew's notes in one call, then writes the report
   in a separate call with no tools. If even that is impossible, the harness
   writes the report from the journal.
7. The harness adds one line for the run to the crew's `memory/index.md`.

## Memory

Each crew has its own memory, and a run never sees another crew's. What a
run costs stays the same however many runs came before it.

- **`memory/index.md`**: one line per run, written by the harness: date, run
  ID, outcome, one-line brief and the path to its report. Every run gets
  one, including stopped, failed and insufficient briefs.
- **`memory/notes.md`**: standing lessons in two parts. **From you** holds
  your own dated entries (added by hand or with `/remember` in chat); no
  model ever changes or drops them. **From runs** is the Lead's: at the end
  of each run that briefed a role, it rewrites this part from what the roles
  returned, or keeps it as it is. The whole file has a cap of 10,000
  characters. A rewrite over the cap is refused and the Lead is told how much
  room it has; a second refusal leaves the notes as they were. The report's
  "Changes to notes.md" lists what was added and removed, worked out by the
  harness. The old file is kept as `notes.previous.md`.
- **At the start of a run** the Lead is given notes.md and the 20 most recent
  index entries, each at most 200 characters. Nothing else.
- **`memory.search`** (the Lead only) finds passages in the crew's past
  reports by keyword, up to 4,000 characters in all, and is capped at 8 calls
  a run. It does not use a round.

The figures are in `limits.md`, in characters, so they mean the same on
every model: `memory_notes_max_chars`, `memory_index_entries`,
`memory_index_entry_chars`, `memory_search_max_chars`.

## Chat

`ombro chat` is a conversation with the Lead, with two modes; the current one
is always shown in the prompt (`[Chat] You:` or `[Brief] You:`).

- **Chat** is read-only and sees every crew. The Lead can search and read
  reports, specs, notes, indexes and workspace files, and use web search and
  fetch. It cannot brief roles, start runs, approve the outbox, write files or
  use outward tools; those calls are refused. Chat transcripts, journals,
  keys and settings cannot be read.
- **Brief** is the task interview. When the Lead hears a task it offers to
  brief it, and never switches on its own. Accept, or type `/brief [@crew]
  [text]` at any point: the interview starts from what you said in the chat
  (your own messages, most recent first, up to 4,000 characters) and the
  Lead's one-line summary if you accepted its offer. It saves a spec, which
  you run with `ombro run --spec <name>`. `/chat` goes back with nothing written.
- **`/remember [@crew] <text>`** adds a dated line to a crew's notes.md
  ("From you"); the default crew unless you name one, or `ombro chat --crew
  <name>` sets it for the session. The Lead can offer to remember something
  and propose a wording. Nothing is written until you confirm the wording. If
  it would pass the cap, you are shown the sizes and your entries and asked
  what to trim.
- **Bounded.** A chat has its own token cap (`chat_max_tokens`, set by the
  allowance), per-tool call caps (`chat_tool_max_calls`) and a cap on model
  calls for one message. Spend is shown after each reply. Once the
  conversation passes `chat_history_chars` (30,000), the oldest turns are no
  longer sent to the model.
- **Saved, not remembered.** Each chat is saved in `chats/`. Transcripts are
  never loaded into a later chat or run and never searched. Only what you
  chose to remember carries forward.

In chat and interviews, a line that starts with `"""` begins a block of
several lines, ended by a line that ends with `"""`, so a pasted paragraph
is one answer.

## Schedules

A schedule is a file in `crews/<crew>/schedules/<name>.md`:

```yaml
---
name: morning-brief
cron: "30 7 * * 1-5"          # local time: minute hour day month weekday
spec: specs/morning-brief.md  # a spec saved in this crew; scheduled runs never interview
enabled: true
limits: {allowance: conservative, run_timeout: 15m}   # optional; tokens, never dollars
grace: 10m                     # optional; default schedule_grace in limits.md
---
```

The files are the source of truth. `ombro schedule install` adds one system
scheduler entry (a launch agent on macOS, a crontab line elsewhere) that runs
`ombro tick` every minute; nothing is installed without your confirmation,
and `ombro schedule add` offers it when it is missing. Editing a schedule
needs no reinstalling: the next tick reads it, and the change takes effect
from the schedule's next slot.

- **Starting.** Tick starts each due slot once, detached, exactly as
  `ombro run --detach` does, under limits fixed at the start and recorded in
  run.md. run.md and the report name the schedule and the slot. The limits
  are limits.md, then the spec's `limits:`, then the schedule's. A scheduled
  run must have a token cap, so `unlimited` is refused.
- **Late and missed.** A slot is started if a tick sees it within its grace
  period. A slot missed because the machine was off, asleep or logged out is
  never started late: it is noted in the crew's `index.md`, one entry per gap:

  ```
  - 2026-11-10 · schedule:morning-brief@2026-11-10T07:30..2026-11-12T07:30 · missed · 3 slots not run: the machine was off or asleep (no tick from 09 Nov 23:00 to 12 Nov 09:14) · crews/default/schedules/morning-brief.md
  ```
- **Overlap.** If a schedule's previous run is still going, or
  `schedule_max_running` scheduled runs are going, or this tick has already
  started `schedule_max_starts`, the slot waits while it is within grace, then
  is skipped and noted with the reason.
- **Never twice.** Ticks take a lock, and a slot is looked up by its
  wall-clock time in the runs and the index before it starts. When clocks go
  back, the repeated hour's slots run once; when they go forward, a slot in
  the skipped hour runs at the first minute after the change.
- **Never silent.** A due slot that cannot start (a missing spec, invalid
  limits, no model) becomes a `failed` run with a report and an index entry.
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
| `workspace.read`, `workspace.write`, `workspace.list` | Text files in the run's `workspace/` and `scratch/` folders. |
| `memory.search` | The Lead only: passages from this crew's past reports. |

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
| `fake` | search, fetch | Invented results. Records every request in the run's `fake-calls.jsonl`. |

```yaml
---
tools:
  web.search: {provider: firecrawl, max_calls: 20, cost_per_call: 0.002}
  web.fetch: {provider: firecrawl, max_calls: 30}
  workspace.write: {max_calls: 40}
providers:
  firecrawl: {key: FIRECRAWL_API_KEY}
---
```

`max_calls` caps a tool's calls per run. `cost_per_call` is optional: dollars
per call for services that bill in their own units, such as Firecrawl credits.
It is added to the estimated cost in the report, and is never a limit. Like
limits, tool settings are fixed when a run starts and recorded in `run.md`.

Keys live in `.env` (owner-only) and are read at the moment of use, so a key
set with `ombro keys set` is picked up on the next call, even in a run already
going. Keys never reach a model, a journal or a report.

## Outside services: MCP, permissions and the outbox

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
  remote:
    url: https://mcp.example.invalid/mcp  # a remote server
    headers: {Authorization: "Bearer ${REMOTE_TOKEN}"}
tools:
  mcp.sheets.update_cells: {max_calls: 10}
  mcp.sheets.share_spreadsheet: {permission: refuse}
---
```

`${NAME}` is read from `.env` when the server starts and is never logged.
A server's tools are named `mcp.<server>.<tool>`; `ombro tools <server>`
lists them. A role can use only the ones its `tools:` list names, so a large
server costs only what a role uses. Remote servers that need an OAuth
sign-in are not supported yet. Ombro speaks MCP itself (tools only), rather
than through the SDK.

**Pin what a server runs.** A package runner such as `uvx` downloads and
runs the server's code, so name an exact version (`==0.6.3`, never
`@latest`). It also resolves the server's own dependencies afresh, so pin
those where the server needs it: `mcp-google-sheets` 0.6.3 asks for any
`mcp>=1.8`, gets 2.x and fails to start, hence `--with "mcp==1.30.0"`. If a
server can limit the tools it offers (here `--include-tools`), offer only
those your roles use. When a server fails to start, Ombro shows the last
lines it printed; in a run, all of it is in the run's `mcp-<server>.log`.

**Classes.** Each tool is `read` (the server marks it read-only), `change`
(it changes something but, by the server's marking, destroys nothing) or
`send` (anything else, including unmarked tools). The markings are the
server's own claims: `class:` in `tools.md` overrides them, and `ombro tools
<server>` shows what each tool is.

**Permissions.** Every outward call is allowed (it runs, and is recorded),
staged (it waits in the outbox) or refused:

| `outward` | read | change | send |
|---|---|---|---|
| `manual` | allow | stage | stage |
| `balanced` (default) | allow | allow | stage |
| `auto` | allow | allow | allow |

Set your default with `outward:` in `preferences/config.md`. A spec
overrides it, with a preset and, if wanted, particular tools:

```yaml
outward: {preset: balanced, allow: [mcp.sheets.add_rows], stage: [mcp.social.post]}
```

The task interview asks what may happen without you whenever the crew has
outward tools, and writes the answer into the spec. `permission: refuse` in
`tools.md` beats everything. Permissions are fixed when a run starts and
recorded in `run.md`; scheduled runs follow them like any other.

**The outbox.** A staged call does not happen. The harness writes it to the
run's `outbox/` as one file (tool, arguments, the role and its reason, the
time, a status), tells the role it was staged, and never stages an identical
call twice in a run. Staging counts towards the tool's `max_calls`. The
report's Outbox section lists everything done and staged, from the harness's
own record.

You decide on named actions only; there is no "approve all":

- `ombro outbox` lists what is waiting, across runs, by reference (`ab12-3`).
- `ombro outbox approve ab12-1 ab12-3` shows each in full, asks once, and
  runs them. `decline` drops them for good. `edit` changes an action's
  arguments (in your editor, or `--arguments '<json>'`); they are checked
  against the tool's rules and the action waits for approval again. You may
  also edit `arguments` in the file by hand; it is shown as edited and
  checked before it runs.
- An action runs at most once. Its status is written before and after the
  call, under a lock. If Ombro stops mid-call, or the server does not
  answer, the action becomes `uncertain`: it may or may not have happened,
  and it is never retried. Settle it with `ombro outbox resolve <ref> sent`
  or `not-sent`.
- Outcomes are recorded in the action's file, the run's journal, run.md and
  an "Outbox decisions" section added to the report.
- Every outbox command takes `--json`, and `approve` and `decline` take
  `--yes`, for an interface that has already shown and selected the actions.

`ombro-fake-mcp` is a fake MCP server, with one tool of each class, that
writes what it would have done to a file. The tests use it, and so can a dry
run (see `examples/`).

## Guardrails

Limits are enforced by code, never by asking the model. Every model call and
tool call passes through one gate that checks all limits before the call and
records the cost after it.

One word in `limits.md` sets how far runs and interviews may go:

| `allowance` | run tokens | rounds | run time | each interview | each chat |
|---|---|---|---|---|---|
| `conservative` | 150,000 | 4 | 15m | 40,000 | 60,000 |
| `moderate` (default) | 400,000 | 8 | 30m | 100,000 | 150,000 |
| `generous` | 1,500,000 | 16 | 1h | 250,000 | 400,000 |
| `unlimited` | no cap | 50 | 4h | no cap | no cap |

Any of these can be set by hand in `limits.md` or a spec's `limits:` block,
which wins over the level. Even `unlimited` keeps every other cap:

| Limit | Scope |
|---|---|
| `max_tokens` | run; total tokens |
| `max_rounds` | run; Lead delegation rounds |
| `run_timeout` | run; wall clock |
| `max_steps` | each brief; model calls a role may make |
| `tool_max_calls` | each tool, per run (`max_calls` in `tools.md`) |
| `max_output_tokens` | each model call |
| `outward` | each outward call: allowed, staged or refused (see above) |
| `loop_threshold` | the same tool with the same arguments repeated |
| `interview_max_tokens`, `interview_max_turns` | each interview |
| `chat_max_tokens`, `chat_tool_max_calls`, `chat_calls_per_message` | each chat |

Dollars are not a limit. If `models.md` has prices, reports and `ombro status`
show an estimated cost.

Tokens, rounds, time and a stop request end the whole run. Narrower limits
end only what they bound, and the run goes on:

- A role's `max_steps` or loop detection ends that brief. The Lead is told and
  can brief again more narrowly or finish. The report records which limit
  ended which brief.
- A tool that has used its `max_calls` refuses further calls for the rest of
  the run. The role is told and carries on without it.
- The Lead's own step limit, and the Lead repeating the same call, still stop
  the run.

A share of the token limit (`report_reserve`) is held back so the final
report can always be written. The Lead's rewrite of notes.md also comes out
of the reserve, and is skipped if it would leave too little for the report. Limits are fixed when a run starts and recorded
in its `run.md`. Interviews run under their own gate and cap, so an interview
never uses a run's allowance.

If a run's process dies, the next `ombro` command that notices writes a report
from the journal and marks the run `failed`.

## Files

Everything variable lives in the home directory, as Markdown with YAML front
matter, except the journal, which is JSON Lines:

```
~/.config/ombro/
  .env  limits.md  models.md  tools.md
  preferences/config.md  preferences/instructions.md
  chats/<time>.md                      chat transcripts (never loaded or searched)
  scheduler/tick.md, tick.log, scheduler.log   the last tick and the scheduler's logs
  crews/default/
    crew.md
    roles/<role>/config.md, soul.md
    memory/index.md, notes.md
    specs/<name>.md
    schedules/<name>.md
    interviews/<time>-<kind>.md
    removed/<role>/                  roles removed with `ombro role remove`
    removed/schedules/<name>.md      schedules removed with `ombro schedule remove`
    runs/<run_id>/run.md, spec.md, interview.md, journal.jsonl, report.md, workspace/, scratch/, outbox/
```

A role is a folder with `config.md` (name, kind, description, model, tools,
skills, max_steps) and `soul.md` (its prompt). `kind` is `lead`, `specialist`
or `helper`; helpers default to the `cheap` model and fewer steps.

## Testing without a model

`models.md` accepts `api: fake` with a `script:` JSON file of canned replies,
and `tools.md` accepts `provider: fake` for search and fetch, and the fake
MCP server for outward tools. The test suite uses the same fakes, scripted answers for interviews, and mock HTTP
transports for the real providers, so every guardrail, interview and tool is
tested without a network connection or spend. Schedules are tested with a fake
clock and a fake scheduler installer; no test touches launchctl or crontab.
