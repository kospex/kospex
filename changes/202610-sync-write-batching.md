# Sync write batching: one transaction per repo sync

**Status:** spec / not yet implemented
**Date:** 2026-10
**Tracked in:** kospex/kospex#305
**Prerequisite:** kospex/kospex#119, `changes/202607-sqlite-wal-busy-timeout.md`
**Related:** `changes/202610-sync-time-summary-stats.md` (read-side performance and the `idx_commits_repo_author_when` index, #304)
**Planned release:** a dot release following the next release

## Problem

### Per-row transactions dominate sync time

`Kospex.sync_repo()` (`src/kospex_core.py`) writes one row at a time:

```python
for commit in commits:
    ...
    self.kospex_db.table(KospexSchema.TBL_COMMITS).upsert(commit, pk=["_repo_id", "hash"])
    for file_info in commit_files:
        ...
        self.kospex_db.table(KospexSchema.TBL_COMMIT_FILES).upsert(
            file_info, pk=["file_path", "_repo_id", "hash"]
        )
```

In sqlite-utils 4.x, every `upsert()` wraps itself in `db.atomic()`. With no outer transaction open, that is a separate `BEGIN` / `COMMIT` per row. The DB runs in the default rollback-journal mode with `synchronous=FULL`, so **every row costs a journal write and an fsync**.

### An interrupted sync permanently loses commits

`git log` lists newest first, and each row commits on its own. A sync killed partway (Ctrl-C, a crash, a reboot, a laptop going to sleep) therefore leaves the **newest** commits stored and the older ones absent. The next incremental sync starts from `get_latest_commit_datetime()`, which is `MAX(committer_when)`, so it never goes back for them. Nothing logs an error, and later syncs update `last_sync` as normal.

Reproduced with a throwaway repo of 10 commits on consecutive days, killing the sync after 3 commit upserts:

```
commits in DB after interrupted sync: 3 of 10
commits in DB after the next normal sync: 3 of 10
```

A partly written commit is also possible: a commit row can be stored while its `commit_files` rows are missing, if the process dies between the two writes.

## Benchmark

Production rows from one repo (TypeScript) were re-inserted under a throwaway `_repo_id` into a copy of a 1.1M-commit, 374-repo DB. Setup: macOS, sqlite-utils 4.2.1, `journal_mode=delete`, `synchronous=FULL`, best of two runs. Each run's row count was asserted before cleanup.

| Method | `commits` (20,000 rows) | `commit_files` (609,590 rows) |
|---|---|---|
| One `upsert()` per row (today) | 11.12s (556µs/row) | **362.69s** (595µs/row) |
| One `upsert()` per row, inside one `db.atomic()` | 1.75s (**6.4x**) | 52.24s (**6.9x**) |
| `upsert_all(batch_size=500)` | 0.53s (**21.2x**) | 12.61s (**28.8x**) |

Most of the cost is the per-row commit, not the SQL: wrapping the existing loop in a single transaction already removes about 85% of it. `upsert_all` then removes most of the remaining per-statement overhead.

**Projected scale:** a 1,500-repo estate at about 4.6M commits and about 30M `commit_files` rows (today's ratio of 6.6 file rows per commit).

| | `commits` | `commit_files` |
|---|---|---|
| Today | about 43 minutes | about 5 hours |
| `upsert_all` | about 2 minutes | about 11 minutes |

These are projections from the per-row rates, not measured at that scale. They cover only the DB writes: `git log`, `file_metadata` and the developer-stats rebuild come on top.

## Design

### 1. One transaction per repo sync

Everything `sync_repo()` writes to describe the commit walk commits together, or not at all:

```python
with self.kospex_db.atomic():
    self.kospex_db.table(KospexSchema.TBL_COMMITS).upsert_all(
        commit_rows, pk=["_repo_id", "hash"], batch_size=BATCH)
    self.kospex_db.table(KospexSchema.TBL_COMMIT_FILES).upsert_all(
        file_rows, pk=["file_path", "_repo_id", "hash"], batch_size=BATCH)
    self.update_repo_status(last_sync=last_sync)
```

- **Inside the transaction:** commit rows, `commit_files` rows (including the merge-content rows from `merge_file_rows()`), and the `repos` row written by `update_repo_status()`, which carries `last_sync` and `file_path`.
- **Outside it, as today:** `file_metadata()`, `update_developer_stats()` and `update_developer_file_stats()`. Each is a full recompute for the repo, so an interruption there is healed by the next sync. They should become atomic in their own right (per the `202608-save-dependencies-atomic.md` pattern), but that is not part of this change.
- **Nested `atomic()` is safe.** sqlite-utils turns an inner `atomic()` into a `SAVEPOINT`, so `upsert_all`'s own wrapping nests correctly. `update_repo_status()` writes with a single `table.upsert()` and never calls `conn.commit()`, so it nests too (checked on 2026-10-23).

An interrupted sync now leaves the repo exactly as it was before the sync started. The next sync redoes the same window, which fixes the data loss above.

### 2. `upsert_all` in batches

- **Keep upsert, not insert.** An incremental sync re-reads commits at its `--since` boundary (the boundary is inclusive), so rows that already exist are re-sent. `insert_all` would fail on the primary key.
- **The keys must match within each table.** `upsert_all` takes its column list from the first batch. Commit dicts all come from `parse_commit_log()` plus `add_git_to_dict()`, so they have identical keys. File rows from `--numstat` and from `merge_file_rows()` both carry `file_path`, `path_change`, `additions` and `deletions`, then gain the same `hash`, `_ext`, `committer_when` and git fields. A test should pin this, because a key added to only one path would silently become NULL for the other.
- **Batch size and SQLite 3.26.** `SQLITE_MAX_VARS` is 999 in sqlite-utils, and it shrinks each batch so that rows × columns stays within that limit. That keeps it inside the 3.26 floor (RHEL 8) automatically. Pass `batch_size=500` as the upper bound, and run the new tests under the 3.26 guard in `tests/conftest.py`.
- **Memory.** `parse_commit_log()` already holds the whole `git log` output and every commit dict in memory, and `results` keeps a copy of each commit, so building the row lists adds little. For a very large first sync (hundreds of thousands of commits), streaming the batches into the open transaction would bound memory without changing atomicity. Keep that as an option if measurements call for it.

### 3. Progress output

The `+` printed per commit, and the "Synced N commits so far" line every 500, happen while rows are written. Under batching:
- print the commit count once `git log` has been parsed (`Writing N commits, M file rows...`);
- print one line per batch for repos above a threshold (e.g. every 10,000 file rows), so a large first sync still shows signs of life;
- keep `Synced N total commits` at the end.

Callers that capture stdout (tests, `kgit clone`) rely on the final line only, so check that before changing it.

### 4. Locking: why #119 is a prerequisite

A single transaction holds the write lock for the whole write phase. Measured above, that is about 21µs per `commit_files` row, so **around 20s for a repo with about 1M file rows**, even after batching.

- **Rollback-journal mode (today):** readers can continue while the writer holds `RESERVED`. When the transaction commits, or when its page cache spills, it needs `EXCLUSIVE`, which waits for readers to finish and then blocks new ones. With no `busy_timeout` set, kweb, CLI and `krunner` reads that collide with it fail immediately with `database is locked`. A second concurrent writer fails immediately as well.
- **WAL with `busy_timeout`:** readers never block on the writer, and a second writer waits up to the timeout instead of failing.

So #119 (`changes/202607-sqlite-wal-busy-timeout.md`) should land first, or in the same release. Without it, this change trades a slow sync for intermittent lock errors in kweb while a large repo syncs.

The single-writer limit stays: two concurrent syncs still serialise on the lock. With `busy_timeout`, the second one waits up to the timeout and then fails. A long first sync of a huge repo can exceed a 5s timeout, so concurrent ingest either needs a longer timeout for writers or per-batch transactions (see the alternatives).

### 5. The index write cost

`idx_commits_repo_author_when` (migration `0011`, #304) made per-row commit upserts 26% slower (about 120µs per row, measured with today's one-row-per-transaction writes). That figure is dominated by the per-row transaction overhead this change removes. Re-measure it under `upsert_all`, and correct `changes/202610-sync-time-summary-stats.md` if the cost changes materially.

## Related change: choose commits by hash, not by date

This is a separate change that touches the same loop. An incremental sync picks its window with `--since=MAX(committer_when)`. A branch merged with a merge commit keeps the committer dates of its commits, so commits dated before the last sync but merged after it are excluded, and every later sync excludes them again. Squash and rebase merges are not affected, because they create new commits dated at the merge.

The fix is to compute the commits to ingest as `git rev-list HEAD` minus the hashes already stored for the repo, and pass those to `git log --no-walk --stdin --numstat`. `merge_file_rows()` would need the same hash list in place of its `--since` window. This also heals the gaps an interrupted sync left before this change. Its cost is one hash-set comparison per repo: under a second for a large repo, and about 12s across 374 repos for a read-only check of the whole DB.

The two changes can ship separately. If they ship together, the transaction in §1 wraps the hash-selected writes unchanged.

## Alternatives considered

- **Wrap the existing loop in one `db.atomic()`, without `upsert_all`.** That is a 6–7x speed-up from a few lines, and it keeps the per-commit progress output. It is a reasonable first step if `upsert_all` turns up a problem, and it fixes the data loss just the same.
- **One transaction per batch of N commits, instead of per repo.** It holds the lock for a bounded time, but a crash leaves the newest N×k commits stored, which brings back the interrupted-sync loss. It's only safe once commits are chosen by hash (above), because the next sync then fills in whatever is missing.
- **`synchronous=NORMAL` instead of batching.** It cuts the fsync cost but keeps a commit per row, and in rollback-journal mode it trades durability for speed. Not pursued.

## Files to change

- `src/kospex_core.py`: `sync_repo()` builds row lists and writes them in one transaction with `upsert_all`, and the progress output changes.
- `tests/`: new tests (below).
- `changes/202610-sync-time-summary-stats.md`: correct the index write-cost figure once it is re-measured (§5).

## Tests

- **Interrupted sync.** Monkeypatch the write path to raise after N rows. Assert no `commits`, `commit_files` or `last_sync` change for that repo. Then run a normal sync and assert the DB matches `git rev-list --count HEAD`. This turns the reproduction above into a regression test.
- **Same keys.** For a fixture repo with a merge that has content of its own (#121), every commit row has the same keys, every file row has the same keys, and the merge-content rows match the `--numstat` rows.
- **Idempotent incremental sync.** Sync twice with no new commits. Row counts are unchanged, and the boundary commit is re-sent without error.
- **Values unchanged.** Rows written by the new path match those written by the old per-row path for the same fixture repo, column for column.
- **SQLite 3.26.** All of the above run under the existing `conftest.py` guard.
- **Web tests with a live kweb**, as usual. Also, by hand, sync a large repo while loading `/` and `/repos/` in kweb, with and without #119, to confirm the locking behaviour in §4.

## Rollout

- No schema change and no migration.
- **Existing gaps from earlier interrupted syncs are not healed by this change alone.** It only prevents new ones. Healing needs the hash-based commit selection above, or a full-window re-sync of the affected repos (`sync_repo(path, from_date="1970-01-01T00:00:00Z")`). A repo with gaps can be found by comparing its stored commit count with `git rev-list --count HEAD`.
- CHANGELOG: mention the speed-up and the interrupted-sync fix, and point to #119 for the WAL change if they ship together.
