"""/generate-repo-id/ returns a real repo_id (#94 Step 6).

It returned the literal string 'TODO_IMPLEMENT_REPO_ID_GENERATION' for every
URL. It now uses the same parser and builder as sync, so the id it reports is
the one the database will hold.
"""
import asyncio
import json

import pytest

import kweb2
from kospex_git import KospexGit


def _get(url):
    """Call the endpoint handler directly.

    Not TestClient: starlette's requires httpx2, which is not installed, so the
    existing web tests importorskip and never run. A handler this small is
    better exercised directly than not at all.
    """
    response = asyncio.run(kweb2.generate_repo_id(url=url))
    return response.status_code, json.loads(response.body)


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/acme/svc", "github.com~acme~svc"),
    ("https://github.com/acme/svc.git", "github.com~acme~svc"),
    ("git@github.com:acme/svc.git", "github.com~acme~svc"),
    ("https://gitlab.com/group/subgroup/repo.git", "gitlab.com~group~~subgroup~repo"),
    ("https://dev.azure.com/myorg/MyProject/_git/MyRepo",
     "dev.azure.com~myorg~~MyProject~MyRepo"),
    ("https://bitbucket.example.com/scm/PROJ/repo.git",
     "bitbucket.example.com~PROJ~repo"),
])
def test_endpoint_returns_the_canonical_repo_id(url, expected):
    status, body = _get(url)
    assert status == 200
    assert body["repo_id"] == expected


def test_endpoint_agrees_with_the_builder_used_by_sync():
    """The point of the endpoint: tell the user the id sync would record."""
    url = "https://gitlab.com/group/subgroup/repo.git"
    parts = KospexGit.parse_git_remote(url)
    expected = KospexGit.generate_repo_id(
        parts["remote"], parts["org"], parts["repo"])

    assert _get(url)[1]["repo_id"] == expected


def test_endpoint_reports_the_components():
    _, body = _get("https://dev.azure.com/myorg/MyProject/_git/MyRepo")
    assert body["git_server"] == "dev.azure.com"
    assert body["org"] == "myorg/MyProject"
    assert body["repo"] == "MyRepo"


@pytest.mark.parametrize("url", [
    "ftp://not-a-git-host/whatever",
    "https://example.com/single",
    "not a url at all",
])
def test_endpoint_rejects_a_non_remote(url):
    """parse_git_remote can return None now, so the endpoint must say so
    rather than inventing an id."""
    status, body = _get(url)
    assert status == 400
    assert "error" in body
