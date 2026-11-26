"""The read-only GitLab REST client."""
import pytest
import requests

from kospex.integrations.gitlab.client import (
    GitLabAuthError,
    GitLabClient,
    GitLabError,
    has_path_prefix,
    server_url,
)

TOKEN = "glpat-secret-value"


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, bad_json=False):
        self.status_code = status
        self.headers = headers or {}
        self._body = [] if body is None else body
        self._bad_json = bad_json
        self.reason = "Reason"

    def json(self):
        if self._bad_json:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Answers each GET with the next scripted response (or raises it)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": headers,
                           "allow_redirects": allow_redirects})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def no_gitlab_env(monkeypatch):
    monkeypatch.delenv("GITLAB_URL", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)


def make(*responses, server="gitlab.example.com", **kw):
    session = FakeSession(*responses)
    kw.setdefault("sleep", lambda s: None)
    return GitLabClient(token=TOKEN, server=server, session=session, **kw), session


# --- server_url -------------------------------------------------------------

def test_the_default_server_is_gitlab_com():
    assert server_url(None) == "https://gitlab.com"


def test_gitlab_url_is_the_default_when_set(monkeypatch):
    monkeypatch.setenv("GITLAB_URL", "gitlab.example.com")
    assert server_url(None) == "https://gitlab.example.com"


@pytest.mark.parametrize("value, expected", [
    ("gitlab.example.com", "https://gitlab.example.com"),
    ("gitlab.example.com:8443", "https://gitlab.example.com:8443"),
    ("https://GitLab.Example.com/", "https://gitlab.example.com"),
    ("https://host/gitlab/api/v4/", "https://host/gitlab"),
])
def test_server_forms(value, expected):
    assert server_url(value) == expected


@pytest.mark.parametrize("value", [
    "", "https://", "http://host", "ftp://host", "https://u:p@host",
    "https://host?x=1", "https://host#x", "host:abc", "host:70000",
])
def test_servers_that_are_refused(value):
    with pytest.raises(GitLabError):
        server_url(value)


def test_a_refused_server_does_not_echo_its_password():
    with pytest.raises(GitLabError) as e:
        server_url("https://u:hunter2@host")
    assert "hunter2" not in str(e.value)


def test_has_path_prefix():
    assert has_path_prefix("https://host/gitlab")
    assert not has_path_prefix("https://host:8443")


# --- requests ---------------------------------------------------------------

def test_a_token_is_required():
    with pytest.raises(GitLabAuthError, match="GITLAB_TOKEN"):
        GitLabClient(server="gitlab.example.com", session=FakeSession())


def test_the_token_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", f" {TOKEN}\n")
    client = GitLabClient(session=FakeSession(FakeResponse()))
    assert client.token == TOKEN


def test_get_sends_the_token_and_does_not_follow_redirects():
    client, session = make(FakeResponse())
    client.get("/user")
    call = session.calls[0]
    assert call["url"] == "https://gitlab.example.com/api/v4/user"
    assert call["headers"]["PRIVATE-TOKEN"] == TOKEN
    assert call["allow_redirects"] is False


def test_a_redirect_is_an_error_naming_the_target():
    client, session = make(FakeResponse(302, headers={"Location": "https://sso.example.com/x"}))
    with pytest.raises(GitLabError, match="https://sso.example.com/x"):
        client.get("/user")
    assert len(session.calls) == 1


def test_a_port_is_kept_on_every_page():
    client, session = make(
        FakeResponse(body=[{"id": 1}], headers={"X-Next-Page": "2"}),
        FakeResponse(body=[{"id": 2}], headers={"X-Next-Page": ""}),
        server="https://gitlab.example.com:8929")
    client.paginate("/groups")
    assert [c["url"] for c in session.calls] == [
        "https://gitlab.example.com:8929/api/v4/groups"] * 2


def test_401_names_the_server_and_the_variable():
    client, _ = make(FakeResponse(401))
    with pytest.raises(GitLabAuthError) as e:
        client.get("/user")
    assert "https://gitlab.example.com" in str(e.value)
    assert "GITLAB_TOKEN" in str(e.value)


