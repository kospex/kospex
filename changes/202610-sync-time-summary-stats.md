# Design: summary stats computed at sync time

Status: **proposed**, no code yet. Follows #298 (scoped request cache) and #302
(debug reload scope).

## Problem

kweb's busiest pages aggregate the whole `commits` table on every cold load.
Measured on a dev DB with about 375 repos and 1.1M commits:

| Query | Time | Used by |
|---|---|---|
| `KospexQuery.developers()` | ~5.0s | `/`, `/summary/`, `/repos/` |
| `KospexQuery.repos()` | ~2.2s | `/`, `/summary/`, `/repos/` |
| `KospexQuery.active_devs()` | ~1.1s | `/repos/` |

#298 caches these results, but any repo sync empties the whole cache. A site
running `kospex-agent` every 5 minutes therefore sees the cold load (7–13s for
`/repos/`) after almost every sync. The cost grows with total commits, not with
what changed.

## What already exists: `developer_stats`

`sync_repo()` already ends by calling
`KospexQuery.update_developer_stats(repo_id)` and
`update_developer_file_stats(repo_id)`. Together they rebuild one row per
(`_repo_id`, `author_email`) from that repo's commits. The table:

- covers **all 374 repos**, with 91,279 rows against 1.1M commits;
- is rebuilt from the whole repo on each sync, so an interrupted sync is
  repaired by the next one;
- carries `_repo_id`, so `kreaper delete-repo` already removes its rows
  (`get_repo_tables` finds every table with that column);
- is currently read only by `kospex stats` (key-person analysis).

The same aggregates computed from it instead of `commits`, timed on the same
DB:

| Equivalent of | From `commits` | From `developer_stats` |
|---|---|---|
| `developers()` (72,649 authors) | ~5.0s | **0.25s** |
| `repos()` commits + authors (374 repos) | ~2.2s | **0.05s** |
| `active_devs()` | ~1.1s | **0.01s** |

`SUM(total_commits)` per repo matched `COUNT(*)` from `commits` for **all 374
repos**.

### Why it can't be used as-is

1. **Different timestamp.** `developer_stats.first_commit` / `last_commit` use
   `author_when`, while the web queries use `committer_when`. The two differ in
   date for **19,573 of 91,279** (repo, author) pairs: rebases, cherry-picks
   and patches applied later. Status buckets (active / aging / stale) depend on
   the last commit date, so swapping silently changes the numbers. The
   `active_devs()` equivalent above already returned 149 repos against 150 from
   `commits`.
2. **No committer counts.** `repos()` reports
   `COUNT(DISTINCT committer_email)` per repo, and nothing stores that.
3. **`commits_last_90_days` is frozen at sync time.** A repo nobody syncs keeps
   claiming last quarter's activity. Anything "in the last N days" has to be
   computed at query time, from stored timestamps.

## Proposal

### Schema (migration `0011_summary_stats`; check the directory for the next free number)

1. Add to `developer_stats`:
   - `first_committed` TEXT: `MIN(committer_when)`, normalised to UTC `Z` the
     way `repos()` does today;
   - `last_committed` TEXT: `MAX(committer_when)`, same format.
2. New table `repo_stats`, one row per repo:

   | Column | Value |
   |---|---|
   | `_repo_id` (PK), `_git_server`, `_git_owner`, `_git_repo` | identity, as elsewhere |
   | `commits` | `COUNT(*)` |
   | `authors` | `COUNT(DISTINCT author_email)` (case-sensitive, as `repos()` is today) |
   | `committers` | `COUNT(DISTINCT committer_email)` |
   | `first_commit`, `last_commit` | `MIN` / `MAX(committer_when)`, UTC `Z` |
   | `last_update` | when the row was computed |

   A separate table rather than columns on `repos`, so that "the repo exists"
   and "its stats are current" stay separate facts. `kreaper` picks it up
   automatically.

Per CLAUDE.md: add the columns **in the migration only**, never also in
`kospex_schema.py`'s `SQL_CREATE_*`, and bump the hardcoded migration counts in
the three test files.

