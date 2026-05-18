# DB Migration System

Design spec for replacing kospex's auto-`ALTER TABLE` `upgrade-db` command with a proper script-driven migration system, plus a small refactor to make the table-name registry self-maintaining.

## Overview & Goals

Replace the current "diff schema and auto-generate ALTER TABLE" behaviour of `kospex upgrade-db` with a versioned, script-driven migration runner. Each schema change is a reviewable SQL file (with an optional Python file for data backfills), checksummed, and applied in order. Applied migrations are tracked per-row in a new `schema_migrations` table; the existing `KOSPEX_DB_VERSION_KEY` integer in `kospex_config` is kept as a cache so existing code (`KospexQuery.get_kospex_db_version()`) keeps working.

Same refactor also replaces the hand-maintained `KOSPEX_TABLES` / `REPO_TABLES` list constants in `kospex_schema.py` with runtime introspection helpers, so migrations that add new tables don't require a parallel constant update.

### Goals

- Author schema changes as explicit, reviewable SQL files instead of inferred `ALTER TABLE` commands.
- Support data backfills via an optional Python step (e.g. backfill a calculated column after adding it).
- Detect drift and tampering via per-migration SHA-256 checksums.
- Idempotent and safe to rerun — apply only what is pending.
- Eliminate "I forgot to update `REPO_TABLES`" as a class of bug, by detecting `_repo_id` columns at runtime in `kreaper` etc.

### Non-goals (intentionally out of scope)

- **Down/rollback migrations.** Forward-only. To undo, write a new forward migration.
- **Moving `kospex_schema.py` into the `src/kospex/` namespace.** Separate, larger project tracked in `ideas/sqlmodel-migration.md`. This spec establishes a beachhead at `src/kospex/db/` but does not relocate existing files.
- **DuckDB or non-SQLite support.** SQLite only.
- **Branching / merge-resolution tooling.** Single-maintainer cadence does not justify it.
- **A `create-migration` scaffolding command** or other CLI sub-commands beyond status + apply. Easy to add later.

### Baseline

`KOSPEX_DB_VERSION = 2` in `kospex_schema.py` is frozen as the new baseline. Fresh DBs created from `connect_or_create_kospex_db()` are stamped at v2 with an empty `schema_migrations` table. The first new migration is `0003_*`. The constant is no longer manually bumped — the migrator owns the live version going forward.

## Architecture & Layout

```
src/
├── kospex_schema.py              # in place, lightly trimmed (see Cleanup)
└── kospex/
    ├── __init__.py               # empty marker
    └── db/
        ├── __init__.py           # exports Migrator
        ├── migrator.py           # the runner
        ├── introspect.py         # get_kospex_tables, get_repo_tables, cache
        └── migrations/
            ├── __init__.py       # empty (helps test discovery)
            ├── .gitkeep          # keep dir present even with no migrations
            ├── README.md         # short note on naming + how to add one
            ├── 0003_<slug>.sql   # first new migration
            ├── 0003_<slug>.py    # optional Python backfill (same prefix)
            └── ...
```

**Why `src/kospex/db/` and not `src/kospex/migrations/`:** when `kospex_schema.py` is eventually moved under the namespace, the natural landing spot is `src/kospex/db/schema.py` as a peer of `migrations/`. This avoids reorganising later.

### Module responsibilities

- **`kospex/db/migrator.py`** — the runner. Public API:
  - `Migrator(db)` — constructed with a `sqlite_utils.Database`
  - `.discover()` → sorted list of `Migration` objects from the migrations directory
  - `.applied()` → list of applied migration IDs from `schema_migrations`
  - `.pending()` → discovered minus applied
  - `.apply(migration)` → run one migration (SQL + optional Python) in a transaction, record it
  - `.apply_pending()` → loop pending, stop on first failure
  - `.verify_checksums()` → re-hash applied files, return mismatches/missing
  - `.print_status()` → friendly summary used by the CLI in dry-run mode
