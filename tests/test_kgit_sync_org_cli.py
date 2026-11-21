"""`kgit sync --org` / `--user` end to end, with a fake GitHub and a throwaway DB.

Design: changes/202610-kgit-sync-org.md (#335)

GitHub is replaced by FakeGitHub, so these run offline. Cloning and syncing are
replaced too, except in the last test, which clones real (local) repositories
through git's `insteadOf` and syncs them into the throwaway DB.
"""
import os
import subprocess

import pytest
import sqlite_utils
from click.testing import CliRunner

import kgit as kgit_module
import kospex.org_sync_manifest as M

ORG = "github.com~acme"


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeGitHub:
    """Stands in for KospexGithub. Configure the class attributes per test."""
    TOKEN_ENV_VARS = ("GITHUB_AUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
    account_type = "Organization"
    token = True
    auth_ok = True
    listing = []
    listing_error = None
    list_calls = 0

    def __init__(self):
        self.base_url = "https://api.github.com"
        self.token_source = None

    def get_env_credentials(self):
        if self.token:
            self.token_source = "GH_TOKEN"
        return self.token

    def test_auth(self):
        return self.auth_ok

    def auth_identity(self):
        if not self.auth_ok:
            return None
        return {"login": "octocat", "kind": "fine-grained", "source": self.token_source,
                "expires_at": "2027-05-20T00:00:00Z"}

    def _make_request(self, url, params=None, method="GET"):
        if self.account_type is None:
            return None, FakeResponse(404)
        return {"type": self.account_type}, FakeResponse(200)

    def _failure_reason(self, response):
        return "HTTP 500"

    def get_repos(self, owner, no_auth=False):
        FakeGitHub.list_calls += 1
        if self.listing_error:
            raise self.listing_error
        return list(self.listing)


def _repo(name, pushed_at="2026-11-01T00:00:00Z", **kw):
    return {
        # Distinct per name, as GitHub's are: a shared id reads as a rename.
        "id": int.from_bytes(name.encode(), "big"), "name": f"acme/{name}",
        "clone_url": f"https://github.com/acme/{name}.git",
        "ssh_url": f"git@github.com:acme/{name}.git",
        "visibility": "public", "archived": False, "fork": False,
        "pushed_at": pushed_at, **kw,
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A throwaway KOSPEX_HOME and DB, FakeGitHub, and recorded clones and syncs."""
    from kospex.db.migrator import Migrator
    from kospex.habitat_config import HabitatConfig
    import kospex_schema as KospexSchema
    from kospex_core import Kospex

    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "code").mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path / "code"))
    monkeypatch.delenv("KOSPEX_CLONE_FORKS_DEFAULT", raising=False)
    HabitatConfig.reset_instance()

    db = sqlite_utils.Database(str(home / "kospex.db"))
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    Migrator(db).apply_pending()
    kospex = Kospex(kospex_db=db)
    monkeypatch.setattr(kgit_module, "kospex", kospex)

    for attr, value in dict(account_type="Organization", token=True, auth_ok=True,
                            listing=[], listing_error=None, list_calls=0).items():
        monkeypatch.setattr(FakeGitHub, attr, value)
    monkeypatch.setattr(kgit_module, "KospexGithub", FakeGitHub)

    calls = {"cloned": [], "synced": [], "env": []}

    def clone_repo(url):
        calls["cloned"].append(url)
        calls["env"].append(os.environ.get("GIT_TERMINAL_PROMPT"))
        return f"/code/{url}"

    def sync_repo(path, **kw):
        repo_id = "github.com~acme~" + path.rsplit("/", 1)[-1].removesuffix(".git").lower()
        calls["synced"].append(repo_id)
        name = repo_id.rsplit("~", 1)[-1]
        db["repos"].upsert({"_repo_id": repo_id, "_git_server": "github.com",
                            "_git_owner": "acme", "_git_repo": name}, pk="_repo_id")

    monkeypatch.setattr(kgit_module.kgit, "clone_repo", clone_repo)
    monkeypatch.setattr(kospex, "sync_repo", sync_repo)
    monkeypatch.setattr(kgit_module, "clone_path_conflict", lambda *a, **k: False)
    yield {"db": db, "home": home, "calls": calls}
    HabitatConfig.reset_instance()


def run(*args):
    result = CliRunner().invoke(kgit_module.cli, ["sync", *args])
    # Rich wraps at 80 columns; compare text with the wrapping undone.
    result.text = " ".join(result.output.split())
    return result


def _items(db):
    return {r["_repo_id"].split("~")[-1]: (r["status"], r["detail"]) for r in db.query(
        f"SELECT _repo_id, status, detail FROM {M.TBL_ORG_SYNC_ITEMS}")}


# --- refusals ----------------------------------------------------------------

def test_org_given_a_user_says_to_use_user(env):
    FakeGitHub.account_type = "User"
    result = run("--org", "octocat")
    assert result.exit_code == 1
    assert "'octocat' is a GitHub user, not an organisation. Use: kgit sync --user octocat" in result.text
    assert FakeGitHub.list_calls == 0


def test_user_given_an_org_says_to_use_org(env):
    result = run("--user", "acme")
    assert result.exit_code == 1
    assert "'acme' is a GitHub organisation, not a user. Use: kgit sync --org acme" in result.text


def test_an_owner_that_does_not_exist(env):
    FakeGitHub.account_type = None
    result = run("--org", "nope")
    assert result.exit_code == 1
    assert "No GitHub organisation or user called 'nope'" in result.output


def test_no_token_names_the_variables_and_suggests_no_auth(env):
    FakeGitHub.token = False
    result = run("--org", "acme")
    assert result.exit_code == 1
    assert "GITHUB_AUTH_TOKEN, GH_TOKEN, GITHUB_TOKEN" in result.output
    assert "-no-auth" in result.output


def test_a_rejected_token_stops_before_listing(env):
    FakeGitHub.auth_ok = False
    result = run("--org", "acme")
    assert result.exit_code == 1
    assert "GitHub rejected the token in GH_TOKEN" in result.output
    assert FakeGitHub.list_calls == 0


@pytest.mark.parametrize("args, message", [
    (["--org", "--user", "acme"], "not both"),
    (["--org", "acme", "-inventory", "-next", "5"], "one of -inventory, -next, -status"),
    (["--org", "acme", "-status", "-history"], "one of -inventory, -next, -status"),
    (["--org", "acme", "-history", "-refresh"], "-history reads the DB only"),
    (["--org", "acme", "-history", "-active"], "-history reads the DB only"),
    (["https://github.com/acme/app", "-history"], "only apply with --org or --user"),
    (["--org", "acme", "-next", "0"], "positive number"),
    (["--org", "acme", "-include-forks", "-skip-forks"], "not both"),
    (["--org", "acme/app"], "looks like a repository"),
    (["https://github.com/acme/app", "-next", "3"], "only apply with --org or --user"),
])
def test_option_mistakes_are_usage_errors(env, args, message):
    result = run(*args)
    assert result.exit_code == 2, result.output
    assert message in result.output


def test_a_bad_clone_forks_default_is_a_usage_error(env, monkeypatch):
    monkeypatch.setenv("KOSPEX_CLONE_FORKS_DEFAULT", "ture")
    result = run("--org", "acme")
    assert result.exit_code == 2
    assert "isn't true or false" in result.output


def test_an_incomplete_listing_saves_nothing(env):
    from kospex_github import GitHubListingIncomplete
    FakeGitHub.listing_error = GitHubListingIncomplete("u", 2, 100, "HTTP 502")
    result = run("--org", "acme")
    assert result.exit_code == 1
    assert "page 2" in result.output and "Nothing was saved or synced" in result.output
    assert env["db"][M.TBL_ORG_SYNC_RUNS].count == 0
    assert env["calls"]["cloned"] == []


def test_a_held_lock_means_nothing_to_do_not_an_error(env):
    import logging
    import kospex.org_sync as OrgSync
    from kospex.run_lock import RunLock
    FakeGitHub.listing = [_repo("a")]
    with RunLock(OrgSync.lock_path(env["home"], ORG), "x", 60, logging.getLogger("t")):
        result = run("--org", "acme")
    assert result.exit_code == 0
    assert "Nothing to do" in result.output
    assert env["calls"]["cloned"] == []


def test_a_long_running_sync_is_not_taken_over_after_six_hours(env):
    """osi's six-hour bound let a second sync take over a slow but live one."""
    import json
    import socket
    import subprocess
    import sys
    import kospex.org_sync as OrgSync
    FakeGitHub.listing = [_repo("a")]
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        OrgSync.lock_path(env["home"], ORG).write_text(json.dumps({
            "pid": live.pid, "host": socket.gethostname(),
            "started_at": _hours_ago(10)}))
        result = run("--org", "acme")
    finally:
        live.kill()
        live.wait()
    assert result.exit_code == 0
    assert "Nothing to do" in result.output
    assert env["calls"]["cloned"] == []


def _hours_ago(hours):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- -inventory --------------------------------------------------------------

def test_inventory_saves_the_listing_and_clones_nothing(env):
    FakeGitHub.listing = [_repo("a"), _repo("b", archived=True), _repo("c", fork=True)]
    result = run("--org", "acme", "-inventory")
    assert result.exit_code == 0, result.output
    assert "Using token from GH_TOKEN" in result.output
    assert "Listed 3 repos for github.com~acme, saved as run " in result.output
    assert "Nothing cloned. Run `kgit sync --org acme -next 10`" in result.output
    assert env["calls"]["cloned"] == []
    assert _items(env["db"]) == {"a": (M.PENDING, None), "b": (M.PENDING, None),
                                 "c": (M.SKIPPED, "fork")}


def test_inventory_counts_each_visibility(env):
    # GitHub Enterprise internal repos: readable across the enterprise, not
    # public, and not private either.
    FakeGitHub.listing = [_repo("a", visibility="internal", private=True),
                          _repo("b", visibility="internal", private=True),
                          _repo("c", visibility="private", private=True), _repo("d")]
    result = run("--org", "acme", "-inventory")
    assert result.exit_code == 0, result.output
    assert "public 1 private 1 internal 2" in result.text
    assert "not recorded" not in result.text
    env["db"].execute(f"UPDATE {M.TBL_ORG_SYNC_ITEMS} SET visibility = NULL "
                      "WHERE _repo_id LIKE '%~d'")
    # A repo listed before 0014: -status, offline, says so rather than guessing.
    result = run("--org", "acme", "-status")
    assert result.exit_code == 0, result.output
    assert "public 0 private 1 internal 2 not recorded 1" in result.text


def test_inventory_keeps_an_unfinished_run_unless_restart(env):
    FakeGitHub.listing = [_repo("a"), _repo("b")]
    run("--org", "acme", "-inventory")
    first = M.unfinished_run(env["db"], ORG)["run_id"]

    FakeGitHub.listing = [_repo("b"), _repo("c"), _repo("d")]
    result = run("--org", "acme", "-inventory")
    assert result.exit_code == 0, result.output
    assert "Listed 3 repos for github.com~acme (not saved)" in result.output
    assert f"Run {first} is unfinished" in result.output
    assert "2 repos are new since that run, 1 are gone. Kept the unfinished run." in result.output
    assert "To merge this listing into it: kgit sync --org acme -inventory -refresh" in result.text
    assert "To replace it: kgit sync --org acme -inventory -restart" in result.text
    assert env["db"][M.TBL_ORG_SYNC_RUNS].count == 1

    result = run("--org", "acme", "-inventory", "-restart")
    assert result.exit_code == 0, result.output
    assert M.unfinished_run(env["db"], ORG)["run_id"] != first
    assert env["db"][M.TBL_ORG_SYNC_RUNS].count == 2


def test_relist_is_gone_restart_replaces_it(env):
    result = run("--org", "acme", "-relist")
    assert result.exit_code == 2
    assert "No such option" in result.output  # Click names it as '-r'


def test_inventory_refresh_merges_into_the_unfinished_run(env):
    FakeGitHub.listing = [_repo("a"), _repo("b")]
    run("--org", "acme", "-next", "1")          # a synced, b pending
    first = M.unfinished_run(env["db"], ORG)["run_id"]
    assert _items(env["db"]) == {"a": (M.SYNCED, None), "b": (M.PENDING, None)}

    FakeGitHub.listing = [_repo("b", "2026-11-10T00:00:00Z"), _repo("c")]
    result = run("--org", "acme", "-inventory", "-refresh")
    assert result.exit_code == 0, result.output
    assert f"Refreshed run {first} for github.com~acme from a new listing of 2: " \
           "1 new, 1 gone." in result.text
    assert env["db"][M.TBL_ORG_SYNC_RUNS].count == 1
    assert M.unfinished_run(env["db"], ORG)["run_id"] == first
    assert _items(env["db"]) == {"a": (M.GONE, "was synced"), "b": (M.PENDING, None),
                                 "c": (M.PENDING, None)}
    b = env["db"].execute(f"SELECT pushed_at FROM {M.TBL_ORG_SYNC_ITEMS} "
                          "WHERE _repo_id = 'github.com~acme~b'").fetchone()[0]
    assert b == "2026-11-10T00:00:00Z"
    assert env["calls"]["cloned"] == [env["calls"]["cloned"][0]]  # nothing more cloned


def test_refresh_with_no_unfinished_run_just_lists(env):
    FakeGitHub.listing = [_repo("a")]
    result = run("--org", "acme", "-refresh")
    assert result.exit_code == 0, result.output
    assert "saved as run" in result.output
    assert env["calls"]["synced"] == ["github.com~acme~a"]


def test_a_plain_resume_never_lists_again_however_old(env):
    FakeGitHub.listing = [_repo("a"), _repo("b")]
    run("--org", "acme", "-next", "1")
    _age_listing(env["db"], days=30)
    result = run("--org", "acme", "-next", "1")
    assert result.exit_code == 0, result.output
    assert "Resuming run" in result.output
    assert FakeGitHub.list_calls == 1


# --- -active -----------------------------------------------------------------

def _days_ago(days):
    return _hours_ago(days * 24)


def _age_listing(db, days):
    db.execute(f"UPDATE {M.TBL_ORG_SYNC_RUNS} SET listed_at = ?", [_days_ago(days)])
    db.conn.commit()


def _active_listing():
    return [_repo("fresh", _days_ago(10)), _repo("recent", _days_ago(200)),
            _repo("old", _days_ago(900)), _repo("empty", None)]


@pytest.mark.parametrize("args, message", [
    (["--org", "acme", "-active", "0"], "positive number of days"),
    (["--org", "acme", "-inventory", "-active"], "Run them separately"),
    (["--org", "acme", "-refresh", "-restart"], "-refresh or -restart, not both"),
    (["--org", "acme", "-status", "-refresh"], "-status reads the DB only"),
    (["--org", "-active", "acme"], "'acme' is not a valid integer"),
    (["https://github.com/acme/app", "-active"], "only apply with --org or --user"),
])
def test_active_option_mistakes(env, args, message):
    result = run(*args)
    assert result.exit_code == 2, result.output
    assert message in result.text


def test_active_defaults_to_90_days_and_leaves_the_rest_pending(env):
    FakeGitHub.listing = _active_listing()
    run("--org", "acme", "-inventory")
    result = run("--org", "acme", "-active")
    assert result.exit_code == 0, result.output
    assert FakeGitHub.list_calls == 1            # listed minutes ago: not stale
    assert "1 to do in the last 90 days, 3 pending outside it." in result.text
    assert env["calls"]["synced"] == ["github.com~acme~fresh"]
    assert _items(env["db"])["recent"] == (M.PENDING, None)
    assert "Nothing left to do in the last 90 days." in result.text
    assert "3 repos are pending outside the window." in result.text
    assert M.unfinished_run(env["db"], ORG) is not None


def test_a_wider_window_later_picks_up_what_was_left(env):
    FakeGitHub.listing = _active_listing()
    run("--org", "acme", "-active")
    result = run("--org", "acme", "-active", "365")
    assert result.exit_code == 0, result.output
    assert env["calls"]["synced"] == ["github.com~acme~fresh", "github.com~acme~recent"]
    assert "Nothing to sync" not in result.output

    result = run("--org", "acme")                 # no window: the rest
    assert env["calls"]["synced"][2:] == ["github.com~acme~old", "github.com~acme~empty"]
    assert M.unfinished_run(env["db"], ORG) is None


def test_active_with_next_continues_with_the_same_window(env):
    FakeGitHub.listing = [_repo(n, _days_ago(5)) for n in "abc"] + [_repo("z", _days_ago(400))]
    result = run("--org", "acme", "-active", "30", "-next", "2")
    assert result.exit_code == 0, result.output
    assert len(env["calls"]["synced"]) == 2
    assert "1 repos still to do in the last 30 days. Continue with: " \
           "kgit sync --org acme -active 30 -next 2" in result.text


def test_suggested_commands_keep_no_auth(env):
    # A suggestion without -no-auth would list with the token next time.
    FakeGitHub.listing = [_repo(n, _days_ago(5)) for n in "abcd"]
    result = run("--org", "acme", "-inventory", "-no-auth")
    assert "kgit sync --org acme -next 10 -no-auth" in result.text

    result = run("--org", "acme", "-inventory", "-no-auth")
    assert "kgit sync --org acme -inventory -refresh -no-auth" in result.text
    assert "kgit sync --org acme -inventory -restart -no-auth" in result.text

    result = run("--org", "acme", "-next", "1", "-no-auth")
    assert result.exit_code == 0, result.output
    assert "Continue with: kgit sync --org acme -next 1 -no-auth" in result.text

    result = run("--org", "acme", "-active", "30", "-next", "1", "-no-auth")
    assert result.exit_code == 0, result.output
    assert "Continue with: kgit sync --org acme -active 30 -next 1 -no-auth" in result.text

    result = run("--org", "acme", "-next", "1")
    assert "Continue with: kgit sync --org acme -next 1" in result.text
    assert "-no-auth" not in result.text


def test_suggestions_carry_every_option(env):
    # A copied suggestion must repeat the command that was run: -ssh, the fork
    # and archive choices and -no-prompt as well as -active and -no-auth.
    FakeGitHub.listing = [_repo(n, _days_ago(5)) for n in "abcd"]
    typed = ("-active", "90", "-ssh", "-skip-archived", "-no-prompt", "-include-forks",
             "-no-auth")
    result = run("--org", "acme", "-inventory", "-ssh", "-skip-archived", "-no-auth")
    assert "kgit sync --org acme -next 10 -skip-archived -ssh -no-auth" in result.text

    result = run("--org", "acme", "-inventory", "-ssh", "-no-auth")
    assert "kgit sync --org acme -inventory -refresh -ssh -no-auth" in result.text
    assert "kgit sync --org acme -inventory -restart -ssh -no-auth" in result.text

    result = run("--org", "acme", "-next", "1", *typed)
    assert result.exit_code == 0, result.output
    assert ("Continue with: kgit sync --org acme -active 90 -next 1 -include-forks "
            "-skip-archived -ssh -no-prompt -no-auth") in result.text

    result = run("--org", "acme", "-next", "1", "-retry-failed", "-ssh", "-no-auth")
    assert "Continue with: kgit sync --org acme -next 1 -ssh -no-auth" in result.text


def test_interrupted_sync_resumes_with_every_option(env):
    FakeGitHub.listing = [_repo(n, _days_ago(5)) for n in "abcd"]

    def interrupted(path, **kw):
        raise KeyboardInterrupt

    env_kospex = kgit_module.kospex
    original = env_kospex.sync_repo
    env_kospex.sync_repo = interrupted
    try:
        result = run("--org", "acme", "-next", "2", "-ssh", "-no-auth")
    finally:
        env_kospex.sync_repo = original
    assert result.exit_code == 130
    assert "Resume with: kgit sync --org acme -next 2 -ssh -no-auth" in result.text


def test_active_refreshes_a_stale_listing_in_place(env):
    FakeGitHub.listing = _active_listing()
    run("--org", "acme", "-inventory")
    first = M.unfinished_run(env["db"], ORG)["run_id"]
    _age_listing(env["db"], days=3)

    # Since the inventory, `old` was pushed to and a new repo appeared.
    FakeGitHub.listing = [_repo("fresh", _days_ago(10)), _repo("recent", _days_ago(200)),
                          _repo("old", _days_ago(1)), _repo("empty", None),
                          _repo("brandnew", _days_ago(2))]
    result = run("--org", "acme", "-active")
    assert result.exit_code == 0, result.output
    assert FakeGitHub.list_calls == 2
    assert "was listed 3 days ago, over KOSPEX_ORG_SYNC_REFRESH_DAYS=2: listing again." \
        in result.text
    assert "1 new, 0 gone." in result.text
    assert M.unfinished_run(env["db"], ORG)["run_id"] == first
    assert sorted(env["calls"]["synced"]) == ["github.com~acme~brandnew",
                                              "github.com~acme~fresh", "github.com~acme~old"]


def test_refresh_days_setting(env, monkeypatch):
    FakeGitHub.listing = _active_listing()
    run("--org", "acme", "-inventory")
    _age_listing(env["db"], days=3)
    monkeypatch.setenv("KOSPEX_ORG_SYNC_REFRESH_DAYS", "7")
    run("--org", "acme", "-active")
    assert FakeGitHub.list_calls == 1             # 3 days old is fresh enough

    monkeypatch.setenv("KOSPEX_ORG_SYNC_REFRESH_DAYS", "0")
    run("--org", "acme", "-active")
    assert FakeGitHub.list_calls == 2             # 0: every time

    monkeypatch.setenv("KOSPEX_ORG_SYNC_REFRESH_DAYS", "two")
    result = run("--org", "acme", "-active")
    assert result.exit_code == 2
    assert "isn't a whole number of days" in result.text


def test_a_failed_refresh_leaves_the_run_untouched(env):
    from kospex_github import GitHubListingIncomplete
    FakeGitHub.listing = _active_listing()
    run("--org", "acme", "-inventory")
    _age_listing(env["db"], days=5)
    before = _items(env["db"])
    FakeGitHub.listing_error = GitHubListingIncomplete("u", 2, 100, "HTTP 502")
    result = run("--org", "acme", "-active")
    assert result.exit_code == 1
    assert "Nothing was saved or synced" in result.output
    assert _items(env["db"]) == before
    assert env["calls"]["cloned"] == []


def test_status_with_active_shows_the_window_offline(env, monkeypatch):
    FakeGitHub.listing = _active_listing()
    run("--org", "acme", "-inventory")

    class NoNetwork:
        def __init__(self):
            raise AssertionError("-status must not touch GitHub")
    monkeypatch.setattr(kgit_module, "KospexGithub", NoNetwork)
    result = run("--org", "acme", "-status", "-active", "365")
    assert result.exit_code == 0, result.output
    assert "2 to do in the last 365 days, 2 pending outside it" in result.text


# --- listing history: -history -----------------------------------------------

def test_history_shows_each_listing_and_what_changed_offline(env, monkeypatch):
    FakeGitHub.listing = [_repo("a"), _repo("b"), _repo("c")]
    run("--org", "acme", "-inventory")
    FakeGitHub.listing = [_repo("a", archived=True), _repo("b"), _repo("d")]
    result = run("--org", "acme", "-inventory", "-refresh")
    assert "from a new listing of 3: 1 new, 1 gone, 1 newly archived." in result.text

    class NoNetwork:
        def __init__(self):
            raise AssertionError("-history must not touch GitHub")
    monkeypatch.setattr(kgit_module, "KospexGithub", NoNetwork)
    result = run("--org", "acme", "-history")
    assert result.exit_code == 0, result.output
    lines = [line.split() for line in result.output.splitlines() if "│" in line]
    rows = [[cell for cell in line if cell != "│"] for line in lines]
    first, refresh = [r for r in rows if r and r[0].startswith("20")]
    #        kind      repos new gone back renamed archived+ unarchived archived forks private internal
    assert first[2:] == ["first", "3", "3", "0", "0", "0", "0", "0", "0", "0", "0", "0"]
    assert refresh[2:] == ["refresh", "3", "1", "1", "0", "0", "1", "0", "1", "0", "0", "0"]

    result = run("--org", "acme", "-status")
    assert "github.com~acme listed 2 times since" in result.text
    assert "kgit sync --org acme -history" in result.text


def test_history_with_no_listings(env):
    result = run("--org", "acme", "-history")
    assert result.exit_code == 0, result.output
    assert "No listings for github.com~acme" in result.output


def test_created_at_from_the_listing_is_saved(env):
    FakeGitHub.listing = [_repo("a", created_at="2019-05-01T00:00:00Z")]
    run("--org", "acme", "-inventory")
    assert env["db"].execute(f"SELECT created_at FROM {M.TBL_ORG_SYNC_ITEMS}").fetchone()[0] \
        == "2019-05-01T00:00:00Z"


# --- syncing -----------------------------------------------------------------

def test_next_syncs_the_most_recently_pushed_and_resumes_without_relisting(env):
    FakeGitHub.listing = [_repo("old", "2025-01-01T00:00:00Z"),
                          _repo("new", "2026-11-05T00:00:00Z"),
                          _repo("mid", "2026-06-01T00:00:00Z")]
    result = run("--org", "acme", "-next", "2")
    assert result.exit_code == 0, result.output
    assert env["calls"]["synced"] == ["github.com~acme~new", "github.com~acme~mid"]
    assert "1 repos still to do. Continue with: kgit sync --org acme -next 2" in result.output
    assert FakeGitHub.list_calls == 1

    result = run("--org", "acme", "-next", "2")
    assert result.exit_code == 0, result.output
    assert "Resuming run" in result.output
    assert FakeGitHub.list_calls == 1  # the saved listing, not a new one
    assert env["calls"]["synced"][-1] == "github.com~acme~old"
    assert "is finished: 3 synced" in result.output
    assert M.unfinished_run(env["db"], ORG) is None


def test_a_finished_run_is_followed_by_a_fresh_listing(env):
    FakeGitHub.listing = [_repo("a")]
    run("--org", "acme")
    run("--org", "acme")
    assert FakeGitHub.list_calls == 2
    assert env["db"][M.TBL_ORG_SYNC_RUNS].count == 2


def test_synced_repos_get_last_fetch_and_their_provider_id(env):
    FakeGitHub.listing = [_repo("a")]
    result = run("--org", "acme")
    assert result.exit_code == 0, result.output
    row = env["db"].execute(
        "SELECT provider_repo_id, provider_checked_at, last_fetch FROM repos").fetchone()
    assert row[0] == f"github.com:{_repo('a')['id']}" and row[1] and row[2]


def test_forks_skipped_by_default_synced_on_request_or_by_default(env, monkeypatch):
    FakeGitHub.listing = [_repo("a"), _repo("f", fork=True)]
    run("--org", "acme")
    assert env["calls"]["synced"] == ["github.com~acme~a"]

    env["calls"]["synced"].clear()
    run("--org", "acme", "-include-forks")
    assert sorted(env["calls"]["synced"]) == ["github.com~acme~a", "github.com~acme~f"]

    env["calls"]["synced"].clear()
    monkeypatch.setenv("KOSPEX_CLONE_FORKS_DEFAULT", "true")
    run("--org", "acme")
    assert "github.com~acme~f" in env["calls"]["synced"]

    env["calls"]["synced"].clear()
    run("--org", "acme", "-skip-forks")
    assert env["calls"]["synced"] == ["github.com~acme~a"]


def test_skip_archived_and_ssh(env):
    FakeGitHub.listing = [_repo("a"), _repo("old", archived=True)]
    run("--org", "acme", "-skip-archived", "-ssh")
    assert env["calls"]["cloned"] == ["git@github.com:acme/a.git"]
    assert _items(env["db"])["old"] == (M.SKIPPED, "archived")


def test_no_prompt_applies_to_the_clones_and_is_undone_after(env, monkeypatch):
    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    FakeGitHub.listing = [_repo("a")]
    run("--org", "acme", "-no-prompt")
    assert env["calls"]["env"] == ["0"]
    assert "GIT_TERMINAL_PROMPT" not in os.environ


def test_a_failing_repo_is_reported_and_the_rest_continue(env, monkeypatch):
    FakeGitHub.listing = [_repo("bad", "2026-11-05T00:00:00Z"), _repo("good")]

    def clone_repo(url):
        return None if "bad" in url else f"/code/{url}"
    monkeypatch.setattr(kgit_module.kgit, "clone_repo", clone_repo)

    result = run("--org", "acme")
    assert result.exit_code == 0, result.output
    assert "FAILED   github.com~acme~bad: clone or pull failed" in result.output
    assert "SYNCED   github.com~acme~good" in result.output
    assert "kgit sync --org acme -status" in result.output
    assert M.unfinished_run(env["db"], ORG) is None  # failures don't hold it open


def test_the_user_form_syncs_only_repos_the_user_owns(env):
    FakeGitHub.account_type = "User"
    FakeGitHub.listing = [_repo("a"), {**_repo("x"), "name": "someone-else/x"}]
    result = run("--user", "acme")
    assert result.exit_code == 0, result.output
    assert "Left out 1 repos owned by other accounts" in result.output
    assert env["calls"]["synced"] == ["github.com~acme~a"]
    assert M.latest_run(env["db"], ORG)["owner_type"] == "user"


# --- -status -----------------------------------------------------------------

def test_status_is_offline(env, monkeypatch):
    FakeGitHub.listing = [_repo("a"), _repo("f", fork=True)]

    def clone_repo(url):
        return None
    monkeypatch.setattr(kgit_module.kgit, "clone_repo", clone_repo)
    run("--org", "acme")

    class NoNetwork:
        def __init__(self):
            raise AssertionError("-status must not touch GitHub")
    monkeypatch.setattr(kgit_module, "KospexGithub", NoNetwork)
    env["db"]["repos"].insert({"_repo_id": "github.com~acme~deleted", "_git_server": "github.com",
                               "_git_owner": "acme", "_git_repo": "deleted"}, pk="_repo_id")

    result = run("--org", "acme", "-status")
    assert result.exit_code == 0, result.output
    assert "synced 0, skipped 1, failed 1, pending 0" in result.output
    assert "skipped: fork 1" in result.output
    assert "clone or pull failed" in result.output
    assert "github.com~acme~deleted" in result.output


def test_status_with_no_runs(env):
    result = run("--org", "acme", "-status")
    assert result.exit_code == 0
    assert "No sync runs for github.com~acme" in result.output


# --- real git ----------------------------------------------------------------

def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_DATE": "2025-01-01T00:00:00",
                        "GIT_COMMITTER_DATE": "2025-01-01T00:00:00",
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})


