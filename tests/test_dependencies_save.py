"""Tests for KospexDependencies.save_dependencies().

save_dependencies() is the reusable DB-write path used by `krunner osi`
(and intended to back the other dependency writers). It must:
  - persist pre-parsed records into TBL_DEPENDENCY_DATA with latest=1,
  - stamp the [source] column with the calling tool,
  - derive _git_server/_git_owner/_git_repo from _repo_id,
  - strip extractor-only template fields that are not schema columns, and
  - demote prior rows for the same (_repo_id, file_path, package_name)
    to latest=0 so re-runs don't accumulate stale "latest" rows.
"""
import os

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_dependencies import KospexDependencies


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    """Constructing KospexQuery/KospexDependencies sets KOSPEX_* env vars as a
    side effect; restore them so we don't leak state into later tests."""
    keys = ("KOSPEX_CODE", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_HOME", "KOSPEX_LOGS")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


_PK = [
    "_repo_id", "hash", "file_path",
    "package_type", "package_name", "package_version",
]


def _make_db():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
    return db


def test_save_dependencies_writes_rows_with_latest_source_and_git_fields():
    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)

    records = [{
        "_repo_id": "github.com~kospex~kospex",
        "hash": "abc123",
        "file_path": "requirements.txt",
        "package_type": "pypi",
        "package_name": "requests",
        "package_version": "2.31.0",
        "versions_behind": 3,
        "advisories": 0,
        # extractor-only template fields that are NOT schema columns:
        "ecosystem": "PyPi",
        "requirements_type": "direct",
    }]

    count = kdeps.save_dependencies(records, source="krunner osi")

    assert count == 1
    rows = list(db[KospexSchema.TBL_DEPENDENCY_DATA].rows)
    assert len(rows) == 1
    row = rows[0]
    assert row["package_name"] == "requests"
    assert row["package_version"] == "2.31.0"
    assert row["latest"] == 1
    assert row["source"] == "krunner osi"
    # _git_* derived from _repo_id
    assert row["_git_server"] == "github.com"
    assert row["_git_owner"] == "kospex"
    assert row["_git_repo"] == "kospex"
    # extractor-only fields must not have been persisted as columns
    assert "ecosystem" not in row
    assert "requirements_type" not in row


def test_save_dependencies_demotes_prior_latest_rows():
    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)

    # An existing "latest" row for an older version of the same package
    # in the same file (e.g. a prior krunner osi run).
    db[KospexSchema.TBL_DEPENDENCY_DATA].insert({
        "_repo_id": "github.com~kospex~kospex",
        "hash": "oldhash",
        "file_path": "requirements.txt",
        "package_type": "pypi",
        "package_name": "requests",
        "package_version": "2.30.0",
        "latest": 1,
    }, pk=_PK)

    kdeps.save_dependencies([{
        "_repo_id": "github.com~kospex~kospex",
        "hash": "newhash",
        "file_path": "requirements.txt",
        "package_type": "pypi",
        "package_name": "requests",
        "package_version": "2.31.0",
    }], source="krunner osi")

    rows = {r["package_version"]: r for r in db[KospexSchema.TBL_DEPENDENCY_DATA].rows}
    assert rows["2.30.0"]["latest"] == 0   # prior version demoted
    assert rows["2.31.0"]["latest"] == 1   # new current version


def test_save_dependencies_empty_is_noop():
    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)
    assert kdeps.save_dependencies([], source="krunner osi") == 0
    assert list(db[KospexSchema.TBL_DEPENDENCY_DATA].rows) == []


def test_save_dependencies_persists_resolution():
    import sqlite_utils, kospex_schema as KospexSchema
    from kospex_dependencies import KospexDependencies
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
    db.execute("ALTER TABLE dependency_data ADD COLUMN resolution TEXT")
    kd = KospexDependencies(kospex_db=db)
    kd.save_dependencies([{
        "_repo_id": "s~o~r", "hash": "h", "file_path": "req.txt", "package_type": "pypi",
        "package_name": "foo", "package_version": "^1.0",
        "versions_behind": None, "resolution": "unresolved_spec",
    }], source="krunner osi")
    row = next(db.query("SELECT resolution, versions_behind FROM dependency_data"))
    assert row["resolution"] == "unresolved_spec" and row["versions_behind"] is None
