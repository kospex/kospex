# Design: summary stats computed at sync time

Status: **in progress**. Phases 1–3 merged (#303, #304, #307). Phase 4 implemented. Revision 5, updated after measurement and
review. Follows #298 (scoped request cache) and #302 (debug reload scope).

## Problem

kweb's busiest pages aggregate the whole `commits` table on every cold load.
Measured on a dev DB with about 375 repos and 1.1M commits (cold, live kweb):

| Query | Time | Used by |
|---|---|---|
| `KospexQuery.developers()` | ~5.0s | `/`, `/summary/`, `/repos/` |
| `KospexQuery.repos()` | ~2.2s | `/`, `/summary/`, `/repos/` |
| `KospexQuery.active_devs()` | ~1.1s | `/repos/` |

#298 caches these results, but any repo sync empties the whole cache. A site
running `kospex-agent` every 5 minutes therefore sees the cold load (7–13s for
`/repos/`) after almost every sync. The cost grows with total commits, not with
what changed.

Measurement also showed that much of `developers()`'s time is **Python, not
SQL** (Phase 1 below).

## Plan at a glance

| Phase | Change | Storage | Effect |
|---|---|---|---|
| 1 | Fast path for date parsing in `days_between_datetimes()` | none | `developers()` loses ~2.5s of its ~3.9s, on every page that lists developers |
| 2 | Migration `0011`: two author-time columns plus one covering index on `commits` | +190MB (+4.4%), +26% on commit upserts | `repos()` 3–10x and `active_devs()` ~13x faster |
| 3 | `update_developer_stats` stores committer time in `first_commit` / `last_commit` | none | `developer_stats` matches what the web layer reports, and heals itself on sync |
| 4 | `/` and `/summary/` read status counts from `developer_stats` | none | ~5s → ~0.06s for estate developer counts |
| 5 (later) | `repo_stats` table for `/repos/` | small | committer counts per repo without a scan |

Phases 1 and 2 stand alone and help pages beyond the summary. Phase 4 depends
on 2 and 3.

## Phase 1 — date parsing fast path (Python)

### What is slow

`developers()` computes `tenure` for each developer:

```python
i["tenure"] = KospexUtils.days_between_datetimes(
    i["first_commit"], i["last_commit"], min_one=True
)
```

`days_between_datetimes()` (`src/kospex_utils.py`) parses both strings with
`dateutil.parser.parse`. That is a general-purpose parser: it tokenises the
string and tries many layouts, at about 17µs per call. Over 72,649 developers,
that is 145,298 calls:

| Measured on the same 72,649 rows | Time |
|---|---|
| `developers()` total | 3.94s |
| of which, day differences via `dateutil.parser.parse` | **2.54s** |
| the same day differences via `datetime.fromisoformat` | **0.035s** |

A cProfile run put 10.0s of 11.5s (profiler-inflated) inside
`days_between_datetimes`, almost all of it in `dateutil`'s tokeniser.

`days_ago()`, which `development_status()` uses, already parses with
`datetime.fromisoformat` and is not a cost. `days_between_datetimes()` is the
only `dateutil.parser.parse` call in `src/`. The one in
`kospex_dependencies.py` is `dateutil.parser.isoparse`, a different, ISO-only
function.

### The change

Try the C-implemented stdlib ISO parser first, and fall back to `dateutil` only
for strings it rejects:

```python
def _parse_datetime(value: str) -> datetime:
    """Parse a timestamp, ISO 8601 fast path first.

    datetime.fromisoformat (C, Python 3.11+ accepts 'Z', offsets, a space
    separator and date-only strings) handles every timestamp kospex stores, at
    ~70x the speed of dateutil. dateutil stays as the fallback, so any other
    format behaves exactly as before, including raising on the same input.
    """
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return parser.parse(value)
```

`days_between_datetimes()` then calls `_parse_datetime()` in place of
`parser.parse()`. Nothing else changes: callers, signature, rounding and
`min_one` stay as they are.

### Why it is safe

- **Same result wherever the fast path succeeds.** Every distinct timestamp in
  the dev DB was parsed both ways: 1,131,765 values from
  `commits.committer_when`, `repos.last_seen` / `last_sync` and
  `developer_stats.first_commit` / `last_commit`, plus space-separated,
  date-only and `Z` samples. There were **0 mismatches** in value, in
  naive-vs-aware, or in UTC offset.
- **Same behaviour where it doesn't.** Two malformed shapes exist in real data.
  `fromisoformat` rejects both, so they fall back to `dateutil`, which returns
  exactly what it returned before:
  - `2011-09-08T02:38:50+518:00`: a broken git timezone, 4 commits. `dateutil`
    does **not** raise here. It returns a datetime with an out-of-range offset,
    which errors only when used. `days_between_datetimes()`'s `except ValueError`
    then catches it and returns 0, both before and after. (An earlier draft of
    this spec said `dateutil` raises. That was wrong: the error surfaced while
    printing the result.)
  - Multi-line `repos.first_seen` (35 repos): `dateutil` raises `ParserError`.
    `get_repos()` already splits these on `\n` before calling.
- `requires-python = ">=3.12"`, so `fromisoformat`'s 3.11 `Z` and offset support
  is always available.

### Tests

- A golden corpus test: for each sample (`Z`, offset, space separator,
  date-only, naive), `_parse_datetime(s) == dateutil.parser.parse(s)`. The
  `+518:00` value still gives 0 days, and the multi-line value still raises.
  Implemented in `tests/test_parse_datetime.py`.
- `days_between_datetimes` results unchanged for the same pairs, including
  `min_one`.

### Existing defect, unchanged by this

`days_between_datetimes()`'s `try` wraps only the subtraction, so a parse error
propagates to the caller. Mixing a naive and an aware value raises `TypeError`,
which the `except ValueError` also misses. Both are current behaviour and stay
so here, to keep the change provably equivalent. Fixing them is a separate
decision about what callers should see.

## Phase 2 — migration `0011`: columns and one index

Schema only, with **no data migration**. Phase 3 makes every sync rebuild a
repo's rows, so existing rows heal on their next sync (see "Healing" below).

As implemented, in `src/kospex/db/migrations/0011_commits_covering_index.sql`:

```sql
CREATE INDEX IF NOT EXISTS idx_commits_repo_author_when
    ON commits (_repo_id, _git_server, _git_owner, _git_repo,
                author_email, committer_email, committer_when, author_when);

ALTER TABLE developer_stats ADD COLUMN first_authored TEXT;
ALTER TABLE developer_stats ADD COLUMN last_authored TEXT;
```

(No semicolons inside the file's comments: the runner's statement splitter
breaks on them.)

Per CLAUDE.md: the columns and index go **in the migration only**, never also
in `kospex_schema.py`. Bump the hardcoded migration counts in the three test
files, and expect `test_fresh_db_has_no_pending_migrations` to guard the
clean-install path.

### Index measurements

Measured on a copy of the dev DB (4.3GB), page-cache warm, so the numbers are
lower than the cold figures above. Compare within the table. Per-repo
queries show tensorflow (199k commits) / kotlin (145k commits).

| Query | Now (PK indexes only) | + index A | + index B as well |
|---|---|---|---|
| `update_developer_stats` SELECT | 0.54 / 0.34s | **0.19 / 0.13s** | same |
| `update_developer_file_stats` SELECT | 7.6 / 9.4s | **3.2 / 3.5s** | 1.4 / 1.8s |
| `developers()` SQL, estate | 1.39s | **0.64s** | same |
| `repos()`, estate | 1.96s | **0.42s** | same |
| `active_devs()`, estate | 1.01s | **0.07s** | same |
| Size / build time | — | **154MB (+3.6%) / 1.8s** | +1,062MB (+25%) / 25s |

- **Index A** (first draft), `commits(_repo_id, author_email, committer_email, committer_when, author_when)`:
  **superseded by A′, below.** The table above was measured with a hand-written
  `repos()` query that omitted `_git_server`, `_git_owner` and `_git_repo`.
  Production `repos()` selects all three, so with A it ignored the index and
  scanned the table.
- **Index B**, `commit_files(_repo_id, hash, file_path, additions, deletions)`:
  **reject.** It duplicates most of the 1.6GB table to halve one query that
  index A already cuts by two-thirds. `commit_files` keeps only its primary key,
  `(hash, file_path, _repo_id)`, which serves the per-commit lookups the join
  needs.

### Index A′, as implemented

`commits(_repo_id, _git_server, _git_owner, _git_repo, author_email, committer_email, committer_when, author_when)`.
Measured again with the **production** `KospexQuery` methods on a fresh copy of
the DB (warm):

| Production call | PK only | 5-column A | **8-column A′** |
|---|---|---|---|
| `repos()`, estate | 1.66s | ~1.5–2.0s, A unused (same plan as PK only) | **0.49s** |
| `repos(org_key)` | 0.92s | 0.89s, A unused | **0.09s** |
| `developers(org_key)` | 0.22s | 0.22s, A unused | **0.17s** |
| `active_devs()`, estate | 1.02s | 0.07s | **0.08s** |
| `update_developer_stats(tensorflow)`, SELECT + writes | 2.29s | 2.33s | 2.46s |
| Size / build time | — | 154MB / 1.6s | **190MB (+4.4%) / 4.6s** |

- A single 2.67s reading for `repos()` with the 5-column index was noise. A
  re-run, 7 timings per configuration over two rounds, gave medians of
  1.50–2.02s both with and without it, all with the identical plan. Single runs
  on the dev machine vary by up to 2x (one run with no index took 3.62s), so
  compare medians, not single runs.
- `update_developer_stats` as a whole does not get faster. Its SELECT does
  (0.54s → 0.19s, measured above), but row-by-row deletes and inserts dominate
  the total.
- `developers()` estate barely moves (3.99s → 3.77s), because Python dominates
  it. Phase 1 fixes that part.
- `tests/test_commits_covering_index.py` captures the SQL that `repos()`,
  `repos(repo_id)`, `active_devs()` and `update_developer_stats()` actually
  send, and asserts each uses `COVERING INDEX idx_commits_repo_author_when`.
  That test caught the A-versus-A′ gap.

**Write cost, measured.** 20,000 sync-style commit upserts (one
`table.upsert()` per commit, as `sync_repo()` does): 9.22s without the index,
11.62s with it. That is **+26%, about 120µs per commit**. `commit_files`
upserts, the bulk of a sync, are unaffected. For a 1,500-repo initial sync of
around 4.6M commits, that is roughly 9 minutes extra in total.

## Phase 3 — `update_developer_stats` stores committer time

`developer_stats` is inconsistent today. `commits_last_90_days` counts by		
`committer_when`, but `first_commit` / `last_commit` are taken from
`author_when`. The web layer uses committer time throughout. Rebases,
cherry-picks and applied patches keep the original author time, so the two
differ. Of 91,279 (repo, author) pairs on the dev DB, first or last differs:

| Measure | Pairs |
|---|---:|
| Any difference in the timestamp | 20,911 (22.9%) |
| A different local date (date string) | 22,983 (25.2%) |
| A different UTC day | 11,803 (12.9%) |

(An earlier revision cited 19,573, which none of these measures reproduces.)

Change the SELECT in `update_developer_stats` so that:

| Column | Becomes |
|---|---|
| `first_commit` / `last_commit` | `MIN` / `MAX(committer_when)`, UTC `Z` |
| `first_authored` / `last_authored` (new) | `MIN` / `MAX(author_when)`, UTC `Z`, which is what `first_commit` / `last_commit` held before |

It is the same scan of the repo's commits with two more aggregates, and still
answered from `idx_commits_repo_author_when` alone. Measured on the largest
repo (199k commits, 4,948 authors), the SELECT goes from 0.16s to 0.24s
(median of 7). That's +80ms per sync of a repo that size, small next to the
rest of a sync.

**Effect on `kospex stats`.** It displays `first_commit` / `last_commit`, so
after a repo resyncs those dates move to committer time. Rebased and
cherry-picked work shows later dates. This is intended: one definition of "last
commit" across CLI and web.

**Not affected: `kospex key-person`.** `key_person()` computes first/last
commit and tenure live from `commits` (via `commit_stats()`), by author time,
and doesn't read `developer_stats`. It is deliberately left unchanged. Tenure
is currently calculated in three places: `key_person()` uses author time, while
`developers()` and `kospex developer` use committer time, and `kospex developer`
divides by 365.25 rather than 365. Which time tenure should use is a separate
decision, to be made before unifying them.

### Healing (no backfill)

Every sync path (`kgit clone` / `pull`, `kospex sync-directory`,
`kospex-agent`, `krunner`) goes through `Kospex.sync_repo()`, which calls
`update_developer_stats()` at the end **even when no commits are new**.
`update_developer_stats()` recomputes the repo from all its commits. So:

- each repo is corrected on its next sync, and an interrupted sync is repaired
  by the one after;
- `last_authored IS NULL` marks a row written before the fix, whose
  `last_commit` still holds author time;
- how quickly a whole DB heals depends on sync cadence. `kgit pull --all` heals
  everything in one run. `kospex-agent` syncs at most 3 stale repos per 5-minute
  cycle, which is about 10 hours for 374 repos;
- without git, from the DB alone: `kospex rebuild developer-stats -all`
  (added in Phase 4, 3.0s for the dev DB). Phase 4 makes this step required on
  upgrade, since the summary pages no longer fall back.

## Phase 4 — `/` and `/summary/` read `developer_stats`

The summary pages only count statuses. That needs each developer's last commit
and each repo's last commit, and both now come from `developer_stats`. Add two
narrow `KospexQuery` methods, scoped by repo, org or server like
`developers()`:

```sql
-- developer statuses: one row per developer, as developers() groups them
SELECT MAX(last_commit) FROM developer_stats WHERE <scope> GROUP BY LOWER(author_email)

-- repo statuses
SELECT MAX(last_commit) FROM developer_stats WHERE <scope> GROUP BY _repo_id
```

`development_status()` is applied in Python against now, as today.

**Validated before writing this.** A temporary copy of the table with committer
time, checked against the live full-scan queries:

| Scope | Current query | From `developer_stats` | Status counts identical |
|---|---|---|---|
| Developers, estate (72,649) | ~5s | 0.06s | yes |
| Repos, estate (374) | ~2.2s | 0.03s | yes |
| Developers, org `github.com~kospex` | — | 0.007s | yes |

**Unhealed rows: no fallback (decided).** A first implementation fell back to
the `commits` queries for any scope containing an unhealed row or a repo with
no stats. That made the speed-up all-or-nothing: the estate page stayed slow
until the last repo resynced, and one stuck repo kept it slow indefinitely. It
also added code that nobody installing after this release would ever run.
kospex has very few users today, so the fallback was dropped. Instead the
release is a breaking upgrade: run `kospex rebuild developer-stats -all` after
`upgrade-db`, and `kospex system-status` reports any repo still to rebuild.

`developers()` and `repos()` themselves are **not** re-pointed in this phase.
Other pages call them with `from_date` / `to_date` / `days` windows, which
all-time rows cannot answer.

### As implemented

- **`KospexQuery.developer_statuses()` / `repo_statuses()`**, taking the same
  `org_key` / `repo_id` / `server` as `developers()`. They return rows with
  `status` (plus `days_ago` for repos).
- **kweb:** `summary_rows()` in `kweb2.py` reads them under new cache keys
  (`developer_statuses`, `repo_statuses`). `/summary/<base64 email>`, which
  `get_id_params()` turns into an `author_email` scope, is a 404: nothing
  links to it, it returned 500 before (`developers()` / `repos()` take no
  `author_email`), and a developer's page is `/developers/`. `/summary2/` is
  unchanged.
- **`KospexQuery.developer_stats_coverage()`**, shown by `kospex system-status`
  under "Summary stats coverage": the number of repos with rows not rebuilt yet
  (`last_authored IS NULL`, or a legacy `NULL` `stat_type`), and the number with
  commits but no `ALL_TIME` rows. "Repos with commits" comes from a loose index
  scan of the `commits` primary key, one seek per repo: 8ms for 374 repos,
  against 85ms warm and 2.4s cold for `SELECT DISTINCT _repo_id`. A `repos`
  row with no commits doesn't count, because `repos()` doesn't count it either.
- **`kospex rebuild developer-stats REPO_ID | -all | -org | -server`**
  (`src/kospex_rebuild.py`). It is the first task in a small registry: each
  task declares what it writes and whether it needs a clone, and shares scope
  resolution (exactly one scope, as `kgit pull`), the per-repo loop with
  failures isolated, `-dry-run` and a summary.
- **`update_developer_stats()` is one transaction per repo.** sqlite-utils'
  `execute()` commits each write on its own when no transaction is open, so
  every developer row was a commit and a disk sync, and the `DELETE` committed
  before the inserts. A full rebuild of the dev DB went from 63.2s to **3.0s**
  (DefinitelyTyped 11.85s to 0.32s), and the step that ends every sync gets the
  same saving.

**Measured on the real DB.** Phase 3 rows were first simulated with a
connection-only TEMP table shadowing `developer_stats` (read-only, nothing
written), with the since-removed coverage check included in the timings. Warm:

| Scope | Developers / repos | Status counts identical | Stats path | Commits queries |
|---|---|---|---|---|
| Estate | 72,649 / 374 | yes | 0.24s | 1.81s (~5s cold) |
| `server=github.com` | 71,723 / 363 | yes | 0.27s | 2.00s |
| `org_key=github.com~jshttp` | 140 / 14 | yes | 0.06s | 0.20s |
| `org_key=go.googlesource.com~` | 1,501 / 11 | yes | 0.06s | 0.17s |
| `org_key=github.com~ljharb` | 97 / 9 | yes | 0.06s | 0.20s |
| `server=go.googlesource.com` | 1,501 / 11 | yes | 0.07s | 0.19s |
| `repo_id=github.com~jetbrains~kotlin` | 1,255 / 1 | yes | 0.07s | 0.19s |

The estate's 0.24s breaks down as about 100ms for the `GROUP BY
LOWER(author_email)` over 91k rows, 60ms for 72,649 `development_status()`
calls in Python, and two coverage checks. That is higher than the 0.06s
"Validated" figure above, which timed the developer query alone.

**After a real rebuild.** On a copy of the dev DB, `kospex rebuild
developer-stats -all` (3.0s) left coverage at 0 / 0. Read from the real table
without the coverage check, status counts again matched `developers()` /
`repos()` for the estate, an org, a server and a repo: estate 0.19s, org
0.03s, server 0.03s, repo 0.001s.

### The request cache after this

Keep it. A 0.06s query is still worth caching on a busy page, and invalidating
everything on each sync stops costing anything users notice.

## Phase 5 (later) — `repo_stats` for `/repos/`

`/repos/` also shows `COUNT(DISTINCT committer_email)` per repo, which no stats
table stores. A `repo_stats` table, one row per repo (`commits`, `authors`,
`committers`, first and last commit), rebuilt in `sync_repo()`, would answer it
without a scan. Index A′ already brings `repos()` from 1.66s to 0.49s, so
measure again after Phase 2 before building this.

## Related: `update_developer_file_stats` on every sync

Its SELECT joins `commit_files` and costs 12–17s on the largest repos (cold),
or about 3.5s with index A. It runs on **every** sync, including syncs that
bring in no new commits. Skipping it on those syncs would remove most of that
cost, but only some of its columns are safe to skip:

| Columns | Can they change with no new commits? | Safe to skip? |
|---|---|---|
| `additions`, `deletions`, `unique_files` (all time) | No: they only change when commits are added | **Yes** |
| `additions_90_days`, `deletions_90_days`, `unique_files_90_days` | **Yes**: the window is relative to when the sync ran, so it moves even when the data doesn't | **No** |

**Caveat.** If the whole function is skipped, the 90-day columns freeze at their
last values. A repo with no commits for three months should report zero recent
activity, but it would keep reporting last quarter's numbers indefinitely.
`update_developer_stats` has the same exposure in `commits_last_90_days` and
`pct_90_days`. It avoids it only because it runs on every sync.

Two ways to make the skip safe:

1. **Skip only the all-time part.** On a no-commit sync, recompute just the
   90-day columns. That is cheaper, because only commits inside the window join
   to `commit_files`.
2. **Stop storing windowed values.** Keep the timestamps in the table and compute
   "last N days" at query time, as Phase 4 does for active developers. This is
   cleaner and has no staleness at all, but it changes what `kospex stats`
   reads, so it is a bigger change.

Do **not** skip `update_developer_stats` either way. It must still run, so that
no-commit syncs keep healing rows (Phase 3) and its own 90-day columns stay
current.

### Scaling note

Measured per-repo stats cost is roughly linear in that repo's commits: about
7µs per commit for the `update_developer_stats` SELECT. It is independent of
estate size, because both queries look the repo up by `_repo_id`. Estate-wide
aggregates grow a little faster than linearly, since `GROUP BY` and
`COUNT(DISTINCT)` sort. Times also jump once the DB no longer fits in the page
cache: the dev DB is 4.3GB on a 16GB machine, and a 1,500-repo estate is likely
3–4x that. The `commit_files` lookups in this function, which hop across the
table by `hash`, degrade the most when reads go to disk.

## Verification

- **Phase 1:** the parser corpus test above.
- **Phases 2–4:** a characterisation A/B, in the spirit of
  `tests/test_url_parsing_characterisation.py`. For the estate, each server,
  each org and a sample of repos, compare the current `commits`-based status
  counts with the `developer_stats`-based ones.
  - Run it on a fixture DB in the suite, and once by hand against a real
    estate DB.
  - An empty diff covers only the scopes it ran. Include a nested-group (`~~`)
    org, an owner-less host (`go.googlesource.com` repos have an empty
    `_git_owner`), mixed-case author emails, and a repo whose author and
    committer times differ.
  - Assert each side returned rows, so `[] == []` cannot pass.
- **Index:** confirm with `EXPLAIN QUERY PLAN` that the target queries use
  `idx_commits_repo_author_when`, and time a full sync with and without it.

## Decisions

Resolved in review:

- Committer time is the definition of first and last commit, in
  `developer_stats` and everywhere else. Author time is kept in new
  `*_authored` columns.
- No data migration. Rows heal on sync, and unhealed scopes fall back.
- Index A′ (8 columns) yes, index B no.

Still open:

1. **Case in author counts.** `repos()` counts `author_email` case-sensitively,
   while `developers()` and `orgs()` lowercase it. Recommended: preserve each
   behaviour exactly here, so the A/B diff stays empty, and fix it separately.
2. **Skip file stats on no-commit syncs**: in this work or as its own change.

## Out of scope

- `orgs()`: distinct committers per org would need a per-(committer, repo)
  table. Index A may make it fast enough without one.
- `tenure`, `/developers/` and the other `commits` scans. Phase 1 and index A
  already help them, so re-measure first.
- Identity resolution (`commits_view` / `email_map`). Stats are keyed on the raw
  email, so mapping can still be applied at query time.
- Background cache warming. It becomes unnecessary once cold loads are fast.
