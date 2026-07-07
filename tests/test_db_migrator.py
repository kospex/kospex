"""Tests for kospex.db.migrator."""
import hashlib
from pathlib import Path


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_migration_from_sql_only(tmp_path):
    from kospex.db.migrator import Migration
    sql = _write(tmp_path / "0003_add_widgets.sql", "CREATE TABLE widgets (id INTEGER);")

    m = Migration.from_paths(sql_path=sql, py_path=None)

    assert m.id == "0003_add_widgets"
    assert m.sequence == 3
    assert m.slug == "add_widgets"
    assert m.sql_path == sql
    assert m.py_path is None


def test_migration_from_sql_and_python(tmp_path):
    from kospex.db.migrator import Migration
    sql = _write(tmp_path / "0004_backfill.sql", "ALTER TABLE widgets ADD COLUMN x INTEGER;")
    py = _write(tmp_path / "0004_backfill.py", "def up(db): pass\n")

    m = Migration.from_paths(sql_path=sql, py_path=py)

    assert m.id == "0004_backfill"
    assert m.sequence == 4
    assert m.py_path == py


def test_checksum_combines_sql_and_python(tmp_path):
    from kospex.db.migrator import Migration
    sql = _write(tmp_path / "0003_x.sql", "CREATE TABLE x (id INTEGER);")
    py = _write(tmp_path / "0003_x.py", "def up(db): pass\n")
    m = Migration.from_paths(sql_path=sql, py_path=py)

    expected = (
        hashlib.sha256(sql.read_bytes()).hexdigest()
        + ":"
        + hashlib.sha256(py.read_bytes()).hexdigest()
    )

    assert m.checksum() == expected


def test_checksum_sql_only_has_trailing_colon(tmp_path):
    from kospex.db.migrator import Migration
    sql = _write(tmp_path / "0003_x.sql", "CREATE TABLE x (id INTEGER);")
    m = Migration.from_paths(sql_path=sql, py_path=None)

    assert m.checksum() == hashlib.sha256(sql.read_bytes()).hexdigest() + ":"


def test_discover_empty_dir(tmp_path):
    from kospex.db.migrator import discover_migrations
    assert discover_migrations(tmp_path) == []


def test_discover_returns_sorted_by_sequence(tmp_path):
    from kospex.db.migrator import discover_migrations
    _write(tmp_path / "0005_e.sql", "SELECT 1;")
    _write(tmp_path / "0003_c.sql", "SELECT 1;")
    _write(tmp_path / "0004_d.sql", "SELECT 1;")

    migrations = discover_migrations(tmp_path)

    assert [m.id for m in migrations] == ["0003_c", "0004_d", "0005_e"]


def test_discover_pairs_sql_with_matching_python(tmp_path):
    from kospex.db.migrator import discover_migrations
    _write(tmp_path / "0003_x.sql", "SELECT 1;")
    _write(tmp_path / "0003_x.py", "def up(db): pass\n")
    _write(tmp_path / "0004_y.sql", "SELECT 1;")

    migrations = discover_migrations(tmp_path)

    by_id = {m.id: m for m in migrations}
    assert by_id["0003_x"].py_path is not None
    assert by_id["0004_y"].py_path is None


def test_discover_ignores_unrelated_files(tmp_path):
    from kospex.db.migrator import discover_migrations
    _write(tmp_path / "0003_x.sql", "SELECT 1;")
    _write(tmp_path / "README.md", "docs")
    _write(tmp_path / "__init__.py", "")
    _write(tmp_path / "not_a_migration.sql", "SELECT 1;")

    migrations = discover_migrations(tmp_path)

    assert [m.id for m in migrations] == ["0003_x"]


import pytest


def test_validate_duplicate_prefix_raises(tmp_path):
    from kospex.db.migrator import discover_migrations, validate_migrations
    # Two .sql files sharing prefix 0003 but different slugs — discovery will
    # only keep one (dict overwrite), so we have to construct the list manually
    # to test the validator's duplicate detection.
    from kospex.db.migrator import Migration
    sql1 = _write(tmp_path / "0003_foo.sql", "SELECT 1;")
    sql2 = _write(tmp_path / "0003_bar.sql", "SELECT 1;")
    migrations = [
        Migration.from_paths(sql_path=sql1, py_path=None),
        Migration.from_paths(sql_path=sql2, py_path=None),
    ]

    with pytest.raises(ValueError, match="Duplicate migration sequence: 3"):
        validate_migrations(migrations)


def test_validate_orphan_python_raises(tmp_path):
    from kospex.db.migrator import discover_migrations, validate_migrations
    _write(tmp_path / "0003_foo.py", "def up(db): pass\n")  # no matching .sql

    # discover_migrations only indexes .sql; the orphan .py is invisible to it.
    # Validator runs against the directory itself for this check.
    with pytest.raises(ValueError, match="Orphan Python migration file"):
        validate_migrations(discover_migrations(tmp_path), migrations_dir=tmp_path)