def test_end_to_end_with_real_clones(env, tmp_path, monkeypatch):
    """Two local repos stand in for github.com/acme/*: cloned, synced, recorded."""
    from kospex_core import Kospex
    from kospex_git import KospexGit

    gitconfig = tmp_path / "gitconfig"
    lines = []
    for name in ("one", "two"):
        bare = tmp_path / "upstream" / f"{name}.git"
        bare.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        work = tmp_path / f"work-{name}"
        subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True,
                       capture_output=True)
        (work / "app.py").write_text(f"print('{name}')\n")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", f"init {name}")
        _git(work, "push", "-q", "origin", "main")
        lines.append(f'[url "{bare}"]\n\tinsteadOf = https://github.com/acme/{name}.git\n')
    gitconfig.write_text("".join(lines))

    # Real clone and sync this time, on the throwaway DB. The insteadOf
    # redirect applies only while cloning: kospex reads the remote with the
    # rewrite applied, so leaving it on would give every repo the same
    # local-path repo_id instead of github.com~acme~<name>.
    real = KospexGit()
    real_clone = real.clone_repo

    def clone_repo(url):
        os.environ["GIT_CONFIG_GLOBAL"] = str(gitconfig)
        try:
            return real_clone(url)
        finally:
            del os.environ["GIT_CONFIG_GLOBAL"]

    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.setattr(real, "clone_repo", clone_repo)
    monkeypatch.setattr(kgit_module, "kgit", real)
    monkeypatch.setattr(kgit_module, "kospex", Kospex(kospex_db=env["db"]))
    FakeGitHub.listing = [_repo("one", id=1), _repo("two", id=2)]

    result = run("--org", "acme", "-no-prompt")
    assert result.exit_code == 0, result.output
    assert "is finished: 2 synced" in result.output
    assert _items(env["db"]) == {"one": (M.SYNCED, None), "two": (M.SYNCED, None)}
    assert env["db"].execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 2
    assert (tmp_path / "code" / "github.com" / "acme" / "one" / "app.py").exists()
    rows = env["db"].execute(
        "SELECT _repo_id, provider_repo_id, last_fetch FROM repos ORDER BY 1").fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        ("github.com~acme~one", "github.com:1"), ("github.com~acme~two", "github.com:2")]
    assert all(r[2] for r in rows)


