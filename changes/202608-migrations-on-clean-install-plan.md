# Migrations on Clean Install — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply pending DB migrations automatically when the kospex database is created, and warn loudly on every command when an existing database is behind.

**Architecture:** Bootstrap, warning, and reporting are three separate concerns with three different cadences. Bootstrap fires inside `connect_or_create_kospex_db()` wherever the DB is created. The warning fires from each Click group callback, so it only appears when a subcommand actually runs. Reporting is a read-only helper consumed by `init` and `system-status`. All three live behind lazy imports because `kospex/db/*` imports `kospex_schema`, which imports `kospex_utils`.

**Tech Stack:** Python 3.12, Click 8.3.1, sqlite_utils, FastAPI, pytest.

**Spec:** `changes/202608-migrations-on-clean-install.md`

## Global Constraints

- **Never import `kospex.db.*` at module level from `kospex_schema.py` or `kospex_utils.py`.** `kospex/db/migrator.py` imports `kospex_schema` inside three methods; a module-level import the other way creates a cycle. Always import inside the function body.
- **The banner goes to stderr, never stdout.** `kospex list-repos -out file.csv` and krunner's CSV paths must keep byte-identical stdout.
- **Nothing blocks.** No command is refused, no exit code changes. The banner is the entire intervention.
- **Migrations are never auto-applied to an existing populated database.** Only to one being created.
- **Do not edit any file under `src/kospex/db/migrations/`.** Migrations are immutable once shipped.
- **Tests must set `KOSPEX_HOME` via `monkeypatch.setenv` AND call `HabitatConfig.reset_instance()`** — the config is a cached singleton, so setting the env var alone does not redirect the DB path. `tests/conftest.py` handles cleanup automatically.
- **Run tests with `PYTHONPATH=$PWD/src pytest`** — an editable install resolves the flat `kospex_*` modules to the main checkout, so without it tests silently exercise the wrong code.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/kospex/db/migrator.py` | **Modify.** Add `format_behind_banner()` and `warn_if_behind()`. |
| `src/kospex_schema.py` | **Modify.** Add `LAST_BOOTSTRAP` record and `_bootstrap_migrations()`; widen the `new_db` test. |
| `src/kospex/db/health.py` | **Create.** Read-only `db_status()`. |
| `src/kospex_cli.py` | **Modify.** Banner in the `cli` group callback; `Database:` block in `init`; fill the `system-status` stub. |
| `src/kgit.py`, `src/krunner.py`, `src/kreaper.py` | **Modify.** Banner in each group callback. |
| `src/kweb2.py` | **Modify.** Lifespan hook. |
| `src/kospex_utils.py` | **Modify.** `database` section in `validate_kospex_setup()`. |

Task order matters: Task 1 produces `warn_if_behind` (used by 4 and 5), Task 2 produces `LAST_BOOTSTRAP` (used by 3), Task 3 produces `db_status` (used by 6).

---

### Task 1: Behind-DB banner

**Files:**
- Modify: `src/kospex/db/migrator.py`
- Test: `tests/test_db_migrator.py`

**Interfaces:**
- Consumes: existing `Migrator.pending()`, `Migrator.discover()`.
- Produces:
  - `format_behind_banner(pending_count: int, version) -> str`
  - `warn_if_behind(db, quiet: bool = False, stream=None, migrations_dir=None) -> int` — returns the pending count (0 when current), prints the banner to `stream` (default `sys.stderr`), never raises, never blocks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db_migrator.py`:

```python
def _seed_migrations(tmp_path):
    """A migrations dir with two migrations, so pending counts are deterministic."""
    d = tmp_path / "migrations"
    d.mkdir()
    _write(d / "0003_a.sql", "CREATE TABLE a (id INTEGER);")
    _write(d / "0004_b.sql", "CREATE TABLE b (id INTEGER);")
    return d


def _db_with_migrations_table(tmp_path):
    import sqlite_utils
    import kospex_schema as KospexSchema
    db = sqlite_utils.Database(tmp_path / "kospex.db")
    db.execute(KospexSchema.SQL_CREATE_SCHEMA_MIGRATIONS)
    db.execute(KospexSchema.SQL_CREATE_KOSPEX_CONFIG)
    return db


def test_banner_lines_are_all_the_same_width():
    from kospex.db.migrator import format_behind_banner
    lines = format_behind_banner(3, 2).splitlines()
    assert len({len(line) for line in lines}) == 1


def test_banner_reports_count_and_version_and_fix_command():
    from kospex.db.migrator import format_behind_banner
    banner = format_behind_banner(3, 2)
    assert "3 migration(s) pending (DB version 2)." in banner
    assert "kospex upgrade-db -apply" in banner
    assert "DATABASE SCHEMA IS OUT OF DATE" in banner


def test_banner_widens_for_long_content():
    """A large count must not break the box."""
    from kospex.db.migrator import format_behind_banner
    lines = format_behind_banner(1234567, "unknown").splitlines()
    assert len({len(line) for line in lines}) == 1


def test_warn_if_behind_writes_banner_and_returns_count(tmp_path):
    import io
    from kospex.db.migrator import warn_if_behind
    db = _db_with_migrations_table(tmp_path)
    stream = io.StringIO()

    count = warn_if_behind(db, stream=stream, migrations_dir=_seed_migrations(tmp_path))

    assert count == 2
    assert "DATABASE SCHEMA IS OUT OF DATE" in stream.getvalue()


def test_warn_if_behind_silent_when_current(tmp_path):
    import io
    from kospex.db.migrator import warn_if_behind
    db = _db_with_migrations_table(tmp_path)
    # The directory must EXIST and be empty. A missing dir makes
    # validate_migrations() raise on iterdir(), which warn_if_behind swallows —
    # the test would then pass for the wrong reason.
    empty = tmp_path / "empty"
    empty.mkdir()
    stream = io.StringIO()

    count = warn_if_behind(db, stream=stream, migrations_dir=empty)

    assert count == 0
    assert stream.getvalue() == ""


def test_warn_if_behind_respects_quiet(tmp_path):
    import io
    from kospex.db.migrator import warn_if_behind
    db = _db_with_migrations_table(tmp_path)
    stream = io.StringIO()

    count = warn_if_behind(
        db, quiet=True, stream=stream, migrations_dir=_seed_migrations(tmp_path)
    )

    assert count == 0
    assert stream.getvalue() == ""


def test_warn_if_behind_handles_missing_schema_migrations_table(tmp_path):
    """A pre-migration-system DB has no schema_migrations: treat all as pending."""
    import io
    import sqlite_utils
    from kospex.db.migrator import warn_if_behind
    db = sqlite_utils.Database(tmp_path / "old.db")
    db.execute("CREATE TABLE commits (hash TEXT)")
    stream = io.StringIO()

    count = warn_if_behind(db, stream=stream, migrations_dir=_seed_migrations(tmp_path))

    assert count == 2
    assert "2 migration(s) pending" in stream.getvalue()


def test_warn_if_behind_never_raises_on_a_broken_db(tmp_path):
    """It is a warning, not a gate: any failure must be swallowed."""
    import io
    from kospex.db.migrator import warn_if_behind

    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

    assert warn_if_behind(Broken(), stream=io.StringIO()) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_db_migrator.py -k "banner or warn_if_behind" -v`
