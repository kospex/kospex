"""An author's technology by language, as the developer pages show it.

The developer view listed file extensions while the repo view listed languages.
author_languages() names each historical path the way the repo view would, so
files since deleted or moved are still counted.
"""
import os

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_query import KospexQuery

DEV = "dev@example.com"
WHEN = "2026-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    """Constructing KospexQuery sets KOSPEX_* env vars as a side effect."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _db():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMITS)
    db.execute(KospexSchema.SQL_CREATE_COMMIT_FILES)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    return db


def _change(db, repo, hash_, path, when=WHEN, email=DEV):
    db[KospexSchema.TBL_COMMITS].insert(
        {"_repo_id": repo, "hash": hash_, "author_email": email,
         "author_when": when, "committer_when": when},
        pk=["_repo_id", "hash"], ignore=True)
    db[KospexSchema.TBL_COMMIT_FILES].insert(
        {"_repo_id": repo, "hash": hash_, "file_path": path,
         "committer_when": when},
        pk=["hash", "file_path", "_repo_id"])


def _languages(db, email=DEV):
    return KospexQuery(kospex_db=db).author_languages(email)


def _by_language(db):
    return {row["language"]: row for row in _languages(db)}


def test_commits_are_counted_once_per_language():
    db = _db()
    for path in ("a.py", "b.py", "c.html"):
        _change(db, "s~o~r", "h1", path)

    rows = _by_language(db)

    assert rows["Python"]["commits"] == 1
    assert rows["Python"]["files"] == 2
    assert rows["HTML"]["commits"] == 1


def test_one_file_in_many_commits():
    db = _db()
    for hash_ in ("h1", "h2", "h3"):
        _change(db, "s~o~r", hash_, "a.py")

    row = _by_language(db)["Python"]

    assert (row["commits"], row["files"]) == (3, 1)


def test_shared_hash_in_two_repos_is_not_multiplied():
    db = _db()
    _change(db, "s~o~a", "h1", "README.md")
    _change(db, "s~o~b", "h1", "README.md")

    row = _by_language(db)["Markdown"]

    assert (row["commits"], row["files"], row["repos"]) == (2, 2, 2)


def test_path_no_longer_present_is_still_counted():
    """There is no file_metadata table at all: history alone names the language."""
    db = _db()
    _change(db, "s~o~r", "h1", "old/gone.go")

    assert [row["language"] for row in _languages(db)] == ["Go"]


def test_unnameable_and_null_paths_are_unknown():
    db = _db()
    for hash_, path in (("h1", "noext"), ("h2", "x.zzz"), ("h3", None)):
        _change(db, "s~o~r", hash_, path)

    rows = _languages(db)

    assert [(row["language"], row["commits"]) for row in rows] == [("Unknown", 3)]


def test_dates_compare_as_instants():
    """As text the +01:00 value sorts later, but it is the earlier instant."""
    db = _db()
    _change(db, "s~o~r", "h1", "a.py", when="2026-04-16T18:34:03+01:00")
    _change(db, "s~o~r", "h2", "a.py", when="2026-04-16T16:59:54-04:00")

    row = _by_language(db)["Python"]

    assert row["first_commit"] == "2026-04-16T17:34:03Z"
    assert row["last_commit"] == "2026-04-16T20:59:54Z"


def test_null_author_when():
    db = _db()
    _change(db, "s~o~r", "h1", "a.py", when=None)

    row = _by_language(db)["Python"]

    assert row["last_commit"] is None
    assert row["days_active"] == 0


def test_only_the_exact_author():
    db = _db()
    _change(db, "s~o~r", "h1", "a.py")
    _change(db, "s~o~r", "h2", "x.go", email="other@example.com")

    assert [row["language"] for row in _languages(db)] == ["Python"]
    assert _languages(db, "nobody@example.com") == []


def test_ordered_by_commits():
    db = _db()
    _change(db, "s~o~r", "h1", "a.py")
    _change(db, "s~o~r", "h2", "a.go")
    _change(db, "s~o~r", "h3", "b.go")

    assert [row["language"] for row in _languages(db)] == ["Go", "Python"]
