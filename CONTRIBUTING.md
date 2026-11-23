# Contributing to Ombro

Thank you for helping. Bug reports, ideas and pull requests are all welcome.

## Issues

Open an issue for a bug or an idea. For a bug, say what you did, what you
expected and what happened, with the version (`ombro --version`, or the app's
About box) and your platform.

Security problems go privately, not in an issue: see [SECURITY.md](SECURITY.md).

## Pull requests

1. For anything bigger than a small fix, open an issue first, so we can agree
   on the approach before you spend time on it.
2. Keep a pull request to one change, with tests where the change can be
   tested.
3. Run the checks before you push (see below).
4. The first time you open a pull request, a bot asks you to accept the
   [Ombro Contributor Agreement](CLA.md) by commenting one sentence:

   > I have read the Ombro Contributor Agreement and I agree to it

   You do this once, and it covers your later pull requests too. We can't
   merge a pull request until everyone who wrote its commits has accepted it.
   If a commit's email isn't linked to a GitHub account, link it or rewrite
   the commit with one that is, then comment `recheck`.

### Why an agreement

Ombro is under the AGPLv3, and stays so. The agreement lets Heliotrope Atelier
Ltd, which looks after Ombro, also offer your contribution under other terms,
for example in paid add-ons. You keep your copyright, and as long as Ombro is
open source, your contribution stays available under its open-source licence
(section 4).

## Checks

From the repository's root:

```bash
uv run ruff check && uv run ruff format --check
uv run pytest
```

For the desktop app, in `gui/`:

```bash
npm ci && npm test && npm run build
```

and in `gui/src-tauri`, `cargo test`. The tests make no network or paid
calls. The README's "For developers" section says where things are.

## Name and logo

"Ombro" and the Ombro logo are trademarks of Heliotrope Atelier Ltd: see the
README's Trademark section.