Expected: FAIL with `ImportError: cannot import name 'format_behind_banner'`

- [ ] **Step 3: Implement**

Add `import sys` to the imports at the top of `src/kospex/db/migrator.py` (it already imports `sqlite3`, `re`, `time`). Then add these three functions at module level, after `_utcnow_iso()` and before `class Migrator`:

```python
def format_behind_banner(pending_count: int, version) -> str:
    """Render the out-of-date banner.

    Pure string function so the box maths can be tested without a database.
    The box widens to fit its content — a large pending count or a long
    version string must not break the border alignment.
    """
    title = "DATABASE SCHEMA IS OUT OF DATE"
    body = [
        f"{pending_count} migration(s) pending (DB version {version}).",
        "Commands that write may fail or record incomplete data.",
        "",
        "Back up your database, then run:",
        "    kospex upgrade-db -apply",
    ]
    width = max(len(line) for line in [title, *body])
    inner = width + 4  # two spaces of padding each side

    def row(text: str) -> str:
        return "║  " + text.ljust(width) + "  ║"

    return "\n".join([
        "╔" + "═" * inner + "╗",
        row(title),
        "╠" + "═" * inner + "╣",
        *[row(line) for line in body],
        "╚" + "═" * inner + "╝",
    ])


def _current_version(db) -> str:
    """The KOSPEX_DB_VERSION_KEY value, or 'unknown' when unreadable."""
    import kospex_schema as KospexSchema
    try:
        rows = list(db.execute(
            "SELECT value FROM kospex_config WHERE key=? AND latest=1",
            [KospexSchema.KOSPEX_DB_VERSION_KEY],
        ).fetchall())
        return rows[0][0] if rows else "unknown"
    except Exception:
        return "unknown"


def warn_if_behind(db, quiet: bool = False, stream=None, migrations_dir=None) -> int:
    """Print the out-of-date banner when migrations are pending.

    Returns the pending count (0 when current or suppressed). Writes to stderr
    so it cannot corrupt piped stdout. Never raises and never blocks — the
    caller proceeds regardless. See changes/202608-migrations-on-clean-install.md.
    """
    if quiet:
        return 0
    stream = stream or sys.stderr
    try:
        migrator = Migrator(db, migrations_dir=migrations_dir)
        try:
            pending = migrator.pending()
        except sqlite3.OperationalError:
            # No schema_migrations table (pre-migration-system DB): nothing has
            # been applied, so everything on disk is pending.
            pending = migrator.discover()
    except Exception:
        return 0

    if not pending:
        return 0

    print(format_behind_banner(len(pending), _current_version(db)), file=stream)
    return len(pending)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_db_migrator.py -k "banner or warn_if_behind" -v`
Expected: PASS (8 tests)

- [ ] **Step 5: DRY up `print_status`**

`Migrator.print_status()` inlines the same version lookup. Replace its version block with the new helper. In `src/kospex/db/migrator.py`, find:

```python
        # Resolve current version int (kospex_config may not exist on baseline DB)
        try:
            version_row = list(self.db.execute(
                "SELECT value FROM kospex_config WHERE key=? AND latest=1",
                [KospexSchema.KOSPEX_DB_VERSION_KEY],
            ).fetchall())
            version = version_row[0][0] if version_row else "unknown"
        except sqlite3.OperationalError:
            version = "unknown"
```

Replace with:

```python
        version = _current_version(self.db)
```

- [ ] **Step 6: Run the full migrator suite**

Run: `PYTHONPATH=$PWD/src pytest tests/test_db_migrator.py tests/test_kospex_cli_upgrade_db.py -v`
Expected: PASS, no regressions (39 existing + 8 new)

- [ ] **Step 7: Commit**

```bash
git add src/kospex/db/migrator.py tests/test_db_migrator.py
git commit -m "feat(db): add a behind-migrations warning banner

warn_if_behind() reports pending migrations to stderr without blocking.
The box widens to fit its content so a large count cannot break the
border. Handles a missing schema_migrations table (pre-migration-system
DBs) by treating every discovered migration as pending, and swallows all
errors — it is a warning, not a gate."
```

---

### Task 2: Fresh-DB bootstrap

**Files:**
- Modify: `src/kospex_schema.py:444-486`
- Test: `tests/test_kospex_schema.py`

**Interfaces:**
- Consumes: `Migrator.apply_pending()` from Task 1's module.
- Produces:
  - `LAST_BOOTSTRAP: dict` with keys `created: bool`, `migrations_applied: int`, `migration_error: str | None` — module-level in `kospex_schema`, reset on every `connect_or_create_kospex_db()` call.
  - `_bootstrap_migrations(kospex_db) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_kospex_schema.py`:

