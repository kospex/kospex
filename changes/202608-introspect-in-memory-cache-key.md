# Introspection cache served stale tables for in-memory databases

## Overview

`get_kospex_tables()` keyed its cache by `id(db)` for in-memory databases. `id()`
is unique only among *live* objects, so a new database landing on a freed
address inherited the dead one's table set. Since `KospexData` validates every
table name against that cache before interpolating it into SQL, the result was a
query builder that rejected tables which plainly existed.

Closes #184.

## Problem

`_db_key()` fell back to a "per-instance key":

```python
return file_path or f"<mem:{id(db)}>"
```

The intent is clear from the docstring, but `id()` does not provide instance
identity across a lifetime — CPython reuses addresses, and does so aggressively.
Measured over 300 in-memory databases, each created after the previous was
garbage collected:

```
stale cache hits: 292 / 300
  first at iteration 5
    get_kospex_tables() said : ['tbl_3']
    database actually has    : ['tbl_5']
distinct cache keys used: 8
```

Eight distinct keys for 300 databases. Address reuse is not an edge case here,
it is the normal outcome.

## How it surfaced

`KospexData.from_table()` uses this cache as a SQL-injection guard, checking a
table name against the live schema before interpolating it:

```python
valid = get_kospex_tables(self.kospex_db)
for table in tables:
    if table not in valid:
        raise ValueError(f"Table '{table}' is not a known Kospex table")
```

A stale answer therefore rejects a table that exists. It appeared as
`tests/test_merge_authorship.py::test_merge_carrying_its_own_content_is_authorship`
failing roughly one full-suite run in six while passing every time in isolation
— the flake needs a *previous* in-memory database to have been created and
freed, which only happens when other tests run first.

Reproduced deterministically by replaying that chain — an in-memory DB holding
different tables, garbage collected, then the merge-authorship setup:

```
commit_stats failures: 44 / 60
  ValueError: Table 'commits' is not a known Kospex table
```

## Not only a test problem

Every file-backed database keys on its path, so the CLI and web UI are correct
today. But the defect is latent rather than harmless: anything building a kospex
database in memory hits it. `KospexQuery.create_memory_kospex_query()`, which
`krunner osi` uses to load tables for dependency analysis, builds exactly such a
database.

## Fix

`_db_key()` returns `None` for in-memory databases, and `None` means "do not
cache". File-backed databases are unaffected.

Caching them was worth nothing measurable:

```
live kospex.db (file-backed)      10.1 us/call   (17 tables)
in-memory (1 table, as in tests)   1.1 us/call
```

A page building 50 queries saves ~0.5 ms on the file DB — worth keeping. The
in-memory case saves ~1.1 us per call, which is the entire cost of the extra
`sqlite_master` read now being done.

A `WeakKeyDictionary` would have preserved that microsecond, but caching an
in-memory database is wrong in principle regardless of key: they are built up
table by table at runtime, so a cached set would need invalidating on every
schema change. A file-backed schema only moves under a migration, which already
calls `invalidate_cache()`.

## Files changed

- `src/kospex/db/introspect.py` — `_db_key()` returns None for in-memory;
  `get_kospex_tables()`, `get_repo_tables()` and `invalidate_cache()` handle it
- `tests/test_db_introspect.py` — four regression tests

## Testing

647 passed, 75 skipped (643 before).

The targeted reproduction goes from **44 failures in 60** to **0 in 60**. That is
the load-bearing evidence: eight consecutive clean full-suite runs would only be
about 23% unlikely at the old failure rate, so the suite runs corroborate rather
than prove.

The regression tests assert that a fresh in-memory database never inherits a
dead one's tables (across 120 create/collect cycles), that in-memory databases
leave no cache entries at all, that `get_repo_tables` is covered too since it
shares the key, and — importantly — that file-backed caching still works,
including that a table added behind the cache stays invisible until
`invalidate_cache()`. Without that last one, the fix could pass by disabling
caching everywhere.
