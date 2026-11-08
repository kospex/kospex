# Retired panopticas tags are accepted and shown as retired

Fixes [#317](https://github.com/kospex/kospex/issues/317).

## Overview

`KospexData.where_tag()` validates a tag against `panopticas.get_tags()`, which lists
only the tags panopticas currently assigns. A tag it has since renamed was rejected the
same way as a typo, although rows recorded before the rename still carry it. On the
development estate that is `Claude Code` (13 files in 6 repositories), renamed to
`Claude`. `/tag/Claude Code` returned 404 "Claude Code is an unknown Tag."

panopticas 0.0.22 added `get_deprecated_tags()`, mapping each retired spelling to its
replacement, or None for a tag dropped outright. The 0.0.23 floor from #349 makes it
available, so nothing upstream is outstanding.

## Behaviour

- `where_tag()` accepts a retired tag, case-insensitively, as it does a current one.
  Anything else is still rejected.
- `/tag/{tag}` for a retired tag lists its repositories as usual, under a note:
  "panopticas no longer assigns this tag; it now uses Claude. This page shows only
  files still tagged Claude Code, recorded before the change. For files tagged
  Claude, see Claude." The last `Claude` is a link. A dropped tag gets the first two
  sentences only. The listing otherwise looks like any tag page, so the note says
  which files it covers.
- The "See Files" drill-down (`/tag/{tag}?repo_id=...`) carries the same note. It
  previously said only "Repository Files", with no filter or repository, and that
  applied to `/tech/{tech}?repo_id=...` too, since both use `repo_files.html`. Both
  now carry the repository pages' breadcrumb,
  `github.com / facebook / react / Tag: Claude Code`, built by
  `KospexWeb.scope_context()` like the others, so a nested GitLab group keeps its
  subgroup. Under it, a heading names the filter ("Tag: Claude Code"; the
  repository is already in the breadcrumb), and a link goes back to the listing
  across all repositories.
- `/tags/` ends with a "Retired tags" section listing each one beside its
  replacement, or "(dropped)". Today that is `Claude Code → Claude` and
  `Gemini CLI → Gemini`.

## Decisions

- **A change of case is not a retirement.** `get_deprecated_tags()` also lists
  recasings (`eslint` → `ESLint`, `Github` → `GitHub`, `maven` → `Maven`,
  `PyPi` → `PyPI`). `where_tag()` already compares case-insensitively and SQLite's
  `LIKE` matches either spelling, so to kospex those are the same tag. Listing them
  would show a tag beside itself. `retired_tags()` filters them out.
- **The query is not widened to the replacement.** `/tag/Claude Code` matches only
  rows stored as `Claude Code`. The note links `Claude` for the current files, rather
  than a page that silently mixes two tags.
- **No message distinguishing "retired" from "unknown" in the error.** The issue
  suggested one, but a retired tag no longer reaches the error path, so the note on
  the page carries that information instead.

## Files changed

- `src/kospex_query.py` - `retired_tags()` and `retired_tag_replacement()`, and
  `where_tag()` accepts retired tags.
- `src/kweb2.py` - `/tags/` passes the retired list; `/tag/{tag}` passes whether the
  tag is retired and its replacement; `/tag/` and `/tech/` pass the breadcrumb context
  to the drill-down.
- `src/templates/_retired_tag_note.html` (new) - the note, shared by both `/tag/` pages.
- `src/templates/tags.html`, `src/templates/repos-tech.html` - the retired section and
  the note.
- `src/templates/repo_files.html` - the breadcrumb; names its filter and repository,
  links back to the listing, and includes the note.
- `tests/test_where_tag_binding.py` - the test that pinned the rejection is replaced
  by tests that a renamed or dropped tag is accepted, a typo still is not, stored rows
  are found, and recasings are left out.
- `tests/test_tags_index.py`, `tests/test_repos_by_tag.py` - the retired section and
  the note.
- `CHANGELOG.md`.

## Testing

Full suite: 2,546 passed, 23 skipped, 1 xfailed, on panopticas 0.0.23. The live web
tests ran against the kweb on `:8000`, which serves `main`, not this branch. A kweb
started from the branch on a separate port, against the development database,
served `/tags/` with the retired section and `/tag/Claude Code` with 6 repositories
and the note, where `main` returns 404.