def test_validate_empty_sql_raises(tmp_path):
    from kospex.db.migrator import discover_migrations, validate_migrations
    _write(tmp_path / "0003_empty.sql", "   -- only a comment\n\n")

    with pytest.raises(ValueError, match="empty"):
        validate_migrations(discover_migrations(tmp_path))


def test_validate_python_missing_up_raises(tmp_path):
    from kospex.db.migrator import discover_migrations, validate_migrations
    _write(tmp_path / "0003_no_up.sql", "SELECT 1;")
    _write(tmp_path / "0003_no_up.py", "x = 1\n")  # no up() function

    with pytest.raises(ValueError, match="missing 'up'"):
        validate_migrations(discover_migrations(tmp_path))


def test_validate_clean_set_passes(tmp_path):
    from kospex.db.migrator import discover_migrations, validate_migrations
    _write(tmp_path / "0003_a.sql", "CREATE TABLE a (id INTEGER);")
    _write(tmp_path / "0004_b.sql", "CREATE TABLE b (id INTEGER);")
    _write(tmp_path / "0004_b.py", "def up(db): pass\n")

    # Should not raise
    validate_migrations(discover_migrations(tmp_path), migrations_dir=tmp_path)


import sqlite_utils


def _baseline_db(tmp_path):
    """A fresh kospex-shaped DB with the schema_migrations table only."""
    db = sqlite_utils.Database(tmp_path / "kospex.db")
    db.execute("""CREATE TABLE [schema_migrations] (
        [id] TEXT PRIMARY KEY,
        [sequence] INTEGER NOT NULL,
        [checksum] TEXT NOT NULL,
        [applied_at] TEXT NOT NULL,
        [duration_ms] INTEGER,
        [has_python] INTEGER NOT NULL
    )""")
    return db


def test_apply_sql_only_creates_table(tmp_path):
    from kospex.db.migrator import Migrator, discover_migrations
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_widgets.sql",
           "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrations = discover_migrations(migrations_dir)
    migrator.apply(migrations[0])

    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "widgets" in tables


def test_apply_records_in_schema_migrations(tmp_path):
    from kospex.db.migrator import Migrator, discover_migrations
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_widgets.sql", "CREATE TABLE widgets (id INTEGER);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrations = discover_migrations(migrations_dir)
    migrator.apply(migrations[0])

    rows = list(db.execute("SELECT id, sequence, has_python FROM schema_migrations").fetchall())
    assert rows == [("0003_widgets", 3, 0)]


def test_apply_succeeds_when_connection_already_in_transaction(tmp_path):
    """The real CLI reaches apply() with the sqlite_utils connection already
    mid-transaction (schema bootstrap on a fresh DB), which made apply()'s
    explicit BEGIN raise 'cannot start a transaction within a transaction'."""
    from kospex.db.migrator import Migrator, discover_migrations
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_widgets.sql", "CREATE TABLE widgets (id INTEGER);")

    # Simulate the bootstrap leaving an open implicit transaction.
    db.conn.execute("BEGIN")
    db.conn.execute("CREATE TABLE bootstrap (id INTEGER)")
    assert db.conn.in_transaction

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply(discover_migrations(migrations_dir)[0])  # must not raise

    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "widgets" in tables


def test_apply_runs_python_up_after_sql(tmp_path):
    from kospex.db.migrator import Migrator, discover_migrations
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_widgets.sql",
           "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);")
    _write(migrations_dir / "0003_widgets.py", """
def up(db):
    db["widgets"].insert({"id": 1, "name": "backfilled"})
""")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply(discover_migrations(migrations_dir)[0])

    rows = list(db.execute("SELECT id, name FROM widgets").fetchall())
    assert rows == [(1, "backfilled")]

    schema_rows = list(db.execute("SELECT has_python FROM schema_migrations").fetchall())
    assert schema_rows == [(1,)]


def test_apply_rolls_back_when_python_raises(tmp_path):
    from kospex.db.migrator import Migrator, discover_migrations
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_widgets.sql",
           "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    _write(migrations_dir / "0003_widgets.py", """
def up(db):
    raise RuntimeError("boom")