- **`kospex/db/introspect.py`** — table-introspection helpers (see Table Introspection section).
- **`kospex_cli.py`** — `upgrade-db` command body shrinks from ~100 lines to ~10; delegates to `Migrator`.
- **`kospex_schema.py`** — keeps `TBL_*` constants and `SQL_CREATE_*` statements; adds `TBL_SCHEMA_MIGRATIONS` and `SQL_CREATE_SCHEMA_MIGRATIONS`; loses `KOSPEX_TABLES`, `REPO_TABLES`, and the auto-alter helpers.
- **`kospex_core.py`** — `apply_alter_table_commands()` removed; two `KOSPEX_TABLES` usages switch to `get_kospex_tables(db)`.

## Migration File Format

### SQL file (required)

`0003_<slug>.sql` — plain SQL, executed via `Database.executescript()` so multiple statements work. Comments with `--` allowed. No templating, no variable substitution.

The file must **not** contain `BEGIN` / `COMMIT` / `ROLLBACK` — the runner manages the transaction boundary.

Example:

```sql
-- 0003_add_repos_size_bytes.sql
ALTER TABLE [repos] ADD COLUMN [size_bytes] INTEGER;
CREATE INDEX IF NOT EXISTS idx_repos_size_bytes ON [repos]([size_bytes]);
```

### Python file (optional, post-SQL)

`0003_<slug>.py` — runs **after** the SQL, in the same transaction. Contract: a single `up(db)` function.

```python
def up(db):
    """
    db: sqlite_utils.Database — same connection the SQL just ran on,
    inside the same transaction. Do not commit/rollback here.
    Raise on failure; the runner will roll the whole migration back.
    """
    ...
```

- Imports allowed (normal Python module). Runner loads it by file path via `importlib`.
- No global side effects on import; only `up(db)` is invoked.
- If `up()` is missing, validation fails before any apply happens.

Example:

```python
# 0003_add_repos_size_bytes.py
def up(db):
    for r in db["repos"].rows:
        size = compute_size(r["file_path"])
        db["repos"].update(r["_repo_id"], {"size_bytes": size})
```

### Migration identity

- ID = prefix + slug, without extension: `0003_add_repos_size_bytes`.
- That string is recorded as `schema_migrations.id`.
- `.sql` and `.py` for the same prefix must share the same slug. Mismatch (`0003_foo.sql` + `0003_bar.py`) → fatal validation error.

### Checksum

SHA-256 over the bytes of the SQL file and (if present) the Python file:

```
checksum = sha256(sql_bytes).hexdigest() + ":" + (sha256(py_bytes).hexdigest() if py else "")
```

Stored in `schema_migrations.checksum`. Re-hashed on every run by `verify_checksums()` for tamper detection.

### Authoring discipline (documented, not enforced)

- Once a migration is committed and shipped, **do not edit it.** Write a new one.
- New migrations always go at the end (next sequential number).
- Do not squash or renumber existing migrations.

## Tracking: `schema_migrations` Table + Version Int

### Table schema

```sql
CREATE TABLE IF NOT EXISTS [schema_migrations] (
    [id] TEXT PRIMARY KEY,         -- e.g. '0003_add_repos_size_bytes'
    [sequence] INTEGER NOT NULL,   -- 3 (extracted from prefix; cheap ORDER BY)
    [checksum] TEXT NOT NULL,      -- sha256(sql)[:sha256(py)]
    [applied_at] TEXT NOT NULL,    -- ISO 8601 UTC, e.g. '2026-05-18T14:30:15Z'
    [duration_ms] INTEGER,         -- how long it took (debug aid)
    [has_python] INTEGER NOT NULL  -- 0 or 1 — was a .py step also run?
)
```

Added to `kospex_schema.py` as `TBL_SCHEMA_MIGRATIONS` + `SQL_CREATE_SCHEMA_MIGRATIONS` and included in `DB_CREATE_STATEMENTS` so fresh DBs get it automatically via `connect_or_create_kospex_db()`.

### Version int reconciliation

The existing `KOSPEX_CONFIG.value` for `KOSPEX_DB_VERSION_KEY` stays as a cache. After every successful `apply_pending()` run, the runner updates it to:

```
MAX(KOSPEX_DB_VERSION constant, MAX(sequence) FROM schema_migrations)
```

