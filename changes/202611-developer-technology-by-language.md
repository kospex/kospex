# Developer view: show technology by language

**Status:** design approved and built 2026-11-26. Plan:
`202611-developer-technology-by-language-plan.md`.

## Why

The developer view (`/developers/?author_email=` and `/developer/`) lists technology as
file extensions (`commit_files._ext`). The repo view lists languages
(`file_metadata.Language`). The two pages should name technology the same way.

## Goals

- The developer view's "Technology View" table and radar chart show language names
  matching the repo view.
- A developer's history is kept, including files since deleted or moved.
- "# Commits" counts commits.

## Non-goals

- Changing `author_tech()`, so the JSON API and `kospex author-tech` keep extension output.
- Speeding up the page.
- Changing the repo view, or `developer_tech()` as used by `krunner developer-tech`.
- Schema changes, migrations or re-syncs.
- Linking developer languages to `/tech/` (that page cannot filter by author).

**Would count as failure:** the same file shown under a different name on the developer
and repo views, or history silently dropping out of the totals.

## Approaches

| Approach | Trade-off |
|---|---|
| Join history to current `file_metadata` rows | Reads the stored value, but files that no longer exist at that path get no language |
| **Chosen:** run panopticas on each historical path | Covers all history; reads the path only, not file contents |
| Reuse `developer_tech()` | Already detects by path, but joins on commit hash without the repo, so shared history can be counted twice |

Measured on one developer in a local kospex DB (4,440 file changes): the join matched
3,789 and left 651 (15%) without a language. Path detection took 0.01s, left 98 as
`Unknown`, and agreed with `file_metadata.Language` on all 3,789 matched files.

## Design

A new `KospexQuery` method returns one row per language for an author:

- Reads `commit_files` joined to `commits` on both `_repo_id` and `hash`, filtered on
  exact `author_email`, as today.
- Language is `panopticas.get_language(file_path, skip_shebang=True)`. Anything it
  cannot name is shown as `Unknown` and stays in the table and chart.
- Per language:

| Column | Meaning |
|---|---|
| Technology | language |
| # Commits | distinct (`_repo_id`, `hash`). A commit touching two languages counts once under each |
| # Files | distinct (`_repo_id`, `file_path`) over history (new column) |
| # Repos | distinct `_repo_id` |
| Last Seen, Days Active, Years Active | from `author_when`, as today |

- Dates are compared as instants, not as text, because stored values carry differing
  UTC offsets.
- Rows are ordered by commits, highest first. The radar chart takes the same top rows
  as today.
- Both handlers in `kweb2.py` that render `developer_view.html` use the new method.

## Constraints

- SQL must run on SQLite 3.26.
- A rename recorded under an old path is classified by that path.
- The two views can still name a file differently. A script named only by its shebang
  line (such as `gradlew`) is `Unknown` on the developer view, because file contents are
  not read. The repo view shows the language stored when the repository's file metadata
  was last built, which an older panopticas may have named differently until
  `krunner file-metadata -force` is run.
- The new method filters `commits` on `author_email`, which has no index leading with
  that column, so it is as slow as `author_tech()` is today. The index, and whether to
  match email case-insensitively, are left to
  [#492](https://github.com/kospex/kospex/issues/492). Whatever it settles should apply
  to the new method too, since the developer pages will no longer call `author_tech()`.

## Files

- `src/kospex_query.py` - new method
- `src/kweb2.py` - the two handlers
- `src/templates/developer_view.html` - language column and "# Files"
- `tests/` - a test of the method against a small DB: commit counting, the same hash in
  two repos, a deleted file, an unknown path, mixed UTC offsets
- `CHANGELOG.md` - entry under `## Unreleased`

## Left to the builder

Method name, and how rows are grouped (SQL or Python).

## Effort

About half a day, including tests and a check against a running `kweb`.
