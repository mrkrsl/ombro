# Ombro

A small, local harness for briefing a crew of AI roles and getting bounded,
reported work back.

Ombro assumes models will try to do too much, so it puts deterministic limits
around them. You write a spec, start a run, walk away, and read a report
later. Every run ends in a report, whether it finished, hit a limit or failed.

**Status: early.** This is the headless core, native tools and interviews:
the home directory, roles and crews, a model connection, the set-up, role
and task interviews, the Lead loop, the guardrail gate, the result contract,
the journal, reports, web search and fetch, workspace files and secrets.
Memory, chat, schedules, the outbox and MCP are not built. Outward tools
(anything that sends or changes something outside the run) are refused.

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
ombro stop <run_id>                    # stop a run; it writes its report first
ombro open [run_id]                    # open a run's workspace folder
ombro limits                           # what runs and interviews may use
ombro limits set generous              # change the allowance level
ombro keys                             # list keys (masked) and what uses them
ombro keys set FIRECRAWL_API_KEY       # set one; typed into a hidden prompt
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

1. Ombro loads the crew's roles, the limits and the spec.
2. The Lead decides whom to brief and with what (`brief`), one role at a time.
3. The role works within its own step limit and returns a structured result.
4. The harness validates the result and records it in the journal. An invalid
   result is retried once with a stricter instruction, and then the brief is
   marked failed and the Lead is told.
5. The Lead briefs again or calls `finish`.
6. On finish, or when any limit is reached, the Lead writes the report in a
   separate call with no tools. If even that is impossible, the harness writes
   the report from the journal.

## Tools

A role gets only the tools its `config.md` lists:

| Tool | Does |
|---|---|
| `web.search` | Web search through a provider. Returns titles, URLs and snippets. |
| `web.fetch` | Reads one page as text or Markdown through a provider. |
| `workspace.read`, `workspace.write`, `workspace.list` | Text files in the run's `workspace/` and `scratch/` folders. |

Workspace paths start with `workspace/` (deliverables) or `scratch/` (working
notes). Absolute paths, `..`, and symlinks that lead outside those folders
are refused, and nothing is written through a symlink. A deliverable a role
names by path is listed in the report with its full path.

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

## Guardrails

Limits are enforced by code, never by asking the model. Every model call and
tool call passes through one gate that checks all limits before the call and
records the cost after it.

One word in `limits.md` sets how far runs and interviews may go:

| `allowance` | run tokens | rounds | run time | each interview |
|---|---|---|---|---|
| `conservative` | 150,000 | 4 | 15m | 40,000 |
| `moderate` (default) | 400,000 | 8 | 30m | 100,000 |
| `generous` | 1,500,000 | 16 | 1h | 250,000 |
| `unlimited` | no cap | 50 | 4h | no cap |

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
| `loop_threshold` | the same tool with the same arguments repeated |
| `interview_max_tokens`, `interview_max_turns` | each interview |

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
report can always be written. Limits are fixed when a run starts and recorded
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
  crews/default/
    crew.md
    roles/<role>/config.md, soul.md
    specs/<name>.md
    interviews/<time>-<kind>.md
    removed/<role>/                  roles removed with `ombro role remove`
    runs/<run_id>/run.md, spec.md, interview.md, journal.jsonl, report.md, workspace/, scratch/, outbox/
```

A role is a folder with `config.md` (name, kind, description, model, tools,
skills, max_steps) and `soul.md` (its prompt). `kind` is `lead`, `specialist`
or `helper`; helpers default to the `cheap` model and fewer steps.

## Testing without a model

`models.md` accepts `api: fake` with a `script:` JSON file of canned replies,
and `tools.md` accepts `provider: fake` for search and fetch. The test suite
uses the same fakes, scripted answers for interviews, and mock HTTP
transports for the real providers, so every guardrail, interview and tool is
tested without a network connection or spend.
