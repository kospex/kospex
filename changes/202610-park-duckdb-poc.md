# Park the DuckDB proof of concept

## Overview

The DuckDB git ingest (January 2026, #86) was an experiment to see whether DuckDB
could store `commits` and `commit_files` instead of SQLite. It was never wired
into reporting or the web UI: `kospex.db` (SQLite) is the store. DuckDB is still
being researched for feasibility and usefulness, so the code is parked rather
than deleted.

## What changed

| File | Change |
|---|---|
| `src/kgit.py` | `kgit sync-repo` commented out |
| `src/kospex_cli.py` | `kospex init-duckdb` commented out |
| `pyproject.toml` | `duckdb>=1.5.6,<2` commented out, with a note |
| `CLAUDE.md` | Command list says both are parked |
| `CHANGELOG.md` | *Changed* entry |

## Left in place, deliberately

- `src/kospex/git_duckdb.py` and `src/kospex/git_ingest.py`, still importable
  from the `kospex` package through its lazy `__getattr__`. `git_duckdb.py`
  imports `duckdb` at module level, so using them needs `duckdb` installed
  separately. Nothing else in kospex imports them.
- `HabitatConfig.duckdb_path` and `KOSPEX_DUCKDB`: path config only, no import.
- `docs/data-schemas.md`, which documents the DuckDB schema.
- Any existing `~/kospex/kospex-git.duckdb` on disk.

## Restoring

Uncomment the two command blocks and the dependency. The blocks are unchanged
apart from the `# ` prefix.
