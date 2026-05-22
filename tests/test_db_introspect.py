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


def test_get_repo_tables_filters_by_repo_id_column(tmp_path):
    from kospex.db.introspect import get_repo_tables
    db = Database(tmp_path / "test.db")
    db.execute("CREATE TABLE [repos] (_repo_id TEXT, name TEXT)")
    db.execute("CREATE TABLE [commits] (_repo_id TEXT, hash TEXT)")
    db.execute("CREATE TABLE [kospex_config] (key TEXT, value TEXT)")  # no _repo_id

    tables = get_repo_tables(db)

    assert tables == {"repos", "commits"}
    assert "kospex_config" not in tables


def test_invalidate_cache_picks_up_new_table(tmp_path):
    from kospex.db.introspect import get_kospex_tables, invalidate_cache
    db = Database(tmp_path / "test.db")
    db.execute("CREATE TABLE [repos] (_repo_id TEXT)")

    before = get_kospex_tables(db)
    assert before == {"repos"}

    db.execute("CREATE TABLE [widgets] (id INTEGER)")
    # Without invalidation, cache still says only "repos"
    cached = get_kospex_tables(db)
    assert cached == {"repos"}

    invalidate_cache(db)
    after = get_kospex_tables(db)
    assert after == {"repos", "widgets"}


def test_invalidate_cache_no_db_clears_all(tmp_path):
    from kospex.db.introspect import get_kospex_tables, invalidate_cache
    db1 = Database(tmp_path / "a.db")
    db1.execute("CREATE TABLE [a] (x TEXT)")
    db2 = Database(tmp_path / "b.db")
    db2.execute("CREATE TABLE [b] (y TEXT)")

    get_kospex_tables(db1)
    get_kospex_tables(db2)

    invalidate_cache()  # no arg -> clear all

    from kospex.db.introspect import _TABLE_CACHE, _REPO_TABLE_CACHE
    assert _TABLE_CACHE == {}
    assert _REPO_TABLE_CACHE == {}
