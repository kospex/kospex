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
/tags/                               the tag vocabulary, three columns, alphabetical
/tag/AI                              repositories with the AI tag, most files first
/tag/AI?absent=true                  repositories with NO file carrying the tag
/tag/AI?repo_id=<repo_id>            that tag's files within one repository
/tag/C%23                            '#' must be percent-encoded or it never arrives
/tech/Python?absent=true             the same negative view for a Language
/tech/Fortran                        404 -- nothing here has ever been Fortran
```

Unknown tags are a **404**, not an empty table — see below.

**Neither route is linked from the header nav**, and neither is in
`sitemap.xml`. `/tags/` is the entry point to `/tag/{tag}`; whether `/tags/`
itself earns a nav entry is a separate decision.

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
- `repos_without_tag(tag)` — the complement, as a set difference against
  `repos_with_file_metadata()`.
- `repos_with_file_metadata()` / `count_repos_with_file_metadata()` — the
  universe, and its size for the wording. The count is its own query because
  the listing page wants the number without the rows (24 ms versus 31 ms).

**One table, no join, no new index, no migration.** Measured on a database of
~500k `file_metadata` rows, the lookup is **78–84 ms** and flat across
selectivity (1 repository or 359), because `tech_type LIKE '%|tag|%'` has a
leading wildcard and scans either way.

The delimiters are load-bearing: `tech_type` is pipe-delimited *and*
pipe-terminated (`|Git|GitHub|dependencies|`), so `%|Git|%` matches `Git` without
also matching `GitHub`. On live data those two tags resolve to 353 and 316
repositories respectively — a substring match would have conflated them.

### Files changed

- `src/kospex_query.py` — `repos_with_tag()`, `repo_files_by_tag()`,
  `repos_without_tag()`, `repos_without_tech()`, `repos_with_file_metadata()`,
  `count_repos_with_file_metadata()`
- `src/kweb2.py` — the `/tag/{tag}` and `/tags/` routes; `/tech/` now passes the
  same template variables and supports the same `?absent=true`;
  `_absent_requested()` is shared by both
- `src/templates/tags.html` — the tag index
- `src/templates/404.html` — unchanged; its `error` callout simply gets filled
  now
- `src/templates/repos-tech.html` — the heading says `Tag:` or `Technology:`
  from `tech_kind`; the "See Files" link comes from `tech_url` instead of being
  hardcoded to `/tech/`; a line under the heading gives the repository count;
  and the table gained Server / Org / Repo / # Files columns. `tech_kind` and
  `tech_url` default to the `/tech/` behaviour, so the template is unchanged for
  any caller that does not set them.
- `tests/test_repos_by_tag.py` — 21 tests: the counts, the `latest = 1` filter,
  the `Git`/`GitHub` delimiter case, the 404 mapping, and the columns the
  templates consume
- `tests/test_tags_index.py` — 11 tests: the sort, the three columns and their
  fill order, the percent-encoding of `#` and spaces, and that every listed tag
  passes `where_tag()`
- `tests/test_web_endpoints.py` — `/tag/AI` and `/tags/` added to the
  parametrised lists

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

## The absent view — which repositories *don't* have it

`?absent=true` lists the repositories with no file carrying the tag. This is
the adoption question: "everyone should have a Dependabot file" is only
answerable from the complement.

The tag listing reports both sides, with the second half linking to the absent
view:

