"""The summary pages count statuses from developer_stats, not a commits scan (Phase 4).

/ and /summary/ only need each developer's and each repo's last commit, to
count Active / Aging / Stale / Unmaintained. developers() and repos() get
that by aggregating the whole commits table (~5s and ~2s on a 1.1M-commit
estate, cold). developer_stats already holds it per (repo, author), in
committer time since Phase 3, so the counts come from ~91k rows instead.

There is deliberately no fallback to the commits queries. A DB upgraded from
before Phase 3 has rows with author-time dates (last_authored IS NULL); the
upgrade note says to run `kospex rebuild developer-stats -all`, and
developer_stats_coverage() / `kospex system-status` report what's left.

Each test compares against developers() / repos() on the same DB, the
production queries the summary used before, not against a restated copy.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

import kospex_schema as KospexSchema
from kospex_query import KospexQuery

# repo_id -> (server, owner, repo)
REPOS = {
    "github.com~acme~api": ("github.com", "acme", "api"),
    "github.com~acme~web": ("github.com", "acme", "web"),
    "gitlab.com~beta~svc": ("gitlab.com", "beta", "svc"),
}

# (repo_id, author_email, days ago committed, days ago authored)
COMMITS = [
    ("github.com~acme~api", "dev@example.com", 10, 40),     # Active by committer, Active by author
    ("github.com~acme~api", "dev@example.com", 500, 500),
    ("github.com~acme~api", "old@example.com", 400, 400),   # Unmaintained
    ("github.com~acme~web", "Dev@Example.com", 120, 300),   # same dev, other case, Aging here
    ("github.com~acme~web", "rebase@example.com", 60, 250), # Active by committer, Stale by author
    ("gitlab.com~beta~svc", "dev@example.com", 200, 200),   # Stale
    ("gitlab.com~beta~svc", "new@example.com", 1, 1),
]

SCOPES = [
    {},
    {"org_key": "github.com~acme"},
    {"org_key": "gitlab.com~beta"},
    {"repo_id": "github.com~acme~web"},
    {"server": "github.com"},
    {"server": "gitlab.com"},
]


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago, hours=1)).isoformat(timespec="seconds")


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
    for i, (repo_id, author, committed, authored) in enumerate(COMMITS):
        server, owner, repo = REPOS[repo_id]
        db["commits"].insert({
            "hash": f"h{i}", "_repo_id": repo_id, "_git_server": server,
            "_git_owner": owner, "_git_repo": repo,
            "author_email": author, "committer_email": author,
            "author_when": _iso(authored), "committer_when": _iso(committed),
            "parents": 1, "_files": 1,
        })
    kq = KospexQuery(kospex_db=db)
    for repo_id in REPOS:
        kq.update_developer_stats(repo_id)
    return db


def _status_counts(rows):
    return Counter(r["status"] for r in rows)


@pytest.mark.parametrize("scope", SCOPES, ids=lambda s: str(s) or "estate")
def test_developer_status_counts_match_developers(db, scope):
    kq = KospexQuery(kospex_db=db)
    fast = kq.developer_statuses(**scope)
    assert fast is not None, "fully healed stats must not fall back"
    assert _status_counts(fast) == _status_counts(kq.developers(**scope))
    assert sorted(r["author"] for r in fast) == sorted(r["author"] for r in kq.developers(**scope))


@pytest.mark.parametrize("scope", SCOPES, ids=lambda s: str(s) or "estate")
def test_repo_status_counts_match_repos(db, scope):
    kq = KospexQuery(kospex_db=db)
    fast = kq.repo_statuses(**scope)
    assert fast is not None
    assert _status_counts(fast) == _status_counts(kq.repos(**scope))
    assert sorted(r["_repo_id"] for r in fast) == sorted(r["_repo_id"] for r in kq.repos(**scope))


def test_the_fixture_exercises_every_status_and_the_author_time_trap(db):
    # Guard against vacuous passes: the estate must cover several statuses, and
    # one developer must have a different status by author time than by
    # committer time, so reading the wrong column would change a count.
    kq = KospexQuery(kospex_db=db)
    seen = set()
    for scope in SCOPES:
        seen |= set(_status_counts(kq.developers(**scope)))
    assert seen == {"Active", "Aging", "Stale", "Unmaintained"}
    row = next(r for r in db.query(
        "SELECT * FROM developer_stats WHERE author_email = 'rebase@example.com'"))
    from kospex_utils import development_status
    assert development_status(row["last_commit"]) != development_status(row["last_authored"])


def test_mixed_case_emails_are_one_developer(db):
    kq = KospexQuery(kospex_db=db)
    authors = [r["author"] for r in kq.developer_statuses(org_key="github.com~acme")]
    assert authors.count("dev@example.com") == 1


def test_a_malformed_org_key_raises_like_developers(db):
    kq = KospexQuery(kospex_db=db)
    with pytest.raises(ValueError):
        kq.developers(org_key="no-tilde")
    with pytest.raises(ValueError):
        kq.developer_statuses(org_key="no-tilde")


def test_coverage_reports_rows_not_rebuilt_and_repos_without_stats(db):
    kq = KospexQuery(kospex_db=db)
    assert kq.developer_stats_coverage() == {"repos_not_rebuilt": 0, "repos_without_stats": 0}
    db.execute("UPDATE developer_stats SET last_authored = NULL WHERE _repo_id = 'github.com~acme~web'")
    db.execute("DELETE FROM developer_stats WHERE _repo_id = 'gitlab.com~beta~svc'")
    db.conn.commit()
    assert kq.developer_stats_coverage() == {"repos_not_rebuilt": 1, "repos_without_stats": 1}


def test_coverage_counts_a_legacy_null_stat_type_row_as_not_rebuilt(db):
    db["developer_stats"].insert({"_repo_id": "gitlab.com~beta~svc", "_git_server": "gitlab.com",
                                  "_git_owner": "beta", "author_email": "x@example.com",
                                  "stat_type": None, "last_commit": _iso(5)})
    assert KospexQuery(kospex_db=db).developer_stats_coverage()["repos_not_rebuilt"] == 1


def test_coverage_ignores_a_repo_row_without_commits(db):
    # An empty repo (cloned, nothing committed) has a repos row and no stats;
    # repos() doesn't count it either, so it isn't missing anything.
    db["repos"].insert({"_repo_id": "github.com~acme~empty", "_git_server": "github.com",
                        "_git_owner": "acme", "_git_repo": "empty"})
    assert KospexQuery(kospex_db=db).developer_stats_coverage()["repos_without_stats"] == 0


# --- kweb wiring -------------------------------------------------------------

@pytest.fixture
def kweb(db):
    import kweb2
    kweb2.request_cache.clear()
    yield kweb2
    kweb2.request_cache.clear()


def _forbid(monkeypatch, *names):
    def boom(*a, **k):
        raise AssertionError("the summary scanned commits instead of reading developer_stats")
    for name in names:
        monkeypatch.setattr(KospexQuery, name, boom)


@pytest.mark.parametrize("scope", SCOPES, ids=lambda s: str(s) or "estate")
def test_summary_reads_stats_and_counts_match(kweb, monkeypatch, scope):
    expected_devs = _status_counts(KospexQuery().developers(**scope))
    expected_repos = _status_counts(KospexQuery().repos(**scope))
    _forbid(monkeypatch, "developers", "repos")
    devs, repos = kweb.summary_rows(scope)
    assert _status_counts(devs) == expected_devs
    assert _status_counts(repos) == expected_repos


def test_summary_uses_the_commits_queries_for_a_scope_stats_cannot_express(kweb, monkeypatch):
    calls = []
    monkeypatch.setattr(KospexQuery, "developers", lambda self, **kw: calls.append(kw) or [])
    monkeypatch.setattr(KospexQuery, "repos", lambda self, **kw: [])
    _forbid(monkeypatch, "developer_statuses", "repo_statuses")
    kweb.summary_rows({"author_email": "dev@example.com"})
    assert calls == [{"author_email": "dev@example.com"}]
