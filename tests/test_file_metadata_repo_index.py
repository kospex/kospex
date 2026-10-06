"""file_metadata must be indexed on (_repo_id, latest) — migration 0007.

The table's only index was its primary key, `(Provider, hash, _repo_id)`, led by
the filename. Every query that scopes to one repository therefore scanned the
whole table, and those are the common shape: `get_dependency_files()`,
`repo_files()`, `file_metadata()`, `tech_landscape(repo_id)`, and the
`UPDATE file_metadata SET LATEST = 0 WHERE _repo_id = ?` that every sync runs
before writing.

Measured for a median-sized repo:

                                131k rows        8.05M rows
    file_metadata(repo_id)      25.11 -> 0.19ms  8,506 -> 1.63ms
    get_dependency_files        14.30 -> 0.04ms  16,612 -> 0.31ms
    sync's UPDATE reset        (294 -> 2.4ms at 2.04M rows)

The write path gains more than it loses: the index costs ~9% on bulk insert and
~12.6% on disk, against ~250ms saved per repo synced at 2M rows.

Note the estate-wide queries (`tech_landscape()` with no repo, `repos_with_tech`)
cannot use this index — they never constrain _repo_id — and are deliberately out
of scope here.
"""
import sqlite_utils

import kospex_schema as KospexSchema

INDEX_NAME = "idx_file_metadata_repo_latest"


def _fresh_home(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()
    return home


def test_fresh_db_has_the_index(tmp_path, monkeypatch):
    _fresh_home(tmp_path, monkeypatch)

    db = KospexSchema.connect_or_create_kospex_db()

    names = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='file_metadata'"
    ).fetchall()]
    assert INDEX_NAME in names, f"indexes present: {names}"


def test_index_covers_repo_id_then_latest(tmp_path, monkeypatch):
    """Column order matters: _repo_id must lead, so a repo-scoped query can seek."""
    _fresh_home(tmp_path, monkeypatch)

    db = KospexSchema.connect_or_create_kospex_db()

    cols = [r[2] for r in db.execute(f"PRAGMA index_info({INDEX_NAME})").fetchall()]
    assert cols == ["_repo_id", "latest"], f"got {cols}"


def test_repo_scoped_dependency_lookup_seeks_instead_of_scanning(tmp_path, monkeypatch):
    """The query shape krunner osi and the repo pages issue must use the index."""
    _fresh_home(tmp_path, monkeypatch)

    db = KospexSchema.connect_or_create_kospex_db()

    sql = ("SELECT * FROM file_metadata WHERE latest = 1 AND _repo_id = ? "
           "AND tech_type LIKE '%|dependencies|%'")
    plan = " ".join(str(r) for r in db.execute(
        "EXPLAIN QUERY PLAN " + sql, ["github.com~acme~svc"]).fetchall())

    assert "SCAN file_metadata" not in plan, f"still a full scan: {plan}"
    assert INDEX_NAME in plan, f"index not used: {plan}"


def test_sync_latest_reset_seeks_instead_of_scanning(tmp_path, monkeypatch):
    """The write path too: every sync resets latest for the repo before writing."""
    _fresh_home(tmp_path, monkeypatch)

    db = KospexSchema.connect_or_create_kospex_db()

    plan = " ".join(str(r) for r in db.execute(
        "EXPLAIN QUERY PLAN UPDATE file_metadata SET LATEST = 0 WHERE _repo_id = ?",
        ["github.com~acme~svc"]).fetchall())

    assert "SCAN file_metadata" not in plan, f"still a full scan: {plan}"
    assert INDEX_NAME in plan, f"index not used: {plan}"


def test_index_is_not_declared_in_the_baseline_schema():
    """0007 owns it. Declaring it in SQL_CREATE_* too fails every clean install.

    A new DB runs the baseline CREATEs and *then* bootstraps the migrations, so
    anything declared in both places collides -- the same trap the 0006 columns
    are kept out of SQL_CREATE_DEPENDENCY_DATA to avoid.
    """
    assert INDEX_NAME not in KospexSchema.SQL_CREATE_FILE_METADATA
    for sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        assert INDEX_NAME not in sql