This handles both cases cleanly:

- Fresh DB at constant=2 with no migration rows → version = 2.
- Same DB after applying 0003, 0004, 0005 → version = 5.

`KospexQuery.get_kospex_db_version()` keeps working unchanged.

### Bootstrapping for existing v2 DBs

On first run of the new system against an existing DB:

1. Create `schema_migrations` table if missing.
2. Do **not** mark anything as applied — the empty table means "we are at the kospex_schema.py baseline".
3. Apply pending migrations (anything `0003_*` and up) as normal.

Fresh DBs created by `connect_or_create_kospex_db()` get the table via `DB_CREATE_STATEMENTS` and end up in the same state.

### `verify_checksums()` behaviour

For each row in `schema_migrations`, re-hash the corresponding files on disk:

- File missing → warn (`"0003_foo no longer on disk"`).
- Checksum mismatch → warn (`"0003_foo has been modified since it was applied"`).

These are warnings, not errors. They don't block new migrations from running.

## Runner Behaviour

### Top-level flow

```
1. Connect to DB
2. Discover migration files → sorted list
3. Validate the discovered set (see Validation)
4. Read schema_migrations table → applied set
5. Compute pending = discovered - applied
6. verify_checksums on applied (warnings only)
7. If no -apply flag → print status and exit
8. If -apply flag → loop pending, apply each in turn
```

### Discovery

- Scan `src/kospex/db/migrations/` for files matching `^\d{4}_.+\.(sql|py)$`.
- Group by prefix. Each prefix produces one `Migration` object: `id`, `sequence`, `slug`, `sql_path`, `py_path` (None if absent).
- Sort by `sequence` ascending.
- Located via `importlib.resources` so it works in dev install and in an installed wheel.

### Validation

**Fatal** (abort before any execution):

- Duplicate prefix (`0003_foo.sql` and `0003_bar.sql`).
- SQL/Python slug mismatch (`0003_foo.sql` with `0003_bar.py`).
- Orphan Python (`0003_foo.py` with no `0003_*.sql`).
- SQL file empty (zero bytes after stripping comments and whitespace).
- Python file missing `up(db)` function — checked at load time before applying.

**Warning** (do not abort):

- Gap in sequence (`0003`, `0005` with no `0004`).
- Checksum mismatch or missing file for already-applied migration.

### Applying one migration

```python
def apply(self, migration):
    started = time.monotonic()
    with self.db.conn:                        # sqlite3 context = transaction
        self.db.executescript(read(migration.sql_path))
        if migration.py_path:
            mod = load_module(migration.py_path)
            mod.up(self.db)
        self.db["schema_migrations"].insert({
            "id": migration.id,
            "sequence": migration.sequence,
            "checksum": migration.checksum(),
            "applied_at": utcnow_iso(),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "has_python": 1 if migration.py_path else 0,
        })
    # committed on success; exception above rolls back everything
    self._update_version_int()
    invalidate_table_cache(self.db)           # tables may have changed
```

### Transaction guarantees

- One transaction per migration. Includes: SQL execution, Python `up()`, insert into `schema_migrations`.
- If anything raises, rollback. The row is not inserted, version int is not bumped, next run sees the migration as still-pending.
- SQLite supports DDL inside transactions, so this works for the typical migration.

### Failure handling in `apply_pending`

- Iterate pending in order.
- On failure: print the error, the migration ID, the offending SQL/Python. Stop. Do not attempt subsequent migrations.
- Earlier migrations that succeeded in this run remain committed (each had its own transaction). Partial progress is preserved by design — user fixes the broken migration, reruns, picks up where it failed.
- Exit code non-zero on any failure.

### Status output (no `-apply`)

```
Kospex DB version: 4 (baseline 2 + 2 migrations applied)
Database: /Users/foo/kospex/kospex.db

Applied migrations (2):
  0003_add_repos_size_bytes        applied 2026-04-12 10:14:22 UTC  (12ms)
  0004_index_commits_author        applied 2026-04-15 09:01:08 UTC  (8ms)

Pending migrations (1):
  0005_backfill_cycle_time         sql + python   3.1 KB

Run with -apply to execute pending migrations.
WARNING: backup your database before applying.
```

