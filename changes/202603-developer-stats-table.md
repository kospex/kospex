# Developer Stats Table for Key Person Analysis

## Overview

New `developer_stats` table to store precomputed per-developer commit and file-level statistics per repository. Designed to support "key person risk" analysis without re-running aggregate queries on every request.

## Table: `developer_stats`

| Column | Type | Description |
|---|---|---|
| `author_email` | TEXT | Developer email (from git commits) |
| `total_commits` | INTEGER | Total commits all time for this repo |
| `additions` | INTEGER | Total line additions all time |
| `deletions` | INTEGER | Total line deletions all time |
| `unique_files` | INTEGER | Unique files changed all time |
| `pct_all_time` | REAL | Percentage of total repo commits all time |
| `first_commit` | TEXT | Date of first commit in this repo |
| `last_commit` | TEXT | Date of most recent commit in this repo |
| `commits_last_90_days` | INTEGER | Commits in the last 90 days at time of update |
| `additions_90_days` | INTEGER | Line additions in the last 90 days |
| `deletions_90_days` | INTEGER | Line deletions in the last 90 days |
| `unique_files_90_days` | INTEGER | Unique files changed in the last 90 days |
| `pct_90_days` | REAL | Percentage of total repo commits in the last 90 days |
| `last_update` | TEXT | When this row was last computed (sync time) |
| `stat_type` | TEXT | Type of stats: "ALL_TIME", future: "weekly", "monthly" |
| `from_date` | TEXT | Date range start (NULL for ALL_TIME) |
| `to_date` | TEXT | Date range end (NULL for ALL_TIME) |
| `_git_server` | TEXT | Git server (e.g. github.com) |
| `_git_owner` | TEXT | Repository owner/org |
| `_git_repo` | TEXT | Repository name |
| `_repo_id` | TEXT | Normalised repo identifier |

**Primary Key:** `(_repo_id, author_email)`

Note: When weekly/monthly stat_type support is added, the PK will need to become `(_repo_id, author_email, stat_type, from_date)`.

## Schema constant

- `TBL_DEVELOPER_STATS = "developer_stats"` in `kospex_schema.py`
- Added to `KOSPEX_TABLES`, `REPO_TABLES`, and `DB_CREATE_STATEMENTS`
- Created in `connect_or_create_kospex_db()`

## KospexQuery methods

### `update_developer_stats(repo_id)`

Populates or refreshes the table for a given repo. Runs a single aggregate query against the `commits` table with a conditional SUM for the 90-day commit count. Deletes existing ALL_TIME rows for the repo and inserts fresh stats with `stat_type = "ALL_TIME"`. After inserting all rows, computes `pct_all_time` and `pct_90_days` for each developer by summing total commits and 90-day commits across all developers in the repo and calculating each developer's share as a percentage. Returns the number of developer rows written.

### `update_developer_file_stats(repo_id)`

Updates existing developer_stats rows with file-level metrics by joining `commits` to `commit_files`. Computes `additions`, `deletions`, `unique_files` (all time and 90-day variants). Must be called after `update_developer_stats` which creates the rows.

### `has_developer_stats(repo_id)`

Quick boolean check whether stats exist for a repo.

### `get_developer_stats(repo_id=None)`

Retrieve stats ordered by `total_commits DESC`, optionally filtered by `repo_id`.

## Sync integration

Both methods are called automatically at the end of `kospex_core.sync_repo()`:

```python
repo_id = self.git.get_repo_id()
if repo_id:
    self.kospex_query.update_developer_stats(repo_id)       # commits table only
    self.kospex_query.update_developer_file_stats(repo_id)   # joins commit_files
```

This runs after commit sync and file metadata processing, so all data is available.

## CLI command

`kospex stats <REPO_ID>` — displays a Rich table of developer stats for the given repo. Shows all columns including 90-day activity, plus `% All Time` and `% 90 Days` percentage columns. Added in `kospex_cli.py`.

## Implementation notes

- Raw `sqlite_utils` `.execute()` calls do not auto-commit. Both `update_developer_stats` and `update_developer_file_stats` call `self.kospex_db.conn.commit()` after writes.
- `update_developer_file_stats` must run after `update_developer_stats` (it UPDATEs rows that the first method INSERTs).
- On large repos, stats computation may be slow. A future `-no-stats` flag on clone/sync-all could allow deferring stats to a separate process.

## Triggered by

Developer stats are auto-updated via `sync_repo()` which is called by:
- `kgit clone` (with sync flag)
- `kospex sync-directory`
- `kgit sync-all`

Note: there is currently no standalone `kospex sync <repo>` command.

## Also included

- `file_hotspots` table schema added to `kospex_schema.py` (not yet populated)
- `KospexData.set_params_by_id()` enhanced to accept a `request_id` string directly

## Files changed

- `src/kospex_schema.py` -- Table constant, CREATE statement, registration in lookup dicts and DB init
- `src/kospex_query.py` -- `update_developer_stats`, `update_developer_file_stats`, `has_developer_stats`, `get_developer_stats` methods, `set_params_by_id` enhancement
- `src/kospex_core.py` -- Sync integration at end of `sync_repo()`
- `src/kospex_cli.py` -- `kospex stats` command with Rich table output
