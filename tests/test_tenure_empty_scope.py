"""/tenure/ on a scope with no commits (#206).

get_activity_stats() is a pure aggregate, and SQL returns one row for an
aggregate even when nothing matches: commits = 0, first/last commit NULL. The
old `if kresults:` guard was always true, so the date parser got None and the
page returned 500. That happened for an unknown id, for a known repo with no
commits, and for the unscoped page on a fresh install with an empty database.

The handler is called directly rather than through TestClient (starlette's
needs httpx2, which is not installed, so TestClient suites skip).
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import kospex_schema as KospexSchema
import kweb2
from kospex_query import KospexQuery

REPO = "github.com~acme~api"
EMPTY_REPO = "github.com~acme~empty"   # has a repos row, no commits


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _new_db(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()
    db = KospexSchema.connect_or_create_kospex_db()
    # Route the handler's KospexQuery() at this DB.
    monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: KospexQuery(kospex_db=db))
    return db


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    """A freshly created database: the state straight after `kospex init`."""
    return _new_db(tmp_path, monkeypatch)


@pytest.fixture
def db(tmp_path, monkeypatch):
    db = _new_db(tmp_path, monkeypatch)
    for repo_id in (REPO, EMPTY_REPO):
        server, owner, repo = repo_id.split("~")
        db["repos"].insert({"_repo_id": repo_id, "_git_server": server,
                            "_git_owner": owner, "_git_repo": repo})
    for i, days in enumerate((10, 400)):
        db["commits"].insert({
            "hash": f"h{i}", "_repo_id": REPO, "_git_server": "github.com",
            "_git_owner": "acme", "_git_repo": "api",
            "author_email": "dev@example.com", "committer_email": "dev@example.com",
            "author_when": _iso(days), "committer_when": _iso(days),
            "parents": 1, "_files": 1,
        })
    return db


def _request():
    return Request({
        "type": "http", "method": "GET", "path": "/tenure/",
        "headers": [], "query_string": b"", "app": kweb2.app,
    })


def _tenure(id=None):
    """Render /tenure/{id} and return (status, body)."""
    try:
        response = kweb2.tenure(_request(), id=id)
    except HTTPException as e:
        return e.status_code, str(e.detail)
    return response.status_code, response.body.decode()


class TestGetActivityStats:
    def test_empty_database_returns_zero_commits(self, empty_db):
        stats = KospexQuery(kospex_db=empty_db).get_activity_stats({})
        assert stats["commits"] == 0
        assert stats["days_active"] == 0
        assert stats["years_active"] == 0

    def test_scope_matching_nothing_returns_zero_commits(self, db):
        stats = KospexQuery(kospex_db=db).get_activity_stats({"repo_id": EMPTY_REPO})
        assert stats["commits"] == 0
        assert stats["days_active"] == 0

    def test_scope_with_commits_is_unchanged(self, db):
        stats = KospexQuery(kospex_db=db).get_activity_stats({"repo_id": REPO})
        assert stats["commits"] == 2
        assert stats["repos"] == 1
        assert stats["days_active"] >= 389


class TestScopeExists:
    @pytest.mark.parametrize("params", [
        {},
        {"repo_id": REPO},
        {"repo_id": EMPTY_REPO},
        {"org_key": "github.com~acme"},
        {"server": "github.com"},
    ])
    def test_known_scope(self, db, params):
        assert KospexQuery(kospex_db=db).scope_exists(params) is True

    @pytest.mark.parametrize("params", [
        {"repo_id": "github.com~acme~nope"},
        {"org_key": "github.com~nope"},
        {"org_key": "not-an-org-key"},     # malformed: no match, not an error
        {"server": "gitlab.example.com"},
    ])
    def test_unknown_scope(self, db, params):
        assert KospexQuery(kospex_db=db).scope_exists(params) is False

    def test_all_scope_exists_on_an_empty_database(self, empty_db):
        assert KospexQuery(kospex_db=empty_db).scope_exists({}) is True


class TestTenurePage:
    def test_fresh_install_renders_with_no_commits_message(self, empty_db):
        status, body = _tenure()
        assert status == 200
        assert "No commits synced yet." in body

    def test_known_repo_without_commits_renders_with_message(self, db):
        status, body = _tenure(EMPTY_REPO)
        assert status == 200
        assert "No commits synced for this repository." in body

    def test_repo_with_commits_has_no_empty_message(self, db):
        status, body = _tenure(REPO)
        assert status == 200
        assert "No commits synced" not in body

    @pytest.mark.parametrize("id,kind", [
        ("github.com~acme~nope", "repository"),
        ("github.com~nope", "organisation"),
        ("no-such-server", "server"),
    ])
    def test_unknown_id_is_404_naming_the_id(self, db, id, kind):
        status, body = _tenure(id)
        assert status == 404
        assert f"No {kind} with ID" in body
        assert id in body