# --- each command is recorded (org_sync_commands) ----------------------------

def _commands(db):
    return list(db.query(f"SELECT * FROM {M.TBL_ORG_SYNC_COMMANDS} ORDER BY command_id"))


def test_each_working_command_is_recorded(env):
    FakeGitHub.listing = [_repo(n, _days_ago(5)) for n in "abc"]
    assert run("--org", "acme", "-inventory").exit_code == 0
    assert run("--org", "acme", "-next", "1", "-ssh").exit_code == 0
    assert run("--org", "acme").exit_code == 0

    rows = _commands(env["db"])
    run_id = env["db"].execute(f"SELECT run_id FROM {M.TBL_ORG_SYNC_RUNS}").fetchone()[0]
    assert [r["run_id"] for r in rows] == [run_id] * 3
    assert [r["listing"] for r in rows] == ["first", None, None]
    assert [r["synced_any"] for r in rows] == [0, 1, 1]
    assert [r["synced"] for r in rows] == [0, 1, 2]
    assert [r["options"] for r in rows] == ["-inventory", "-next 1 -ssh", ""]
    assert [r["command"] for r in rows] == [
        "kgit sync --org acme -inventory", "kgit sync --org acme -next 1 -ssh",
        "kgit sync --org acme"]
    assert (rows[0]["new"], rows[0]["gone"]) == (3, 0)
    assert all(r["finished_at"] and r["error"] is None for r in rows)
    assert {(r["auth_login"], r["auth_kind"], r["auth_source"], r["auth_expires_at"])
            for r in rows} == {("octocat", "fine-grained", "GH_TOKEN",
                                "2027-05-20T00:00:00Z")}


