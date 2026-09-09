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


def _rid(url):
    parts = KospexGit.parse_git_remote(url)
    assert parts is not None, f"failed to parse {url}"
    return KospexGit.generate_repo_id(parts["remote"], parts["org"], parts["repo"])


def test_ado_org_and_project_use_the_nested_org_encoding():
    """ADO org/project is a hierarchy, so encode it like a GitLab subgroup.

    Supersedes the hyphen join specified in #50. '/' cannot appear in an ADO
    org, project or repo name, so 'org/project' is unambiguous; '-' can, which
    is why the hyphen join collides (see the test below).
    """
    parts = KospexGit.parse_git_remote(
        "https://dev.azure.com/myorg/MyProject/_git/MyRepo")
    assert parts["org"] == "myorg/MyProject"
    assert _rid("https://dev.azure.com/myorg/MyProject/_git/MyRepo") == \
        "dev.azure.com~myorg~~MyProject~MyRepo"


def test_hyphenated_ado_org_and_project_no_longer_collide():
    """Two different repos must not share a repo_id. #50's hyphen join did.

    'my-org/Project' and 'my/org-Project' both produced
    'dev.azure.com~my-org-Project~R'.
    """
    a = _rid("https://dev.azure.com/my-org/Project/_git/R")
    b = _rid("https://dev.azure.com/my/org-Project/_git/R")
    assert a != b


def test_ado_repo_name_ending_in_git_characters_is_not_truncated():
    """#135 -- rstrip('.git') strips a character set, not a suffix.

    A repo named 'digit' lost everything but the leading 'd'.
    """
    parts = KospexGit.parse_git_remote(
        "https://dev.azure.com/myorg/MyProject/_git/digit")
    assert parts["repo"] == "digit"


def test_ado_dot_git_suffix_is_still_removed():
    parts = KospexGit.parse_git_remote(
        "https://dev.azure.com/myorg/MyProject/_git/MyRepo.git")
    assert parts["repo"] == "MyRepo"


def test_legacy_visualstudio_org_comes_from_the_hostname():
    """The org lives in the hostname; using the project as the org lost it."""
    parts = KospexGit.parse_git_remote(
        "https://myorg.visualstudio.com/MyProject/_git/MyRepo")
    assert parts["org"] == "myorg/MyProject"


def test_ado_ssh_agrees_with_https():
    """ADO's SSH clone URL addresses the same repository as the HTTPS one.

    Format is 'git@ssh.dev.azure.com:v3/{org}/{project}/{repo}' -- 'v3' is a
    path prefix, not a port, and ssh.dev.azure.com is the SSH endpoint of the
    same service rather than a different origin. Both must yield one id.
    """
    https = _rid("https://dev.azure.com/myorg/MyProject/_git/MyRepo")
    scp = _rid("git@ssh.dev.azure.com:v3/myorg/MyProject/MyRepo")
    ssh_scheme = _rid("ssh://git@ssh.dev.azure.com/v3/myorg/MyProject/MyRepo")
    assert scp == https
    assert ssh_scheme == https


def test_ado_default_collection_is_not_part_of_the_identity():
    """Legacy collection URLs address the same project.

    'DefaultCollection' sits between the host and the project on older ADO and
    on-prem TFS URLs; it previously made the URL unparseable as ADO entirely.
    """
    with_collection = _rid(
        "https://myorg.visualstudio.com/DefaultCollection/MyProject/_git/MyRepo")
    without = _rid("https://myorg.visualstudio.com/MyProject/_git/MyRepo")
    assert with_collection == without


def test_trailing_slash_does_not_make_a_url_unparseable():
    """A trailing slash is a legitimate clone URL and must not change the parse.

    The old dispatcher chose between two overlapping rules by counting slashes
    in the URL, and both were anchored with no allowance for a trailing one, so
    a valid remote returned None.
    """
    for base in ("https://github.com/acme/svc",
                 "https://gitlab.com/group/subgroup/repo.git"):
        assert KospexGit.parse_git_remote(base) == \
               KospexGit.parse_git_remote(base + "/")


