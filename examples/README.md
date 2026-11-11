# Examples

Generic roles and specs for trying Ombro.

- `roles/writer`: a specialist that drafts short prose. No tools.
- `roles/editor`: a helper that tightens a draft. No tools.
- `roles/researcher`: a specialist with web search, fetch and the workspace
  tools. It writes sourced findings to a file and returns the path.
- `specs/composting-guide.md`: a small writing spec with its own tight limits.
- `specs/library-hours.md`: a small research spec for the researcher. It
  searches, reads pages and writes `workspace/libraries.md`.

- `roles/publisher`: a helper that posts finished text through an outward
  tool, `mcp.fake.post_message`, from Ombro's fake MCP server.
- `specs/post-update.md`: the writer drafts two posts and the publisher
  posts them. Under `outward: balanced` the posts are staged for you.

- `schedules/library-hours-weekly.md`: runs the research spec every Monday
  morning, unattended, under a conservative allowance. Saved disabled.

Copy the role folders into `~/.config/ombro/crews/default/roles/` (or your
`$OMBRO_HOME`), then run:

    ombro run --spec examples/specs/composting-guide.md
    ombro run --spec examples/specs/library-hours.md

The research spec uses whichever provider `tools.md` names for `web.search`
and `web.fetch`: DuckDuckGo and a direct download by default, or Firecrawl
once its key is set.

To schedule the research spec, copy `specs/library-hours.md` into the crew's
`specs/` folder and `schedules/library-hours-weekly.md` into its
`schedules/` folder, then:

    ombro schedule enable library-hours-weekly
    ombro schedule install        # once; asks before installing anything

To try the outbox without any account, add Ombro's fake MCP server to
`tools.md`. It writes what it would have done to `fake-mcp-sent.jsonl` in
your Ombro home, and reaches nothing:

    servers:
      fake:
        command: ombro-fake-mcp

Copy `roles/writer` and `roles/publisher` into the crew, then:

    ombro run --spec examples/specs/post-update.md
    ombro outbox                       # the two staged posts
    ombro outbox approve <ref>         # only the ones you name reach the fake file
