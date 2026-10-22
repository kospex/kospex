"""RequestCache: the in-memory cache kweb uses for slow summary queries.

The key had no scope in it, so /summary/<org_key> served the estate-wide
developer list cached by / (3 repos, 72,649 developers). The scope is now part
of the key, and an entry expires on a newer repo sync or after max_age.
"""
from datetime import datetime, timedelta

from kospex_request_cache import RequestCache


def _iso(dt):
    """A last_sync value in the shape the repos table stores it."""
    return dt.isoformat(timespec="seconds")


def test_key_separates_scopes():
    estate = RequestCache.key("developers")
    org = RequestCache.key("developers", org_key="github.com~kospex")
    server = RequestCache.key("developers", server="github.com")
    assert len({estate, org, server}) == 3


def test_key_ignores_unset_params_and_order():
    assert RequestCache.key("repos", org_key=None, server=None) == RequestCache.key("repos")
    assert RequestCache.key("x", a="1", b="2") == RequestCache.key("x", b="2", a="1")


def test_hit_before_any_newer_sync():
    cache = RequestCache()
    cache.set("k", [1])
    earlier = datetime.now().astimezone() - timedelta(minutes=5)
    assert cache.get("k", _iso(earlier)) == [1]


def test_empty_result_is_a_hit_not_a_miss():
    """A scope with no rows is a valid answer; it must not recompute every time."""
    cache = RequestCache()
    cache.set("k", [])
    assert cache.get("k", None) == []


def test_newer_sync_expires_the_entry():
    cache = RequestCache()
    cache.set("k", [1])
    later = datetime.now().astimezone() + timedelta(minutes=5)
    assert cache.get("k", _iso(later)) is None


def test_sync_compared_as_a_time_not_a_string():
    """A sync recorded in another offset: earlier in absolute time, but the
    string sorts higher. String comparison called that stale."""
    cache = RequestCache(max_age_seconds=10**9)
    cached_at = datetime.fromisoformat("2026-10-17T00:30:00+00:00")

    # 10:30+11:00 is 2026-10-16T23:30Z, an hour BEFORE the cache time, but as
    # a string "2026-10-17T10:30" sorts after "2026-10-17T00:30"
    cache.set("k", [1], cache_time=cached_at)
    assert cache.get("k", "2026-10-17T10:30:00+11:00") == [1]

    # 01:00Z is after it
    cache.set("k", [1], cache_time=cached_at)
    assert cache.get("k", "2026-10-17T01:00:00+00:00") is None


def test_sync_during_the_query_expires_the_entry():
    """cache_time is when the query started: a sync that lands while it runs
    may be missing from the result, so it must still invalidate it."""
    cache = RequestCache()
    started = datetime.now().astimezone() - timedelta(seconds=10)
    sync_mid_query = started + timedelta(seconds=5)
    cache.set("k", [1], cache_time=started)
    assert cache.get("k", _iso(sync_mid_query)) is None


def test_max_age_expires_the_entry():
    """Statuses and days_ago are relative to now, so an entry ages even with
    no sync at all."""
    cache = RequestCache(max_age_seconds=60)
    cache.set("k", [1], cache_time=datetime.now().astimezone() - timedelta(seconds=61))
    assert cache.get("k", None) is None


def test_unparseable_last_sync_is_a_miss():
    cache = RequestCache()
    cache.set("k", [1])
    assert cache.get("k", "not a date") is None


def test_entry_count_is_bounded():
    """The scope comes from the URL, so arbitrary ids each add a key."""
    cache = RequestCache(max_entries=3)
    for i in range(100):
        cache.set(RequestCache.key("developers", org_key=f"junk~{i}"), [])
    assert len(cache.cache) == 3


def test_eviction_is_least_recently_used():
    cache = RequestCache(max_entries=2)
    cache.set("a", [1])
    cache.set("b", [2])
    cache.get("a", None)      # a is now the most recently used
    cache.set("c", [3])       # evicts b, not a
    assert cache.get("a", None) == [1]
    assert cache.get("b", None) is None
    assert cache.get("c", None) == [3]


def test_clear():
    cache = RequestCache()
    cache.set("k", [1])
    cache.clear()
    assert cache.get("k", None) is None
