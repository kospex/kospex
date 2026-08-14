# save_dependencies(): demote and upsert in one transaction

## Overview

`KospexDependencies.save_dependencies()` ran two DB statements with no
transaction between them. If the second failed, the first stayed committed and
the manifest was left reporting zero current dependencies — silently, because
the statement that caused the damage succeeded.

This is a durability bug in the write path. It stands independently of whether
DB migrations have been applied.

## Problem

`save_dependencies()` does two things:

1. Demote every prior `latest=1` row for each `(_repo_id, file_path)` being
   rewritten (`UPDATE ... SET latest = 0`).
2. Upsert the incoming records with `latest=1`.

Run as separate statements, a failure in step 2 leaves step 1 committed. The
file then has no rows at `latest=1` at all: every current-dependencies view for
that manifest reads empty.

Nothing is deleted — the rows survive at `latest=0`, and the demote is an
UPDATE, never a DELETE — so the data is recoverable by fixing the cause and
re-syncing. But until then it is not reported.

Reproduced against a database missing the `resolution` column, with a fresh
connection and a reopened file to prove the loss is committed rather than
uncommitted state visible on the writing handle:

```
before (same conn):  3
save_dependencies raised: OperationalError
after  (same conn):  0
conn.in_transaction: False
after  (other conn): 0   <-- committed state
after  (reopened):   0   <-- survives process exit
```

### Why the exposure widened

`a298f97` changed the demote from `(_repo_id, file_path, package_name)` to
`(_repo_id, file_path)`. That is correct — keying on the package name left
renamed and removed packages stuck at `latest=1` forever — but it means a
failure now clears the whole manifest rather than only the packages in the
incoming batch.

### The trigger is general

A pre-0005 database hitting `table dependency_data has no column named
resolution` is the case seen in the wild, and closing that migration gap removes
that particular trigger. It does not fix this: any transient error between the
two statements — a lock timeout, a disk error, a malformed record — produces the
same silent blanking.

## Fix

Both statements now run inside one `Database.atomic()` block, so a failed upsert
rolls the demote back and the previous dependency set stays current. The
exception still propagates: the caller must know the write failed.

`atomic()` is required rather than sqlite3's `with conn:`. The latter relies on
the implicit transaction and does **not** protect the demote here, because
something inside `upsert_all()` ends it. `atomic()` issues an explicit `BEGIN`,
and nests via savepoints if a caller already holds a transaction. Measured
directly:

```
with conn:     -> LOST ROWS (n=0)
db.atomic()    -> ROLLED BACK (safe)
```

`cleaned` is built before the block — it is pure computation and does not need
to hold the transaction open.

## Files changed

- `src/kospex_dependencies.py` — `save_dependencies()` wraps the demote and the
  upsert in `self.kospex_db.atomic()`
- `pyproject.toml` — `sqlite-utils` floored at `>=4.1.1` for `Database.atomic()`,
  matching the pin already in `requirements.txt`
- `tests/test_dependencies_save.py` — regression test
- `CHANGELOG.md`

## Testing

`test_upsert_failure_leaves_prior_rows_current` seeds three current rows, drives
a save whose upsert must fail, and asserts all three are still `latest=1`.

The failure is triggered with a deliberately generic unknown column rather than
`resolution`, so the test keeps testing the durability property rather than the
migration gap once that gap closes.

## Not addressed

The other two `upsert_all` call sites in this file (the assess path and the
`store` path) have **no demote**, so they cannot blank a manifest and are not
affected by this bug.

They do set `latest = 1` without demoting anything, which means they can leave
renamed or removed packages stuck at `latest=1` — the same problem `a298f97`
fixed for `save_dependencies()`, still present in that writer. That is a
correctness issue, not a durability one, and needs a decision about whether
those paths should demote at all. Tracked separately.