@pytest.mark.parametrize("status", [403, 404, 500])
def test_other_errors_carry_the_status(status):
    client, _ = make(FakeResponse(status, body={"message": "nope"}))
    with pytest.raises(GitLabError) as e:
        client.get("/user")
    assert e.value.status == status


def test_an_untrusted_certificate_says_what_to_set():
    client, _ = make(requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED"))
    with pytest.raises(GitLabError, match="REQUESTS_CA_BUNDLE"):
        client.get("/user")


@pytest.mark.parametrize("error", [
    requests.exceptions.ConnectionError(f"refused {TOKEN}"),
    requests.exceptions.InvalidHeader(f"Invalid header value b'{TOKEN}\\n'"),
    requests.exceptions.SSLError(f"bad {TOKEN}"),
])
def test_no_error_message_contains_the_token(error):
    client, _ = make(error)
    with pytest.raises(GitLabError) as e:
        client.get("/user")
    assert TOKEN not in str(e.value)


# --- paging -----------------------------------------------------------------

def test_paginate_follows_x_next_page():
    client, session = make(
        FakeResponse(body=[{"id": 1}], headers={"X-Next-Page": "2"}),
        FakeResponse(body=[{"id": 2}], headers={"X-Next-Page": ""}))
    assert client.paginate("/groups", {"order_by": "id"}) == [{"id": 1}, {"id": 2}]
    assert [c["params"]["page"] for c in session.calls] == [1, 2]
    assert session.calls[0]["params"]["per_page"] == 100
    assert session.calls[0]["params"]["order_by"] == "id"


def test_without_the_header_a_full_page_means_there_may_be_more():
    full = [{"id": n} for n in range(100)]
    client, session = make(FakeResponse(body=full), FakeResponse(body=[{"id": 100}]))
    assert len(client.paginate("/groups")) == 101
    assert len(session.calls) == 2


@pytest.mark.parametrize("second", [
    FakeResponse(500),
    FakeResponse(body={"message": "not a list"}),
    FakeResponse(bad_json=True),
    requests.exceptions.Timeout("slow"),
])
def test_a_failed_later_page_returns_nothing(second):
    client, _ = make(FakeResponse(body=[{"id": 1}], headers={"X-Next-Page": "2"}), second)
    with pytest.raises(GitLabError):
        client.paginate("/groups")


def test_a_next_page_that_does_not_advance_is_an_error():
    client, _ = make(FakeResponse(body=[{"id": 1}], headers={"X-Next-Page": "1"}))
    with pytest.raises(GitLabError):
        client.paginate("/groups")


# --- rate limits ------------------------------------------------------------

def test_a_429_waits_and_retries():
    waits = []
    client, _ = make(FakeResponse(429, headers={"Retry-After": "2"}), FakeResponse(),
                     sleep=waits.append)
    client.get("/user")
    assert waits == [2]


def test_a_429_that_persists_is_an_error_and_the_wait_is_capped():
    waits = []
    client, _ = make(*[FakeResponse(429, headers={"Retry-After": "999"})] * 4,
                     sleep=waits.append)
    with pytest.raises(GitLabError) as e:
        client.get("/user")
    assert e.value.status == 429
    assert waits == [60, 60, 60]


# --- identity ---------------------------------------------------------------

def test_identity():
    client, _ = make(
        FakeResponse(body={"username": "dev"}),
        FakeResponse(body={"version": "17.1.0"}),
        FakeResponse(body={"scopes": ["read_api"], "expires_at": "2027-01-01"}))
    assert client.identity() == {"username": "dev", "version": "17.1.0",
                                 "scopes": ["read_api"], "expires_at": "2027-01-01"}


def test_identity_without_the_optional_endpoints():
    client, _ = make(FakeResponse(body={"username": "dev"}), FakeResponse(404),
                     FakeResponse(404))
    assert client.identity() == {"username": "dev", "version": None,
                                 "scopes": None, "expires_at": None}
