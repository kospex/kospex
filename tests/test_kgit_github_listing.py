"""`kgit github OWNER`: a user's listing is the repos they own unless -include-member,
and -sync is `kgit sync --org` / `--user`. No network: the GitHub calls are faked."""
import pytest
from click.testing import CliRunner

import kgit as kgit_module
from kospex_github import KospexGithub


def _repo(full_name, fork=False):
    return {"id": hash(full_name) % 10**6, "full_name": full_name, "name": full_name.split("/")[1],
            "fork": fork, "clone_url": f"https://github.com/{full_name}.git"}


LISTING = [
    _repo("octocat/hello"),
    _repo("octocat/my-fork", fork=True),
    _repo("github/docs"),                      # a repo octocat is a member of
    _repo("someorg/their-fork", fork=True),   # a fork octocat doesn't own
]


# --- KospexGithub.get_repos(owned_only=...) ---------------------------------

def _gh(monkeypatch, listing=LISTING, owner_type="User"):
    gh = KospexGithub()
    looked_up = []
    monkeypatch.setattr(gh, "_make_request",
                        lambda url, **kw: ({"login": "octocat", "type": owner_type}, None))
    monkeypatch.setattr(gh, "_get_user_repos", lambda *a, **kw: iter(listing))
    monkeypatch.setattr(gh, "_get_org_repos", lambda *a, **kw: iter(listing))

    def details(owner, name):
        looked_up.append(name)
        return {"parent": {"full_name": "up/x"}, "source": {"full_name": "up/x"}}

    monkeypatch.setattr(gh, "_get_repo_details", details)
    return gh, looked_up


def test_owned_only_keeps_the_owners_repos_forks_included(monkeypatch):
    gh, _ = _gh(monkeypatch)
    names = [r["name"] for r in gh.get_repos("octocat", no_auth=True, owned_only=True)]
    assert names == ["octocat/hello", "octocat/my-fork"]


def test_owned_only_matches_the_owner_ignoring_case(monkeypatch):
    gh, _ = _gh(monkeypatch)
    assert len(gh.get_repos("OctoCat", no_auth=True, owned_only=True)) == 2


def test_owned_only_skips_the_fork_lookup_for_repos_it_drops(monkeypatch):
    """At 60 requests an hour, a lookup per fork the user doesn't own matters."""
    gh, looked_up = _gh(monkeypatch)
    gh.get_repos("octocat", no_auth=True, owned_only=True)
    assert looked_up == ["my-fork"]


def test_without_owned_only_everything_is_listed(monkeypatch):
    gh, looked_up = _gh(monkeypatch)
    assert len(gh.get_repos("octocat", no_auth=True)) == 4
    assert sorted(looked_up) == ["my-fork", "their-fork"]


# --- the token's own account: /user/repos -------------------------------------

def _own_account_request(monkeypatch, owned_only):
    gh = KospexGithub()
    gh.authenticated = True
    seen = {}
    monkeypatch.setattr(gh, "_get_authenticated_user", lambda: {"login": "me"})

    def paginate(url, params=None):
        seen["url"], seen["params"] = url, dict(params or {})
        return iter([])

    monkeypatch.setattr(gh, "_paginate", paginate)
    list(gh._get_user_repos("me", repo_type="all", owned_only=owned_only))
    return seen


@pytest.mark.parametrize("owned_only, affiliation", [
    (True, "owner"), (False, "owner,collaborator,organization_member")])
def test_own_account_listing_never_sends_type_with_affiliation(monkeypatch, owned_only,
                                                                affiliation):
    """GitHub answers 422 "If you specify visibility or affiliation, you cannot
    specify type" (checked live), which failed every listing of your own account."""
    seen = _own_account_request(monkeypatch, owned_only)
    assert seen["url"].endswith("/user/repos")
    assert "type" not in seen["params"]
    assert seen["params"]["affiliation"] == affiliation


# --- kgit github OWNER --------------------------------------------------------

def _cli(monkeypatch, account_type, *args):
    seen = {}

    def get_repos(self, owner, no_auth=False, owned_only=False):
        seen["owned_only"] = owned_only
        return [{"name": "octocat/hello", "fork": False}]

    monkeypatch.setattr(KospexGithub, "get_account_type", lambda self, owner: account_type)
    monkeypatch.setattr(KospexGithub, "get_repos", get_repos)
    monkeypatch.setattr(kgit_module, "_sync_owner",
                        lambda **kw: seen.setdefault("sync", kw))
    result = CliRunner().invoke(kgit_module.cli, ["github", "-no-auth", *args])
    return result, seen


def test_a_user_listing_is_owned_only_by_default(monkeypatch):
    result, seen = _cli(monkeypatch, "User", "octocat")
    assert result.exit_code == 0, result.output
    assert seen["owned_only"] is True
    assert "-include-member also lists" in result.output


def test_include_member_lists_everything(monkeypatch):
    result, seen = _cli(monkeypatch, "User", "octocat", "-include-member")
    assert result.exit_code == 0, result.output
    assert seen["owned_only"] is False
    assert "-include-member also lists" not in result.output


def test_an_org_listing_is_unchanged(monkeypatch):
    result, seen = _cli(monkeypatch, "Organization", "acme")
    assert result.exit_code == 0, result.output
    assert seen["owned_only"] is False


def test_include_member_on_an_org_says_it_does_not_apply(monkeypatch):
    result, _ = _cli(monkeypatch, "Organization", "acme", "-include-member")
    assert "-include-member applies to a user" in result.output


# --- -sync is kgit sync --org / --user ---------------------------------------

def test_sync_on_an_org_runs_kgit_sync_org(monkeypatch):
    result, seen = _cli(monkeypatch, "Organization", "acme", "-sync")
    assert result.exit_code == 0, result.output
    assert seen["sync"]["owner_kind"] == "org"
    assert seen["sync"]["target"] == "acme"
    assert seen["sync"]["no_auth"] is True
    assert "owned_only" not in seen, "-sync must not run the listing itself"
    assert "Running: kgit sync --org acme -no-auth" in result.output


def test_sync_on_a_user_runs_kgit_sync_user(monkeypatch):
    """The bug this fixes: -sync used to sync the 195 repos octocat doesn't own."""
    result, seen = _cli(monkeypatch, "User", "octocat", "-sync")
    assert result.exit_code == 0, result.output
    assert seen["sync"]["owner_kind"] == "user"


def test_sync_refuses_listing_only_options(monkeypatch):
    for extra in (["-out-repo-list", "x.txt"], ["-ssh-clone-url"], ["-include-member"]):
        result, seen = _cli(monkeypatch, "Organization", "acme", "-sync", *extra)
        assert result.exit_code == 2, result.output
        assert "only apply to the listing" in result.output
        assert "sync" not in seen


def test_sync_on_an_unknown_owner_is_an_error(monkeypatch):
    result, seen = _cli(monkeypatch, None, "nobody-here", "-sync")
    assert result.exit_code == 1
    assert "Couldn't find a GitHub organisation or user" in result.output
    assert "sync" not in seen
