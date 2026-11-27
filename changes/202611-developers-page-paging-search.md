# /developers/: overview, server-side pages and email search

Status: design, awaiting approval.

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

**Scope.** `/developers/{id}` or `?server=`, `?org_key=`, `?repo_id=`, for server, org or
repo. The narrowest wins. An unknown scope is a 404. Server scope is ignored today.

**Always at the top:** the author and committer cards, an email search box, and counts for
Active (90 days or less), Aging (91–180), Stale (181–365), Unmaintained and All. Each
count links to its list.

**With no `status` or `q`:**

- 250 or fewer active developers: the active list.
- More than 250: an overview with the 10 busiest orgs by active developers (repos when
  scoped to an org; nothing for a repo), a "List all active" link and the CSV download.

```
Developers · All repositories
Search email: [ paul________ ]

  Active   Aging   Stale   Unmaintained   All
  6,218    3,374   3,614   66,723         79,929

Busiest orgs (by active developers)
  org-a    3,665  >
  org-b      ...  >

[ List all active ]   [ Download CSV ]
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

**CSV** (`download=true`): everyone in the current scope, status and search, not just the
visible page. Columns are unchanged.

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
