"""Tests for KospexQuery.extracted_dependency_file_keys (sub-project B)."""
import sqlite_utils
import kospex_schema as KospexSchema
from kospex_query import KospexQuery


def _kq_with_rows(rows):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
    for r in rows:
        db["dependency_data"].insert(r, pk=["_repo_id", "hash", "file_path",
                                            "package_type", "package_name",
                                            "package_version"], alter=True)
    return KospexQuery(kospex_db=db)


def test_returns_latest_repo_file_pairs():
    kq = _kq_with_rows([
        {"_repo_id": "s~o~r", "hash": "h1", "file_path": "requirements.txt",
         "package_type": "pypi", "package_name": "a", "package_version": "1", "latest": 1},
        {"_repo_id": "s~o~r", "hash": "h1", "file_path": "requirements.txt",
         "package_type": "pypi", "package_name": "b", "package_version": "2", "latest": 1},
        {"_repo_id": "s~o~r", "hash": "h2", "file_path": "package.json",
         "package_type": "npm", "package_name": "c", "package_version": "3", "latest": 1},
        {"_repo_id": "s~o~old", "hash": "h3", "file_path": "old.txt",
         "package_type": "pypi", "package_name": "d", "package_version": "4", "latest": 0},
    ])
    keys = kq.extracted_dependency_file_keys()
    assert keys == {("s~o~r", "requirements.txt"), ("s~o~r", "package.json")}
    assert isinstance(keys, set)


def test_scoped_by_repo_id():
    kq = _kq_with_rows([
        {"_repo_id": "s~o~r1", "hash": "h1", "file_path": "requirements.txt",
         "package_type": "pypi", "package_name": "a", "package_version": "1", "latest": 1},
        {"_repo_id": "s~o~r2", "hash": "h2", "file_path": "package.json",
         "package_type": "npm", "package_name": "c", "package_version": "3", "latest": 1},
    ])
    keys = kq.extracted_dependency_file_keys({"repo_id": "s~o~r1"})
    assert keys == {("s~o~r1", "requirements.txt")}
