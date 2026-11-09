"""KospexGithub._make_request: which responses count as rate limiting.

Before: only a 403 with X-RateLimit-Remaining: 0 set `throttled`. GitHub's
secondary rate limits (403 or 429, usually with Retry-After) fell through as
ordinary HTTP errors, so a long per-repo run recorded every remaining repo as
an error instead of stopping.
"""
import json

import pytest
import requests

import kospex_github
from kospex_github import KospexGithub


def response(status, body=None, headers=None):
    r = requests.Response()
    r.status_code = status
    r.url = "https://api.github.com/x"
    r.headers.update(headers or {})
    if body is not None:
        r._content = json.dumps(body).encode()
    else:
        r._content = b""
    return r


@pytest.fixture
def gh(monkeypatch):
    """A client whose requests return scripted responses, in order."""
    client = KospexGithub()
    client.sleeps = []
    client._sleep = client.sleeps.append
    client.replies = []

    def fake_request(method, url, headers=None, params=None, timeout=None):
        return client.replies.pop(0)

    monkeypatch.setattr(kospex_github.requests, "request", fake_request)
    return client


def test_429_sets_throttled(gh):
    gh.replies = [response(429, {"message": "Too Many Requests"})]
    data, resp = gh._make_request("https://api.github.com/x")
    assert data is None and resp.status_code == 429
    assert gh.throttled is True


def test_403_with_long_retry_after_sets_throttled(gh):
    gh.replies = [response(403, {"message": "slow down"}, {"Retry-After": "120"})]
    data, _ = gh._make_request("https://api.github.com/x")
    assert data is None
    assert gh.throttled is True
    assert gh.sleeps == []


def test_short_retry_after_waits_once_then_retries(gh):
    gh.replies = [response(429, {"message": "slow down"}, {"Retry-After": "5"}),
                  response(200, [])]
    data, resp = gh._make_request("https://api.github.com/x")
    assert data == [] and resp.status_code == 200
    assert gh.throttled is False
    assert gh.sleeps == [5]


def test_short_retry_after_twice_gives_up(gh):
    gh.replies = [response(429, {"message": "slow down"}, {"Retry-After": "5"}),
                  response(429, {"message": "slow down"}, {"Retry-After": "5"})]
    data, _ = gh._make_request("https://api.github.com/x")
    assert data is None
    assert gh.throttled is True
    assert gh.sleeps == [5]


def test_secondary_limit_message_sets_throttled(gh):
    gh.replies = [response(403, {"message": "You have exceeded a secondary rate limit. "
                                            "Please wait a few minutes before you try again."})]
    data, _ = gh._make_request("https://api.github.com/x")
    assert data is None
    assert gh.throttled is True


def test_ordinary_403_is_not_throttled(gh):
    gh.replies = [response(403, {"message": "Dependabot alerts are disabled for this repository."})]
    data, resp = gh._make_request("https://api.github.com/x")
    assert data is None and resp.status_code == 403
    assert gh.throttled is False


def test_quiet_status_is_not_printed(gh, capsys):
    # A caller that turns a 403/404 into a status doesn't want "HTTP Error" noise.
    gh.replies = [response(403, {"message": "Dependabot alerts are disabled for this repository."})]
    data, resp = gh._make_request("https://api.github.com/x", quiet_statuses=(403, 404))
    assert data is None and resp.status_code == 403
    assert "HTTP Error" not in capsys.readouterr().out


def test_other_statuses_still_printed(gh, capsys):
    gh.replies = [response(502, {"message": "Bad Gateway"})]
    gh._make_request("https://api.github.com/x", quiet_statuses=(403, 404))
    assert "HTTP Error" in capsys.readouterr().out


def test_errors_printed_by_default(gh, capsys):
    gh.replies = [response(404, {"message": "Not Found"})]
    gh._make_request("https://api.github.com/x")
    assert "HTTP Error" in capsys.readouterr().out


def test_primary_limit_still_detected(gh):
    gh.replies = [response(403, {"message": "API rate limit exceeded"},
                           {"X-RateLimit-Remaining": "0"})]
    data, _ = gh._make_request("https://api.github.com/x")
    assert data is None
    assert gh.throttled is True