@pytest.mark.parametrize("args, setup", [
    (["--org", "acme", "-status"], None),
    (["--org", "acme", "-history"], None),
    (["--org", "acme"], "rejected token"),
    (["--org", "acme"], "no such owner"),
])
def test_reads_and_refusals_leave_no_row(env, args, setup):
    FakeGitHub.listing = [_repo("a")]
    if setup == "rejected token":
        FakeGitHub.auth_ok = False
    if setup == "no such owner":
        FakeGitHub.account_type = None
    run(*args)
    assert _commands(env["db"]) == []


def test_a_held_lock_leaves_no_row(env):
    import logging
    import kospex.org_sync as OrgSync
    from kospex.run_lock import RunLock
    FakeGitHub.listing = [_repo("a")]
    with RunLock(OrgSync.lock_path(env["home"], ORG), "x", 60, logging.getLogger("t")):
        run("--org", "acme")
    assert _commands(env["db"]) == []


def test_no_auth_is_recorded_as_none(env):
    FakeGitHub.listing = [_repo("a")]
    assert run("--org", "acme", "-no-auth").exit_code == 0
    [row] = _commands(env["db"])
    assert (row["auth_kind"], row["auth_login"], row["auth_source"],
            row["auth_expires_at"]) == ("none", None, None, None)
    assert row["options"] == "-no-auth"