### Apply output

```
Applying 0005_backfill_cycle_time...
  -> executing SQL (1 statement)
  -> running Python backfill
  OK applied (1842ms)

DB version is now 5.
```

### Logging

Use the existing `kospex_logging` module. Console gets the friendly summary; `~/kospex/logs/kospex.log` gets details (per-migration info, durations, checksum warnings, full exceptions).

```python
logger = get_kospex_logger("kospex")
logger.info(f"Applying migration {migration.id}")
logger.error(f"Migration {migration.id} failed: {exc}", exc_info=True)
```

Existing `--debug` / `--verbose` global flags automatically apply.

## CLI Surface

Single command, mirrors today's UX:

```
kospex upgrade-db            # status / dry-run preview (default)
kospex upgrade-db -apply     # apply pending migrations
```

Click definition (replaces `kospex_cli.py:1227-1332`):

```python
@cli.command("upgrade-db")
@click.option("-apply", is_flag=True, default=False,
              help="Apply pending migrations. Without this flag, runs in status/preview mode.")
def upgrade_db(apply):
    """
    Show the kospex DB migration status, or apply pending migrations.
    Without options: prints current version, applied migrations, pending migrations.
    With -apply: runs each pending migration in sequence.
    """
    click.echo(f"Kospex CLI version {Kospex.VERSION}")
    click.echo("\nWARNING: backup your database before performing ANY upgrade.\n")

    db = kospex.kospex_db
    migrator = Migrator(db)

    if apply:
        migrator.apply_pending()
    else:
        migrator.print_status()
```

### Edge case behaviour

| Scenario | Behaviour |
|---|---|
| Fresh DB, no migrations directory yet | Status: "DB version 2, no migrations to apply." Not an error. |
| Migrations dir empty | Same as above. Not an error. |
| `schema_migrations` rows but no version int row | Recompute and write version int from `MAX(sequence)`. Not an error. |
| Version int present but no `schema_migrations` table | Create the table empty (existing v2 DB upgrading). |
| `schema_migrations` rows reference files that don't exist | Warn in status. Do not block new migrations. |
| Python `up(db)` raises | Roll back, print error with ID, exit non-zero. Stop applying. |
| Python file missing `up()` function | Validation error before any apply. Abort. |
| Two migrations with same prefix | Validation error. Abort. |
| Rerun `-apply` after partial failure | Picks up from the failed migration (still pending). Earlier successes stay applied. |
| User edits an already-applied migration file | Checksum mismatch warning in status. Does not block. |
| `KOSPEX_DB_VERSION_KEY` row missing from `kospex_config` | Treated as version=0. Written correctly after first apply. |
| `-apply` with nothing pending | "No pending migrations. DB is at version N." Exit 0. |

## Table Introspection (replacing `KOSPEX_TABLES` / `REPO_TABLES`)

The hand-maintained `KOSPEX_TABLES` and `REPO_TABLES` lists in `kospex_schema.py` become a correctness liability once migrations can add tables (forget to update them, and `kreaper delete-repo -repo_id` silently leaves orphan rows in new tables).

Replace with runtime helpers in `src/kospex/db/introspect.py`:

```python
_TABLE_CACHE: dict[str, set[str]] = {}
_REPO_TABLE_CACHE: dict[str, set[str]] = {}

def get_kospex_tables(db) -> set[str]:
    key = str(db.path)
    if key not in _TABLE_CACHE:
        _TABLE_CACHE[key] = {
            r[0] for r in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    return _TABLE_CACHE[key]

def get_repo_tables(db) -> set[str]:
    """Tables with a _repo_id column — auto-detected via PRAGMA."""
    key = str(db.path)
    if key not in _REPO_TABLE_CACHE:
        out = set()
        for t in get_kospex_tables(db):
            cols = [c[1] for c in db.execute(f"PRAGMA table_info([{t}])").fetchall()]
            if "_repo_id" in cols:
                out.add(t)
        _REPO_TABLE_CACHE[key] = out
    return _REPO_TABLE_CACHE[key]

def invalidate_cache(db=None):
    """Migrator calls this after applying migrations."""
    if db is None:
        _TABLE_CACHE.clear()
        _REPO_TABLE_CACHE.clear()
    else:
        key = str(db.path)
        _TABLE_CACHE.pop(key, None)
        _REPO_TABLE_CACHE.pop(key, None)
```