```python
def _fresh_home(tmp_path, monkeypatch):
    """Point kospex at an empty KOSPEX_HOME.

    The HabitatConfig singleton caches the resolved paths, so setting the env
    var is not enough — it must be reset. conftest.py restores both afterwards.
    """
    from kospex.habitat_config import HabitatConfig
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()


def test_fresh_db_has_no_pending_migrations(tmp_path, monkeypatch):
    """The clean-install invariant.

    This fails the moment a new migration is added without checking the
    clean-install path, which is the whole point of it.
    """
    import kospex_schema as KospexSchema
    from kospex.db.migrator import Migrator
    _fresh_home(tmp_path, monkeypatch)

    db = KospexSchema.connect_or_create_kospex_db()

    assert Migrator(db).pending() == []


def test_fresh_db_has_migrated_columns(tmp_path, monkeypatch):
    import kospex_schema as KospexSchema
    _fresh_home(tmp_path, monkeypatch)

    db = KospexSchema.connect_or_create_kospex_db()

    repos = [c[1] for c in db.execute("PRAGMA table_info(repos)").fetchall()]
    deps = [c[1] for c in db.execute("PRAGMA table_info(dependency_data)").fetchall()]
    assert "last_fetch" in repos                 # 0004
    assert "last_sync_hash" in repos             # 0003
    assert "last_panopticas_version" in repos    # 0003
    assert "last_scc_version" in repos           # 0003
    assert "resolution" in deps                  # 0005


def test_fresh_db_records_migrations_as_applied(tmp_path, monkeypatch):
    import kospex_schema as KospexSchema
    _fresh_home(tmp_path, monkeypatch)

    db = KospexSchema.connect_or_create_kospex_db()

    applied = [r[0] for r in db.execute(
        "SELECT id FROM schema_migrations ORDER BY sequence").fetchall()]
    assert applied == [
        "0003_repos_sync_provenance",
        "0004_repos_last_fetch",
        "0005_dependency_data_resolution",
    ]
    assert KospexSchema.LAST_BOOTSTRAP["created"] is True
    assert KospexSchema.LAST_BOOTSTRAP["migrations_applied"] == 3
    assert KospexSchema.LAST_BOOTSTRAP["migration_error"] is None


def test_last_fetch_write_succeeds_on_a_fresh_db(tmp_path, monkeypatch):
    """Regression: kgit pull crashed with 'no such column: last_fetch'."""
    import kospex_schema as KospexSchema
    _fresh_home(tmp_path, monkeypatch)
    db = KospexSchema.connect_or_create_kospex_db()
    db["repos"].insert({"_repo_id": "github.com~acme~app"}, pk="_repo_id")

    db.execute(
        "UPDATE repos SET last_fetch = ? WHERE _repo_id = ?",
        ["2026-08-15T00:00:00+00:00", "github.com~acme~app"],
    )

    row = db.execute(
        "SELECT last_fetch FROM repos WHERE _repo_id = ?", ["github.com~acme~app"]
    ).fetchone()
    assert row[0] == "2026-08-15T00:00:00+00:00"


def test_resolution_write_succeeds_on_a_fresh_db(tmp_path, monkeypatch):
    """Regression: deps -save crashed with 'no column named resolution'."""
    import kospex_schema as KospexSchema
    _fresh_home(tmp_path, monkeypatch)
    db = KospexSchema.connect_or_create_kospex_db()

    db["dependency_data"].insert(
        {"hash": "h1", "package_name": "requests", "resolution": "resolved"}
    )

    row = db.execute("SELECT resolution FROM dependency_data").fetchone()
    assert row[0] == "resolved"


def test_existing_db_is_not_re_bootstrapped(tmp_path, monkeypatch):
    import kospex_schema as KospexSchema
    _fresh_home(tmp_path, monkeypatch)
    KospexSchema.connect_or_create_kospex_db()

    KospexSchema.connect_or_create_kospex_db()

    assert KospexSchema.LAST_BOOTSTRAP["created"] is False
    assert KospexSchema.LAST_BOOTSTRAP["migrations_applied"] == 0


def test_zero_table_db_file_is_repaired(tmp_path, monkeypatch):
    """A kweb-first clean install leaves an empty file behind.

    os.path.isfile() is then true, so the isfile-only check would never
    bootstrap it and the DB would stay unmigrated forever.
    """
    import sqlite3
    import kospex_schema as KospexSchema
    from kospex.db.migrator import Migrator
    _fresh_home(tmp_path, monkeypatch)
    sqlite3.connect(tmp_path / "kospex.db").close()   # zero tables
    assert (tmp_path / "kospex.db").exists()

    db = KospexSchema.connect_or_create_kospex_db()

    assert KospexSchema.LAST_BOOTSTRAP["created"] is True
    assert Migrator(db).pending() == []


def test_bootstrap_failure_is_not_fatal(tmp_path, monkeypatch):
    """A failed migration must leave a usable DB, loudly, not crash the CLI."""
    import kospex_schema as KospexSchema
    from kospex.db import migrator as migrator_module
    _fresh_home(tmp_path, monkeypatch)

    def boom(self):
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(migrator_module.Migrator, "apply_pending", boom)

    db = KospexSchema.connect_or_create_kospex_db()

    assert db is not None
    assert db["repos"].exists()
    assert "migration exploded" in KospexSchema.LAST_BOOTSTRAP["migration_error"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_kospex_schema.py -v`
Expected: FAIL — `test_fresh_db_has_no_pending_migrations` asserts `[] == [3 migrations]`, and `LAST_BOOTSTRAP` does not exist.

- [ ] **Step 3: Add the bootstrap record and helper**

In `src/kospex_schema.py`, add `import sys` to the imports at the top (the file currently imports `os` on line 2 but not `sys`).

Add this immediately above `def connect_or_create_kospex_db():`:

```python
# What the most recent connect_or_create_kospex_db() call actually did.
#
# `kospex init --validate` cannot simply report "the DB exists" — the
# module-level Kospex() in each CLI runs at import, so the DB has always just
# been created by the time the command body runs. This record lets it report
# what happened instead. See changes/202608-migrations-on-clean-install.md.
LAST_BOOTSTRAP = {
    "created": False,
    "migrations_applied": 0,
    "migration_error": None,
}


def _bootstrap_migrations(kospex_db):
    """Apply pending migrations to a newly created database.

    Migrator is imported inside the function on purpose: kospex/db/migrator.py
    imports this module, so a module-level import here is a circular import.

    Failure is loud but not fatal. Each migration is individually
    transactional, so a partial failure leaves the DB consistent at whatever
    version succeeded; the caller gets a usable database either way.
    """
    try:
        from kospex.db.migrator import Migrator
        ran = Migrator(kospex_db).apply_pending()
        LAST_BOOTSTRAP["migrations_applied"] = len(ran)
    except Exception as exc:
        LAST_BOOTSTRAP["migration_error"] = str(exc)
        print(
            "\nWARNING: could not apply database migrations to the new database:"
            f"\n  {exc}"
            "\n  The database is usable but may be missing columns."
            "\n  Run `kospex upgrade-db` to see what is pending.\n",
            file=sys.stderr,
        )
```

