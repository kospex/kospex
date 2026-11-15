"""What kgit sync records about the GitHub token: login, kind, source, expiry.

Never the token. GitHub returns the login from GET /user and the expiry in the
GitHub-Authentication-Token-Expiration header; the kind is the token's prefix.
"""
import json

import pytest
import requests
from requests.structures import CaseInsensitiveDict

import kospex_github
from kospex_github import KospexGithub


def _response(status, body=None, headers=None):
    response = requests.models.Response()
    response.status_code = status
    response._content = json.dumps(body).encode() if body is not None else b""
    response.headers = CaseInsensitiveDict(headers or {})
    response.url = "https://api.github.com/user"
    return response


@pytest.fixture
def github(monkeypatch):
    def respond(response):
        monkeypatch.setattr(kospex_github.requests, "request", lambda **kw: response)
        gh = KospexGithub()
        gh.set_access_token("github_pat_example")
        gh.token_source = "GH_TOKEN"
        return gh
    return respond


@pytest.mark.parametrize("token,kind", [
    ("github_pat_11AAAA", "fine-grained"),
    ("ghp_abc", "classic"),
    ("ghs_abc", "app"),
    ("gho_abc", "oauth"),
    ("", "none"),
    (None, "none"),
    ("something-else", "unknown"),
])
def test_token_kind_from_prefix(token, kind):
    assert KospexGithub.token_kind(token) == kind


def test_identity_with_expiry(github):
    gh = github(_response(200, {"login": "octocat"},
                          {"github-authentication-token-expiration": "2027-05-20 00:00:00 UTC"}))
    assert gh.auth_identity() == {"login": "octocat", "kind": "fine-grained",
                                  "source": "GH_TOKEN", "expires_at": "2027-05-20T00:00:00Z"}


def test_identity_without_expiry(github):
    gh = github(_response(200, {"login": "octocat"}))
    assert gh.auth_identity()["expires_at"] is None


def test_unparseable_expiry_is_none(github):
    gh = github(_response(200, {"login": "octocat"},
                          {"GitHub-Authentication-Token-Expiration": "soon"}))
    assert gh.auth_identity()["expires_at"] is None


def test_rejected_token_is_none(github, capsys):
    gh = github(_response(401, {"message": "Bad credentials"}))
    assert gh.auth_identity() is None
    assert "Authentication failed: Invalid token" in capsys.readouterr().out


def test_test_auth_keeps_its_behaviour(github, capsys):
    assert github(_response(200, {"login": "octocat"})).test_auth() is True
    assert "Authenticated as: octocat" in capsys.readouterr().out
    assert github(_response(401, {"message": "Bad credentials"})).test_auth() is False


def test_the_token_is_not_in_the_identity(github):
    gh = github(_response(200, {"login": "octocat"}))
    assert "github_pat_example" not in repr(gh.auth_identity())
