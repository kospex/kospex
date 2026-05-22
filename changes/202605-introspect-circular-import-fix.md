# Fix: circular import via kospex package init (kweb startup)

## Overview

`kweb` failed to start with:

```
ImportError: cannot import name 'KospexQuery' from partially initialized module
'kospex_query' (most likely due to a circular import)
```

CLI commands (`kgit clone`, `krunner osi`, `kospex summary`) worked; only the
web entry point tripped over it, because its first import path reached the
`kospex` package while `kospex_query` was still mid-import.

## Root cause

Commit `49f52f6` (runtime table introspection) added to `kospex_schema.py`:

```python
from kospex.db.introspect import get_kospex_tables
```

Importing that submodule forces `kospex/__init__.py` to execute, and the package
init was **eagerly** importing the heavy legacy classes:

```python
from .git_ingest import GitIngest      # -> kospex_git -> kospex_query
from .git_duckdb import GitDuckDB
from .habitat_config import HabitatConfig
```

So the cycle was:

```
kospex_query -> kospex_schema -> kospex.db.introspect
   -> (runs kospex/__init__.py) -> git_ingest -> kospex_git -> kospex_query  (partial!)
```

`introspect.py` is fully self-contained (pure SQLite reads, no kospex imports),
so a lightweight DB helper should never have pulled in the git-ingest stack. The
CLI worked only by luck of import ordering — its entry modules fully loaded
`kospex_query` before `kospex_schema` triggered the package init.

## Fix

`src/kospex/__init__.py` — replaced the eager top-level imports with lazy
resolution via PEP 562 `__getattr__`. `from kospex import GitIngest` still works
and resolves on first access, but importing a submodule such as
`kospex.db.introspect` no longer drags in `git_ingest -> kospex_git ->
kospex_query`, so the cycle never forms.

## Files changed

- `src/kospex/__init__.py` — lazy `__getattr__` exports for `GitIngest`,
  `GitDuckDB`, `HabitatConfig` instead of eager imports.

## Verification

- `kweb --help` starts cleanly (previously crashed on import).
- `import kospex.db.introspect` no longer pulls in the git stack; the lazy
  exports still resolve.
- `pytest` — 243 passed, 67 skipped.
