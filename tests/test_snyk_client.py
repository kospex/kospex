"""SnykClient: auth, base URL, paging, error mapping, retries, probe."""
import pytest

from kospex.integrations.snyk import client as snyk_client
from kospex.integrations.snyk.client import (
    REST_VERSION, SnykAuthError, SnykClient, SnykError, SnykNotAvailable,
    SnykRateLimited, api_url_from_env)

API = "https://api.snyk.io"


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None, reason=""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.reason = reason

    def json(self):
        return self._body


class FakeSession:
    """Replays responses in order and records every GET."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if not self.responses:
            raise AssertionError(f"unexpected request: {url}")
        return self.responses.pop(0)


def make(responses, **kw):
    session = FakeSession(responses)
    sleeps = []
    c = SnykClient(token="tok", api_url=kw.pop("api_url", ""), session=session,
                   sleep=sleeps.append, **kw)
    return c, session, sleeps


def page(items, next_link=None):
    body = {"data": items, "links": {}}
    if next_link:
        body["links"]["next"] = next_link
    return FakeResponse(200, body)


FORBIDDEN = {"errors": [{"status": "403", "title": "Forbidden", "detail": "Forbidden"}]}


class TestConfig:
    def test_missing_token(self, monkeypatch):
        monkeypatch.delenv("SNYK_TOKEN", raising=False)
        with pytest.raises(SnykAuthError, match="SNYK_TOKEN"):
            SnykClient(session=FakeSession([]))

    def test_blank_token_is_missing(self, monkeypatch):
        monkeypatch.setenv("SNYK_TOKEN", "   ")
        with pytest.raises(SnykAuthError):
            SnykClient(session=FakeSession([]))

    def test_token_from_env_is_stripped(self, monkeypatch):
        monkeypatch.setenv("SNYK_TOKEN", " abc \n")
        assert SnykClient(session=FakeSession([]), api_url="").token == "abc"

    @pytest.mark.parametrize("value,expected", [
        ("", API),
        ("https://api.eu.snyk.io", "https://api.eu.snyk.io"),
        ("https://api.eu.snyk.io/", "https://api.eu.snyk.io"),
        ("https://api.au.snyk.io/v1", "https://api.au.snyk.io"),
        ("https://api.snyk.io/rest/", API),
    ])
    def test_api_url_forms(self, value, expected):
        assert api_url_from_env(value) == expected

    @pytest.mark.parametrize("value", ["http://api.snyk.io", "api.snyk.io",
                                       "https://api.snyk.io/other"])
    def test_api_url_refused(self, value):
        with pytest.raises(SnykError):
            api_url_from_env(value)

    def test_api_url_from_env_var(self, monkeypatch):
        monkeypatch.setenv("SNYK_API", "https://api.eu.snyk.io/v1")
        assert api_url_from_env() == "https://api.eu.snyk.io"


class TestGet:
    def test_sends_version_and_token_header(self):
        c, s, _ = make([FakeResponse(200, {"data": {"id": "u"}})])
        assert c.self_user() == {"id": "u"}
        call = s.calls[0]
        assert call["url"] == f"{API}/rest/self"
        assert call["params"]["version"] == REST_VERSION
        assert call["headers"]["Authorization"] == "token tok"
        assert c.requests == 1

    @pytest.mark.parametrize("status,exc", [(401, SnykAuthError), (403, SnykNotAvailable),
                                            (500, SnykError)])
    def test_status_mapping(self, status, exc):
        c, _, _ = make([FakeResponse(status, FORBIDDEN if status == 403 else {})])
        with pytest.raises(exc) as info:
            c.get("/orgs/o1/issues")
        assert info.value.status == status
        assert info.value.path == "/orgs/o1/issues"

    def test_403_message_names_the_plan(self):
        c, _, _ = make([FakeResponse(403, FORBIDDEN)])
        with pytest.raises(SnykNotAvailable, match="Enterprise"):
            c.get("/orgs/o1/projects")

    def test_429_waits_and_retries(self):
        c, s, sleeps = make([FakeResponse(429, headers={"Retry-After": "2"}),
                             FakeResponse(200, {"data": {}})])
        c.get("/self")
        assert sleeps == [2]
        assert len(s.calls) == 2 and c.requests == 2

    def test_retry_after_is_capped(self):
        c, _, sleeps = make([FakeResponse(429, headers={"Retry-After": "9999"}),
                             FakeResponse(200, {"data": {}})])
        c.get("/self")
        assert sleeps == [snyk_client.MAX_RATE_LIMIT_WAIT]

    def test_persistent_429_raises(self):
        n = snyk_client.MAX_RETRIES + 1
        c, s, _ = make([FakeResponse(429)] * n)
        with pytest.raises(SnykRateLimited):
            c.get("/self")
        assert len(s.calls) == n


class TestPaginate:
    def test_follows_relative_next_without_resending_params(self):
        c, s, _ = make([page([{"id": 1}], "/orgs/o1/issues?version=v&starting_after=abc"),
                        page([{"id": 2}])])
        assert [i["id"] for i in c.paginate("/orgs/o1/issues", type="x")] == [1, 2]
        assert s.calls[0]["params"]["limit"] == 100
        assert s.calls[0]["params"]["type"] == "x"
        assert s.calls[1]["url"] == f"{API}/rest/orgs/o1/issues?version=v&starting_after=abc"
        assert s.calls[1]["params"] is None

    def test_next_with_rest_prefix(self):
        c, s, _ = make([page([1], "/rest/orgs?starting_after=z"), page([2])])
        assert list(c.paginate("/orgs")) == [1, 2]
        assert s.calls[1]["url"] == f"{API}/rest/orgs?starting_after=z"

    def test_next_absolute_same_host(self):
        c, s, _ = make([page([1], f"{API}/rest/orgs?starting_after=z"), page([2])])
        assert list(c.paginate("/orgs")) == [1, 2]

    def test_next_to_another_host_is_refused_before_sending_token(self):
        c, s, _ = make([page([1], "https://evil.example.com/rest/orgs?x=1")])
        with pytest.raises(SnykError, match="another host"):
            list(c.paginate("/orgs"))
        assert len(s.calls) == 1

    def test_max_items_stops_without_next_request(self):
        c, s, _ = make([page([1, 2, 3], "/orgs?starting_after=z")])
        assert list(c.paginate("/orgs", max_items=2)) == [1, 2]
        assert len(s.calls) == 1

    def test_error_on_second_page_raises(self):
        c, _, _ = make([page([1], "/orgs?starting_after=z"), FakeResponse(403, FORBIDDEN)])
        with pytest.raises(SnykNotAvailable):
            list(c.paginate("/orgs"))


class TestEndpoints:
    def test_issue_filters_are_comma_joined(self):
        c, s, _ = make([page([])])
        list(c.issues("o1", statuses=("open", "resolved"), severities=("critical", "high"),
                      project_id="p1", ignored=False))
        p = s.calls[0]["params"]
        assert s.calls[0]["url"] == f"{API}/rest/orgs/o1/issues"
        assert p["type"] == "package_vulnerability"
        assert p["status"] == "open,resolved"
        assert p["effective_severity_level"] == "critical,high"
        assert p["scan_item.id"] == "p1" and p["scan_item.type"] == "project"
        assert p["ignored"] == "false"

    def test_issue_defaults_leave_optional_filters_out(self):
        c, s, _ = make([page([])])
        list(c.issues("o1"))
        p = s.calls[0]["params"]
        assert p["status"] == "open"
        for key in ("effective_severity_level", "scan_item.id", "ignored"):
            assert key not in p

    def test_projects_ids_and_counts(self):
        c, s, _ = make([page([])])
        c.projects("o1", ids=["p1", "p2"], issue_counts=True)
        p = s.calls[0]["params"]
        assert p["ids"] == "p1,p2"
        assert p["meta.latest_issue_counts"] == "true"


class TestProbe:
    def test_free_plan_shape_never_raises(self):
        """The measured free-plan result: identity and orgs work, the rest 403."""
        c, s, _ = make([
            FakeResponse(200, {"data": {"id": "u"}}),
            page([{"id": "o1"}]),
            FakeResponse(403, FORBIDDEN),
            FakeResponse(403, FORBIDDEN),
            page([]),
        ])
        results = c.probe()
        assert [(r.name, r.status) for r in results] == [
            ("self", 200), ("orgs", 200), ("projects", 403), ("issues", 403),
            ("targets", 200)]
        assert results[2].path == "/orgs/o1/projects"
        assert results[2].detail == "Forbidden"
        assert all(call["params"].get("limit") in (None, 10) for call in s.calls)
        assert c.requests == 5

    def test_no_orgs_stops_after_orgs(self):
        c, s, _ = make([FakeResponse(200, {"data": {}}), page([])])
        assert [r.name for r in c.probe()] == ["self", "orgs"]
        assert len(s.calls) == 2
