"""developer_stats stores committer time in first_commit / last_commit (Phase 3).

update_developer_stats took first_commit / last_commit from author_when while
commits_last_90_days counted by committer_when, and the web layer uses
committer time throughout. A rebase, cherry-pick or applied patch keeps the
original author time, so the two disagree for about a quarter of
(repo, author) pairs on a real estate.

first_commit / last_commit now hold committer time, so "last commit" means
the same in developer_stats as on the web. Author time is kept in
first_authored / last_authored (columns from migration 0011). Every sync
rebuilds a repo's rows, so rows written before this change heal on the next
sync, and last_authored IS NULL marks a row that hasn't been rebuilt yet.

key_person() computes tenure live from commits and is deliberately left on
author time; that is a separate methodology decision.
"""
from datetime import datetime, timedelta, timezone

import pytest

import kospex_schema as KospexSchema
from kospex_query import KospexQuery

REPO = "github.com~acme~svc"


def _iso(days_ago, offset_hours=0):
    tz = timezone(timedelta(hours=offset_hours))
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).astimezone(tz).isoformat(timespec="seconds")


def _utc_z(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# (hash, author, author_when, committer_when)
COMMITS = [
    # rebased: written 400 days ago, landed 300 days ago
    ("h0", "dev@example.com", _iso(400), _iso(300)),
    # recent: written 50 days ago, landed 10 days ago, committer in UTC+11
    ("h1", "dev@example.com", _iso(50, -8), _iso(10, 11)),
    ("h2", "dev@example.com", _iso(200), _iso(200)),
    # other author, author time later than committer time (clock skew)
    ("h3", "other@example.com", _iso(20), _iso(30, -5)),
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
    db["repos"].insert({"_repo_id": REPO, "_git_server": "github.com",
                        "_git_owner": "acme", "_git_repo": "svc"})
    for h, author, authored, committed in COMMITS:
        db["commits"].insert({
            "hash": h, "_repo_id": REPO, "_git_server": "github.com",
            "_git_owner": "acme", "_git_repo": "svc",
            "author_email": author, "committer_email": author,
            "author_when": authored, "committer_when": committed, "parents": 1, "_files": 1,
        })
    return db


def _rows(db):
    return {r["author_email"]: r for r in db.query(
        "SELECT * FROM developer_stats WHERE _repo_id = ? AND stat_type = 'ALL_TIME'", [REPO])}


def _expected(author, field, pick):
    return _utc_z(pick((c for c in COMMITS if c[1] == author),
                       key=lambda c: datetime.fromisoformat(c[field]))[field])


@pytest.mark.parametrize("author", ["dev@example.com", "other@example.com"])
def test_first_and_last_commit_are_committer_time_in_utc(db, author):
    KospexQuery(kospex_db=db).update_developer_stats(REPO)
    row = _rows(db)[author]
    assert row["first_commit"] == _expected(author, 3, min)
    assert row["last_commit"] == _expected(author, 3, max)


@pytest.mark.parametrize("author", ["dev@example.com", "other@example.com"])
def test_first_and_last_authored_are_author_time_in_utc(db, author):
    KospexQuery(kospex_db=db).update_developer_stats(REPO)
    row = _rows(db)[author]
    assert row["first_authored"] == _expected(author, 2, min)
    assert row["last_authored"] == _expected(author, 2, max)


def test_the_two_actually_differ_in_this_fixture(db):
    # Guard against a vacuous pass: if author and committer time matched, every
    # assertion above would hold whichever column the code read.
    KospexQuery(kospex_db=db).update_developer_stats(REPO)
    row = _rows(db)["dev@example.com"]
    assert row["first_commit"] != row["first_authored"]
    assert row["last_commit"] != row["last_authored"]


def test_min_and_max_compare_instants_not_strings(db):
    # h1's committer time is in UTC+11, so its string sorts differently from its
    # instant. MAX over strings would still pick it here, so add a commit whose
    # string sorts later but whose instant is earlier.
    later_string_earlier_instant = (datetime.now(timezone.utc) - timedelta(days=10, hours=5)) \
        .astimezone(timezone(timedelta(hours=14))).isoformat(timespec="seconds")
    db["commits"].insert({
        "hash": "h4", "_repo_id": REPO, "_git_server": "github.com", "_git_owner": "acme",
        "_git_repo": "svc", "author_email": "dev@example.com", "committer_email": "dev@example.com",
        "author_when": later_string_earlier_instant, "committer_when": later_string_earlier_instant,
    })
    KospexQuery(kospex_db=db).update_developer_stats(REPO)
    assert _rows(db)["dev@example.com"]["last_commit"] == _utc_z(COMMITS[1][3])


def test_ninety_day_count_still_uses_committer_time(db):
    KospexQuery(kospex_db=db).update_developer_stats(REPO)
    rows = _rows(db)
    # dev: h1 landed 10 days ago (authored 50 ago); h0 and h2 landed 200+ days ago
    assert rows["dev@example.com"]["commits_last_90_days"] == 1
    assert rows["other@example.com"]["commits_last_90_days"] == 1
    assert rows["dev@example.com"]["total_commits"] == 3


def test_a_row_written_before_the_change_heals_on_the_next_rebuild(db):
    # The pre-change shape: author time in first/last_commit, *_authored NULL.
    db["developer_stats"].insert({
        "_repo_id": REPO, "author_email": "dev@example.com", "stat_type": "ALL_TIME",
        "total_commits": 3, "first_commit": _utc_z(COMMITS[0][2]),
        "last_commit": _utc_z(COMMITS[1][2]), "first_authored": None, "last_authored": None,
    })
    KospexQuery(kospex_db=db).update_developer_stats(REPO)
    rows = list(db.query("SELECT * FROM developer_stats WHERE _repo_id = ? AND author_email = ?",
                         [REPO, "dev@example.com"]))
    assert len(rows) == 1
    assert rows[0]["last_authored"] is not None
    assert rows[0]["last_commit"] == _expected("dev@example.com", 3, max)


def test_rebuild_is_idempotent(db):
    kq = KospexQuery(kospex_db=db)
    kq.update_developer_stats(REPO)
    first = _rows(db)
    kq.update_developer_stats(REPO)
    second = _rows(db)
    strip = lambda rows: {k: {f: v for f, v in r.items() if f != "last_update"} for k, r in rows.items()}  # noqa: E731
    assert strip(first) == strip(second)


def test_key_person_tenure_stays_on_author_time(db):
    # Deliberately unchanged: key_person() reads commits live, by author time.
    people = KospexQuery(kospex_db=db).key_person(repo_id=REPO)
    dev = next(p for p in people if p["author"] == "dev@example.com")
    authored, committed = _utc_z(COMMITS[0][2])[:10], _utc_z(COMMITS[0][3])[:10]
    assert authored != committed
    assert dev["first_commit"] == authored