- [ ] **Step 4: Widen the `new_db` test and call the bootstrap**

In `connect_or_create_kospex_db()`, replace lines 446-452:

```python
    new_db = False
    db_path = KospexUtils.get_kospex_db_path()

    if not os.path.isfile(db_path):
        new_db = True

    kospex_db = Database(db_path)
```

with:

```python
    db_path = KospexUtils.get_kospex_db_path()
    new_db = not os.path.isfile(db_path)

    kospex_db = Database(db_path)

    # A file with no kospex_config is not a usable database — it is the empty
    # file sqlite leaves behind when something opened a missing DB path (kweb
    # via KospexQuery did exactly this). Treat it as new so it gets the version
    # stamp and the migrations, rather than being stuck unmigrated forever
    # because os.path.isfile() happens to be true.
    #
    # This must be tested BEFORE the CREATE block below, which would create
    # kospex_config itself and defeat the check.
    if not new_db and not kospex_db["kospex_config"].exists():
        new_db = True
```

Then replace the `if new_db:` block at the end (lines 479-484) with:

```python
    LAST_BOOTSTRAP["created"] = new_db
    LAST_BOOTSTRAP["migrations_applied"] = 0
    LAST_BOOTSTRAP["migration_error"] = None

    if new_db:
        # Set the database version
        kospex_db.execute(
            f"INSERT INTO {TBL_KOSPEX_CONFIG} (key, value, format, latest) VALUES (?, ?, ?, ?)",
            [KOSPEX_DB_VERSION_KEY, str(KOSPEX_DB_VERSION), 'INTEGER', 1]
        )
        _bootstrap_migrations(kospex_db)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_kospex_schema.py -v`
Expected: PASS (4 existing + 8 new)

- [ ] **Step 6: Run the full suite for regressions**

Run: `PYTHONPATH=$PWD/src pytest -q`
Expected: PASS. Every test that builds a DB now runs migrations too, so watch for tests asserting exact column lists.

- [ ] **Step 7: Verify by hand against a real clean install**

```bash
rm -rf /tmp/kospex-clean && mkdir -p /tmp/kospex-clean
KOSPEX_HOME=/tmp/kospex-clean PYTHONPATH=$PWD/src python -m kospex_cli upgrade-db
```
Expected: `No pending migrations. DB is at version 5.`

- [ ] **Step 8: Commit**

```bash
git add src/kospex_schema.py tests/test_kospex_schema.py
git commit -m "fix(db): apply migrations when the database is created

A clean install built the frozen v2 baseline and never ran the migrations,
so a new DB was three behind on first use: kgit pull crashed on
repos.last_fetch and deps -save on dependency_data.resolution.

The new_db test now also treats a file with no kospex_config as new, which
repairs the zero-table DB left behind when something opens a missing path;
isfile() alone would have left it unmigrated permanently."
```

---

### Task 3: Read-only DB health helper

**Files:**
- Create: `src/kospex/db/health.py`
- Test: `tests/test_db_health.py`

**Interfaces:**
- Consumes: `KospexSchema.LAST_BOOTSTRAP` (Task 2), `Migrator` (Task 1).
- Produces: `db_status(db=None) -> dict` with exactly these keys: `path: str`, `exists: bool`, `writable: bool`, `version: str`, `applied_count: int`, `pending_count: int`, `pending_ids: list[str]`, `schema_migrations_present: bool`, `created_this_run: bool`, `migrations_applied_this_run: int`, `migration_error: str | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_health.py`:

```python
"""Tests for kospex.db.health.db_status().

db_status() is the single source of DB health for `kospex init --validate`,
`kospex init` and `kospex system-status`. It must never create or modify the
database — it only describes it.
"""


def _fresh_home(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()


def test_status_on_a_missing_db(tmp_path, monkeypatch):
    from kospex.db.health import db_status
    _fresh_home(tmp_path, monkeypatch)

    status = db_status()

    assert status["exists"] is False
    assert status["pending_count"] == 0
    assert status["version"] == "unknown"


def test_status_does_not_create_the_db(tmp_path, monkeypatch):
    """The whole point of the helper: describing must not be creating."""
    import os
    from kospex.db.health import db_status
    _fresh_home(tmp_path, monkeypatch)

    db_status()

    assert not os.path.isfile(tmp_path / "kospex.db")


def test_status_on_a_fresh_db_is_current(tmp_path, monkeypatch):
    import kospex_schema as KospexSchema
    from kospex.db.health import db_status
    _fresh_home(tmp_path, monkeypatch)
    db = KospexSchema.connect_or_create_kospex_db()

    status = db_status(db)

    assert status["exists"] is True
    assert status["pending_count"] == 0
    assert status["applied_count"] == 3
    assert status["schema_migrations_present"] is True
    assert status["created_this_run"] is True
    assert status["migrations_applied_this_run"] == 3
    assert status["migration_error"] is None


def test_status_reports_pending_on_a_behind_db(tmp_path, monkeypatch):
    """A v2 baseline DB with the migrations never applied."""
    import sqlite_utils
    import kospex_schema as KospexSchema
    from kospex.db.health import db_status
    _fresh_home(tmp_path, monkeypatch)

    db = sqlite_utils.Database(tmp_path / "kospex.db")
    db.execute(KospexSchema.SQL_CREATE_SCHEMA_MIGRATIONS)
    db.execute(KospexSchema.SQL_CREATE_KOSPEX_CONFIG)
    db.execute(
        "INSERT INTO kospex_config (key, value, format, latest) VALUES (?, ?, ?, ?)",
        [KospexSchema.KOSPEX_DB_VERSION_KEY, "2", "INTEGER", 1],
    )

    status = db_status(db)

    assert status["pending_count"] == 3
    assert status["applied_count"] == 0
    assert status["version"] == "2"
    assert "0004_repos_last_fetch" in status["pending_ids"]


def test_status_handles_a_missing_schema_migrations_table(tmp_path, monkeypatch):
    import sqlite_utils
    from kospex.db.health import db_status
    _fresh_home(tmp_path, monkeypatch)
    db = sqlite_utils.Database(tmp_path / "kospex.db")
    db.execute("CREATE TABLE commits (hash TEXT)")

    status = db_status(db)

    assert status["schema_migrations_present"] is False
    assert status["pending_count"] == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_db_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kospex.db.health'`