The `TBL_*` string constants in `kospex_schema.py` stay — they serve a different purpose (typo-safe symbolic references in queries like `kd.from_table(KospexSchema.TBL_COMMITS)`) and are referenced widely.

**Edge case to verify in implementation:** `KospexData.__init__` accepts `kospex_db=None`. If an instance is ever created without a db and a validation method is called, the new code path will fail. Verify via grep that all current call sites pass a db; if any don't, raise a clear error rather than silently passing.

**FK ordering note:** kospex schema appears to be flat (no FK constraints between tables — shared `_repo_id` is a soft join key). Deletion order in `kreaper` therefore doesn't matter. Confirm during implementation via `PRAGMA foreign_keys` inspection; if any FKs exist, the helper should return repo tables in child-first order.

## Cleanup & Change List

### `kospex_schema.py`

**Remove:**

- `KOSPEX_TABLES = [...]` (lines 31-34)
- `REPO_TABLES = [...]` (lines 37-39)
- `generate_alter_table(old_create_sql, new_create_sql, tbl_name)` (lines 540-594)
- `validate_square_brackets(create_sql)` (lines 653-684)
- `validate_square_brackets2(create_sql)` (lines 596-651) — already dead

**Modify:**

- `drop_table(table)` (line 472-479) — switch `KOSPEX_TABLES` check to `get_kospex_tables(db)`. Also rewrite the line 479 print: `f"Invalid table '{table}', was not in KOSPEX_TABLES"` → `f"Invalid table '{table}', not a known Kospex table"` (the old message names a constant that no longer exists).
- `KOSPEX_DB_VERSION = 2` stays at 2 (baseline). No longer manually bumped.

**Add:**

- `TBL_SCHEMA_MIGRATIONS = "schema_migrations"`
- `SQL_CREATE_SCHEMA_MIGRATIONS = '''...'''` (table definition above)
- `TBL_SCHEMA_MIGRATIONS: SQL_CREATE_SCHEMA_MIGRATIONS` entry in `DB_CREATE_STATEMENTS`
- One `kospex_db.execute(SQL_CREATE_SCHEMA_MIGRATIONS)` line in `connect_or_create_kospex_db()`

### `kospex_core.py`

**Remove:**

- `apply_alter_table_commands(self, alter_commands)` (line 1284 onward, ~20 LOC)

**Modify:**

- Line 1243: `for db_table in KospexSchema.KOSPEX_TABLES:` → `for db_table in get_kospex_tables(self.kospex_db):`
- Line 1274: `if table not in KospexSchema.KOSPEX_TABLES:` → `if table not in get_kospex_tables(self.kospex_db):`
- Line 1275 error message — already generic (`"table: {table} is not a Kospex table"`), no rewrite needed.
- Add import: `from kospex.db.introspect import get_kospex_tables`

### `kospex_cli.py`

**Modify:**

- `upgrade_db()` command body (lines 1227-1332, ~100 lines) → replaced with the ~10-line version above.
- Add import: `from kospex.db.migrator import Migrator`

### `kospex_query.py`

**Modify:**

- Line 2227 (`KospexData.where_join`): `KospexSchema.KOSPEX_TABLES` → `get_kospex_tables(self.kospex_db)`
- Line 2228: rewrite error message — `f"Table '{t}' not in KospexSchema.KOSPEX_TABLES"` → `f"Table '{t}' is not a known Kospex table"` (the old message names a constant that no longer exists).
- Line 2300 (`KospexData.from_table`): same membership change
- Line 2301: rewrite error message — same fix as line 2228.
- Line 2314 (`KospexData.valid_table_prefix_select`): same membership change.
- Line 2315: commented-out error string referencing `KospexSchema.KOSPEX_TABLES` — delete the comment outright (dead reference once the constant is gone).
- Add import: `from kospex.db.introspect import get_kospex_tables`