TRAILING_SLASH_SHAPES = [
    "https://github.com/acme/svc",
    "https://github.com/acme/svc.git",
    "git@github.com:acme/svc.git",
    "ssh://git@github.com/acme/svc.git",
    "https://gitlab.com/group/subgroup/repo.git",
    "git@gitlab.com:group/subgroup/repo.git",
    "https://dev.azure.com/myorg/MyProject/_git/MyRepo",
    "https://myorg.visualstudio.com/MyProject/_git/MyRepo",
    "git@ssh.dev.azure.com:v3/myorg/MyProject/MyRepo",
    "https://bitbucket.org/team/repo.git",
    "https://bitbucket.example.com/scm/PROJ/repo.git",
    "ssh://git@bitbucket.example.com:7999/PROJ/repo.git",
    "https://go.googlesource.com/oauth2",
    "https://android.googlesource.com/platform/frameworks/base",
]


@pytest.mark.parametrize("url", TRAILING_SLASH_SHAPES)
@pytest.mark.parametrize("suffix", ["/", "//", "///"])
def test_trailing_slashes_never_change_the_parse(url, suffix):
    """A trailing slash carries no meaning and must not change the result.

    Normalisation strips them once, so every provider rule -- including the
    scp-style one, which never sees urlparse -- is covered by the same rule.
    Call sites used to strip slashes themselves; the sync path did not, so a
    repo cloned with a trailing slash parsed on clone and failed on sync.
    """
    assert KospexGit.parse_git_remote(url + suffix) == KospexGit.parse_git_remote(url)


@pytest.mark.parametrize("url", TRAILING_SLASH_SHAPES)
def test_every_shape_still_parses(url):
    """Guards the parametrised test above from passing vacuously on None==None."""
    assert KospexGit.parse_git_remote(url) is not None


def test_a_bare_host_is_not_a_remote():
    """Stripping slashes must not turn 'https://host/' into something parseable."""
    for url in ["https://github.com/", "https://github.com", "/", "//"]:
        assert KospexGit.parse_git_remote(url) is None


MIXED_CASE_HOSTS = [
    ("https://GitHub.com/acme/svc", "https://github.com/acme/svc"),
    ("git@GitHub.com:acme/svc.git", "git@github.com:acme/svc.git"),
    ("ssh://git@GitLab.com/group/subgroup/repo.git",
     "ssh://git@gitlab.com/group/subgroup/repo.git"),
    ("https://Dev.Azure.com/myorg/MyProject/_git/MyRepo",
     "https://dev.azure.com/myorg/MyProject/_git/MyRepo"),
    ("https://MyOrg.VisualStudio.com/MyProject/_git/MyRepo",
     "https://myorg.visualstudio.com/MyProject/_git/MyRepo"),
    ("https://BitBucket.example.com/scm/PROJ/repo.git",
     "https://bitbucket.example.com/scm/PROJ/repo.git"),
    ("https://Go.GoogleSource.com/oauth2", "https://go.googlesource.com/oauth2"),
]


@pytest.mark.parametrize("mixed,lower", MIXED_CASE_HOSTS)
def test_host_case_does_not_change_the_parse(mixed, lower):
    """Hostnames are case-insensitive (DNS), so they must not affect the parse.

    This is separate from the org/repo case question (#147): there is no
    judgement call for a hostname.
    """
    assert KospexGit.parse_git_remote(mixed) == KospexGit.parse_git_remote(lower)


@pytest.mark.parametrize("mixed,lower", MIXED_CASE_HOSTS)
def test_mixed_case_host_is_lowercased(mixed, lower):
    parts = KospexGit.parse_git_remote(mixed)
    assert parts is not None
    assert parts["remote"] == parts["remote"].lower()


def test_mixed_case_host_still_reaches_the_ado_rule():
    """Provider rules match the host by string equality.

    'Dev.Azure.com' failed `netloc == "dev.azure.com"`, fell through to the
    generic rule, and produced org 'myorg/MyProject/_git' -- the '_git' routing
    segment ending up inside the organisation.
    """
    parts = KospexGit.parse_git_remote(
        "https://Dev.Azure.com/myorg/MyProject/_git/MyRepo")
    assert parts["org"] == "myorg/MyProject"
    assert parts["repo"] == "MyRepo"
    assert "_git" not in parts["org"]
