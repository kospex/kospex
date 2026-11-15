"""One staleness rule for kgit pull --stale, --check and kweb /syncs/.

A repo's last refresh is last_fetch, or last_sync when it was never fetched.
"""
from datetime import datetime, timedelta, timezone

from kospex import staleness

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)


def _ago(**delta):
    return (NOW - timedelta(**delta)).isoformat()


def test_last_fetch_wins_over_last_sync():
    repo = {"last_fetch": _ago(days=2), "last_sync": _ago(days=40)}
    assert staleness.last_refreshed(repo) == repo["last_fetch"]
    assert staleness.age_days(repo, NOW) == 2


def test_falls_back_to_last_sync():
    repo = {"last_fetch": None, "last_sync": _ago(days=40)}
    assert staleness.last_refreshed(repo) == repo["last_sync"]
    assert staleness.is_stale(repo, 30, NOW)
    assert not staleness.is_stale(repo, 60, NOW)


def test_neither_is_always_stale():
    repo = {"last_fetch": None, "last_sync": ""}
    assert staleness.age_days(repo, NOW) is None
    assert staleness.is_stale(repo, 1, NOW)
    assert staleness.is_stale(repo, 10_000, NOW)


def test_mixed_offsets_compare_as_instants():
    same_instant = {"last_fetch": "2026-11-15T23:00:00+11:00"}
    in_utc = {"last_fetch": "2026-11-15T12:00:00Z"}
    assert staleness.age_days(same_instant, NOW) == 0
    assert staleness.age_days(in_utc, NOW) == 0


def test_naive_time_is_utc():
    assert staleness.parse_time("2026-11-15 12:00:00") == NOW
    assert staleness.age_days({"last_fetch": "2026-11-14 12:00:00"}, NOW) == 1


def test_unparseable_is_none():
    assert staleness.parse_time("last tuesday") is None
    assert staleness.parse_time(None) is None
    assert staleness.is_stale({"last_fetch": "last tuesday"}, 30, NOW)


def test_boundary():
    exactly = {"last_fetch": _ago(days=30)}
    just_over = {"last_fetch": _ago(days=30, seconds=1)}
    assert not staleness.is_stale(exactly, 30, NOW)
    assert staleness.is_stale(just_over, 30, NOW)


def test_rows_sort_by_fallback_age():
    repos = [
        {"_repo_id": "s~o~synced-yesterday", "last_fetch": None, "last_sync": _ago(days=1)},
        {"_repo_id": "s~o~fetched-40", "last_fetch": _ago(days=40), "last_sync": _ago(days=40)},
        {"_repo_id": "s~o~never", "last_fetch": None, "last_sync": None},
    ]
    rows = staleness.staleness_rows(repos, NOW)
    assert [r["repo_id"] for r in rows] == ["s~o~never", "s~o~fetched-40", "s~o~synced-yesterday"]
    yesterday = rows[2]
    assert yesterday["last_fetch"] == "never"
    assert yesterday["fell_back"] is True
    assert yesterday["fetch_age"] == "1d"
    assert rows[0]["fetch_age"] == "never"
    assert rows[1]["fell_back"] is False
    assert rows[1]["age_days"] == 40
