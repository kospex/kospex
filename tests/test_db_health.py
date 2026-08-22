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
