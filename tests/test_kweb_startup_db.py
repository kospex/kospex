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
    # KOSPEX_HOME must be redirected BEFORE importing kweb2: the module runs
    # KospexUtils.init() at import, which resolves and caches paths. Importing
    # first would run that against the developer's real ~/kospex.
    _fresh_home(tmp_path, monkeypatch)
    import kweb2
    from kospex.db.migrator import Migrator
    import kospex_schema as KospexSchema

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
    _fresh_home(tmp_path, monkeypatch)   # before importing kweb2 — see above
    import kweb2
    import kospex_schema as KospexSchema

    def boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(KospexSchema, "connect_or_create_kospex_db", boom)

    async def run_startup():
        async with kweb2.lifespan(kweb2.app):
            return True

    assert asyncio.run(run_startup()) is True
