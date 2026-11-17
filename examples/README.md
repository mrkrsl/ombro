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

- `roles/organiser`: a helper that renames and tidies files in a shared
  folder with `code.run` scripts, planning first and never deleting.
- `specs/rename-photos.md`: the organiser renames the photos in a folder
  shared as `photos`, by date. The run clones the folder first (where the
  disk allows), and the report lists every file created and removed.

- `schedules/library-hours-weekly.md`: runs the research spec every Monday
  morning, unattended, under a conservative allowance. Saved disabled.

Copy the role folders into `~/.config/ombro/crews/default/roles/` (or your
`$OMBRO_HOME`). Each example spec names `crews: [default]`. Then run:

    ombro run --spec examples/specs/composting-guide.md
    ombro run --spec examples/specs/library-hours.md

The research spec uses whichever provider `tools.md` names for `web.search`
and `web.fetch`: DuckDuckGo and a direct download by default, or Firecrawl
once its key is set.

To schedule the research spec, copy `specs/library-hours.md` into the home's
`specs/` folder and `schedules/library-hours-weekly.md` into its
`schedules/` folder, then:

    ombro schedule enable library-hours-weekly
    ombro schedule install        # once; asks before installing anything

To try the outbox without any account, add Ombro's fake MCP server to
`tools.md`. It writes what it would have done to `fake-mcp-sent.jsonl` in
your Ombro home, and reaches nothing:

    servers:
      fake:
        command: ombro
        args: [fake-mcp]

Copy `roles/writer` and `roles/publisher` into the crew, then:

    ombro run --spec examples/specs/post-update.md
    ombro outbox                       # the two staged posts
    ombro outbox approve <ref>         # only the ones you name reach the fake file

To try code and shared folders, make a test folder with a few copies of
photos (never your only copies), share it, and copy `roles/organiser` into
the crew:

    ombro folders add ~/Pictures/ombro-test --name photos --access write
    ombro run --spec examples/specs/rename-photos.md
    ombro report                       # the Code and Shared folders sections
