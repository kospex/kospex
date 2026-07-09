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


import pytest
from kgit import _resolve_pull_repos


def _seed_repos():
    db = _repos_db([
        {"_repo_id": "github.com~o1~a", "_git_server": "github.com",
         "_git_owner": "o1", "_git_repo": "a", "file_path": "/c/a"},
        {"_repo_id": "github.com~o1~b", "_git_server": "github.com",
         "_git_owner": "o1", "_git_repo": "b", "file_path": "/c/b"},
        {"_repo_id": "bitbucket.org~o2~c", "_git_server": "bitbucket.org",
         "_git_owner": "o2", "_git_repo": "c", "file_path": "/c/c"},
    ])
    return KospexQuery(kospex_db=db)


def test_resolve_by_repo_id():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id="github.com~o1~a",
                                all_flag=False, org=None, server=None)
    assert [r["_repo_id"] for r in repos] == ["github.com~o1~a"]


def test_resolve_by_org():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id=None, all_flag=False,
                                org="github.com~o1", server=None)
    assert sorted(r["_repo_id"] for r in repos) == ["github.com~o1~a", "github.com~o1~b"]


def test_resolve_by_server():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id=None, all_flag=False,
                                org=None, server="bitbucket.org")
    assert [r["_repo_id"] for r in repos] == ["bitbucket.org~o2~c"]


def test_resolve_all():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id=None, all_flag=True,
                                org=None, server=None)
    assert len(repos) == 3


def test_resolve_requires_exactly_one_scope():
    kq = _seed_repos()
    with pytest.raises(ValueError):
        _resolve_pull_repos(kq, repo_id=None, all_flag=False, org=None, server=None)
    with pytest.raises(ValueError):
        _resolve_pull_repos(kq, repo_id="x~y~z", all_flag=True, org=None, server=None)
