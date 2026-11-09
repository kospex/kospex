"""`kgit pull --org` resolves bare names and URLs against the orgs in the DB.

One test per row of the ambiguity table in changes/202610-kgit-sync-org.md
(delivery item 4), plus the forms that worked before and still must.
"""
import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kgit import _resolve_pull_repos
from kospex.org_sync import parse_target, resolve_pull_org
from kospex_query import KospexQuery

REPOS = [
    ("github.com", "kospex", "kospex"),
    ("github.com", "kospex", "panopticas"),
    ("github.com", "acme", "app"),
    ("gitlab.com", "acme", "web"),            # same owner on another server
    ("bitbucket.org", "team", "svc"),
    ("gitlab.com", "group/sub", "tool"),      # nested group
    ("go.googlesource.com", "", "tools"),     # host with no owner
    ("github.com", "acme.io", "site"),        # an owner with a dot
]


def resolve(value):
    return resolve_pull_org(value, REPOS)


# --- forms that resolve -----------------------------------------------------

@pytest.mark.parametrize("value", [
    "github.com~kospex",            # the org key, as before
    "kospex",                       # bare name
    "github.com/kospex",
    "https://github.com/kospex",
    "https://github.com/kospex/",
    "GitHub.com~Kospex",            # case: silently found nothing before
    "KOSPEX",
])
def test_every_form_of_one_org_resolves_to_it(value):
    assert resolve(value) == ("github.com", "kospex")


def test_not_github_is_accepted():
    assert resolve("bitbucket.org/team") == ("bitbucket.org", "team")
    assert resolve("team") == ("bitbucket.org", "team")


def test_nested_group_by_key_and_by_url():
    assert resolve("gitlab.com~group~~sub") == ("gitlab.com", "group/sub")
    assert resolve("gitlab.com/group/sub") == ("gitlab.com", "group/sub")
    assert resolve("https://gitlab.com/group/sub") == ("gitlab.com", "group/sub")


def test_bare_name_with_a_dot_is_an_owner_when_the_db_has_one():
    assert resolve("acme.io") == ("github.com", "acme.io")


# --- the ambiguity table: errors that name what to use instead ----------------

def test_same_owner_on_two_servers_lists_both_and_picks_neither():
    with pytest.raises(ValueError) as e:
        resolve("acme")
    assert "github.com~acme" in str(e.value) and "gitlab.com~acme" in str(e.value)


def test_a_host_narrows_an_ambiguous_owner():
    assert resolve("gitlab.com/acme") == ("gitlab.com", "acme")
    assert resolve("github.com~acme") == ("github.com", "acme")


def test_a_repository_points_to_kgit_pull_repo_id():
    for value in ("gitlab.com/group/sub/tool", "https://github.com/kospex/panopticas.git",
                  "github.com~kospex~panopticas"):
        with pytest.raises(ValueError) as e:
            resolve(value)
        assert "is a repository" in str(e.value) and "kgit pull " in str(e.value)
    with pytest.raises(ValueError, match="kgit pull gitlab.com~group~~sub~tool"):
        resolve("gitlab.com/group/sub/tool")


def test_a_host_with_no_owner_suggests_server():
    for value in ("go.googlesource.com", "https://go.googlesource.com",
                  "go.googlesource.com~", "github.com"):
        with pytest.raises(ValueError, match="--server"):
            resolve(value)


def test_a_bare_name_with_a_dot_that_is_nothing_in_kospex():
    with pytest.raises(ValueError, match="No org 'nope.io' in kospex"):
        resolve("nope.io")


def test_not_in_kospex_suggests_sync_first():
    with pytest.raises(ValueError) as e:
        resolve("newco")
    assert "No org 'newco' in kospex" in str(e.value)
    assert "kgit sync --org newco" in str(e.value)


def test_an_org_key_that_does_not_parse_is_an_error():
    with pytest.raises(ValueError):
        resolve("~")


# --- kgit sync --org still reads the same forms the same way -----------------

@pytest.mark.parametrize("target", [
    "acme", "github.com/acme", "https://github.com/acme", "github.com~acme"])
def test_sync_parse_target_unchanged(target):
    assert parse_target(target) == ("github.com", "acme")


@pytest.mark.parametrize("target", [
    "acme/app", "github.com~acme~app", "gitlab.com/acme", "https://gitlab.com/acme"])
def test_sync_parse_target_still_refuses(target):
    with pytest.raises(ValueError):
        parse_target(target)


# --- through _resolve_pull_repos, against a real DB ---------------------------

def _query(rows):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db["repos"].insert_all([
        {"_repo_id": rid, "_git_server": s, "_git_owner": o, "_git_repo": r,
         "file_path": f"/c/{rid}"} for rid, s, o, r in rows], pk="_repo_id")
    return KospexQuery(kospex_db=db)


def test_pull_scope_by_bare_name(capsys):
    kq = _query([("github.com~kospex~a", "github.com", "kospex", "a"),
                 ("github.com~kospex~b", "github.com", "kospex", "b"),
                 ("github.com~other~c", "github.com", "other", "c")])
    repos = _resolve_pull_repos(kq, org="Kospex")
    assert sorted(r["_repo_id"] for r in repos) == ["github.com~kospex~a", "github.com~kospex~b"]
    assert "Using org github.com~kospex" in capsys.readouterr().out


def test_pull_scope_by_exact_key_prints_nothing_extra(capsys):
    kq = _query([("github.com~kospex~a", "github.com", "kospex", "a")])
    assert len(_resolve_pull_repos(kq, org="github.com~kospex")) == 1
    assert "Using org" not in capsys.readouterr().out


def test_pull_scope_finds_a_mixed_case_owner_from_an_old_db():
    """Before ids were lowercased an owner could be stored as `Kospex`."""
    kq = _query([("github.com~Kospex~a", "github.com", "Kospex", "a")])
    assert [r["_repo_id"] for r in _resolve_pull_repos(kq, org="kospex")] == \
        ["github.com~Kospex~a"]


def test_pull_scope_nested_group():
    kq = _query([("gitlab.com~group~~sub~tool", "gitlab.com", "group/sub", "tool"),
                 ("gitlab.com~group~app", "gitlab.com", "group", "app")])
    repos = _resolve_pull_repos(kq, org="gitlab.com/group/sub")
    assert [r["_repo_id"] for r in repos] == ["gitlab.com~group~~sub~tool"]
