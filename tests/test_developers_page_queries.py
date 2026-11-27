"""Queries behind /developers/: counts, the paged and searchable list, busiest scopes.

One developer is one lower-cased email. The same SQL runs over developer_stats
or over commits, and the two are compared on the same DB.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

import kospex_schema as KospexSchema
from kospex_query import KospexQuery

NOW = datetime.now(timezone.utc).replace(microsecond=0)

REPOS = {
    "github.com~acme~api": ("github.com", "acme", "api"),
    "github.com~acme~web": ("github.com", "acme", "web"),
    "github.com~beta~cli": ("github.com", "beta", "cli"),
    "gitlab.com~grp~~sub~svc": ("gitlab.com", "grp/sub", "svc"),
}

# (repo_id, author, days ago, committer)
COMMITS = [
    ("github.com~acme~api", "paul@example.com", 40, "paul@example.com"),
    ("github.com~acme~api", "paul@example.com", 500, "merge@example.com"),
    ("github.com~acme~api", "old@example.com", 400, "merge@example.com"),
    ("github.com~acme~web", "Paul@Example.com", 300, "Merge@Example.com"),
    ("github.com~acme~web", "mrpaul@example.com", 10, "mrpaul@example.com"),
    ("github.com~acme~web", "aging@example.com", 120, "merge@example.com"),
    ("github.com~beta~cli", "stale@example.com", 250, "stale@example.com"),
    ("github.com~beta~cli", "a_b@example.com", 5, "a_b@example.com"),
    ("github.com~beta~cli", "axb@example.com", 6, "axb@example.com"),
    ("gitlab.com~grp~~sub~svc", "pc%t@example.com", 2, "pc%t@example.com"),
    ("gitlab.com~grp~~sub~svc", "o'neil@example.com", 3, "o'neil@example.com"),
]

SCOPES = [
    {},
    {"server": "github.com"},
    {"org_key": "github.com~acme"},
    {"org_key": "gitlab.com~grp~~sub"},
    {"repo_id": "github.com~acme~web"},
]

BANDS = ("active", "aging", "stale", "unmaintained")


def _when(days_ago, offset_hours=0):
    moment = NOW - timedelta(days=days_ago)
    return moment.astimezone(timezone(timedelta(hours=offset_hours))).isoformat()


def _make_db(tmp_path, monkeypatch, commits, repos=REPOS):
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()
    db = KospexSchema.connect_or_create_kospex_db()
    for repo_id, (server, owner, repo) in repos.items():
        db["repos"].insert({"_repo_id": repo_id, "_git_server": server,
                            "_git_owner": owner, "_git_repo": repo})
    rows = []
    for i, (repo_id, author, days_ago, committer) in enumerate(commits):
        server, owner, repo = repos[repo_id]
        when = days_ago if isinstance(days_ago, str) else _when(days_ago)
        rows.append({
            "hash": f"h{i}", "_repo_id": repo_id, "_git_server": server,
            "_git_owner": owner, "_git_repo": repo,
            "author_email": author, "committer_email": committer,
            "author_when": when, "committer_when": when,
            "parents": 1, "_files": 1,
        })
    db["commits"].insert_all(rows)
    kq = KospexQuery(kospex_db=db)
    for repo_id in repos:
        kq.update_developer_stats(repo_id)
    return kq


@pytest.fixture
def kq(tmp_path, monkeypatch):
    return _make_db(tmp_path, monkeypatch, COMMITS)


def _emails(rows, column="author_email"):
    return [r[column] for r in rows]


@pytest.mark.parametrize("scope", SCOPES, ids=lambda s: str(s) or "estate")
def test_counts_match_developers(kq, scope):
    expected = Counter(r["status"].lower() for r in kq.developers(**scope))
    counts = kq.developer_status_counts(scope)
    assert {b: counts[b] for b in BANDS} == {b: expected[b] for b in BANDS}
    assert counts["all"] == len(kq.developers(**scope)) > 0


def test_band_edges(tmp_path, monkeypatch):
    ages = {90: "active", 91: "aging", 180: "aging", 181: "stale", 365: "stale",
            366: "unmaintained"}
    kq = _make_db(tmp_path, monkeypatch, [
        ("github.com~acme~api", f"d{age}@example.com", age, "c@example.com")
        for age in ages])
    rows, _ = kq.developer_list({}, now=NOW)
    assert {r["author_email"]: r["status"] for r in rows} == {
        f"d{age}@example.com": band for age, band in ages.items()}
    counts = kq.developer_status_counts({}, now=NOW)
    assert counts == {"active": 1, "aging": 2, "stale": 2, "unmaintained": 1, "all": 6}


@pytest.mark.parametrize("scope", SCOPES, ids=lambda s: str(s) or "estate")
def test_stats_and_commits_sources_agree(kq, scope):
    stats = kq.developer_list(scope, use_stats=True, now=NOW)
    commits = kq.developer_list(scope, use_stats=False, now=NOW)
    assert stats == commits
    assert stats[1] == len(stats[0]) > 0


def test_mixed_case_is_one_developer(kq):
    rows, _ = kq.developer_list({"org_key": "github.com~acme"}, search="paul@", now=NOW)
    paul = [r for r in rows if r["author_email"] == "paul@example.com"]
    assert len(paul) == 1
    assert (paul[0]["repos"], paul[0]["commits"]) == (2, 3)


def test_status_filter(kq):
    rows, total = kq.developer_list({}, status="aging", now=NOW)
    assert _emails(rows) == ["aging@example.com"]
    assert total == kq.developer_status_counts({}, now=NOW)["aging"] == 1


def test_search_is_a_case_insensitive_contains(kq):
    rows, total = kq.developer_list({}, search="PAUL", now=NOW)
    assert sorted(_emails(rows)) == ["mrpaul@example.com", "paul@example.com"]
    assert total == 2


@pytest.mark.parametrize("term, found", [
    ("a_b", ["a_b@example.com"]),
    ("%", ["pc%t@example.com"]),
    ("o'neil", ["o'neil@example.com"]),
    ("\\", []),
    ('"', []),
])
def test_search_wildcards_are_literal(kq, term, found):
    rows, _ = kq.developer_list({}, search=term, now=NOW)
    assert _emails(rows) == found


def test_exact_match_is_first(kq):
    rows, _ = kq.developer_list({}, search="Paul@Example.com", now=NOW)
    assert _emails(rows) == ["paul@example.com", "mrpaul@example.com"]


@pytest.mark.parametrize("sort, direction, first, last", [
    ("last_seen", "asc", "pc%t@example.com", "old@example.com"),
    ("last_seen", "desc", "old@example.com", "pc%t@example.com"),
    ("commits", "desc", "paul@example.com", "stale@example.com"),
    ("commits", "asc", "a_b@example.com", "paul@example.com"),
    ("repos", "desc", "paul@example.com", "stale@example.com"),
    ("repos", "asc", "a_b@example.com", "paul@example.com"),
    ("email", "asc", "a_b@example.com", "stale@example.com"),
    ("email", "desc", "stale@example.com", "a_b@example.com"),
])
def test_each_sort_and_direction(kq, sort, direction, first, last):
    rows, _ = kq.developer_list({}, sort=sort, direction=direction, now=NOW)
    assert (_emails(rows)[0], _emails(rows)[-1]) == (first, last)


def test_pages_are_stable_with_ties(tmp_path, monkeypatch):
    same = _when(3)
    kq = _make_db(tmp_path, monkeypatch, [
        ("github.com~acme~api", f"d{i}@example.com", same, "c@example.com")
        for i in range(7)])
    seen = []
    for offset in (0, 3, 6):
        rows, total = kq.developer_list({}, sort="commits", direction="desc",
                                        limit=3, offset=offset, now=NOW)
        assert total == 7
        seen += _emails(rows)
    assert len(seen) == 7 and len(set(seen)) == 7


def test_committers_column(kq):
    rows, total = kq.developer_list(
        {"org_key": "github.com~acme"}, column="committer_email", now=NOW)
    by_email = {r["committer_email"]: r for r in rows}
    assert sorted(by_email) == [
        "merge@example.com", "mrpaul@example.com", "paul@example.com"]
    assert (by_email["merge@example.com"]["commits"],
            by_email["merge@example.com"]["repos"]) == (4, 2)
    assert total == kq.committer_count({"org_key": "github.com~acme"}) == 3


def test_days_window_counts_only_the_window(tmp_path, monkeypatch):
    kq = _make_db(tmp_path, monkeypatch, [
        ("github.com~acme~api", "in@example.com", 10, "c@example.com"),
        ("github.com~acme~api", "in@example.com", 200, "c@example.com"),
        # an hour inside the window, written with a -10:00 offset
        ("github.com~acme~api", "tz@example.com", _when(90 - 1 / 24, -10), "c@example.com"),
        ("github.com~acme~api", "out@example.com", 91, "old@example.com"),
    ])
    rows, total = kq.developer_list({}, days=90, now=NOW)
    assert {r["author_email"]: r["commits"] for r in rows} == {
        "in@example.com": 1, "tz@example.com": 1}
    assert total == 2
    assert kq.committer_count({}, days=90, now=NOW) == 1
    assert kq.developer_list({}, days=10**12, now=NOW)[1] == 3


def test_busiest_orgs_and_repos(kq):
    orgs = kq.busiest_scopes({}, now=NOW)
    assert [(o["id"], o["name"], o["active"]) for o in orgs] == [
        ("github.com~acme", "acme", 2),
        ("github.com~beta", "beta", 2),
        ("gitlab.com~grp~~sub", "grp/sub", 2),
    ]
    assert len(kq.busiest_scopes({}, limit=1, now=NOW)) == 1
    repos = kq.busiest_scopes({"org_key": "github.com~acme"}, by="repo", now=NOW)
    assert [(r["id"], r["name"], r["active"]) for r in repos] == [
        ("github.com~acme~api", "api", 1), ("github.com~acme~web", "web", 1)]


def test_busiest_leaves_out_scopes_with_nobody_active(tmp_path, monkeypatch):
    kq = _make_db(tmp_path, monkeypatch, [
        ("github.com~acme~api", "a@example.com", 400, "c@example.com"),
        ("github.com~beta~cli", "b@example.com", 1, "c@example.com"),
    ])
    assert [o["id"] for o in kq.busiest_scopes({}, now=NOW)] == ["github.com~beta"]


def _mark(kq, repo_id, commits=999):
    """Make a repo's stats disagree with its commits, to show which was read."""
    kq.kospex_db.execute(
        "UPDATE developer_stats SET total_commits = ? WHERE _repo_id = ?", [commits, repo_id])


