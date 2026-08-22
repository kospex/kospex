"""
Tests for kospex schema
"""
import kospex_schema as KospexSchema


class TestPackageUseConstants:
    def test_all_constants_are_strings(self):
        for name in ("PACKAGE_USE_DIRECT", "PACKAGE_USE_DEV", "PACKAGE_USE_PEER",
                     "PACKAGE_USE_OPTIONAL", "PACKAGE_USE_TRANSITIVE"):
            assert isinstance(getattr(KospexSchema, name), str), f"{name} must be a str"

    def test_package_use_values_is_frozenset(self):
        assert isinstance(KospexSchema.PACKAGE_USE_VALUES, frozenset)

    def test_all_constants_in_values_set(self):
        for const in (KospexSchema.PACKAGE_USE_DIRECT, KospexSchema.PACKAGE_USE_DEV,
                      KospexSchema.PACKAGE_USE_PEER, KospexSchema.PACKAGE_USE_OPTIONAL,
                      KospexSchema.PACKAGE_USE_TRANSITIVE):
            assert const in KospexSchema.PACKAGE_USE_VALUES

    def test_values_set_has_five_members(self):
        assert len(KospexSchema.PACKAGE_USE_VALUES) == 5


# --- clean-install migration bootstrap --------------------------------------


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
    from kospex.db.migrator import Migrator
    _fresh_home(tmp_path, monkeypatch)

    db = KospexSchema.connect_or_create_kospex_db()

    assert Migrator(db).pending() == []


def test_fresh_db_has_migrated_columns(tmp_path, monkeypatch):
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
    _fresh_home(tmp_path, monkeypatch)
    db = KospexSchema.connect_or_create_kospex_db()

    db["dependency_data"].insert(
        {"hash": "h1", "package_name": "requests", "resolution": "resolved"}
    )

    row = db.execute("SELECT resolution FROM dependency_data").fetchone()
    assert row[0] == "resolved"


def test_existing_db_is_not_re_bootstrapped(tmp_path, monkeypatch):
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
    from kospex.db.migrator import Migrator
    _fresh_home(tmp_path, monkeypatch)
    sqlite3.connect(tmp_path / "kospex.db").close()   # zero tables
    assert (tmp_path / "kospex.db").exists()

    db = KospexSchema.connect_or_create_kospex_db()

    assert KospexSchema.LAST_BOOTSTRAP["created"] is True
    assert Migrator(db).pending() == []


def test_bootstrap_failure_is_not_fatal(tmp_path, monkeypatch):
    """A failed migration must leave a usable DB, loudly, not crash the CLI."""
    from kospex.db import migrator as migrator_module
    _fresh_home(tmp_path, monkeypatch)

    def boom(self):
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(migrator_module.Migrator, "apply_pending", boom)

    db = KospexSchema.connect_or_create_kospex_db()

    assert db is not None
    assert db["repos"].exists()
    assert "migration exploded" in KospexSchema.LAST_BOOTSTRAP["migration_error"]
