"""Tests for KospexQuery.get_dependencies() latest-version filtering.

The /dependencies/ web view reads via get_dependencies(). It must return
only current rows (latest=1); superseded rows demoted to latest=0 by
KospexDependencies.save_dependencies() must not be returned, otherwise old
package versions are double-counted alongside current ones.
"""
import os

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_query import KospexQuery


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    """Constructing KospexQuery sets KOSPEX_* env vars as a side effect;
    restore them so we don't leak state into later tests."""
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


def _seed_db():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
    rows = [
        # superseded: an older version, demoted to latest=0
        {"_repo_id": "github.com~kospex~kospex", "hash": "old",
         "file_path": "requirements.txt", "package_type": "pypi",
         "package_name": "requests", "package_version": "2.30.0", "latest": 0},
        # current
        {"_repo_id": "github.com~kospex~kospex", "hash": "new",
         "file_path": "requirements.txt", "package_type": "pypi",
         "package_name": "requests", "package_version": "2.31.0", "latest": 1},
    ]
    db[KospexSchema.TBL_DEPENDENCY_DATA].insert_all(rows, pk=_PK)
    return db


def test_get_dependencies_returns_only_latest_rows():
    db = _seed_db()
    kq = KospexQuery(kospex_db=db)

    deps = kq.get_dependencies(request_id={"repo_id": "github.com~kospex~kospex"})

    versions = sorted(d["package_version"] for d in deps)
    assert versions == ["2.31.0"]   # superseded 2.30.0 (latest=0) excluded
