"""The deps.dev response cache TTL, and why one hour is the wrong default.

Every external lookup kospex makes goes through url_request_with_status(), whose
TTL defaulted to 3600 seconds. Seven call sites rely on that default and none
overrides it: deps_dev, deps_dev_status, deps_dev_package, get_url_json,
get_pypi_package_info and two others.

One hour is fine for a single full-estate sweep, which finishes inside the
window and so dedupes the packages shared between repos. It is wrong for a
batched sweep -- `krunner osi -next N` run from cron over hours or days -- where
every tick starts with a cold cache and re-fetches the same packages. On a
167-repo estate 6,381 lookups collapse to 4,853 distinct package+version pairs
(24% duplication), and that ratio rises sharply with estate size as repositories
converge on shared internal standards.

Freshness bound: a cache hit means kospex did NOT contact deps.dev, so
`dependency_data.last_checked` attests "evaluated", not "fetched". The honest
bound on advisory age is `last_checked + TTL`, which is why the default is a day
rather than a week -- and why it is configurable, so a deployment choosing a
slower cadence can trade freshness for traffic deliberately.
"""
import time

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_query import KospexQuery

URL = "https://api.deps.dev/v3alpha/systems/pypi/packages/click/versions/8.1.7"
CACHED_BODY = '{"versionKey":{"name":"click","version":"8.1.7"}}'


@pytest.fixture
def kq():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_URL_CACHE)
    return KospexQuery(kospex_db=db)


def _cache_entry(kq, age_seconds):
    kq.kospex_db.table(KospexSchema.TBL_URL_CACHE).upsert(
        {"url": URL, "content": CACHED_BODY,
         "timestamp": int(time.time()) - age_seconds},
        pk=["url"],
    )


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any cache miss must be a test failure, not a real HTTP call."""
    import requests

    def _boom(*args, **kwargs):
        raise AssertionError("cache miss: a real HTTP request was attempted")

    monkeypatch.setattr(requests, "get", _boom)


def test_a_two_hour_old_response_is_still_served_from_cache(kq):
    """The old 3600s default expired this; a batched sweep needs it kept."""
    _cache_entry(kq, age_seconds=2 * 60 * 60)

    content, status = kq.url_request_with_status(URL)

    assert status == 200
    assert content == CACHED_BODY


def test_a_response_older_than_the_ttl_is_refetched(kq):
    """The cache must still expire -- this is a TTL, not a permanent store."""
    _cache_entry(kq, age_seconds=40 * 24 * 60 * 60)   # 40 days

    with pytest.raises(AssertionError, match="real HTTP request"):
        kq.url_request_with_status(URL)


def test_the_ttl_is_configurable(kq, monkeypatch):
    """A deployment picking a slower cadence can raise it deliberately."""
    monkeypatch.setenv("KOSPEX_URL_CACHE_SECONDS", "60")
    _cache_entry(kq, age_seconds=120)                 # older than the 60s override

    with pytest.raises(AssertionError, match="real HTTP request"):
        kq.url_request_with_status(URL)


def test_an_explicit_cache_argument_still_wins(kq):
    """Callers that pass cache= must not be overridden by the default or env."""
    _cache_entry(kq, age_seconds=120)

    content, status = kq.url_request_with_status(URL, cache=600)
    assert status == 200 and content == CACHED_BODY

    with pytest.raises(AssertionError, match="real HTTP request"):
        kq.url_request_with_status(URL, cache=60)


def test_url_request_shares_the_same_ttl(kq):
    """url_request() delegates, so it must inherit the longer default."""
    _cache_entry(kq, age_seconds=2 * 60 * 60)

    assert kq.url_request(URL) == CACHED_BODY


def test_a_cache_hit_does_not_count_as_a_fetch(kq):
    """url_fetches is the wall-clock term: round trips made, not rows served.

    osi -next records it per repository so cache effectiveness is measurable over
    time. A hit that counted would make the cache look useless.
    """
    _cache_entry(kq, age_seconds=60)
    before = kq.url_fetches

    kq.url_request_with_status(URL)

    assert kq.url_fetches == before, "a cache hit made no request"


def test_a_cache_miss_counts_as_a_fetch_even_when_it_fails(kq, monkeypatch):
    """A failed round trip cost the same wall-clock time as a successful one."""
    import requests

    def _boom(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(requests, "get", _boom)
    before = kq.url_fetches

    content, status = kq.url_request_with_status(URL)

    assert (content, status) == (None, None)
    assert kq.url_fetches == before + 1