- [ ] **Step 3: Implement**

Create `src/kospex/db/health.py`:

```python
"""Read-only health reporting for the kospex database.

Single source of DB status for `kospex init --validate`, `kospex init` and
`kospex system-status`, so the three cannot drift. Nothing here creates or
modifies the database.
"""
import os
import sqlite3


def db_status(db=None) -> dict:
    """Describe the kospex database without touching it.

    Every import is inside the function: kospex_schema imports kospex_utils,
    and kospex/db/migrator.py imports kospex_schema, so module-level imports
    here would cycle.

    `created_this_run` / `migrations_applied_this_run` come from the bootstrap
    record rather than being re-derived. The module-level Kospex() in each CLI
    runs at import, so "does the DB exist?" is always true by the time anything
    asks — the record is what makes the answer honest.
    """
    import sqlite_utils
    import kospex_schema as KospexSchema
    import kospex_utils as KospexUtils
    from kospex.db.migrator import Migrator, _current_version

    path = KospexUtils.get_kospex_db_path()
    exists = os.path.isfile(path)
    parent = os.path.dirname(path) or "."

    status = {
        "path": path,
        "exists": exists,
        "writable": os.access(path, os.W_OK) if exists else os.access(parent, os.W_OK),
        "version": "unknown",
        "applied_count": 0,
        "pending_count": 0,
        "pending_ids": [],
        "schema_migrations_present": False,
        "created_this_run": KospexSchema.LAST_BOOTSTRAP["created"],
        "migrations_applied_this_run": KospexSchema.LAST_BOOTSTRAP["migrations_applied"],
        "migration_error": KospexSchema.LAST_BOOTSTRAP["migration_error"],
    }

    if not exists:
        return status

    if db is None:
        # Safe: the file exists, so this opens rather than creates.
        db = sqlite_utils.Database(path)

    status["version"] = _current_version(db)

    migrator = Migrator(db)
    try:
        applied = migrator.applied()
        status["schema_migrations_present"] = True
        status["applied_count"] = len(applied)
    except sqlite3.OperationalError:
        applied = []

    try:
        discovered = migrator.discover()
    except Exception:
        discovered = []

    pending = [m for m in discovered if m.id not in set(applied)]
    status["pending_count"] = len(pending)
    status["pending_ids"] = [m.id for m in pending]

    return status
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_db_health.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/health.py tests/test_db_health.py
git commit -m "feat(db): add read-only db_status() health helper

Single source of DB health for init --validate, init and system-status.
Reports the bootstrap record rather than re-deriving 'does the DB exist',
which is always true by the time a command body runs — the module-level
Kospex() in each CLI creates it at import."
```

---

### Task 4: Banner on every CLI command

**Files:**
- Modify: `src/kospex_cli.py:89-121` (the `cli` group callback)
- Modify: `src/kgit.py:134-141`, `src/krunner.py:43-48`, `src/kreaper.py:13-19`
- Test: `tests/test_migration_banner_cli.py`

**Interfaces:**
- Consumes: `warn_if_behind` from Task 1.
- Produces: no new API. Behavioural: the banner appears on stderr for every executed subcommand of all four CLIs.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_migration_banner_cli.py`:

```python
"""The behind-migrations banner must appear on every executed subcommand.

It goes to stderr, never blocks, and never changes an exit code. Click 8.3
separates stderr from stdout on CliRunner results by default.
"""
import pytest
from click.testing import CliRunner


def _behind_db(tmp_path, monkeypatch):
    """A fully-built DB whose migrations are recorded as never applied.

    Built via connect_or_create_kospex_db() and then rewound, rather than
    hand-rolling two tables: the subcommands invoked below run real queries, so
    the DB needs its full schema. `pending()` depends only on the
    schema_migrations rows, so clearing them is what makes it read as behind.
    """
    import kospex_schema as KospexSchema
    from kospex.habitat_config import HabitatConfig
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()

    db = KospexSchema.connect_or_create_kospex_db()
    db.execute("DELETE FROM schema_migrations")
    db.execute(
        "UPDATE kospex_config SET value = '2' WHERE key = ?",
        [KospexSchema.KOSPEX_DB_VERSION_KEY],
    )
    db.conn.commit()
    return db


def test_banner_shows_on_a_kospex_subcommand(tmp_path, monkeypatch):
    import kospex_cli
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db", db)

    result = CliRunner().invoke(kospex_cli.cli, ["list-repos"])

    assert "DATABASE SCHEMA IS OUT OF DATE" in result.stderr


def test_banner_goes_to_stderr_not_stdout(tmp_path, monkeypatch):
    """stdout must stay clean — `kospex list-repos -out x.csv` pipes it."""
    import kospex_cli
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db", db)

    result = CliRunner().invoke(kospex_cli.cli, ["list-repos"])

    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stdout


def test_banner_suppressed_by_quiet(tmp_path, monkeypatch):
    import kospex_cli
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db", db)

    result = CliRunner().invoke(kospex_cli.cli, ["--quiet", "list-repos"])

    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stderr


def test_no_banner_on_help(tmp_path, monkeypatch):
    """--help exits during parsing, before the group callback runs."""
    import kospex_cli
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db", db)

    result = CliRunner().invoke(kospex_cli.cli, ["--help"])

    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stderr
    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stdout


def test_banner_does_not_change_the_exit_code(tmp_path, monkeypatch):
    """It warns; it does not gate."""
    import kospex_cli
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(kospex_cli.kospex, "kospex_db", db)

    result = CliRunner().invoke(kospex_cli.cli, ["list-repos"])

    assert result.exit_code == 0


