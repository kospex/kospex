# Aggregate and order commit dates on the true instant, not on their text

Closes #154.

## Overview

Commit dates are stored as ISO-8601 text carrying each committer's **local UTC
offset**. Text comparison of ISO-8601 is only valid when every value shares one
offset — kospex has **33 distinct offsets** across 254,997 commits — so
`MAX(committer_when)` and `ORDER BY committer_when` returned the wrong row
whenever the true latest commit had a more westerly offset than a near tie.

```sql
sqlite> SELECT '2026-04-16T18:34:03+01:00' > '2026-04-16T16:59:54-04:00';
1        -- text says so; the second is actually 3.4 hours LATER
```

This is a storage-format choice, not a SQLite limitation. SQLite has no date
type, so dates are TEXT; ISO-8601 was chosen because it sorts as text, and that
property is lost the moment offsets vary.

## Measured, before the fix

| aggregate | wrong | of | worst error |
|---|---:|---:|---:|
| Repo `last_commit` | **7** | 109 | 3.4 h |
| Developer last-commit | 54 | 19,448 | 12.9 h |
| Per-file latest in `commit_files` | 411 | 218,101 | 16.7 h |
| Commits on the wrong side of a 90-day filter | 26 | 254,997 | — |

The coarser the aggregation, the more likely it is wrong: a single file has few
commits and rarely a near tie, while a repo aggregates thousands.

## The form used

```sql
strftime('%Y-%m-%dT%H:%M:%SZ', MAX(unixepoch(committer_when)), 'unixepoch')
```

Three constraints forced this shape, each verified rather than assumed:

1. **`MAX(unixepoch(...))` alone returns an INTEGER.** `days_ago()` and
   `new Date(node.last_commit)` in `bubble.html` both need a parseable
   timestamp, so a naive swap breaks the UI.
2. **`unixepoch()`, not `strftime('%s', ...)`.** `strftime('%s', ...)` returns
   TEXT — a digit string — so ordering by it is *still* a text sort. It works
   today only because every epoch here is exactly 10 digits, and stays right
   until 2286. That is the same class of latent bug being fixed.
3. **The SQLite bare-column form can't be the general answer.**
   `SELECT MAX(unixepoch(x)), x` does return the winning row's original string,
   but SQLite documents the behaviour as undefined with more than one min/max
   aggregate — and several queries select `first_commit` and `last_commit`
   together. Confirmed empirically: with both, the bare column follows only one.

## Trade-off: these aggregates now render in UTC

```
before:  2026-07-10T16:45:56+09:00      (committer's local offset)
after:   2026-07-10T07:45:56Z           (same instant, UTC)
```

Same instant, same `days_ago`, same status bucket — different presentation. A
column of repo dates in mixed offsets was not comparable by eye anyway, and a
reader silently misread `+09:00` against `-07:00`. The raw `committer_when` on
each row keeps its original offset, so nothing is lost from the data.

## The helper

`KospexData` gains three methods, so the policy lives in one place:

| method | replaces |
|---|---|
| `select_latest_date(column, alias)` | `select_as("MAX(committer_when)", ...)` |
| `select_earliest_date(column, alias)` | `select_as("MIN(committer_when)", ...)` |
| `order_by_date(column, direction)` | `order_by()` on a `*_when` column |

`select_as()` could not be extended: its `extract_select_function_parts()` regex
is `^(\w+)\(([^)]+)\)$`, which cannot parse a nested call.

18 sites converted through the helper; the remaining raw-SQL strings in
`kospex_query.py`, `kospex_core.py`, `kospex_git.py` and `kospex_mergestat.py`
were edited to the same form. No live `MAX`/`MIN`/`ORDER BY` on a bare `*_when`
column remains.

## What was deliberately NOT done

**No tiebreak.** An earlier draft proposed
`ORDER BY unixepoch(x) DESC, committer_when DESC` to make exact-second ties
deterministic — 1,479 files have an ambiguous latest commit. Investigation
showed it solves nothing real: on a tie the *date is identical by definition*,
`file_metadata` content comes from panopticas and scc, and nothing joins on
`file_metadata.hash` (the only attempts are commented out at
`kospex_query.py:1273` and `:1556`). Only the stored hash differs, and both
candidates are legitimately "the last commit at that instant". If those joins
are ever activated, `, hash DESC` is a one-word addition.

**No NULL policy.** `unixepoch()` returns NULL for an unparseable offset — the
live DB has 4 commits at `+518:00`, which git accepted. `MAX` ignores NULLs, and
no repo or author group is *entirely* unparseable (0 of them), so no aggregate
returns NULL. In `ORDER BY` they sort last, which is right for a date that
cannot be read.

## Performance

Median of 7, three runs, against the live 665 MB database.

| query | text (before) | after |
|---|---:|---:|
| `GROUP BY _repo_id` [109 groups] | 120–228 ms | 137–145 ms |
| MIN + MAX together [109 groups] | 124–161 ms | 145–148 ms |
| whole table, 1 group | ~23 ms | ~34 ms |
| `GROUP BY author_email` [19,448 groups] | ~120 ms | ~220 ms |

The per-row `unixepoch()` costs ~37 ns; the outer `strftime` runs once per
*group*, so it is free except where groups are numerous — hence the one
regression on the developer-grouped query. Note the text variant swung
120→228 ms across runs while the new form stayed within ~10 ms: integer
comparison is more predictable than 25-character string comparison.

No date column is indexed today, so wrapping the column in a function forfeits
nothing — these queries already full-scan. An indexed epoch column would make
all of them faster than before, and is the obvious next step if the
developer-grouped query ever matters.

## Verification

All 7 repos whose `last_commit` was wrong now return the true latest:

```
github.com~apache~arrow          was 2026-07-10T16:45:56+09:00  now 2026-07-10T08:49:58Z
github.com~fastapi~fastapi       was 2026-07-07T21:02:48+02:00  now 2026-07-07T19:03:19Z
github.com~python-hyper~h11      was 2025-04-24T21:04:03+01:00  now 2025-04-24T23:29:20Z
github.com~pyca~cryptography     was 2026-07-10T10:13:21Z       now 2026-07-10T11:21:39Z
github.com~kjd~idna              was 2026-07-01T15:28:23Z       now 2026-07-01T17:26:14Z
github.com~jaraco~jaraco.functools was 2026-05-15T21:10:03Z     now 2026-05-15T21:31:00Z
github.com~fastapi~annotated-doc was 2026-07-04T22:03:10+02:00  now 2026-07-04T20:03:37Z
```

Each checked against `ORDER BY unixepoch(committer_when) DESC LIMIT 1`.

## Files changed

- `src/kospex_query.py` — the three helpers, plus converted sites
- `src/kospex_core.py`, `src/kospex_git.py`, `src/kospex_mergestat.py` — raw SQL
  (the `kospex_mergestat.py` line was in dead code — the module had been orphaned
  since 2023 and was deleted shortly after, see
  `202608-remove-kospex-mergestat.md`)
- `tests/test_date_ordering.py` — 11 tests

## Note

No backfill needed. This changes how stored data is *read*, not what is stored.
