"""
Tests for KospexGit
"""
import pytest
from kospex_git import KospexGit

def test_parse_git_remote():
    """ Test the  function """

    parts = KospexGit.parse_git_remote("https://go.googlesource.com/oauth2")
    assert parts is not None
    assert parts["repo"] == "oauth2"
    assert parts["remote_type"] == "https"
    assert parts["remote"] == "go.googlesource.com"
    assert parts["org"] == ""


def test_repo_id():
    """ Test repo_id generation """

    kg = KospexGit()

    sample = "https://github.com/kospex/kospex"
    kg.set_remote_url(sample)
    assert "github.com~kospex~kospex" == kg.repo_id

    kg.set_remote_url("git@github.com:kospex/panopticas.git")
    assert "github.com~kospex~panopticas" == kg.repo_id

    kg.set_remote_url("https://gitlab.com/gitlab-org/cloud-connector/gitlab-cloud-connector.git")
    assert "gitlab.com~gitlab-org~~cloud-connector~gitlab-cloud-connector" == kg.repo_id

    # Bitbucket examples
    # HTTPS
    kg.set_remote_url("https://bitbucket.org/gildas_cherruel/bb.git")
    assert "bitbucket.org~gildas_cherruel~bb" == kg.repo_id
    # HTTPS with embedded username — Bitbucket's REST API returns clone
    # URLs personalised with the authenticated user's Bitbucket username
    # (e.g. https://USERNAME@bitbucket.org/...), so the parser must strip
    # the username prefix to keep repo_id stable.
    kg.set_remote_url("https://USERNAME@bitbucket.org/gildas_cherruel/bb.git")
    assert "bitbucket.org~gildas_cherruel~bb" == kg.repo_id
    # SSH
    # git@bitbucket.org:gildas_cherruel/bb.git
    kg.set_remote_url("git@bitbucket.org:gildas_cherruel/bb.git")
    assert "bitbucket.org~gildas_cherruel~bb" == kg.repo_id

    gitlab_repo_id = KospexGit.generate_repo_id("gitlab.com","gitlab/bob","the_repo")
    assert gitlab_repo_id == "gitlab.com~gitlab~~bob~the_repo"


SSH_ACCEPT_CASES = [
    ("git@github.com:company-org/dashboard.git", "github.com", "company-org", "dashboard"),
    ("git@github.com:company-org/dashboard", "github.com", "company-org", "dashboard"),
    ("git@github.com:company-org/dashboard.js.git", "github.com", "company-org", "dashboard.js"),
    ("git@github.com:company.org/repo.git", "github.com", "company.org", "repo"),
    ("git@gitlab.com:group/sub/repo.git", "gitlab.com", "group/sub", "repo"),
]


@pytest.mark.parametrize("url,remote,org,repo", SSH_ACCEPT_CASES)
def test_parse_ssh_git_url_accepts(url, remote, org, repo):
    """scp-style SSH URLs parse, including dotted org and repo names."""
    parts = KospexGit.parse_ssh_git_url(url)
    assert parts is not None, f"failed to parse {url}"
    assert parts["remote"] == remote
    assert parts["org"] == org
    assert parts["repo"] == repo
    assert parts["remote_type"] == "ssh"


SSH_REJECT_CASES = [
    "git@github.com:company-org/.git",
    "git@github.com:./../etc/passwd",
    "git@github.com:company-org/repo.git; rm -rf /",
    "git@github.com:-org/repo.git",
    "https://github.com/company-org/dashboard.git",
]


@pytest.mark.parametrize("url", SSH_REJECT_CASES)
def test_parse_ssh_git_url_rejects(url):
    """Degenerate, traversal-shaped and non-SSH URLs return None, not a partial parse."""
    assert KospexGit.parse_ssh_git_url(url) is None
