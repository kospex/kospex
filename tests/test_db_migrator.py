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
