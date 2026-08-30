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


class TestInMemoryDatabasesAreNotCached:
    """In-memory databases must never be served a cached table set (#184).

    The cache keyed them by `id(db)`, described as a "per-instance key". It is
    not: id() is unique only among *live* objects, and CPython reuses addresses
    aggressively. A new in-memory Database landing on a freed address inherited
    the dead one's tables — measured at 292 stale reads in 300, from only 8
    distinct keys.

    That surfaced as an intermittent `ValueError: Table 'commits' is not a known
    Kospex table` from KospexData.from_table(), which validates against this
    cache before interpolating a table name into SQL.

    Caching them buys ~1.1us per call, so they are simply not cached. File-backed
    databases still are: they key on a stable path, and the read costs ~10us.
    """

    def test_a_fresh_in_memory_db_never_inherits_dead_tables(self):
        import gc
        from kospex.db.introspect import get_kospex_tables

        for i in range(120):
            db = Database(memory=True)
            db.execute(f"CREATE TABLE [tbl_{i}] (x TEXT)")

            assert get_kospex_tables(db) == {f"tbl_{i}"}, (
                f"iteration {i} was served another database's tables"
            )

            del db
            gc.collect()

    def test_in_memory_dbs_do_not_accumulate_cache_entries(self):
        import gc
        from kospex.db.introspect import (
            get_kospex_tables, invalidate_cache, _TABLE_CACHE,
        )

        invalidate_cache()
        for i in range(20):
            db = Database(memory=True)
            db.execute(f"CREATE TABLE [t_{i}] (x TEXT)")
            get_kospex_tables(db)
            del db
            gc.collect()

        assert _TABLE_CACHE == {}, "in-memory databases must not be cached at all"

    def test_repo_tables_are_also_uncached_in_memory(self):
        """get_repo_tables shares the key, so it shares the defect."""
        import gc
        from kospex.db.introspect import get_repo_tables

        for i in range(60):
            db = Database(memory=True)
            db.execute(f"CREATE TABLE [r_{i}] (_repo_id TEXT)")

            assert get_repo_tables(db) == {f"r_{i}"}, (
                f"iteration {i} was served another database's repo tables"
            )

            del db
            gc.collect()

    def test_file_backed_databases_are_still_cached(self, tmp_path):
        """The fix must not disable caching where it is correct and worthwhile."""
        from kospex.db.introspect import (
            get_kospex_tables, invalidate_cache, _TABLE_CACHE,
        )

        invalidate_cache()
        db = Database(tmp_path / "cached.db")
        db.execute("CREATE TABLE [repos] (x TEXT)")

        get_kospex_tables(db)
        assert len(_TABLE_CACHE) == 1

        # A table added behind the cache is not seen until invalidation — the
        # caching behaviour file-backed databases rely on.
        db.execute("CREATE TABLE [added_later] (y TEXT)")
        assert get_kospex_tables(db) == {"repos"}

        invalidate_cache(db)
        assert get_kospex_tables(db) == {"repos", "added_later"}


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