@pytest.mark.parametrize(
    "module_name,subcommand",
    [("kgit", "status"), ("krunner", "repos"), ("kreaper", "repos")],
)
def test_banner_shows_on_the_other_clis(module_name, subcommand, tmp_path, monkeypatch):
    """`group subcmd --help` runs the group callback then exits cleanly.

    Verified against Click 8.3.1: the group callback runs for
    ['subcmd', '--help'] but not for ['--help']. That lets each CLI's callback
    be exercised without constructing valid arguments for its subcommand.
    """
    import importlib
    module = importlib.import_module(module_name)
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(module.kospex, "kospex_db", db)

    result = CliRunner().invoke(module.cli, [subcommand, "--help"])

    assert "DATABASE SCHEMA IS OUT OF DATE" in result.stderr
    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stdout


@pytest.mark.parametrize("module_name", ["kgit", "krunner", "kreaper"])
def test_no_banner_on_group_help_for_the_other_clis(module_name, tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module(module_name)
    db = _behind_db(tmp_path, monkeypatch)
    monkeypatch.setattr(module.kospex, "kospex_db", db)

    result = CliRunner().invoke(module.cli, ["--help"])

    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stdout
    assert "DATABASE SCHEMA IS OUT OF DATE" not in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_migration_banner_cli.py -v`
Expected: FAIL — no banner in stderr.

- [ ] **Step 3: Wire `kospex_cli.py`**

Add to the imports near the existing `from kospex.db.migrator import Migrator` in `src/kospex_cli.py`:

```python
from kospex.db.migrator import Migrator, warn_if_behind
```

(If the existing import line differs, extend it rather than adding a second import of the same module.)

In the `cli` group callback, add the call after the bare-invocation block. The `ctx.exit(0)` above raises, so a bare `kospex` shows help without the banner:

```python
    if ctx.invoked_subcommand is None:
        # Default behavior when no command is provided
        click.echo(ctx.get_help())
        ctx.exit(0)

    # A behind DB can silently damage data (a deps -save blanks a manifest's
    # current dependencies), so warn before every real subcommand. Never blocks.
    warn_if_behind(kospex.kospex_db, quiet=quiet)
```

- [ ] **Step 4: Wire the other three CLIs**

In `src/kgit.py`, add `from kospex.db.migrator import warn_if_behind` to the imports, and give the group callback a body:

```python
@click.group()
@click.version_option(version=Kospex.VERSION)
def cli():
    """kgit (Kospex Git) is a utility for doing git things with kospex use cases.

    For documentation on how commands run `kgit COMMAND --help`.

    """
    warn_if_behind(kospex.kospex_db)
```

In `src/krunner.py`, same import, and:

```python
@click.group()
def cli():
    """krunner (Kospex Runner) is a utility for running shell commands on multiple git repos.

    For documentation on how commands run `krunner COMMAND --help`.

    """
    warn_if_behind(kospex.kospex_db)
```

In `src/kreaper.py`, same import, and:

```python
@click.group()
def cli():
    """kreaper (Kospex Reaper) is a utility for destroying and deleting thigs in the kospex DB.

    For documentation on how commands run `kreaper COMMAND --help`.

    """
    warn_if_behind(kospex.kospex_db)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_migration_banner_cli.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Run the full suite**

Run: `PYTHONPATH=$PWD/src pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/kospex_cli.py src/kgit.py src/krunner.py src/kreaper.py tests/test_migration_banner_cli.py
git commit -m "feat(cli): warn on every command when the DB is behind

Called from each Click group callback rather than from the DB connect, so
it fires only for subcommands that actually run — the module-level Kospex()
connects at import, which would otherwise put the banner on --help and on
tab-completion.

stderr only, exit codes unchanged, nothing refused."
```

---

### Task 5: kweb startup bootstrap

**Files:**
- Modify: `src/kweb2.py:36-52`
- Test: `tests/test_kweb_startup_db.py`

**Interfaces:**
- Consumes: `connect_or_create_kospex_db()` (Task 2), `warn_if_behind` (Task 1).
- Produces: `lifespan(app)` async context manager attached to the FastAPI app.

- [ ] **Step 1: Write the failing test**

Create `tests/test_kweb_startup_db.py`:

```python
"""kweb must build the schema at startup.

KospexQuery.__init__ opens the DB path directly with sqlite_utils, which
CREATES an empty file when the path is missing. On a clean install where kweb
runs first, every request then died on 'no such table: kospex_config' — and the
empty file it left behind made os.path.isfile() true, so the DB would never be
bootstrapped afterwards either.
"""


def _fresh_home(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()


def test_kospex_query_alone_leaves_an_unusable_db(tmp_path, monkeypatch):
    """Characterises the bug the lifespan hook exists to prevent."""
    import pytest
    import sqlite3
    from kospex_query import KospexQuery
    _fresh_home(tmp_path, monkeypatch)

    query = KospexQuery()

    assert query.kospex_db.table_names() == []
    with pytest.raises(sqlite3.OperationalError):
        query.get_kospex_db_version()


def test_lifespan_builds_and_migrates_the_schema(tmp_path, monkeypatch):
    import asyncio
    import kweb2
    from kospex.db.migrator import Migrator
    import kospex_schema as KospexSchema
    _fresh_home(tmp_path, monkeypatch)

    async def run_startup():
        async with kweb2.lifespan(kweb2.app):
            pass

    asyncio.run(run_startup())

    db = KospexSchema.connect_or_create_kospex_db()
    assert db["kospex_config"].exists()
    assert Migrator(db).pending() == []


def test_lifespan_startup_failure_does_not_stop_the_server(tmp_path, monkeypatch):
    """A broken DB should surface in the log, not prevent kweb booting."""
    import asyncio
    import kweb2
    import kospex_schema as KospexSchema
    _fresh_home(tmp_path, monkeypatch)

    def boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(KospexSchema, "connect_or_create_kospex_db", boom)

    async def run_startup():
        async with kweb2.lifespan(kweb2.app):
            return True

    assert asyncio.run(run_startup()) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_kweb_startup_db.py -v`
Expected: FAIL with `AttributeError: module 'kweb2' has no attribute 'lifespan'`. The first test should already PASS — it characterises existing broken behaviour.

- [ ] **Step 3: Implement**

In `src/kweb2.py`, add to the imports (the file already imports `sys` on line 6):

```python
from contextlib import asynccontextmanager

import kospex_schema as KospexSchema
from kospex.db.migrator import warn_if_behind
```

Insert this immediately before `app = FastAPI(` on line 48:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build (and migrate) the kospex schema once, before serving any request.

    kweb reaches the DB through KospexQuery, which opens the path directly and
    so CREATES an empty file when it is missing — on a clean install that meant
    every request failed on 'no such table: kospex_config'. Doing it here also
    lets kweb participate in the fresh-DB migration bootstrap.

    Once per server boot, not per request: the 47 KospexQuery call sites would
    otherwise re-run ~19 CREATE TABLE IF NOT EXISTS statements each time.
    """
    try:
        db = KospexSchema.connect_or_create_kospex_db()
        pending = warn_if_behind(db)
        if pending:
            logger.warning(
                "kospex DB is %s migration(s) behind - run `kospex upgrade-db -apply`",
                pending,
            )
    except Exception as exc:
        # Never prevent the server booting; the failure is visible in the log
        # and every request will report it anyway.
        logger.error("Could not initialise the kospex database at startup: %s", exc)
    yield


app = FastAPI(
    title="Kospex Web",
    description="Kospex Code and Developer analytics platform",
    version=Kospex.VERSION,
    lifespan=lifespan,
)
```

Note: `logger` is already defined on line 40, above this insertion point.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_kweb_startup_db.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Verify the web suite still passes**

Run: `PYTHONPATH=$PWD/src pytest tests/test_web_endpoints.py tests/test_kweb_help.py -v`
Expected: PASS (many are skipped by design)

- [ ] **Step 6: Commit**

```bash
git add src/kweb2.py tests/test_kweb_startup_db.py
git commit -m "fix(kweb): build the DB schema at startup

kweb reaches the DB via KospexQuery, which opens the path directly and so
creates an empty file when it is missing. On a clean install where kweb ran
first, every request died on 'no such table: kospex_config' — and the empty
file left behind made isfile() true, so the DB was never bootstrapped
afterwards either.

A lifespan hook runs the schema build once per boot, which also lets kweb
participate in the fresh-DB migration bootstrap."
```

---

### Task 6: DB health in `init` and `system-status`

**Files:**
- Modify: `src/kospex_utils.py:215-281` (`validate_kospex_setup`)
- Modify: `src/kospex_cli.py:123-254` (`init`), `src/kospex_cli.py:1300` (`system-status`)
- Test: `tests/test_kospex_utils.py`

**Interfaces:**
- Consumes: `db_status()` from Task 3.
- Produces: `validation["database"]` — the full `db_status()` dict — plus a `"Database N migration(s) behind"` entry in `critical_issues` when pending, which flips `overall_status` to `issues_found`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_kospex_utils.py`:

```python
def _fresh_home_util(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig
    monkeypatch.setenv("KOSPEX_HOME", str(tmp_path))
    HabitatConfig.reset_instance()


def _behind_db_at(tmp_path):
    """Build the DB, then rewind it to look like migrations never ran."""
    import kospex_schema as KospexSchema
    db = KospexSchema.connect_or_create_kospex_db()
    db.execute("DELETE FROM schema_migrations")
    db.execute(
        "UPDATE kospex_config SET value = '2' WHERE key = ?",
        [KospexSchema.KOSPEX_DB_VERSION_KEY],
    )
    db.conn.commit()
    return db


def test_validate_includes_a_database_section(tmp_path, monkeypatch):
    import kospex_utils as KospexUtils
    _fresh_home_util(tmp_path, monkeypatch)
    _behind_db_at(tmp_path)

    validation = KospexUtils.validate_kospex_setup()

    assert "database" in validation
    assert validation["database"]["pending_count"] == 3


def test_behind_db_is_not_healthy(tmp_path, monkeypatch):
    """The regression: a clean install reported HEALTHY over a DB that would
    crash kgit pull."""
    import kospex_utils as KospexUtils
    _fresh_home_util(tmp_path, monkeypatch)
    _behind_db_at(tmp_path)

    validation = KospexUtils.validate_kospex_setup()

    assert validation["overall_status"] != "healthy"
    assert any("migration" in issue for issue in validation["critical_issues"])
    assert any("upgrade-db" in rec for rec in validation["recommendations"])


def test_current_db_is_healthy(tmp_path, monkeypatch):
    import kospex_schema as KospexSchema
    import kospex_utils as KospexUtils
    _fresh_home_util(tmp_path, monkeypatch)
    KospexSchema.connect_or_create_kospex_db()

    validation = KospexUtils.validate_kospex_setup()

    assert validation["database"]["pending_count"] == 0
    assert validation["overall_status"] == "healthy"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_kospex_utils.py -v`
Expected: FAIL with `KeyError: 'database'`

- [ ] **Step 3: Add the database section to `validate_kospex_setup()`**

In `src/kospex_utils.py`, insert after the logging block (after the `validation["logging"]` assignment, before "Generate recommendations"):

```python
    # DB health. Imported here, not at module level: kospex_schema imports this
    # module, and kospex/db/* imports kospex_schema.
    try:
        from kospex.db.health import db_status
        validation["database"] = db_status()
    except Exception as e:
        validation["database"] = {"error": str(e), "pending_count": 0}
```

`validation` is initialised near line 222 — add `"database": None,` and `"critical_issues": [],` to that literal so both keys always exist:

```python
    validation = {
        "environment_vars": {},
        "directories": {},
        "logging": None,
        "database": None,
        "overall_status": "unknown",
        "recommendations": [],
        "critical_issues": []
    }
```

In the recommendations block, add:

```python
    if validation["database"] and validation["database"].get("pending_count"):
        validation["recommendations"].append(
            "Run 'kospex upgrade-db -apply' to apply pending database migrations"
        )
```

In the critical-issues block, add before the `if not critical_issues:` test:

```python
    database = validation["database"] or {}
    if database.get("pending_count"):
        critical_issues.append(
            f"Database {database['pending_count']} migration(s) behind"
        )
    if database.get("exists") and not database.get("writable"):
        critical_issues.append("Database not writable")
```

Then replace the status assignment so `critical_issues` is always populated:

```python
    if not critical_issues:
        validation["overall_status"] = "healthy"
    else:
        validation["overall_status"] = "issues_found"
    validation["critical_issues"] = critical_issues
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_kospex_utils.py -v`
Expected: PASS (4 existing + 3 new)

- [ ] **Step 5: Print the Database block in `kospex init --validate`**

In `src/kospex_cli.py`, inside the `if validate:` branch, after the Logging System block and before the Recommendations block:

```python
        database = validation.get("database") or {}
        if database:
            print("\nDatabase:")
            if database.get("error"):
                print(f"  ✗ Could not read database status: {database['error']}")
            else:
                mark = "✓" if not database.get("pending_count") else "⚠"
                print(f"  {mark} {database['path']}")
                if database.get("created_this_run"):
                    applied = database.get("migrations_applied_this_run", 0)
                    print(f"      created during this invocation, {applied} migration(s) applied")
                elif database.get("pending_count"):
                    print(
                        f"      version {database['version']}, "
                        f"{database['pending_count']} migration(s) pending"
                    )
                else:
                    print(f"      version {database['version']}, up to date")
                if database.get("migration_error"):
                    print(f"  ✗ Migration error: {database['migration_error']}")
```

- [ ] **Step 6: Report the DB in the normal `init` path**

In the same command, after the scc block and before the code-directory block:

```python
    # Report DB state alongside the directory and scc checks.
    try:
        from kospex.db.health import db_status
        db_info = db_status()
        if db_info.get("created_this_run"):
            applied = db_info.get("migrations_applied_this_run", 0)
            print(f"\n✓ Created database: {db_info['path']}")
            print(f"  Applied {applied} migration(s), now at version {db_info['version']}")
        elif db_info.get("pending_count"):
            print(f"\n⚠ Database is {db_info['pending_count']} migration(s) behind")
            print("  Run 'kospex upgrade-db -apply' to update it")
        elif verbose:
            print(f"\n✓ Database up to date: {db_info['path']} (version {db_info['version']})")
    except Exception as e:
        log.error(f"Could not read database status: {e}")
        print(f"\n✗ Could not read database status: {e}")
```

- [ ] **Step 7: Fill the empty `system-status` heading**

In `src/kospex_cli.py`, the `status()` function has a `print("Database table version status\n")` followed by nothing. Replace that line with:

```python
    print("Database table version status")
    print("-----------------------------")
    try:
        from kospex.db.health import db_status
        db_info = db_status()
        print(f"Path:\t\t{db_info['path']}")
        print(f"Version:\t{db_info['version']}")
        print(f"Applied:\t{db_info['applied_count']} migration(s)")
        if db_info["pending_count"]:
            print(f"Pending:\t{db_info['pending_count']} migration(s)")
            for migration_id in db_info["pending_ids"]:
                print(f"\t\t  {migration_id}")
            print("\nRun 'kospex upgrade-db -apply' to apply them.")
        else:
            print("Pending:\tnone")
    except Exception as e:
        print(f"Could not read database status: {e}")
    print()
```

- [ ] **Step 8: Verify by hand**

```bash
rm -rf /tmp/kospex-init && mkdir -p /tmp/kospex-init
KOSPEX_HOME=/tmp/kospex-init PYTHONPATH=$PWD/src python -m kospex_cli init --validate
```
Expected: a `Database:` block reporting `created during this invocation, 3 migration(s) applied`, and `Overall Status: HEALTHY`.

```bash
KOSPEX_HOME=/tmp/kospex-init PYTHONPATH=$PWD/src python -m kospex_cli system-status
```
Expected: the `Database table version status` section shows version 5, 3 applied, no pending.

- [ ] **Step 9: Run the full suite**

Run: `PYTHONPATH=$PWD/src pytest -q`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/kospex_utils.py src/kospex_cli.py tests/test_kospex_utils.py
git commit -m "feat(cli): report database health from init and system-status

kospex init checked directories, permissions, env vars and logging but
never the DB, so on a clean install it reported HEALTHY over a database
three migrations behind that would crash kgit pull. A behind or unwritable
DB is now a critical issue.

Also fills the 'Database table version status' heading in system-status,
which printed a header and nothing else."
```

---

## Final Verification

- [ ] **Full suite:** `PYTHONPATH=$PWD/src pytest -q` — expect all pass, 0 failures.
- [ ] **Clean install end-to-end:**
```bash
rm -rf /tmp/kospex-e2e && mkdir -p /tmp/kospex-e2e
KOSPEX_HOME=/tmp/kospex-e2e PYTHONPATH=$PWD/src python -m kospex_cli upgrade-db
```
Expect `No pending migrations. DB is at version 5.`
- [ ] **Banner appears on a behind DB** and stdout stays clean:
```bash
KOSPEX_HOME=/tmp/kospex-e2e PYTHONPATH=$PWD/src python -m kospex_cli list-repos 2>/dev/null
```
Expect no banner text in stdout.
- [ ] **CHANGELOG:** add an entry under `## Unreleased` describing the clean-install migration fix, the kweb startup fix, and the new DB health reporting.

## Notes for the implementer

**Why `warn_if_behind` swallows every exception.** It runs before every command. If it can throw, a malformed migrations directory or an odd DB takes out the entire CLI. It is a warning, not a gate.

**Why the bootstrap does not swallow silently.** The opposite reasoning: `_bootstrap_migrations` prints to stderr and records the error. Silent `except: pass` around schema changes is exactly how this class of bug stayed invisible — see the provenance methods at `kospex_core.py:1148-1179`, which is why migration 0003's absence went unnoticed.

**Do not "fix" the unguarded call sites.** `set_repo_last_fetch` (`kospex_query.py:606`) and the `dependency_data` upserts stay unguarded, deliberately. See "Decisions taken" in the spec.

**Watch for test pollution.** `tests/conftest.py` resets the `HabitatConfig` singleton and `KOSPEX_*` env vars around every test, but only restores the *pristine* values — inside a test you must call `HabitatConfig.reset_instance()` yourself after setting `KOSPEX_HOME`, or the DB path resolves to the previous location.
