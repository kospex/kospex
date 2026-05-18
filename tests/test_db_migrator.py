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
