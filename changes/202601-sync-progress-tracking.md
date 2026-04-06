# Add Sync Progress Tracking to DuckDB

**Date:** 2026-01-14
**Status:** Implemented

## Summary

Added comprehensive progress tracking for git ingestion operations, including real-time status monitoring, sync history/audit log, resume capability support, and detailed metrics. Progress is stored in DuckDB and can be queried during or after sync operations.

## Features

1. **Real-time sync status**: Query active sync progress from external tools during long syncs
2. **Sync history/audit log**: Record completed sync operations with stats (commits, duration, errors)
3. **Resume capability**: Track progress to enable resuming interrupted syncs from the last successful batch
4. **Metrics/statistics**: Store detailed metrics per sync (timing per phase, encoding errors)

## Schema Design

### New Tables

**sync_operations** - Audit log of all sync operations
- Tracks sync_id, repo_id, start/end times, status, and final stats
- Records encoding errors, commit counts, file counts
- Stores resume information (last_successful_batch, resume_from_hash)

**sync_progress** - Real-time status (one row per active sync)
- Tracks current phase (branches, extraction, commit_insert, file_insert)
- Progress: current_batch, total_batches, items_processed, total_items
- Rolling average of batch duration for ETA estimation

**sync_metrics** - Detailed performance metrics
- Phase timing (duration of each sync phase)
- Error records (encoding issues with details)

## Files Modified

### `/src/kospex/git_duckdb.py`

**Added:**
- SQL schema constants: `SQL_CREATE_SYNC_OPERATIONS`, `SQL_CREATE_SYNC_PROGRESS`, `SQL_CREATE_SYNC_METRICS`
- `PROGRESS_UPDATE_EVERY_N_BATCHES = 5` constant
- `SyncProgressTracker` class with methods:
  - `start_sync()` - Initialize sync operation record
  - `update_phase()` - Track phase transitions with timing
  - `update_batch_progress()` - Update progress every N batches
  - `record_encoding_error()` - Log encoding issues
  - `complete_sync()` - Finalize with stats
  - `fail_sync()` - Record failure

**Query methods on GitDuckDB:**
- `get_active_sync(repo_id)` - Query real-time progress
- `get_sync_history(repo_id, limit)` - Query history
- `get_interrupted_sync(repo_id)` - Find resumable sync

**Modified:**
- `create_schema()` - Now creates progress tracking tables
- `insert_commits_batch()` - Added optional `progress_tracker` parameter
- `insert_commit_files_batch()` - Added optional `progress_tracker` parameter

### `/src/kospex/git_ingest.py`

**Added:**
- Import for `SyncProgressTracker`
- `_get_head_hash()` helper method

**Modified:**
- `_extract_commits()` - Added optional `tracker` parameter for encoding error recording
- `sync()` - Added `track_progress` parameter (default: True), integrated progress tracking throughout

## Usage

### During Sync
Progress is automatically tracked when `track_progress=True` (the default). Query active progress:

```sql
-- Check active sync progress
SELECT * FROM sync_progress WHERE repo_id = 'github.com~torvalds~linux';

-- Get progress percentage
SELECT
    phase,
    items_processed,
    total_items,
    ROUND(items_processed * 100.0 / NULLIF(total_items, 0), 1) as percent_complete,
    avg_batch_duration_ms
FROM sync_progress
WHERE repo_id = 'github.com~torvalds~linux';
```

### After Sync
Query sync history:

```sql
-- Recent sync history
SELECT
    repo_id,
    status,
    sync_type,
    commits_inserted,
    files_inserted,
    encoding_errors,
    started_at,
    completed_at
FROM sync_operations
ORDER BY started_at DESC
LIMIT 10;

-- Get phase timing metrics for a sync
SELECT metric_name, duration_ms
FROM sync_metrics
WHERE sync_id = '<sync_id>'
AND metric_type = 'phase_timing';
```

### Python API
```python
from kospex.git_duckdb import GitDuckDB

db = GitDuckDB()
db.connect()

# Query active sync
progress = db.get_active_sync('github.com~torvalds~linux')
if progress:
    print(f"Phase: {progress['phase']}")
    print(f"Progress: {progress['items_processed']}/{progress['total_items']}")

# Query history
history = db.get_sync_history('github.com~torvalds~linux', limit=5)
```

## Performance Considerations

- Progress updates to database occur every 5 batches (5000 items) to minimize I/O overhead
- Batch timing is tracked in-memory with rolling average
- Phase timing recorded at phase transitions only
- Estimated overhead: ~1-2ms per progress update when triggered

## Verification

All 126 tests pass:
```bash
pytest -v  # 126 passed
```

Manual verification:
1. Run `kgit sync-repo` on a repository with `--verbose`
2. Query `sync_progress` table during sync
3. Query `sync_operations` table after sync completes
4. Test interrupt (Ctrl+C) and verify status = 'running'

## Bug Fixes

### 2026-01-15: Fix sync_metrics NOT NULL constraint error

**Issue**: DuckDB doesn't auto-increment `INTEGER PRIMARY KEY` like SQLite does.

**Error**: `Constraint Error: NOT NULL constraint failed: sync_metrics.id`

**Fix**: Removed `id INTEGER PRIMARY KEY` from `sync_metrics` table schema. The table doesn't require an explicit ID column since metrics are identified by `sync_id + metric_type + metric_name + completed_at`.

**Action Required**: If you encounter this error, delete and recreate the DuckDB database:
```bash
rm ~/kospex/kospex-git.duckdb
kgit init-duckdb
```