### Backfill (`0011_summary_stats.py`, `up(db)`)

Fill `first_committed` / `last_committed` with one grouped `UPDATE` from
`commits`, and `INSERT` `repo_stats` with one `GROUP BY _repo_id`. That is
one full scan. Comparable grouped queries took 1.4–3.4s on this DB. Being a migration, it runs
under the user's explicit `kospex upgrade-db -apply`, never on its own.

### Sync

In `sync_repo()`, next to the existing calls, add an `update_repo_stats(repo_id)`
and extend `update_developer_stats` to write the two new columns. Each covers
only the synced repo, so its cost scales with that repo, not the estate.

Write a repo's rows in **one transaction**: delete, insert and the percentage
update. A reader must never see a repo with its old rows deleted and the new
ones not yet in. The existing `update_developer_stats` commits once at the end,
which already holds; verify it with a test rather than assuming it. (See the
sqlite_utils notes: `insert` / `upsert` auto-commit, and `with conn:` does not
make several statements atomic.)

### Queries

Re-point `repos()`, `developers()` and `active_devs()` at the stats tables:

- `developers(scope)`:
  `SELECT LOWER(author_email), MIN(first_committed), MAX(last_committed), SUM(total_commits) FROM developer_stats WHERE <scope> GROUP BY 1`.
  `status`, `tenure` and `years_active` stay computed in Python, against now.
- `repos(scope)`: read `repo_stats`; `days_ago` and `status` computed against
  now, as today.
- `active_devs(days)`: `COUNT(*) FROM developer_stats WHERE last_committed > :cutoff GROUP BY _repo_id`.
  This is exact: an author committed in the window if and only if their last
  commit in that repo falls inside it. The same identity yields
  `developers(days=N)` and `authors(days=N)` cheaply later.

`from_date` / `to_date` windows cannot be answered from all-time rows, so they
stay on `commits`. Those callers keep the current code path.

**Missing stats.** A repo whose sync predates the migration and that was never
backfilled has no `repo_stats` row. Pick one behaviour and test it:
1. **Fall back** to the `commits` query for that scope. Always correct, but it
   hides the gap, and slowly.
2. **Report** the gap: `kospex system-status` lists repos with commits but no
   stats.

Recommended: both, falling back per scope and reporting the gap.

### The request cache after this

Keep it. A 0.25s query is still worth caching on a busy page. It also stops
being the thing that hides the problem: a cold load becomes a quarter of a
second, so invalidating everything on each sync no longer costs anything a user
notices.

## Verification

Write a characterisation A/B, in the same spirit as
`tests/test_url_parsing_characterisation.py`, before switching any query:

- For the estate, each server, each org and a sample of repos, run the current
  `commits` query and the stats query, and diff every field per row
  (`commits`, `authors`, `committers`, `first_commit`, `last_commit`, `status`,
  `active_devs`).
- Run it against a fixture DB in the suite, and once by hand against a real
  estate DB before merging. **An empty diff only covers the scopes it ran.**
  Include a nested-group (`~~`) org, a repo with mixed-case author emails, and
  one whose author and committer times differ.
- Assert each side returned rows: `[] == []` must not pass.

## Decisions needed

1. **Committer time vs author time.** Recommended: keep committer time, so the
   numbers users see don't change. The alternative is to move the web layer to
   `author_when` and accept that status changes for ~20% of (repo, author)
   pairs.
2. **Case in author counts.** `repos()` counts `author_email` case-sensitively,
   while `developers()` and `orgs()` lowercase it. Recommended: preserve each
   behaviour exactly in this change, and fix it separately so the A/B diff
   stays empty.
3. **Missing-stats behaviour**: fall back, report, or both (recommended).

## Out of scope

- `orgs()`: distinct committers per org would need a per-(committer, repo)
  table. Measure it first.
- `tenure`, `/developers/` and the other `commits` scans. Move them once this
  pattern is proven.
- Identity resolution (`commits_view` / `email_map`). Because stats are keyed
  on the raw email, mapping can still be applied at query time.
- Background cache warming. It becomes unnecessary once cold loads are fast.
