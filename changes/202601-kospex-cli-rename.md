# Fix: Rename kospex.py to kospex_cli.py

**Date:** 2025-01-15

## Problem

The `kospex` CLI command was failing with an import error:

```
Traceback (most recent call last):
  File ".venv/bin/kospex", line 3, in <module>
    from kospex import cli
ImportError: cannot import name 'cli' from 'kospex' (src/kospex/__init__.py)
```

## Root Cause

A naming collision between:
- `src/kospex/` - the new package directory containing `GitIngest` and `GitDuckDB`
- `src/kospex.py` - the CLI module containing the `cli` function

Python's module resolution finds package directories before modules with the same name. When the entry point script tried `from kospex import cli`, it found the package's `__init__.py` (which has no `cli` function) instead of `kospex.py`.

## Solution

1. Renamed `src/kospex.py` to `src/kospex_cli.py`
2. Updated `pyproject.toml` script entry:
   ```toml
   # Before
   kospex = "kospex:cli"

   # After
   kospex = "kospex_cli:cli"
   ```

## Benefits

- The `kospex` package name remains clean for imports: `from kospex import GitIngest, GitDuckDB`
- The CLI entry point works correctly via `kospex_cli:cli`
- No changes required to existing code that imports from the `kospex` package

## Files Changed

- `src/kospex.py` → `src/kospex_cli.py` (renamed)
- `pyproject.toml` (updated script entry)