def test_refresh_restart_and_active_relisting_are_recorded(env):
    FakeGitHub.listing = [_repo(n, _days_ago(5)) for n in "ab"]
    run("--org", "acme", "-inventory")
    FakeGitHub.listing = [_repo(n, _days_ago(5)) for n in "abc"]
    run("--org", "acme", "-inventory", "-refresh")
    run("--org", "acme", "-inventory", "-restart")
    _age_listing(env["db"], 30)
    run("--org", "acme", "-active", "-next", "1")

    rows = _commands(env["db"])
    assert [r["listing"] for r in rows] == ["first", "refresh", "restart", "refresh"]
    assert (rows[1]["new"], rows[1]["gone"]) == (1, 0)
    assert rows[3]["synced_any"] == 1


def test_an_unsaved_listing_is_recorded_without_a_listing(env):
    FakeGitHub.listing = [_repo("a"), _repo("b")]
    run("--org", "acme", "-next", "1")
    run("--org", "acme", "-inventory")
    rows = _commands(env["db"])
    assert rows[1]["listing"] is None
    assert rows[1]["synced_any"] == 0


def test_an_incomplete_listing_records_the_error(env):
    from kospex_github import GitHubListingIncomplete
    FakeGitHub.listing = [_repo("a"), _repo("b")]
    run("--org", "acme", "-inventory")
    FakeGitHub.listing_error = GitHubListingIncomplete("u", 2, 100, "HTTP 502")
    result = run("--org", "acme", "-inventory", "-refresh")
    assert result.exit_code == 1
    row = _commands(env["db"])[-1]
    assert "page 2" in row["error"]
    assert row["finished_at"] is not None
    assert row["run_id"] is not None


