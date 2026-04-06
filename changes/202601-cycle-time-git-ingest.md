# Add Cycle Time Calculation to GitIngest

**Date:** 2026-01-14
**Status:** Implemented

## Summary

Added cycle time calculation (in seconds) between `author_when` and `committer_when` timestamps in the GitIngest module. The `_cycle_time` field previously existed but was hardcoded to `0`.

## Background

**Cycle time** represents the time difference between when code was authored versus when it was committed. This metric is useful because:

- Most commits have `author_when == committer_when` (cycle time = 0)
- Rebased commits will have a later `committer_when` than `author_when`
- Cherry-picked commits show when code was originally written vs applied
- Squashed commits reflect the merge timing vs original authoring
- Can indicate workflow patterns (e.g., long review cycles, batch commits)

Both timestamps come from git log in ISO 8601 format using `%aI` (author) and `%cI` (committer) format specifiers.

## File Modified

`src/kospex/git_ingest.py`

## Changes

### 1. Added datetime import (line 11)

```python
from datetime import datetime
```

### 2. Added helper method `_calculate_cycle_time()` (lines 63-78)

```python
def _calculate_cycle_time(self, author_when: str, committer_when: str) -> int:
    """Calculate cycle time in seconds between author and committer timestamps.

    Args:
        author_when: ISO 8601 datetime when code was authored
        committer_when: ISO 8601 datetime when code was committed

    Returns:
        Cycle time in seconds (committer - author). Returns 0 if parsing fails.
    """
    try:
        author_dt = datetime.fromisoformat(author_when)
        committer_dt = datetime.fromisoformat(committer_when)
        return int((committer_dt - author_dt).total_seconds())
    except (ValueError, TypeError):
        return 0
```

### 3. Updated `_extract_commits()` to use the calculation (line 259)

Changed from:
```python
"_cycle_time": 0
```

To:
```python
"_cycle_time": self._calculate_cycle_time(author_datetime, committer_datetime)
```

## Verification

### Unit Tests
All 126 tests pass:
```bash
pytest -v
```

### Manual Verification
After syncing a repository, query the DuckDB to verify cycle time values:

```sql
-- Find commits with non-zero cycle time (rebased/cherry-picked)
SELECT hash, author_when, committer_when, _cycle_time
FROM commits
WHERE _cycle_time != 0
LIMIT 10;

-- Distribution of cycle times
SELECT
    CASE
        WHEN _cycle_time = 0 THEN '0 (same time)'
        WHEN _cycle_time < 3600 THEN '< 1 hour'
        WHEN _cycle_time < 86400 THEN '< 1 day'
        ELSE '1+ days'
    END as cycle_category,
    COUNT(*) as count
FROM commits
GROUP BY 1
ORDER BY 2 DESC;
```

## Notes

- The calculation returns 0 if timestamp parsing fails (defensive handling)
- Negative cycle times are theoretically possible if `author_when > committer_when` (rare edge case with clock skew)
- Uses Python's built-in `datetime.fromisoformat()` which handles ISO 8601 with timezone info
