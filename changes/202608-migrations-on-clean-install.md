# Applying DB migrations on a clean install

## Overview

A clean kospex install never applies its DB migrations. `connect_or_create_kospex_db()`
builds the frozen v2 baseline schema and creates an empty `schema_migrations` table, but
nothing calls `Migrator.apply_pending()` except `kospex upgrade-db -apply`. A brand-new
database is therefore three migrations behind on first use, and two of the three gaps
crash real commands.

This change applies pending migrations automatically when the database is created, warns
when an existing database is behind, and routes the web interface through the same
bootstrap so it stops crashing on a clean install.

## Problem

### Reproduction

With a fresh `KOSPEX_HOME`:

```
$ kospex upgrade-db
Kospex DB version: 2
Pending migrations (3):
  0003_repos_sync_provenance               sql
  0004_repos_last_fetch                    sql
  0005_dependency_data_resolution          sql
```

`kospex init` does not touch the database at all — it only sets up directories and
logging — so there is no command in the normal first-run path that applies migrations.

### Impact per migration

| Migration | Fresh-DB behaviour |
| --- | --- |
| `0003_repos_sync_provenance` | Safe. `_recorded_sync_provenance` / `_record_sync_provenance` (`kospex_core.py:1148-1179`) both swallow the exception, so file_metadata simply always rebuilds. |
| `0004_repos_last_fetch` | **Crashes.** `set_repo_last_fetch()` (`kospex_query.py:606`) is unguarded, so `kgit clone` and `kgit pull` raise `no such column: last_fetch`. |
| `0005_dependency_data_resolution` | **Crashes.** The three `dependency_data` `upsert_all` calls in `kospex_dependencies.py` pass no `alter=True`, so a record carrying `resolution` raises `table dependency_data has no column named resolution`. |

Confirmed directly against a fresh database:

```
last_fetch update FAILED: no such column: last_fetch
dep insert FAILED: table dependency_data has no column named resolution
```

### Second, worse defect: kweb on a clean install

`kweb2.py` has no lifespan or startup hook and never constructs `Kospex()` at boot — only
inside two request handlers (`:1421`, `:1543`). Its 47 `KospexQuery()` call sites and all
of `api_routes.py` bypass the bootstrap entirely, because `KospexQuery.__init__`
(`kospex_query.py:27`) goes straight to `Database(path)`.

If kweb is the first thing run on a clean install, sqlite creates a zero-table file and
the request dies:

```
sqlite3.OperationalError: no such table: kospex_config
tables after KospexQuery on missing DB: []
```

This is worse than the migration gap, because the empty file now exists on disk. The
`new_db` check in `connect_or_create_kospex_db()` tests `os.path.isfile()`, which is now
true, so on the next run the user gets the baseline tables (every CREATE is
`IF NOT EXISTS`) but **no version stamp and no migrations, permanently**.

### Why the design allowed this

`changes/202605-db-migration-system.md` deliberately froze `KOSPEX_DB_VERSION = 2` as the
baseline and specified that fresh DBs start with an empty `schema_migrations` table
meaning "we are at the kospex_schema.py baseline". That part is working as designed. The
omission is that nothing was wired to run the migrations afterwards.

## Design

Four pieces. Bootstrap and warning are kept separate because they need different
cadences: bootstrap must fire wherever the DB is created, the warning must fire only when
a user actually runs a command.

### 1. Fresh-DB bootstrap — `kospex_schema.py`

Inside the existing `if new_db:` block of `connect_or_create_kospex_db()`, after the
version stamp:

```python
if new_db:
    kospex_db.execute("INSERT INTO kospex_config ...")   # existing
    _bootstrap_migrations(kospex_db)                      # new
```

`_bootstrap_migrations` imports `Migrator` **inside the function body** and calls
`apply_pending()`.

The lazy import is required, not stylistic. `kospex/db/migrator.py` imports
`kospex_schema` inside three of its methods; a module-level import here would recreate the
`kospex/__init__.py` circular-import class of bug that previously broke kweb.

This path is already anticipated by the migrator: `Migrator.apply` opens with
`if conn.in_transaction: conn.commit()`, and its comment names "schema bootstrap on a
fresh DB" as the case it handles.

**Widened `new_db` test.** `new_db` becomes "the DB file is missing **or** the
`kospex_config` table is absent". This repairs any database already damaged by the
kweb-first bug, which would otherwise never bootstrap.

Ordering note: `new_db` is currently computed from `os.path.isfile()` *before*
`Database(db_path)` is opened. The `kospex_config` half of the test can only run after the
connection exists, so the flag is computed in two steps — the `isfile` check before
connecting, then OR'd with a `kospex_config`-absent check after connecting but before the
CREATE block runs (the CREATEs would otherwise create `kospex_config` themselves and
defeat the test).

**Failure handling.** Wrap `apply_pending()` in try/except. On failure: log the exception
and print a loud message to stderr directing the user to `kospex upgrade-db`. Do not
crash the invoking command and do not delete the database. Each migration is individually
transactional, so a partial failure leaves the DB consistent at whatever version
succeeded. The exception must not be swallowed silently — silent failure is how this bug
reached users.

### 2. Shared behind-check — `kospex/db/migrator.py`

A module-level `warn_if_behind(db, quiet=False)`:

- Returns silently when there are no pending migrations.
- Catches `sqlite3.OperationalError` for a missing `schema_migrations` table
  (pre-migration-system databases) and reports those as behind rather than propagating.
- Writes one line to **stderr**, never stdout, so it cannot corrupt piped output from
  `kospex list-repos` or krunner's CSV paths.

```
kospex: database is 3 migration(s) behind — run `kospex upgrade-db -apply`
```

Cost is one `SELECT id FROM schema_migrations` plus a small `iterdir()`, once per process.

### 3. CLI wiring — `kospex_cli.py`, `kgit.py`, `krunner.py`, `kreaper.py`

`warn_if_behind` is called from each Click **group callback**, not from
`connect_or_create_kospex_db()`.

This placement matters. All four modules construct `kospex = Kospex()` at module scope
(`kospex_cli.py:30`, `kgit.py:29`, `krunner.py:36`, `kreaper.py:11`), so the DB connect
happens at import, before Click dispatches. Warning from the connect would print on
`kospex --help`, on mistyped subcommands, and on shell tab-completion. Click invokes the
group callback only when a subcommand actually runs.

- `kospex_cli.cli` — already has `--quiet`; pass it through as `quiet=quiet`
- `kgit.cli`, `krunner.cli`, `kreaper.cli` — bare `def cli():` with no options, so the
  warning always prints. Adding `--quiet` to these is out of scope.

The warning also fires ahead of `kospex upgrade-db` itself, which is redundant but
harmless — the command's own output immediately supersedes it. Not worth special-casing.

Each has a module-level `kospex` instance, so `kospex.kospex_db` is the handle.

### 4. kweb lifespan — `kweb2.py`

Add an `asynccontextmanager` lifespan passed to the `FastAPI(...)` constructor at
`kweb2.py:48`. On startup it calls `connect_or_create_kospex_db()` once, then
`warn_if_behind()` to the `kweb2` logger.

This does double duty:

- Guarantees the baseline schema exists before any `KospexQuery()` or `api_routes.py`
  handler touches the DB, fixing the `no such table: kospex_config` crash.
- Makes kweb participate in the fresh-DB bootstrap, so a kweb-first user gets migrations.

`KospexQuery.__init__` **stays** on raw `Database(path)`. Routing it through
`connect_or_create_kospex_db()` would re-run ~19 `CREATE TABLE IF NOT EXISTS` statements
at each of its 47 call sites, per request.

## Decisions taken

**Unguarded call sites stay unguarded.** `set_repo_last_fetch` and the `dependency_data`
upserts are the actual crash points, and after this change they are unreachable on a fresh
install. They will still crash on a stale existing DB, which the warning announces but does
not prevent. That is deliberate: the try/except pattern in `kospex_core.py`'s provenance
methods is precisely what made 0003's absence invisible. A hard failure, preceded by a
clear warning at command start, is more honest than silently not recording `last_fetch`.

**Existing databases are not auto-migrated.** They get the warning only. Auto-applying
would mutate databases holding real data with no backup prompt, which
`changes/202605-db-migration-system.md` explicitly warns against, and would race between
concurrent CLI / kweb / kospex-agent processes.

## Files changed

- `src/kospex_schema.py` — `_bootstrap_migrations()`; widened `new_db` test in
  `connect_or_create_kospex_db()`
- `src/kospex/db/migrator.py` — `warn_if_behind()`
- `src/kospex_cli.py` — `warn_if_behind` in the `cli` group callback, honouring `--quiet`
- `src/kgit.py`, `src/krunner.py`, `src/kreaper.py` — `warn_if_behind` in each group callback
- `src/kweb2.py` — lifespan hook on the `FastAPI` app
- `tests/test_db_migrator.py` — extended (file already exists, 31 tests)
- `tests/test_kospex_schema.py` — extended (file already exists, 4 tests)

## Testing

The load-bearing test is an invariant, not a test of the new code:

```python
def test_fresh_db_has_no_pending_migrations(tmp_path, monkeypatch):
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    db = connect_or_create_kospex_db()
    assert Migrator(db).pending() == []
```

This fails the moment anyone adds `0006_*.sql` without checking the clean-install path,
turning "does a clean install work?" into a permanent CI question instead of something
that must be remembered.

Also covered:

- Columns added by 0003/0004/0005 are present on a fresh DB
- `set_repo_last_fetch()` and a `resolution`-bearing dependency upsert both succeed on a
  fresh DB (regression tests for the two crash paths)
- `warn_if_behind` fires on a DB stamped behind, is silent when current, and handles a
  missing `schema_migrations` table
- The zero-table repair case: an empty DB file is detected and fully rebuilt
- Bootstrap failure is non-fatal and leaves a usable DB

Every test must set `KOSPEX_HOME` via `monkeypatch.setenv` so the suite never touches the
developer's real `~/kospex/kospex.db`.

Run in a worktree with `PYTHONPATH=$PWD/src pytest` — an editable install resolves the
flat `kospex_*` modules to the main checkout, so without it the tests silently exercise
the wrong code.

## Out of scope

**Module-level `Kospex()` in all four CLIs.** Because the instance is constructed at
import, `kospex --help` on a clean machine creates `~/kospex/kospex.db` as a side effect.
Pre-existing behaviour, orthogonal to this fix, and changing it means restructuring four
entry points.

**Adding `--quiet` to `kgit` / `krunner` / `kreaper`.** Their group callbacks currently
take no options.
