"""/repos/ reads developer_stats rather than aggregating the commits table.

Commits, authors, last commit, developer statuses and active developers come
from developer_stats. Committers are not stored there, so they are still
counted from commits. When developer_stats_coverage() reports a gap the page
uses the commits queries instead.

Results are compared with repos() and developers() on the same DB.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
from starlette.requests import Request

import kospex_schema as KospexSchema
from kospex_query import KospexQuery
from kospex_utils import repo_stats

REPOS = {
    "github.com~acme~api": ("github.com", "acme", "api"),
    "github.com~acme~web": ("github.com", "acme", "web"),
    "gitlab.com~beta~~sub~svc": ("gitlab.com", "beta/sub", "svc"),
}


def _when(days_ago, offset_hours=0):
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.astimezone(timezone(timedelta(hours=offset_hours))).isoformat(timespec="seconds")


# (repo_id, author, committer, committer_when)
COMMITS = [
    ("github.com~acme~api", "dev@example.com", "dev@example.com", _when(10)),
    ("github.com~acme~api", "dev@example.com", "merge@example.com", _when(500)),
    ("github.com~acme~api", "old@example.com", "old@example.com", _when(400)),
    # An hour inside the 90 days, written with a -10:00 offset: compared as
    # text it sorts before the UTC cutoff and is missed.
    ("github.com~acme~api", "tz@example.com", "tz@example.com", _when(90 - 1 / 24, -10)),
    ("github.com~acme~web", "Dev@Example.com", "merge@example.com", _when(120)),
    ("github.com~acme~web", "dev@example.com", "merge@example.com", _when(200)),
    ("gitlab.com~beta~~sub~svc", "new@example.com", "new@example.com", _when(1)),
]

SCOPES = [
    {},
    {"org_key": "github.com~acme"},
    {"org_key": "gitlab.com~beta~~sub"},
    {"server": "github.com"},
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()
    db = KospexSchema.connect_or_create_kospex_db()
    for repo_id, (server, owner, repo) in REPOS.items():
        db["repos"].insert({"_repo_id": repo_id, "_git_server": server,
                            "_git_owner": owner, "_git_repo": repo})
    for i, (repo_id, author, committer, when) in enumerate(COMMITS):
        server, owner, repo = REPOS[repo_id]
        db["commits"].insert({
            "hash": f"h{i}", "_repo_id": repo_id, "_git_server": server,
            "_git_owner": owner, "_git_repo": repo,
            "author_email": author, "committer_email": committer,
            "author_when": when, "committer_when": when,
            "parents": 1, "_files": 1,
        })
    kq = KospexQuery(kospex_db=db)
    for repo_id in REPOS:
        kq.update_developer_stats(repo_id)
    return db


@pytest.mark.parametrize("scope", SCOPES, ids=lambda s: str(s) or "estate")
def test_repo_summaries_match_repos(db, scope):
    kq = KospexQuery(kospex_db=db)
    expected = kq.repos(**scope)
    assert expected, "an empty scope would prove nothing"
    rows = kq.repo_summaries(**scope)
    # days_ago is rounded from "now", so two calls can differ by one step
    ages = [row.pop("days_ago") for row in rows]
    assert ages == pytest.approx([row.pop("days_ago") for row in expected], abs=0.02)
    assert rows == expected


def test_active_devs_from_stats_compares_times_not_text(db):
    kq = KospexQuery(kospex_db=db)
    assert kq.active_devs_from_stats() == {
        "github.com~acme~api": 2,       # dev and tz, not old
        "gitlab.com~beta~~sub~svc": 1,
    }


def test_active_devs_from_stats_ignores_the_snapshot_column(db):
    # commits_last_90_days is fixed at the time the row was built
    db.execute("UPDATE developer_stats SET commits_last_90_days = 99")
    db.conn.commit()
    kq = KospexQuery(kospex_db=db)
    assert "github.com~acme~web" not in kq.active_devs_from_stats()


# --- the /repos/ route -------------------------------------------------------

@pytest.fixture
def kweb(db):
    import kweb2
    kweb2.request_cache.clear()
    yield kweb2
    kweb2.request_cache.clear()


def _page(kweb, query=b""):
    request = Request({"type": "http", "method": "GET", "path": "/repos/",
                       "query_string": query, "headers": []})
    response = kweb.repos(request)
    assert response.status_code == 200
    return response.context


def _forbid(monkeypatch, *names):
    for name in names:
        def boom(*a, _name=name, **k):
            raise AssertionError(f"/repos/ called {_name}()")
        monkeypatch.setattr(KospexQuery, name, boom)


@pytest.mark.parametrize("query, scope", [
    (b"", {}),
    (b"org_key=github.com~acme", {"org_key": "github.com~acme"}),
    (b"server=github.com", {"server": "github.com"}),
], ids=["estate", "org", "server"])
def test_the_page_reads_developer_stats(kweb, monkeypatch, query, scope):
    kq = KospexQuery()
    expected = {r["_repo_id"]: r for r in kq.repos(**scope)}
    statuses = repo_stats(kq.developers(**scope), "last_commit")
    active = kq.active_devs_from_stats()
    _forbid(monkeypatch, "repos", "developers", "active_devs")

    context = _page(kweb, query)

    assert {r["_repo_id"] for r in context["data"]} == set(expected)
    for row in context["data"]:
        for column in ("commits", "authors", "committers", "last_commit", "status"):
            assert row[column] == expected[row["_repo_id"]][column], column
        assert row["active_devs"] == active.get(row["_repo_id"], 0)
    assert Counter(context["developer_status"]) == Counter(statuses)


@pytest.mark.parametrize("gap", [
    "DELETE FROM developer_stats WHERE _repo_id = 'github.com~acme~web'",
    "UPDATE developer_stats SET last_authored = NULL WHERE _repo_id = 'github.com~acme~web'",
], ids=["repo without stats", "repo not rebuilt"])
def test_the_page_falls_back_to_commits_when_stats_have_gaps(db, kweb, monkeypatch, gap):
    db.execute(gap)
    db.conn.commit()
    expected = KospexQuery().repos()
    _forbid(monkeypatch, "repo_summaries", "developer_statuses", "active_devs_from_stats")

    context = _page(kweb)

    assert [r["commits"] for r in context["data"]] == [r["commits"] for r in expected]
