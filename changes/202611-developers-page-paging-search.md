# /developers/: overview, server-side pages and email search

Status: implemented. Plan: `202611-developers-page-paging-search-plan.md`.

## Intent

With hundreds or thousands of developers, a list of all of them is not useful, and on a
large estate it is slow: about 7s and 88 MB for 80k authors. The page should open on who
is active, offer ways to narrow down when that is still too many, and let someone find a
developer by part of their email.

## Goals

- Active developers first; every developer still reachable.
- Slice by server, org or repo, and by status.
- Find a developer by partial email.
- The all-repositories page loads in about a second and is well under 1 MB.
- Status counts agree with `/summary/` for the same scope.

## Non-goals

- Developer names (not surfaced anywhere in kospex).
- Committer search or a committer status overview.
- The JSON API, the CLI, and other developer lists in kweb.

## Approaches

| Approach | For | Against |
|---|---|---|
| **A. Server-side pages (chosen)**: each slice is a URL, SQL returns one page | Shareable URLs, little JavaScript, easy to test | Sorting and paging reload the page; no DataTables on these lists |
| B. DataTables server-side mode | Same feel as other tables | More JavaScript, a second endpoint, shareable URLs take extra work |
| C. Cap at 500 rows | Smallest change | Cannot browse past the cap; sorting only covers those rows |

## Behaviour (authors view)

**Scope** is in the path, and every link the page generates uses that form:

```
/developers/github.com                      server
/developers/github.com~kospex               org
/developers/github.com~kospex~kospex        repo
/developers/github.com~kospex?status=active&page=2
```

- Everything else (`status`, `q`, `sort`, `dir`, `page`, `view`, `days`, `download`) stays
  in the query string.
- `?org_key=`, `?repo_id=` and `?server=` keep working for existing links. A path ID wins.
- An org or repo ID is recognised by its `~`. Any other ID is a server if the `repos` table
  has it, otherwise a 404. Server scope is ignored today.
- A base64 email is not a `/developers/` scope (today it shows everyone). It is a 404 that
  points to `?author_email=`. Base64 developer links go to `/developer/{id}`, unchanged.
- The server check comes before any base64 test: a server name with no dot, such as `git1`,
  also passes `is_base64()`. Test it with such a name.

**Always at the top:** totals, an email search box and the status circles (see Layout):
Active (90 days or less), Aging (91–180), Stale (181–365) and Dormant (over a year).

**With no `status` or `q`:**

- 250 or fewer active developers: the active list.
- More than 250: an overview with the 10 busiest orgs by active developers (repos when
  scoped to an org; nothing for a repo), a "List all active" link and the CSV download.

```
Developers
80,149 developers · 26,576 committers
Search email in all repositories [ paul________ ] [Search]

     (6.2k)    (3.4k)    (3.6k)    (  67k  )
     Active    Aging     Stale      Dormant

Busiest orgs                      Active developers
  org-a                                       3,665
  org-b                                         ...

List all 6,218 active  |  Download CSV
```

**Lists** (`?status=active|aging|stale|unmaintained|all`, case-insensitive, so the
`?status=Active` links on `/summary2/` start working):

- Columns: email (with bot badge), commits, repos, last seen (days). Commits and repos are
  all-time within the scope.
- Over 250 rows: 50 per page (`?page=`).
- `?sort=last_seen|commits|repos|email` and `?dir=asc|desc`; most recently seen first by
  default.
- An invalid `status`, `sort`, `dir` or `page` is a 400.

**Search** (`?q=`): case-insensitive "contains" on author email, across all statuses in the
scope unless `status` is also given. `%` and `_` are literal. An exact match is listed
first. Results show each developer's status and follow the same paging.

The box names where it searches ("Search email in acme", or "in all repositories"). Inside
a server, org or repo, a "Search all repositories" link repeats the search without the
scope.

**CSV** (`download=true`): everyone in the current scope, status and search, not just the
visible page. Columns are unchanged.

## Layout

Heading, a line of totals (developers and committers, each opening its list), the search
box, then a half-size version of the summary page's status circles. Each circle opens its
list, and the open one is ringed. "Unmaintained" reads as **Dormant**, as on the summary
(`?status=unmaintained` is unchanged). The circles are hidden during a search, with
`?days=` and on the committers view, where all-time counts are not what is listed. This
replaces the author and committer cards. Busiest orgs are a table, and repos with no org
appear as "SERVER (no org)", linking to the server.

## Repos with missing or old stats

A repo whose `developer_stats` are missing or out of date (a sync in progress or one that
failed, or rows not rebuilt) is read from `commits`. Every other repo still comes from
`developer_stats`. With more than 400 such repos the whole page reads `commits`.

## Other views

- **Committers** (`?view=committers`): paged and sorted the same way, still read from
  `commits`. No overview or search.
- **`?days=N`:** still works and still counts only commits in the window, now paged. The
  "Show active developers (last 90 days)" link is replaced by the Active count, whose
  list shows all-time commits instead.
- **`?author_email=`:** unchanged.

## Data

- Authors come from `developer_stats`, grouped by lower-cased email.
- While `developer_stats_coverage()` reports a gap, the same figures come from `commits`.
  Build this as one set of queries over a swappable per-(repo, email) source, not two sets.
- The committer count and committers view stay on `commits`.
- The page stops calling `summary()`, which computes figures it never shows.
- Bot detection runs on the rows shown (and on the CSV). Bots stay in every count.
- The 250 threshold and 50 page size are constants in one place.

## Constraints

- SQL must run on SQLite 3.26.
- No database migration.

## Left to the builder

Query method names, template layout, how an empty email is displayed, and what an
out-of-range page number shows.