""")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migration = discover_migrations(migrations_dir)[0]

    with pytest.raises(RuntimeError, match="boom"):
        migrator.apply(migration)

    # SQL change should be rolled back
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "widgets" not in tables

    # No row recorded
    rows = list(db.execute("SELECT id FROM schema_migrations").fetchall())
    assert rows == []


def _add_config_table(db):
    db.execute("""CREATE TABLE [kospex_config] (
        [format] TEXT, [key] TEXT, [value] TEXT,
        [latest] INTEGER, [created_at] TEXT, [updated_at] TEXT,
        PRIMARY KEY(key, latest)
    )""")


def test_applied_returns_recorded_ids(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    db["schema_migrations"].insert({
        "id": "0003_a", "sequence": 3, "checksum": "x:",
        "applied_at": "2026-05-18T00:00:00Z", "duration_ms": 1, "has_python": 0,
    })

    assert Migrator(db, migrations_dir=tmp_path).applied() == ["0003_a"]


def test_pending_returns_discovered_minus_applied(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    db["schema_migrations"].insert({
        "id": "0003_a", "sequence": 3, "checksum": "x:",
        "applied_at": "2026-05-18T00:00:00Z", "duration_ms": 1, "has_python": 0,
    })
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_a.sql", "SELECT 1;")
    _write(migrations_dir / "0004_b.sql", "CREATE TABLE b (id INTEGER);")

    pending = Migrator(db, migrations_dir=migrations_dir).pending()
    assert [m.id for m in pending] == ["0004_b"]


def test_apply_pending_runs_all_pending_in_order(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_a.sql", "CREATE TABLE a (id INTEGER);")
    _write(migrations_dir / "0004_b.sql", "CREATE TABLE b (id INTEGER);")

    Migrator(db, migrations_dir=migrations_dir).apply_pending()

    applied = [r[0] for r in db.execute(
        "SELECT id FROM schema_migrations ORDER BY sequence"
    ).fetchall()]
    assert applied == ["0003_a", "0004_b"]


def test_apply_pending_stops_on_first_failure(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_good.sql", "CREATE TABLE good (id INTEGER);")
    _write(migrations_dir / "0004_broken.sql", "NOT VALID SQL;")
    _write(migrations_dir / "0005_later.sql", "CREATE TABLE later (id INTEGER);")

    with pytest.raises(Exception):
        Migrator(db, migrations_dir=migrations_dir).apply_pending()

    applied = [r[0] for r in db.execute(
        "SELECT id FROM schema_migrations ORDER BY sequence"
    ).fetchall()]
    assert applied == ["0003_good"]   # earlier success preserved
    # 0004 rolled back, 0005 never attempted


def test_apply_pending_updates_version_int(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    # Seed baseline version int
    db["kospex_config"].insert(
        {"key": "KOSPEX_DB_VERSION_KEY", "value": "2", "format": "INTEGER", "latest": 1},
        pk=["key", "latest"],
    )
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_a.sql", "CREATE TABLE a (id INTEGER);")
    _write(migrations_dir / "0004_b.sql", "CREATE TABLE b (id INTEGER);")

    Migrator(db, migrations_dir=migrations_dir).apply_pending()

    row = list(db.execute(
        "SELECT value FROM kospex_config WHERE key='KOSPEX_DB_VERSION_KEY' AND latest=1"
    ).fetchall())
    assert row == [("4",)]


def test_verify_checksums_clean(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_a.sql", "CREATE TABLE a (id INTEGER);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply_pending()

    issues = migrator.verify_checksums()

    assert issues == []


def test_verify_checksums_detects_modified_file(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    sql = _write(migrations_dir / "0003_a.sql", "CREATE TABLE a (id INTEGER);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply_pending()

    sql.write_text("CREATE TABLE a (id INTEGER, extra TEXT);")
    issues = migrator.verify_checksums()

    assert len(issues) == 1
    assert issues[0]["id"] == "0003_a"
    assert issues[0]["reason"] == "checksum_mismatch"


def test_verify_checksums_detects_missing_file(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    sql = _write(migrations_dir / "0003_a.sql", "CREATE TABLE a (id INTEGER);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply_pending()

    sql.unlink()
    issues = migrator.verify_checksums()

    assert len(issues) == 1
    assert issues[0]["id"] == "0003_a"
    assert issues[0]["reason"] == "file_missing"


import io


def test_print_status_no_pending(tmp_path, capsys):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()

    Migrator(db, migrations_dir=migrations_dir).print_status()

    out = capsys.readouterr().out
    assert "no migrations" in out.lower() or "0 pending" in out.lower()


def test_print_status_lists_applied_and_pending(tmp_path, capsys):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_done.sql", "CREATE TABLE done (id INTEGER);")
    _write(migrations_dir / "0004_pending.sql", "CREATE TABLE pending (id INTEGER);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply(migrator.discover()[0])  # apply just 0003

    migrator.print_status()

    out = capsys.readouterr().out
    assert "0003_done" in out
    assert "0004_pending" in out
    assert "Applied" in out or "applied" in out
    assert "Pending" in out or "pending" in out


def test_apply_invalidates_cache_so_python_up_sees_new_table(tmp_path):
    """Python up() must see tables/columns created by the SQL step in the same migration."""
    from kospex.db.migrator import Migrator, discover_migrations
    from kospex.db.introspect import get_kospex_tables

    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_widgets.sql",
           "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);")
    _write(migrations_dir / "0003_widgets.py", """
from kospex.db.introspect import get_kospex_tables

def up(db):
    tables = get_kospex_tables(db)
    if "widgets" not in tables:
        raise AssertionError(f"widgets not visible to up(); tables={sorted(tables)}")
    db["widgets"].insert({"id": 1, "name": "ok"})
""")

    # Prime the cache BEFORE applying so it's stale without the in-migration invalidation
    get_kospex_tables(db)

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply(discover_migrations(migrations_dir)[0])

    rows = list(db.execute("SELECT id, name FROM widgets").fetchall())
    assert rows == [(1, "ok")]
