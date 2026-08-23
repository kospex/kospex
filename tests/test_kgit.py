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


DELEGATION_URLS = [
    "git@github.com:company-org/dashboard.git",
    "https://dev.azure.com/myorg/myproj/_git/myrepo",
    "https://github.com/company-org/dashboard.git",
    "https://gitlab.com/group/sub/repo.git",
    "https://go.googlesource.com/oauth2",
    "not-a-url",
]


@pytest.mark.parametrize("url", DELEGATION_URLS)
def test_extract_git_url_parts_delegates_to_parse_git_remote(url):
    """One parser, one answer: the deprecated helper must not disagree.

    Before this change extract_git_url_parts had no SSH branch (returning None)
    and routed ADO URLs through the generic gitlab branch (org 'myorg/myproj/_git'
    instead of 'myorg-myproj'), so clone and sync disagreed about the same URL.
    """
    kg = KospexGit()
    assert kg.extract_git_url_parts(url) == KospexGit.parse_git_remote(url)


def test_parse_git_remote_rejects_non_git_schemes():
    """A non-git transport is not a remote. #160."""
    assert KospexGit.parse_git_remote("ftp://not-a-git-host/whatever") is None


def test_parse_git_remote_rejects_org_less_paths_on_unknown_hosts():
    """A single path segment has no org, so it is not a usable remote. #160.

    Gerrit hosts under *.googlesource.com are the deliberate exception -- see
    test_parse_git_remote_allows_single_segment_googlesource.
    """
    assert KospexGit.parse_git_remote("https://example.com/single") is None


def test_parse_git_remote_rejects_junk():
    """#160 -- the old catch-all meant this returned a populated dict."""
    for junk in ["not a url at all", "", None]:
        assert KospexGit.parse_git_remote(junk) is None


def test_parse_git_remote_allows_single_segment_googlesource():
    """Gerrit projects may be a single segment with no org. #160."""
    parts = KospexGit.parse_git_remote("https://go.googlesource.com/oauth2")
    assert parts is not None
    assert parts["remote"] == "go.googlesource.com"
    assert parts["org"] == ""
    assert parts["repo"] == "oauth2"


def test_credentials_and_port_are_stripped_from_the_host():
    """The same repo over SSH and HTTPS must yield one repo_id. #160.

    Bitbucket Server's default SSH URL embeds git@ and port 7999; previously
    both landed in the server segment, so an SSH clone and an HTTPS clone of
    one repository produced two different repo_ids.
    """
    over_ssh = KospexGit.parse_git_remote(
        "ssh://git@bitbucket.example.com:7999/PROJ/repo.git")
    over_https = KospexGit.parse_git_remote(
        "https://bitbucket.example.com/scm/PROJ/repo.git")

    assert over_ssh["remote"] == "bitbucket.example.com"
    assert "/" not in over_ssh["repo"]
    assert KospexGit.generate_repo_id(**{k: over_ssh[k] for k in ("remote", "org", "repo")}) == \
           KospexGit.generate_repo_id(**{k: over_https[k] for k in ("remote", "org", "repo")})


def test_ado_clone_button_url_matches_the_plain_url():
    """ADO's Clone button embeds the org as a username. #160.

    Both forms address one repository and must produce one repo_id.
    """
    def rid(url):
        p = KospexGit.parse_git_remote(url)
        return KospexGit.generate_repo_id(p["remote"], p["org"], p["repo"])

    assert rid("https://myorg@dev.azure.com/myorg/MyProject/_git/MyRepo") == \
           rid("https://dev.azure.com/myorg/MyProject/_git/MyRepo")
