# `osi_runs.started_at` held the finish time

Issue: [#259](https://github.com/kospex/kospex/issues/259)
Migration: `0010_osi_runs_finished_at.sql`

## Overview

`osi_runs.started_at` was declared "UTC, when this repository began" and recorded
the moment the repository *finished*. `record_repo_run()` defaulted it to now, and
its only production caller runs after extraction, enrichment, save and outcome
record are all complete.

The fix captures a wall-clock start in `_process_repo` and passes it through, and
adds `finished_at` so both ends of the window exist.

## Proof it was wrong

From a whole-estate `krunner osi -next 400` on a few-hundred-repository estate:

| | |
|---|---|
| run's own log line | `run <id> starting, limit=400, queued=374` at `15:08:22Z` |
| earliest `started_at` for that run | `15:22:56Z` — 14m34s later |
| that row's `duration_ms` | `874,159 ms` = 14m34s |

`15:08:22 + 14:34 = 15:22:56`. The first repository's `started_at` was exactly the
run start plus its own duration, which is its finish.

## Why both ends, rather than renaming the column

Neither end is derivable from the other, because `duration_ms` is measured with
`time.monotonic()` and that does not advance while the machine is asleep. On the
sweep above, **1h51m of a 5h34m wall clock was system sleep** across 12 windows.
So `finished_at - duration_ms` is not the start, and `started_at + duration_ms` is
not the finish.

One end plus a duration measures **cost**. Two ends measure **occupancy**. The
difference between them is time the process was not running — which is otherwise
only recoverable from operating-system power logs that rotate away, and was how
the sweep's missing 1h51m was found in the first place.

Three things this makes possible, none of which were:

1. **Queue wait** — when a repository was dequeued versus when work began.
2. **Correlating a slow repository with what else was happening**, which needs the
   window it occupied, not one edge of it.
3. **Ordering within a run.** `ORDER BY started_at` returned completion order.
   Identical for a serial run, and wrong the moment anything runs concurrently.

## Files changed

- `src/kospex/db/migrations/0010_osi_runs_finished_at.sql` — new; adds
  `finished_at TEXT`.
- `src/kospex/osi_run_log.py` — `_row_for()` carries `finished_at`;
  `record_repo_run()` and `record_run()` take it. **`started_at` no longer
  defaults to now.**
- `src/kospex/osi_next.py` — `_process_repo` stamps a wall-clock start beside the
  existing monotonic one and passes it to `record_repo_run`.
- `tests/test_osi_runs_started_at.py` — new; 7 tests.
- `tests/test_db_health.py`, `tests/test_kospex_schema.py`,
  `tests/test_kospex_utils.py` — migration counts 7 → 8, and the new id added to
  the expected applied list.

## Implementation notes

**The default was the defect.** `started_at or _utc_now_iso()` meant a caller that
omitted the start got a confident wrong answer. It now writes NULL, which means
unknown. A `setdefault`-shaped guard cannot help a value that is always supplied
by the wrong clock — the fix is to stop supplying it.

`finished_at` *does* default to now, because the row is written at the finish, so
now genuinely is the finish. The asymmetry is the point.

`_utc_now_iso` is imported from `osi_run_log` rather than re-implemented in
`osi_next`, so the two timestamps cannot drift to different formats.

**`record_run()` (the batch variant) writes NULL for `started_at`.** A
`BatchResult` holds no per-repository start, so there is nothing honest to put
there. It has no production callers — `-next` uses `record_repo_run` per
repository — and giving it a real per-repo start was out of scope.

### Pre-fix rows are left alone

Rows written before this carry a `started_at` that is really a finish, and no
backfill is attempted. `0008` has not shipped in a release, so the only such rows
are in development databases. They cannot be corrected anyway: subtracting
`duration_ms` recovers the true start only for a repository that ran entirely
while the machine was awake, and the sweep that produced them spanned 12 sleep
windows. Rewriting them would make history look like it was always right.

`DELETE FROM osi_runs` is the clean option for a development database — the table
is diagnostics and nothing reads it.

### Testing

Timestamps are second-resolution, so telling a start from a finish needs the
repository to take longer than that. `tests/test_osi_runs_started_at.py` patches
`osi_next.extract_repo` to sleep 1.2s, which is why that module is the slow one.

The core test drives `run_next_batch`, not `record_repo_run`, so it still fails if
the parameter is honoured but the call site omits it — which was the actual bug.
Before the fix it reported `started_at '…:21Z' is not the start (window was
…:20Z .. …:21Z)`.

`test_a_supplied_start_is_stored_verbatim` passed before the fix, deliberately
kept: it documents that the parameter already existed and worked, which is why the
change is three lines of production code rather than a new mechanism.