def test_a_first_listing_that_fails_has_no_run(env):
    from kospex_github import GitHubListingIncomplete
    FakeGitHub.listing_error = GitHubListingIncomplete("u", 2, 100, "HTTP 502")
    run("--org", "acme")
    [row] = _commands(env["db"])
    assert row["run_id"] is None
    assert "page 2" in row["error"]


def test_ctrl_c_while_listing_keeps_the_run_on_the_row(env):
    # A raw KeyboardInterrupt (not the sync loop's SystemExit(130)) during a
    # refresh listing: the row must still name its run and read as stopped.
    FakeGitHub.listing = [_repo("a"), _repo("b")]
    run("--org", "acme", "-inventory")
    FakeGitHub.listing_error = KeyboardInterrupt()
    run("--org", "acme", "-inventory", "-refresh")
    row = _commands(env["db"])[-1]
    assert row["run_id"] is not None
    assert row["finished_at"] is None
    assert row["error"] is None


def test_interrupted_command_is_left_unfinished(env):
    FakeGitHub.listing = [_repo(n, _days_ago(5)) for n in "ab"]

    def interrupted(path, **kw):
        raise KeyboardInterrupt

    original = kgit_module.kospex.sync_repo
    kgit_module.kospex.sync_repo = interrupted
    try:
        result = run("--org", "acme")
    finally:
        kgit_module.kospex.sync_repo = original
    assert result.exit_code == 130
    [row] = _commands(env["db"])
    assert row["finished_at"] is None
    assert row["run_id"] is not None
    assert row["listing"] == "first"


