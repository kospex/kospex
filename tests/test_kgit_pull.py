"""Unit tests for kgit pull helpers and provenance."""
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_query import KospexQuery


def _repos_db(rows):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db.execute("ALTER TABLE repos ADD COLUMN last_fetch TEXT")
    if rows:
        db["repos"].insert_all(rows, pk="_repo_id")
    return db


def test_set_repo_last_fetch_stamps_timestamp():
    db = _repos_db([{"_repo_id": "s~o~r", "_git_server": "s", "_git_owner": "o",
                     "_git_repo": "r", "file_path": "/tmp/r"}])
    kq = KospexQuery(kospex_db=db)

    kq.set_repo_last_fetch("s~o~r", when="2026-07-09T00:00:00+00:00")

    row = next(db.query("SELECT last_fetch FROM repos WHERE _repo_id='s~o~r'"))
    assert row["last_fetch"] == "2026-07-09T00:00:00+00:00"
