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
        "id": 1000 + len(name), "name": f"acme/{name}",
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
    (["--org", "acme", "-inventory", "-next", "5"], "one of -inventory, -next and -status"),
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


def test_inventory_keeps_an_unfinished_run_unless_relist(env):
    FakeGitHub.listing = [_repo("a"), _repo("b")]
    run("--org", "acme", "-inventory")
    first = M.unfinished_run(env["db"], ORG)["run_id"]

    FakeGitHub.listing = [_repo("b"), _repo("c"), _repo("d")]
    result = run("--org", "acme", "-inventory")
    assert result.exit_code == 0, result.output
    assert "Listed 3 repos for github.com~acme (not saved)" in result.output
    assert f"Run {first} is unfinished" in result.output
    assert "2 repos are new since that run, 1 are gone. Kept the unfinished run." in result.output
    assert "kgit sync --org acme -inventory -relist" in result.output
    assert env["db"][M.TBL_ORG_SYNC_RUNS].count == 1

    result = run("--org", "acme", "-inventory", "-relist")
    assert result.exit_code == 0, result.output
    assert M.unfinished_run(env["db"], ORG)["run_id"] != first
    assert env["db"][M.TBL_ORG_SYNC_RUNS].count == 2


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
    assert row[0] == "github.com:1001" and row[1] and row[2]


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
