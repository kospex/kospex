# Repositories by tag: a `/tag/` route

Closes the first, smallest piece of #293: **given a tag, which repositories have
it?**

## Overview

`file_metadata.tech_type` holds the tags panopticas assigns to a file — `AI`,
`license`, `workflow`, `pipeline`, `llms.txt`, `dependencies` and ~60 others.
Until now none of them were reachable from the web UI: `tech_type` appeared in
no template and no route filtered on it.

`/tech/{tech}` looks like the page for this, and it is the right *shape*, but it
filters `WHERE Language = ?`. A tag is not a Language, so `/tech/AI` returned
**HTTP 200 with zero rows** — which reads as "no repositories have AI" when in
fact 51 of them do.

`Language` can never answer it. panopticas tags from the *filename*, so `scc`
records `CLAUDE.md` as Markdown and `.mcp.json` as JSON while panopticas tags
both `AI`. The two columns carry different information.

## Usage

```
/tag/AI                              repositories with the AI tag, most files first
/tag/AI?repo_id=<repo_id>            that tag's files within one repository
/tag/C%23                            '#' must be percent-encoded or it never arrives
```

Unknown tags are a **404**, not an empty table — see below.

**The route is deliberately not linked from anywhere**, and is not in
`sitemap.xml`. It is addressable for 0.2.0 and gets an entry point once there is
a page to put it on.

## Why a separate route, not a fallback inside `/tech/`

**9 names are both a Language and a tag**, and they count very different things:

| name | as Language | as tag |
|---|---|---|
| `Go` | 24,236 files | 345 files |
| `JavaScript` | 63,992 files | 249 files |
| `Python` | 17,316 files | 272 files |
| `C#` | 26 files | 9 files |

Also `Java`, `SQL`, `ASP.NET`, `SQLFluff`, `XML`. A route that guessed between
them — in either fallback order — would not be a page anyone could trust. Two
routes keep both meanings addressable, and `/tech/` is unchanged.

## An unknown tag is a 404

`where_tag()` validates the tag against `panopticas.get_tags()` and raises
`ValueError`. The route turns that into a 404 rather than letting it reach the
500 handler, and rather than rendering an empty table: a 200 with no rows is
exactly the failure mode this route exists to remove. So `/tag/Markdown` — a
Language, never a tag — is a bad URL and says so.

**Known limitation.** A tag panopticas has *retired* 404s alongside a typo,
because `get_tags()` reports only the current vocabulary. On a local database
that is `Claude Code` (7 files), renamed to brand-level `Claude`. Tracked as
#317; nothing here makes it worse, but `/tag/Claude%20Code` is a 404 whose files
do exist.

## Implementation notes

Two methods on `KospexQuery`, both built with `KospexData` so the tag is bound
and escaped by `where_tag()`:

- `repos_with_tag(tag)` — `_repo_id` plus git details and `count(*)`, grouped by
  repository, ordered by count. The tag counterpart of `repos_with_tech()`.
- `repo_files_by_tag(tag, repo_id=None)` — the reverse direction, returning the
  same columns as `repo_files()` so the existing file listing renders either.

**One table, no join, no new index, no migration.** Measured on a database of
~500k `file_metadata` rows, the lookup is **78–84 ms** and flat across
selectivity (1 repository or 359), because `tech_type LIKE '%|tag|%'` has a
leading wildcard and scans either way.

The delimiters are load-bearing: `tech_type` is pipe-delimited *and*
pipe-terminated (`|Git|GitHub|dependencies|`), so `%|Git|%` matches `Git` without
also matching `GitHub`. On live data those two tags resolve to 353 and 316
repositories respectively — a substring match would have conflated them.

### Files changed

- `src/kospex_query.py` — `repos_with_tag()`, `repo_files_by_tag()`
- `src/kweb2.py` — the `/tag/{tag}` route; `/tech/` now passes the same two
  template variables
- `src/templates/repos-tech.html` — the heading says `Tag:` or `Technology:`
  from `tech_kind`; the "See Files" link comes from `tech_url` instead of being
  hardcoded to `/tech/`; a line under the heading gives the repository count;
  and the table gained Server / Org / Repo / # Files columns. `tech_kind` and
  `tech_url` default to the `/tech/` behaviour, so the template is unchanged for
  any caller that does not set them.
- `tests/test_repos_by_tag.py` — 21 tests: the counts, the `latest = 1` filter,
  the `Git`/`GitHub` delimiter case, the 404 mapping, and the columns the
  templates consume
- `tests/test_web_endpoints.py` — `/tag/AI` added to the parametrised list

`repo_files.html` needed no change: it already renders filename and Language
generically, and showing the Language of a tagged file is informative here —
an `AI`-tagged file listing reads `CLAUDE.md  Markdown`, which is the
distinction the issue is about.

## The listing table, and a DataTables bug it exposed

The page reports how many repositories carry the tag — `Tag: license`, then
`359 repositories with this tag` (singular at 1). `/tech/` gets the same line in
its own wording, since the template is shared.

The table now has the same shape as `/orgs/`: Server, Org, Repo, and a
right-aligned **# Files** count. That count was already returned by the query
and simply never rendered — the only Files cell was a "See Files" link, so every
row read identically and there was nothing to sort on.

**`repos-tech.html` carried `order: [[4, "asc"]]` against a two-column table.**
DataTables 1.13.11's sort-flatten reads `aoColumns[i].sType` with no bounds
check, so an index past the last column dereferences `undefined` and throws
during init — losing the entire DataTable: no search, no paging, no sorting,
just every row dumped into the page. It is now `[[3, "desc"]]`, which is the
`# Files` column and matches the SQL ordering.

`tests/test_repos_by_tag.py` guards this by rendering the page and asserting the
configured order index is within the table's column count, rather than
hard-coding 3.

## Out of scope

- **A tag listing page** ("which tags exist, with counts"). Not a single-table
  lookup: `tech_type` holds several tags per row, and the obvious
  `GROUP BY tech_type` shortcut double-counts repositories — on live data 25 of
  63 tags come out wrong, one reporting more repositories than exist. A tag can
  also appear twice in one stored value, so splitting needs a `set`. Its own
  issue.
- **Developers behind a tag.** That needs `commit_files` joins, and the per-file
  loop shape it would reach for is #319.
