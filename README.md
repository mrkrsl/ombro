# Ombro

A small, local harness for briefing a crew of AI roles and getting bounded,
reported work back.

Ombro assumes models will try to do too much, so it puts deterministic limits
around them. You write a spec, start a run, walk away, and read a report
later. Every run ends in a report, whether it finished, hit a limit or failed.

**Status: early.** This is the headless core plus native tools: the home
directory, roles, a model connection, the Lead loop, the guardrail gate, the
result contract, the journal, reports, web search and fetch, workspace files
and secrets. Interviews, memory, chat, schedules, the outbox and MCP are not
built. Outward tools (anything that sends or changes something outside the
run) are refused.

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
ombro init                         # creates ~/.config/ombro and connects a model
```

`init` asks for an OpenAI-compatible endpoint (DeepSeek by default), a model
name and an API key, then makes one test call. The key is typed into a hidden
prompt and stored in `.env` with owner-only permissions. It is never sent to a
model or written to a journal or report.

Add a role by copying one from [`examples/roles`](examples/roles) into
`~/.config/ombro/crews/default/roles/`, then write a spec (any Markdown file;
see [`examples/specs`](examples/specs)) and run it:

```bash
ombro run --spec my-spec.md            # attached: progress in the terminal
ombro run --spec my-spec.md --detach   # keeps going after the terminal closes
ombro runs                             # list runs
ombro status [run_id]                  # limits and spend
ombro journal [run_id]                 # the event log, readable
ombro report [run_id]                  # the final report
ombro stop <run_id>                    # stop a run; it writes its report first
ombro open [run_id]                    # open a run's workspace folder
ombro keys                             # list keys (masked) and what uses them
ombro keys set FIRECRAWL_API_KEY       # set one; typed into a hidden prompt
ombro keys remove FIRECRAWL_API_KEY
```

Set `OMBRO_HOME` to use a home directory other than `~/.config/ombro`.

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
It is added to the run's dollar total and checked against `budget_usd`. Like
limits, tool settings are fixed when a run starts and recorded in `run.md`.

Keys live in `.env` (owner-only) and are read at the moment of use, so a key
set with `ombro keys set` is picked up on the next call, even in a run already
going. Keys never reach a model, a journal or a report.

## Guardrails

Limits are enforced by code, never by asking the model. Every model call and
tool call passes through one gate that checks all limits before the call and
records the cost after it.

| Limit | Scope |
|---|---|
| `budget_usd` | run, from token counts and the prices in `models.md` |
| `max_tokens` | run; still holds if a price is wrong |
| `max_rounds` | run; Lead delegation rounds |
| `max_steps` | each brief; model calls a role may make |
| `run_timeout` | run; wall clock |
| `tool_max_calls` | each tool, per run (`max_calls` in `tools.md`) |
| `tool_cost_per_call` | each tool's dollars per call (`cost_per_call` in `tools.md`) |
| `max_output_tokens` | each model call |
| `loop_threshold` | the same tool with the same arguments repeated |

Money, tokens, rounds, time and a stop request end the whole run. Narrower
limits end only what they bound, and the run goes on:

- A role's `max_steps` or loop detection ends that brief. The Lead is told and
  can brief again more narrowly or finish. The report records which limit
  ended which brief.
- A tool that has used its `max_calls` refuses further calls for the rest of
  the run. The role is told and carries on without it.
- The Lead's own step limit, and the Lead repeating the same call, still stop
  the run.

A share of the budget and token limit (`report_reserve`) is held back so the
final report can always be paid for. Limits are fixed when a run starts and
recorded in its `run.md`. A spec may override them for its own run with a
`limits:` block in its front matter.

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
    runs/<run_id>/run.md, spec.md, journal.jsonl, report.md, workspace/, scratch/, outbox/
```

A role is a folder with `config.md` (name, kind, description, model, tools,
skills, max_steps) and `soul.md` (its prompt). `kind` is `lead`, `specialist`
or `helper`; helpers default to the `cheap` model and fewer steps.

## Testing without a model

`models.md` accepts `api: fake` with a `script:` JSON file of canned replies,
and `tools.md` accepts `provider: fake` for search and fetch. The test suite
uses the same fakes, and mock HTTP transports for the real providers, so
every guardrail and tool is tested without a network connection or spend.