def _drop_stats(kq, repo_id):
    kq.kospex_db.execute("DELETE FROM developer_stats WHERE _repo_id = ?", [repo_id])


def _unbuild_stats(kq, repo_id):
    _mark(kq, repo_id, 555)
    kq.kospex_db.execute(
        "UPDATE developer_stats SET last_authored = NULL WHERE _repo_id = ?", [repo_id])


@pytest.mark.parametrize("spoil", [_drop_stats, _unbuild_stats])
def test_only_a_repo_with_a_stats_gap_is_read_from_commits(kq, spoil):
    truth = kq.developer_list({}, use_stats=False, now=NOW)
    spoil(kq, "github.com~acme~web")
    # The gap repo comes from commits, so nothing is lost or taken from old rows
    assert kq.developer_list({}, now=NOW) == truth
    assert kq.developer_status_counts({}, now=NOW) == kq.developer_status_counts(
        {}, use_stats=False, now=NOW)
    assert kq.busiest_scopes({}, now=NOW) == kq.busiest_scopes({}, use_stats=False, now=NOW)

    # ... and every other repo still comes from developer_stats
    _mark(kq, "github.com~acme~api")
    rows, _ = kq.developer_list({}, now=NOW)
    assert max(r["commits"] for r in rows) >= 999


def test_a_gap_outside_the_scope_changes_nothing(kq):
    _drop_stats(kq, "github.com~acme~web")
    _mark(kq, "github.com~acme~api")
    rows, _ = kq.developer_list({"repo_id": "github.com~acme~api"}, now=NOW)
    assert rows and all(r["commits"] == 999 for r in rows)


def test_too_many_gaps_to_name_reads_commits(kq, monkeypatch):
    monkeypatch.setattr(KospexQuery, "_MAX_GAP_REPOS", 0)
    truth = kq.developer_list({}, use_stats=False, now=NOW)
    _drop_stats(kq, "github.com~acme~web")
    _mark(kq, "github.com~acme~api")
    assert kq.developer_list({}, now=NOW) == truth


@pytest.mark.parametrize("kwargs", [
    {"column": "author_name"}, {"sort": "hash"}, {"direction": "up"},
])
def test_unknown_column_or_sort_raises(kq, kwargs):
    with pytest.raises(ValueError):
        kq.developer_list({}, **kwargs)
