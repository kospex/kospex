# SQLite WAL + busy_timeout on connect

**Status:** spec / not yet implemented
**Date:** 2026-07
**Tracked in:** kospex/kospex#119

## Problem

A user ingesting ~3000 repositories with multiple concurrent threads hits
"database is locked" (`SQLITE_BUSY`) errors. SQLite is fundamentally
single-writer: only one write transaction at a time, regardless of pragmas.
Two low-risk mitigations reduce the pain today:

- **`journal_mode=WAL`** — lets many readers run concurrently with the single
  writer (readers no longer block the writer or vice-versa). It does **not**
  allow multiple writers, but it removes reader/writer collisions, which is the
  bulk of the contention for kospex's read-heavy web/report layer running
  alongside ingest.
- **`busy_timeout`** — turns most `SQLITE_BUSY` *errors* into short *waits*, so
  concurrent writers queue on the lock instead of failing immediately.

Currently neither is set. `grep` for `journal_mode`/`WAL`/`busy_timeout` across
`src/` returns nothing, so the DB runs in default rollback-journal mode. There
is no central connection factory — `Database(...)` is opened ad-hoc.

> Note: this is the cheap, first-line fix. The larger wins (batching the
> per-row `upsert()` loop in `kospex_core.py` into `upsert_all` transactions,
> and an optional stage-to-disk + single-writer ingest architecture) are
> separate follow-ups and out of scope here.

## Design

### Pragma lifetimes (why placement matters)

| Pragma | Persistence | Consequence |
|--------|-------------|-------------|
| `journal_mode=WAL` | Persistent — written to the DB file header, survives across connections | Set once and it sticks; re-asserting when already WAL is a cheap no-op (no lock, no file I/O) |
| `busy_timeout` | Per-connection — resets on every new connection | **Must** be applied on every connect |

Because `busy_timeout` has to run on every connection anyway, a connection
helper is required regardless. WAL rides along for free (idempotent).

**WAL cannot be set in a migration.** `PRAGMA journal_mode=WAL` cannot run
inside a transaction, and the migrator (`kospex/db/migrator.py`) wraps every
migration — SQL and Python `up()` — in `BEGIN … COMMIT`. So migrations are the
wrong vehicle; do not add it there.

### 1. Connection helper (source of truth)

Add a helper (proposed: `KospexUtils.get_kospex_db()` in `kospex_utils.py`,
alongside `get_kospex_db_path()`):

```python
def get_kospex_db():
    """Open the kospex SQLite DB with WAL + busy_timeout applied."""
    from sqlite_utils import Database
    db = Database(get_kospex_db_path())
    db.conn.execute("PRAGMA journal_mode=WAL")   # persistent; no-op if already WAL
    db.conn.execute("PRAGMA busy_timeout=5000")  # per-connection; must re-apply
    return db
```

Route existing physical-DB call sites through it:

- `kospex_query.py:27` — `Database(KospexUtils.get_kospex_db_path())`
- `kospex_schema.py:452` — inside `connect_or_create_kospex_db()` (see below)
- `repo_sync.py:194`, `repo_sync.py:284`

Leave `Database(memory=True)` call sites alone (WAL is N/A for in-memory DBs).

This helper is what actually converts the existing 3000-repo deployment: the
first kospex process to connect flips its delete-mode DB to WAL. It is also
self-healing — a restored backup or copied DB re-asserts WAL on next connect.

### 2. Assert WAL at DB creation (kospex init)

In `connect_or_create_kospex_db()` (`kospex_schema.py:449`, already tracks a
`new_db` flag), and hence `kospex init --create`, set WAL immediately after
opening/creating the DB — either by routing through `get_kospex_db()` or by
adding the two pragmas inline.

Benefit: a freshly created DB is canonically WAL before any table creation or
ingest touches it, and the one-time `delete→wal` transition (which needs a
brief exclusive lock) happens at the quietest possible moment. Steady-state
connections then see WAL already set and do the cheap no-op.

## Non-goals

- No migration file for WAL (can't run in a transaction — see above).
- Not batching the `upsert()` loop — separate change.
- Not the stage-to-disk / single-writer ingest redesign — separate change.

## Gotchas

- First `delete→wal` transition needs a momentary exclusive lock; fine at init,
  and fine on first connect since the flipping connection is the first to open.
- WAL adds `-wal` and `-shm` sidecar files; the `-wal` grows until checkpoint.
  For bulk ingest the defaults are fine — monitor WAL size under heavy write
  load, don't tune preemptively.
- Keep WAL consistent across all connections. Switching *out* of WAL requires
  all connections closed; mixing journal modes across connections causes churn.

## Verification

- `sqlite3 ~/kospex/kospex.db "PRAGMA journal_mode;"` → `wal` after a connect or
  after `kospex init --create`.
- Confirm `-wal`/`-shm` files appear next to `kospex.db` during a sync.
- Run a concurrent multi-thread ingest and confirm `SQLITE_BUSY` errors drop
  (they should wait up to `busy_timeout` instead of erroring).

## Files

- `src/kospex_utils.py` — new `get_kospex_db()` helper.
- `src/kospex_schema.py` — `connect_or_create_kospex_db()` sets WAL at creation.
- `src/kospex_query.py`, `src/repo_sync.py` — route through the helper.