All `kd.from_table(KospexSchema.TBL_COMMITS)` call sites and other `TBL_*` symbolic references stay unchanged.

### `kreaper.py`

**Modify:**

- Lines 36, 76: `KospexSchema.KOSPEX_TABLES` → `get_kospex_tables(kospex.kospex_db)`
- Lines 51, 86: "Here's a list of valid tables" display → iterate `get_kospex_tables(kospex.kospex_db)`
- Line 60: `KospexSchema.REPO_TABLES` → `get_repo_tables(kospex.kospex_db)`
- Add import: `from kospex.db.introspect import get_kospex_tables, get_repo_tables`

### New files

```
src/kospex/__init__.py                    # empty marker
src/kospex/db/__init__.py                 # exports Migrator
src/kospex/db/migrator.py                 # the runner (~150-250 LOC)
src/kospex/db/introspect.py               # introspection helpers + cache
src/kospex/db/migrations/__init__.py      # empty marker
src/kospex/db/migrations/.gitkeep         # keep dir present
src/kospex/db/migrations/README.md        # naming convention + how to add one
```

### `pyproject.toml`

Add `kospex` namespace to packaging so it ships in the wheel. Exact form depends on current setup config — verify during implementation that built wheel includes `src/kospex/db/migrations/*.sql` (data files often need explicit inclusion).

### Tests to add

`tests/test_migrator.py`:

- Discovery: empty dir → no migrations; valid dir → sorted list.
- Validation: duplicate prefix raises; slug mismatch raises; orphan `.py` raises; missing `up()` raises.
- Apply: SQL-only migration → table change visible; SQL + Python → both ran.
- Transaction rollback: failing Python → SQL changes reverted, no `schema_migrations` row.
- Checksum verify: edit file → mismatch detected.
- Reapply safety: pending becomes empty after apply; rerunning is a no-op.

`tests/test_introspect.py`:

- `get_kospex_tables(db)` returns the live table list.
- `get_repo_tables(db)` filters to those with `_repo_id`.
- Cache invalidation works.

### Final cleanup pass

After all the above, grep should return zero hits in `src/` (other than introspect.py / migrator.py themselves) for:

- `KOSPEX_TABLES`
- `REPO_TABLES`
- `generate_alter_table`
- `apply_alter_table_commands`
- `validate_square_brackets`

Same for `tests/`. Anything left is dead and should be removed.

**User-facing strings:** also scan with `grep -rn "KOSPEX_TABLES" src/` *before* deleting the constants — any error message, print statement, or comment that name-drops `KOSPEX_TABLES` / `REPO_TABLES` must be reworded (the constant no longer exists). Known spots covered above are `kospex_query.py:2228,2301,2315` and `kospex_schema.py:479`; verify nothing else slipped in since this spec was written.

## Implementation Sequencing

Suggested order for the implementation plan:

1. Create the `src/kospex/db/` namespace skeleton (empty files, `__init__.py`, `pyproject.toml` packaging change). Verify wheel build picks it up.
2. Write `introspect.py` + `tests/test_introspect.py`. Land it independently — pure refactor, no behaviour change yet.
3. Update consumers (`kospex_query.py`, `kospex_core.py`, `kreaper.py`, `kospex_schema.py:drop_table`) to use the introspection helpers. Remove `KOSPEX_TABLES` / `REPO_TABLES` constants. Run full test suite.
4. Add `TBL_SCHEMA_MIGRATIONS` + `SQL_CREATE_SCHEMA_MIGRATIONS` to `kospex_schema.py`; ensure fresh DBs get the table.
5. Write `migrator.py` + `tests/test_migrator.py`.
6. Replace `upgrade-db` CLI body. Remove `apply_alter_table_commands` from `kospex_core.py`. Remove `generate_alter_table` / `validate_square_brackets*` from `kospex_schema.py`.
7. Add a placeholder first migration (`0003_*`) if one is needed, or leave migrations dir empty for the initial PR.

Each step is independently testable and reviewable.
