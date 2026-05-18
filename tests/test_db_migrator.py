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
