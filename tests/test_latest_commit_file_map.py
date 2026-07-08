"""Tests for KospexQuery.latest_commit_file_map().

file_metadata sync needs each file's last-commit hash + date. Rather than an
expensive per-file query (or a git command per file), this builds the whole
map for a repo in a single pass over commit_files:

    {file_path: {"hash": <last commit hash>, "committer_when": <its date>}}

"Latest" matches the prior per-file lookup semantics: the row with the
lexicographically-greatest committer_when (ORDER BY committer_when DESC).
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


def _seed(rows):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMIT_FILES)
    db[KospexSchema.TBL_COMMIT_FILES].insert_all(
        rows, pk=["hash", "file_path", "_repo_id"]
    )
    return KospexQuery(kospex_db=db)


def test_returns_latest_commit_per_file():
    kq = _seed([
        # README.md changed twice -> newer commit wins
        {"_repo_id": "s~o~r", "file_path": "README.md", "hash": "old",
         "committer_when": "2025-01-01T00:00:00+00:00"},
        {"_repo_id": "s~o~r", "file_path": "README.md", "hash": "new",
         "committer_when": "2025-06-01T00:00:00+00:00"},
        # LICENSE changed once
        {"_repo_id": "s~o~r", "file_path": "LICENSE", "hash": "lic",
         "committer_when": "2024-03-01T00:00:00+00:00"},
    ])

    m = kq.latest_commit_file_map("s~o~r")

    assert set(m) == {"README.md", "LICENSE"}
    assert m["README.md"]["hash"] == "new"
    assert m["README.md"]["committer_when"] == "2025-06-01T00:00:00+00:00"
    assert m["LICENSE"]["hash"] == "lic"


def test_scoped_to_repo_id():
    kq = _seed([
        {"_repo_id": "s~o~r1", "file_path": "a.py", "hash": "r1",
         "committer_when": "2025-01-01T00:00:00+00:00"},
        {"_repo_id": "s~o~r2", "file_path": "a.py", "hash": "r2",
         "committer_when": "2025-01-01T00:00:00+00:00"},
    ])

    m = kq.latest_commit_file_map("s~o~r1")

    assert list(m) == ["a.py"]
    assert m["a.py"]["hash"] == "r1"


def test_empty_repo_returns_empty_map():
    kq = _seed([
        {"_repo_id": "s~o~r", "file_path": "a.py", "hash": "h",
         "committer_when": "2025-01-01T00:00:00+00:00"},
    ])
    assert kq.latest_commit_file_map("s~o~nonexistent") == {}
