"""OsvClient: paging, retries, errors, redirects, the cached vuln() path. HTTP is faked."""
import json

import pytest

from kospex.integrations.osv.client import MAX_PAGES, MAX_TOTAL_WAIT, OsvClient, OsvError


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None, text=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(body if body is not None else {})


class FakeSession:
    """Replays responses in order and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, timeout=None, allow_redirects=None, json=None, **_):
        self.calls.append({"method": method, "url": url, "json": json,
                           "allow_redirects": allow_redirects, "timeout": timeout})
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)


def make(responses, **kwargs):
    session = FakeSession(responses)
    sleeps = []
    return OsvClient(session=session, sleep=sleeps.append, **kwargs), session, sleeps


class TestQuery:
    def test_posts_package_ecosystem_and_version(self):
        client, session, _ = make([FakeResponse(200, {"vulns": [{"id": "GHSA-a"}]})])

        assert client.query("PyPI", "requests", "2.25.0") == [{"id": "GHSA-a"}]

        (call,) = session.calls
        assert call["method"] == "POST"
        assert call["url"] == "https://api.osv.dev/v1/query"
        assert call["json"] == {"package": {"name": "requests", "ecosystem": "PyPI"},
                                "version": "2.25.0"}

    def test_no_vulns_key_is_an_empty_list(self):
        client, _, _ = make([FakeResponse(200, {})])
        assert client.query("PyPI", "nothing-here", "1.0") == []

    def test_follows_next_page_token_in_the_body(self):
        client, session, _ = make([
            FakeResponse(200, {"vulns": [{"id": "A"}], "next_page_token": "t1"}),
            FakeResponse(200, {"vulns": [{"id": "B"}]}),
        ])

        assert [v["id"] for v in client.query("npm", "lodash", "4.17.15")] == ["A", "B"]

        assert "page_token" not in session.calls[0]["json"]
        assert session.calls[1]["json"]["page_token"] == "t1"
        assert session.calls[1]["json"]["package"]["name"] == "lodash"

    def test_gives_up_on_endless_paging(self):
        pages = [FakeResponse(200, {"vulns": [], "next_page_token": f"t{i}"})
                 for i in range(MAX_PAGES + 1)]
        client, session, _ = make(pages)

        with pytest.raises(OsvError, match="pages"):
            client.query("npm", "lodash", "1.0.0")
        assert len(session.calls) == MAX_PAGES

    def test_counts_its_requests(self):
        client, _, _ = make([FakeResponse(200, {})])
        client.query("npm", "x", "1")
        assert client.requests_made == 1


class TestRetriesAndErrors:
    def test_redirects_are_never_followed(self):
        client, session, _ = make([FakeResponse(302, headers={"Location": "https://evil.example/"})])

        with pytest.raises(OsvError) as caught:
            client.query("npm", "x", "1")

        assert session.calls[0]["allow_redirects"] is False
        assert caught.value.status == 302
        assert len(session.calls) == 1

    def test_429_waits_for_retry_after_then_succeeds(self):
        client, _, sleeps = make([FakeResponse(429, headers={"Retry-After": "7"}),
                                  FakeResponse(200, {"vulns": []})])

        assert client.query("npm", "x", "1") == []
        assert sleeps == [7.0]

    def test_a_wait_longer_than_the_budget_gives_up_at_once(self):
        # A CLI must not sit silent for minutes: 60s is more than the 20s budget.
        client, session, sleeps = make([FakeResponse(429, headers={"Retry-After": "60"})])

        with pytest.raises(OsvError) as caught:
            client.query("npm", "x", "1")

        assert caught.value.status == 429
        assert sleeps == []
        assert len(session.calls) == 1

    def test_total_waiting_is_bounded(self):
        client, session, sleeps = make([FakeResponse(429, headers={"Retry-After": "9"})] * 10)

        with pytest.raises(OsvError):
            client.query("npm", "x", "1")

        assert sleeps == [9.0, 9.0]          # a third 9s would pass the 20s budget
        assert sum(sleeps) <= MAX_TOTAL_WAIT
        assert len(session.calls) == 3

    def test_5xx_backs_off_then_succeeds(self):
        client, _, sleeps = make([FakeResponse(503), FakeResponse(503), FakeResponse(200, {})])
        client.query("npm", "x", "1")
        assert sleeps == [1.0, 2.0]

    def test_gives_up_after_the_retries(self):
        client, session, _ = make([FakeResponse(500)] * 10)

        with pytest.raises(OsvError) as caught:
            client.query("npm", "x", "1")

        assert caught.value.status == 500
        assert len(session.calls) == 4   # one try and three retries

    def test_a_400_is_not_retried(self):
        client, session, sleeps = make([FakeResponse(400)])

        with pytest.raises(OsvError) as caught:
            client.query("npm", "x", "1")

        assert caught.value.status == 400
        assert len(session.calls) == 1
        assert sleeps == []

    @pytest.mark.parametrize("text", ["<html>", "", "[1, 2]", "null"])
    def test_a_body_that_is_not_a_json_object_is_an_error(self, text):
        client, _, _ = make([FakeResponse(200, text=text)])
        with pytest.raises(OsvError):
            client.query("npm", "x", "1")

    def test_a_network_failure_is_an_osv_error(self):
        import requests

        class Broken:
            def request(self, *args, **kwargs):
                raise requests.ConnectionError("no route")

        with pytest.raises(OsvError, match="ConnectionError"):
            OsvClient(session=Broken()).query("npm", "x", "1")


class TestVuln:
    def test_gets_one_record_by_id(self):
        client, session, _ = make([FakeResponse(200, {"id": "GHSA-a"})])

        assert client.vuln("GHSA-a") == {"id": "GHSA-a"}
        assert session.calls[0]["method"] == "GET"
        assert session.calls[0]["url"] == "https://api.osv.dev/v1/vulns/GHSA-a"

    def test_404_is_none(self):
        client, _, _ = make([FakeResponse(404)])
        assert client.vuln("GHSA-nope") is None

    def test_an_id_cannot_change_the_path(self):
        client, session, _ = make([FakeResponse(200, {})])
        client.vuln("../query?x=1")
        assert session.calls[0]["url"] == "https://api.osv.dev/v1/vulns/..%2Fquery%3Fx%3D1"

    def test_goes_through_cached_get_when_given(self):
        asked = []

        def cached_get(url):
            asked.append(url)
            return json.dumps({"id": "GHSA-a"}), 200

        client, session, _ = make([], cached_get=cached_get)

        assert client.vuln("GHSA-a") == {"id": "GHSA-a"}
        assert asked == ["https://api.osv.dev/v1/vulns/GHSA-a"]
        assert session.calls == []

    def test_cached_get_404_is_none_and_other_failures_raise(self):
        client, _, _ = make([], cached_get=lambda url: (None, 404))
        assert client.vuln("GHSA-a") is None

        client, _, _ = make([], cached_get=lambda url: (None, 503))
        with pytest.raises(OsvError) as caught:
            client.vuln("GHSA-a")
        assert caught.value.status == 503

        client, _, _ = make([], cached_get=lambda url: (None, None))
        with pytest.raises(OsvError):
            client.vuln("GHSA-a")

    def test_query_is_never_cached(self):
        def cached_get(url):
            raise AssertionError("POST /query must not go through the URL cache")

        client, session, _ = make([FakeResponse(200, {})], cached_get=cached_get)
        client.query("npm", "x", "1")
        assert len(session.calls) == 1