# --- kgit.log (#443) ---------------------------------------------------------
# Every line of a run starts `kgit sync github.com~acme:`, so one owner's runs
# can be grepped out of kgit.log.

LOG_PREFIX = f"kgit sync {ORG}: "


def _log(caplog):
    return [(r.levelname, r.getMessage()) for r in caplog.records
            if r.name == "kgit" and r.getMessage().startswith(LOG_PREFIX)]


def _logged(caplog, level, text):
    return any(lv == level and text in msg for lv, msg in _log(caplog))


def test_the_start_is_logged_with_the_command_and_the_token_identity(env, caplog,
                                                                   monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_not_a_real_token_value")
    FakeGitHub.listing = [_repo("a")]
    with caplog.at_level("INFO", logger="kgit"):
        run("--org", "acme", "-next", "1")
    assert _logged(caplog, "INFO", "started: kgit sync --org acme -next 1")
    assert _logged(caplog, "INFO", "token octocat (fine-grained, from GH_TOKEN, "
                                   "expires 2027-05-20T00:00:00Z)")
    assert "ghp_not_a_real_token_value" not in caplog.text


def test_no_auth_is_logged_as_no_token(env, caplog):
    FakeGitHub.listing = [_repo("a")]
    with caplog.at_level("INFO", logger="kgit"):
        run("--org", "acme", "-no-auth")
    assert _logged(caplog, "INFO", "started: kgit sync --org acme -no-auth, no token")


def test_a_held_lock_is_logged(env, caplog):
    import logging
    import kospex.org_sync as OrgSync
    from kospex.run_lock import RunLock
    FakeGitHub.listing = [_repo("a")]
    with RunLock(OrgSync.lock_path(env["home"], ORG), "x", 60, logging.getLogger("t")):
        with caplog.at_level("INFO", logger="kgit"):
            run("--org", "acme")
    assert _logged(caplog, "INFO", "another run holds the lock, nothing to do")


def test_each_listing_is_logged(env, caplog):
    FakeGitHub.listing = [_repo("a"), _repo("b")]
    with caplog.at_level("INFO", logger="kgit"):
        run("--org", "acme", "-inventory")
        FakeGitHub.listing = [_repo("a"), _repo("c")]
        run("--org", "acme", "-inventory", "-refresh")
    assert _logged(caplog, "INFO", "first listing: 2 repos, 2 new, 0 gone, run ")
    assert _logged(caplog, "INFO", "refresh listing: 2 repos, 1 new, 1 gone, run ")


def test_a_listing_that_is_not_saved_is_logged(env, caplog):
    FakeGitHub.listing = [_repo("a"), _repo("b")]
    run("--org", "acme", "-inventory")
    FakeGitHub.listing = [_repo("a"), _repo("b"), _repo("c")]
    with caplog.at_level("INFO", logger="kgit"):
        run("--org", "acme", "-inventory")
    assert _logged(caplog, "INFO", "listed 3 repos, not saved: 1 new, 0 gone since run ")


def test_each_repo_is_logged_failures_and_skips_as_warnings(env, caplog, monkeypatch):
    FakeGitHub.listing = [_repo("bad", "2026-11-05T00:00:00Z"), _repo("good"),
                          _repo("other", "2026-10-01T00:00:00Z")]
    monkeypatch.setattr(kgit_module.kgit, "clone_repo",
                        lambda url: None if "bad" in url else f"/code/{url}")
    monkeypatch.setattr(kgit_module, "clone_path_conflict",
                        lambda kospex, url: "other" in url)
    with caplog.at_level("INFO", logger="kgit"):
        run("--org", "acme")
    assert _logged(caplog, "WARNING", "FAILED github.com~acme~bad: clone or pull failed")
    assert _logged(caplog, "INFO", "SYNCED github.com~acme~good")
    assert _logged(caplog, "WARNING", "SKIPPED github.com~acme~other: "
                                      "already synced from another directory")


def test_the_totals_are_logged(env, caplog, monkeypatch):
    FakeGitHub.listing = [_repo(n, _days_ago(5)) for n in "abc"]
    with caplog.at_level("INFO", logger="kgit"):
        run("--org", "acme", "-next", "2")
        run("--org", "acme")
    assert _logged(caplog, "INFO", "synced 2, skipped 0, failed 0; 1 still to do in run ")
    assert _logged(caplog, "INFO", "synced 1, skipped 0, failed 0; run ")
    assert _logged(caplog, "INFO", " is finished")


def test_an_interrupted_sync_is_logged_with_the_resume_command(env, caplog):
    FakeGitHub.listing = [_repo(n, _days_ago(5)) for n in "ab"]

    def interrupted(path, **kw):
        raise KeyboardInterrupt

    original = kgit_module.kospex.sync_repo
    kgit_module.kospex.sync_repo = interrupted
    try:
        with caplog.at_level("INFO", logger="kgit"):
            run("--org", "acme", "-ssh")
    finally:
        kgit_module.kospex.sync_repo = original
    assert _logged(caplog, "WARNING", "interrupted. Resume with: kgit sync --org acme -ssh")


def test_ctrl_c_while_listing_is_logged(env, caplog):
    FakeGitHub.listing_error = KeyboardInterrupt()
    with caplog.at_level("INFO", logger="kgit"):
        run("--org", "acme")
    assert _logged(caplog, "WARNING", "interrupted")


def test_a_stopped_command_is_logged_with_its_reason(env, caplog):
    from kospex_github import GitHubListingIncomplete
    FakeGitHub.listing_error = GitHubListingIncomplete("u", 2, 100, "HTTP 502")
    with caplog.at_level("INFO", logger="kgit"):
        run("--org", "acme")
    assert _logged(caplog, "WARNING", "stopped: ")
    assert _logged(caplog, "WARNING", "page 2")


def test_an_unexpected_error_is_logged(env, caplog, monkeypatch):
    import kospex.org_sync_manifest as manifest

    def broken(*a, **k):
        raise RuntimeError("db went away")
    FakeGitHub.listing = [_repo("a")]
    monkeypatch.setattr(manifest, "save_listing", broken)
    with caplog.at_level("INFO", logger="kgit"):
        run("--org", "acme")
    assert _logged(caplog, "ERROR", "failed: RuntimeError: db went away")
