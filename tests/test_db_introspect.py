"""Tests for kospex.db.introspect."""
from sqlite_utils import Database


def _make_db(tmp_path):
    db = Database(tmp_path / "test.db")
    db.execute("CREATE TABLE [repos] (_repo_id TEXT, name TEXT)")
    db.execute("CREATE TABLE [commits] (_repo_id TEXT, hash TEXT)")
    db.execute("CREATE TABLE [kospex_config] (key TEXT, value TEXT)")
    return db


def test_get_kospex_tables_lists_user_tables(tmp_path):
    from kospex.db.introspect import get_kospex_tables
    db = _make_db(tmp_path)

    tables = get_kospex_tables(db)

    assert tables == {"repos", "commits", "kospex_config"}


def test_get_kospex_tables_excludes_sqlite_internals(tmp_path):
    from kospex.db.introspect import get_kospex_tables
    db = _make_db(tmp_path)
    # sqlite auto-creates sqlite_sequence when AUTOINCREMENT is used; force one
    db.execute("CREATE TABLE [auto] (id INTEGER PRIMARY KEY AUTOINCREMENT)")

    tables = get_kospex_tables(db)

    assert not any(t.startswith("sqlite_") for t in tables)
    assert "auto" in tables
