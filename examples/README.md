# Examples

Generic roles and a spec for trying Ombro.

- `roles/writer`: a specialist that drafts short prose.
- `roles/editor`: a helper that tightens a draft.
- `specs/composting-guide.md`: a small spec with its own tight limits.

Copy the role folders into `~/.config/ombro/crews/default/roles/` (or your
`$OMBRO_HOME`), then run:

    ombro run --spec examples/specs/composting-guide.md
