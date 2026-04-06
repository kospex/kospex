# Fix Unicode Encoding Errors in GitIngest

**Date:** 2026-01-14
**Status:** Implemented

## Summary

Fixed a `UnicodeDecodeError` that occurred when syncing repositories with non-UTF-8 characters in commit history (e.g., the Linux kernel repository). Added logging to track when encoding errors occur.

## Problem

Running `kgit sync-repo` on the Linux kernel repository failed with:

```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 408831988: invalid continuation byte
```

The error occurred because:
1. The `subprocess.run()` call used `text=True`, which decodes output as UTF-8 by default
2. The Linux kernel (and other long-lived repositories) contains commits with author names or messages encoded in Latin-1 or other legacy encodings
3. These non-UTF-8 bytes caused the decode to fail

## File Modified

`src/kospex/git_ingest.py`

## Changes

### 1. Added logging import and module logger (lines 20, 23)

```python
from kospex_utils import get_kospex_logger

# Module logger
logger = get_kospex_logger('git_ingest')
```

### 2. Updated `_extract_commits()` subprocess handling (lines 175-192)

Changed from:
```python
result = subprocess.run(
    cmd,
    capture_output=True,
    text=True,
    cwd=self.repo_path,
    check=True
)
```

To:
```python
result = subprocess.run(
    cmd,
    capture_output=True,
    cwd=self.repo_path,
    check=True
)

# Decode with error handling for non-UTF-8 characters in old commits
# Some repositories (e.g., Linux kernel) have commits with Latin-1 or other encodings
try:
    output = result.stdout.decode('utf-8')
except UnicodeDecodeError as e:
    logger.warning(
        f"Encoding errors in git log output for {self.repo_id}: {e}. "
        "Some characters will be replaced. This is common in repositories "
        "with old commits using legacy encodings (e.g., Latin-1)."
    )
    output = result.stdout.decode('utf-8', errors='replace')
```

## Behavior

- **Clean repositories**: Decode normally with strict UTF-8
- **Repositories with legacy encodings**: Log a warning and replace invalid bytes with the Unicode replacement character (U+FFFD)
- **Log location**: `~/kospex/logs/git_ingest.log`

## Example Log Output

```
2026-01-14 10:30:15 [ WARNING] [git_ingest] Encoding errors in git log output for github.com~torvalds~linux: 'utf-8' codec can't decode byte 0xe9 in position 408831988: invalid continuation byte. Some characters will be replaced. This is common in repositories with old commits using legacy encodings (e.g., Latin-1).
```

## Notes

- The replacement character approach preserves all commit data except the specific non-UTF-8 characters
- This is a common issue in repositories predating UTF-8 standardization
- Affected characters are typically accented letters in author names (e.g., Latin-1 `0xe9` = 'é')