> **Tag: Dependabot** — 122 repositories with this tag, [252 repositories do not
> have files with this tag](#)

**The heading says it too**, because the heading is what a reader scans:

> **Tag: Dependabot is not present**

`Tag: Dependabot` over a list of repositories reads as the repositories that
*have* it — the opposite of the page. `/tech/` gets the same suffix from the
same template (`Technology: C Header is not present`), and neither positive
listing carries it.

and the absent page states its denominator, so the number is readable:

> 252 repositories have no files with this tag, of 374 repositories that have
> file metadata
>
> The '# Files' column shows the total number of all file types and tags in
> this repository.

Measured on a local ~370-repo database, present + absent equals the universe
exactly for every tag tried, with no repository in both: `Dependabot` 122 + 252,
`AI` 51 + 323, `license` 359 + 15, `llms.txt` 1 + 373, all totalling 374.

**The denominator is repositories with current `file_metadata`, not the `repos`
table.** A repository we have never collected file metadata for cannot be said
to be missing a tagged file — "we have not looked" is a different finding from
"it is not there", and conflating them would put false entries in a compliance
list. On this database the two sets happen to be identical (374 each, with no
repository in `repos` lacking metadata), so the choice costs nothing today and
degrades honestly if that stops being true.

**In this view `# Files` is the repository's total current file count**, not a
count of tagged files, which would be zero on every row. It is the context for
whether a missing file matters — a 3-file repository without a Dependabot
config is a different finding from a 3,000-file one. The "See Files" column is
dropped entirely, since there are no tagged files to show.

The page says that in so many words, because on a list of repositories that
have *no* file with the tag, a non-zero count beside each one otherwise reads
as a contradiction. The same column on the positive listing *is* the tagged
count, so the sentence appears only in the absent view.

**A guard on an undefined variable, not a `none` check.** `/tech/` shares this
template and passes no `absent_count`, and Jinja's `Undefined` satisfies
`is not none` — so `{% if absent_count is not none %}` rendered the second half
of the sentence with no number in it on every technology page
("96 repositories with this technology, repositories do not have files with
this technology"). The condition now tests `is defined` first. A test renders
`/tech/` and asserts the dangling clause is absent, since this is the failure
mode of sharing a template between a route that sets a variable and one that
does not.

`repo_id` takes precedence over `absent`: files within one repository that lack
the tag is not a question with an answer.

`?absent`, `?absent=true`, `?absent=1` and `?absent=yes` all mean yes — a bare
flag is what a person types — while `false`, `0` and `no` mean no. One helper,
`_absent_requested()`, decides this for both routes so they cannot drift on
which spellings they accept.

### `/tech/` gets the same two sides

`/tech/{tech}?absent=true` answers the Language version of the question —
"which repositories have no Python in them?" — and the positive page links to
it in the same wording. Both pages share the template, so the sentence and the
denominator are identical by construction.

The universe is the same count for both, which is not a coincidence worth
glossing over: `Language` and `tech_type` are columns of the *same* table, so
"repositories we have looked at" means exactly the same thing on either side.

**A technology no current file has is a 404 on every view** — the listing, the
absent view and the file drill-down. There is no vocabulary to validate a
Language against, unlike a tag: `scc` reports whatever it reports, so "nothing
current has it" is the only available signal that a name is a typo. And its
complement is *every* repository, so the absent view would otherwise render a
confident list of repositories "missing" something this database has never
seen.

The positive view previously returned **200 with an empty table**, which reads
as "no repositories have Fortran" when the truth is that nothing here has ever
been Fortran — the same confusion #293 exists to remove for tags. All three
paths now agree.

Each path gets the answer from what it already needed, so only the drill-down
pays for a query it would not otherwise run:

| path | how it knows | measured |
|---|---|---|
| listing | `repos_with_tech()` came back empty | free |
| absent | `repos_without_tech()` raises `ValueError` | free |
| drill-down | `has_tech()` — `LIMIT 1`, not a count | 3 ms present, 52 ms absent |

`has_tech()` uses `LIMIT 1` rather than `count(*)` precisely so the common case
is nearly free: a Language that is present stops at the first row, and only a
name nothing has pays for the scan — and that one ends in a 404 anyway.

One side effect worth knowing: `Language = ?` has always been case-sensitive,
so `/tech/python` is now a 404 where it used to be a 200 with an empty table.
Both say "wrong URL"; the 404 says it legibly.

## The 404 page now says why

`404.html` has had an `error` callout since it was written and **nothing ever
filled it**. The handler discarded `exc.detail` entirely, so `/tech/Fortran`,
`/tag/NotATag`, `/repo/nope` and a plain mistyped path all rendered the
identical "Oops! Page not found." — a reader could not tell a name this estate
does not have from a URL that was never a page.

The handler now passes the detail through, which is why several routes' long-
dead messages become visible at once: "Repository not in the kospex database:
…", "Organisation not found", "Summary pages are per repo, org or server."

The two routes this change is about name what was asked for:

> Fortran is an unknown Technology or Language.

> NotATag is an unknown Tag.

"Technology or Language" names both words a reader might have had in mind:
`/tech/` serves `scc`'s `Language` column, while the Landscape pages call the
same thing a technology. The name itself is in the message because the URL may
have come from a paste or a long link whose end is not visible, and the address
bar shows the percent-encoded form (`C%23`, `Azure%20DevOps`) rather than what
was meant.

The callout is centred — `justify-center` on its flex row and `text-center` on
the sentence — because the rest of the page is centred and a left-aligned icon
and sentence in the middle of it read as a misplaced block. Both classes are
already in the compiled `tailwind.css`, so no `npm run build-css`.

**The name comes from the URL path, so the escaping is tested, not assumed.**
Reflecting a path segment into a page is the reflected-markup injection shape
(CWE-79). Jinja autoescapes it — `Jinja2Templates` builds its environment with
`select_autoescape()`, which covers `.html` — and a test renders a payload
through the handler and asserts the markup comes back as entities rather than
tags. Confirmed against the running server too: `"><img src=x onerror=...>`
renders as `&#34;&gt;&lt;img src=x onerror=...&gt;`. The message is plain text
in a `<p>`, never an attribute or an `href`.

**A detail that only restates the heading is dropped.** Starlette's default for
an unmatched route is the bare string `Not Found`, and the catch-all route's is
`Page not found` — the page already says that in 3xl bold, and repeating it in
an orange warning box reads as a second, different problem. Tests cover both
directions: the generic strings are suppressed, and the informative details
other routes already wrote still render.

Measured on a local ~370-repo database, present + absent equals the universe
exactly with no repository in both, for every Language tried: `Python`
96 + 278, `Go` 121 + 253, `Markdown` 363 + 11, `YAML` 343 + 31 — all 374.

**Implemented as a set difference, not a `HAVING` clause.** A single statement
with `HAVING sum(CASE WHEN tech_type LIKE ? ...) = 0` measured 93 ms against the
set difference's 107 ms, and both return identical results. The 14 ms buys
keeping `where_tag()` as the only place a tag is matched: writing the `LIKE` by
hand here would duplicate its escaping and could drift from it. `KospexData` has
no `HAVING` support, so the alternative was either raw SQL or extending the
shared query builder.

## `/tags/` — the vocabulary, not the database

A plain index: `panopticas.get_tags()` sorted case-insensitively and laid out in
three columns, each tag linking to its own page. Jinja's `slice(3)` fills each
column in turn, so the alphabet runs *down* a column rather than across the
rows. No database access and no counts.

It lists what panopticas **can** assign, which is deliberately not "the tags in
this database" — on a local ~370-repo database that is **123 tags, of which 61
have no repositories**. The page says so under the heading rather than implying
every entry has data. The upside of using the vocabulary is that it is the same
set `where_tag()` validates against, so **no listed tag can 404**; a test
asserts that for every tag, and all 123 links were followed against a real
database and returned 200.

Listing only the tags actually present would need the counting that is out of
scope below: `tech_type` holds several tags per row, so the present-tag set
cannot be read without splitting every tagged row.

The three-column grid uses `grid-cols-1 md:grid-cols-3`, both already in the
compiled `tailwind.css`, so no `npm run build-css` is required. (`columns-3`
would have needed a rebuild.)

## Out of scope

- **A tag listing page** ("which tags exist, with counts"). Not a single-table
  lookup: `tech_type` holds several tags per row, and the obvious
  `GROUP BY tech_type` shortcut double-counts repositories — on live data 25 of
  63 tags come out wrong, one reporting more repositories than exist. A tag can
  also appear twice in one stored value, so splitting needs a `set`. Its own
  issue.
- **Developers behind a tag.** That needs `commit_files` joins, and the per-file
  loop shape it would reach for is #319.
