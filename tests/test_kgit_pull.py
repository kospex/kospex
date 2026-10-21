"""Unit tests for kgit pull helpers and provenance."""
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_query import KospexQuery


def _repos_db(rows):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db.execute("ALTER TABLE repos ADD COLUMN last_fetch TEXT")
    if rows:
        db["repos"].insert_all(rows, pk="_repo_id")
    return db


def test_set_repo_last_fetch_stamps_timestamp():
    db = _repos_db([{"_repo_id": "s~o~r", "_git_server": "s", "_git_owner": "o",
                     "_git_repo": "r", "file_path": "/tmp/r"}])
    kq = KospexQuery(kospex_db=db)

    kq.set_repo_last_fetch("s~o~r", when="2026-07-09T00:00:00+00:00")

    row = next(db.query("SELECT last_fetch FROM repos WHERE _repo_id='s~o~r'"))
    assert row["last_fetch"] == "2026-07-09T00:00:00+00:00"


import pytest
from kgit import _resolve_pull_repos


def _seed_repos():
    db = _repos_db([
        {"_repo_id": "github.com~o1~a", "_git_server": "github.com",
         "_git_owner": "o1", "_git_repo": "a", "file_path": "/c/a"},
        {"_repo_id": "github.com~o1~b", "_git_server": "github.com",
         "_git_owner": "o1", "_git_repo": "b", "file_path": "/c/b"},
        {"_repo_id": "bitbucket.org~o2~c", "_git_server": "bitbucket.org",
         "_git_owner": "o2", "_git_repo": "c", "file_path": "/c/c"},
    ])
    return KospexQuery(kospex_db=db)


def test_resolve_by_repo_id():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id="github.com~o1~a",
                                all_flag=False, org=None, server=None)
    assert [r["_repo_id"] for r in repos] == ["github.com~o1~a"]


def test_resolve_by_org():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id=None, all_flag=False,
                                org="github.com~o1", server=None)
    assert sorted(r["_repo_id"] for r in repos) == ["github.com~o1~a", "github.com~o1~b"]


def test_resolve_by_server():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id=None, all_flag=False,
                                org=None, server="bitbucket.org")
    assert [r["_repo_id"] for r in repos] == ["bitbucket.org~o2~c"]


def test_resolve_all():
    kq = _seed_repos()
    repos = _resolve_pull_repos(kq, repo_id=None, all_flag=True,
                                org=None, server=None)
    assert len(repos) == 3


def test_resolve_requires_exactly_one_scope():
    kq = _seed_repos()
    with pytest.raises(ValueError):
        _resolve_pull_repos(kq, repo_id=None, all_flag=False, org=None, server=None)
    with pytest.raises(ValueError):
        _resolve_pull_repos(kq, repo_id="x~y~z", all_flag=True, org=None, server=None)


from kgit import _staleness_rows


def test_staleness_rows_formats_age_and_handles_null():
    now = "2026-07-09T00:00:00+00:00"
    repos = [
        {"_repo_id": "s~o~fresh", "last_fetch": "2026-07-08T00:00:00+00:00",
         "last_sync": "2026-07-08T00:00:00+00:00", "last_seen": "2026-07-01T00:00:00+00:00"},
        {"_repo_id": "s~o~stale", "last_fetch": "2026-05-10T00:00:00+00:00",
         "last_sync": "2026-05-10T00:00:00+00:00", "last_seen": "2026-05-01T00:00:00+00:00"},
        {"_repo_id": "s~o~never", "last_fetch": None,
         "last_sync": "2026-05-10T00:00:00+00:00", "last_seen": None},
    ]

    rows = _staleness_rows(repos, now=now)

    # stalest first: never (no fetch) sorts first, then stale, then fresh
    assert [r["repo_id"] for r in rows] == ["s~o~never", "s~o~stale", "s~o~fresh"]
    assert rows[0]["fetch_age"] == "never"
    assert rows[2]["fetch_age"] == "1d"


from kgit import _pull_command


def test_pull_command_interactive_by_default():
    argv, env = _pull_command("/clone/path", no_prompt=False)
    assert argv[:3] == ["git", "-C", "/clone/path"]
    assert argv[-2:] == ["pull", "--ff-only"]
    assert "GIT_TERMINAL_PROMPT" not in env


def test_pull_command_no_prompt_is_non_interactive():
    argv, env = _pull_command("/clone/path", no_prompt=True)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "-c" in argv and "credential.interactive=false" in argv


from kgit import _git_pull


def test_git_pull_none_path_is_skipped_not_crash():
    ok, detail, commits = _git_pull(None)
    assert ok is False and commits == 0
    assert "path" in detail.lower()



# Longer than 80 characters, so piped output would truncate it if print_table
# fell back to rich's default width.
LONG_REPO_ID = ("gitlab.com~example-organisation~~platform-engineering-subgroup"
                "~a-fairly-long-repository-name")


def test_pull_check_prints_complete_repo_id_when_piped(monkeypatch):
    import kgit
    from click.testing import CliRunner

    assert len(LONG_REPO_ID) > 80
    repos = [
        {"_repo_id": LONG_REPO_ID, "last_fetch": None,
         "last_sync": "2026-05-10T00:00:00+00:00", "last_seen": None},
    ]
    monkeypatch.setattr(kgit, "_resolve_pull_repos", lambda *a, **k: repos)

    # CliRunner output is never a terminal, so this is the piped case.
    result = CliRunner().invoke(kgit.cli, ["pull", "--check", "--all"])

    assert result.exit_code == 0, result.output
    assert "Repo Staleness" in result.output
    assert LONG_REPO_ID in result.output
    assert "2026-05-10T00:00:00+00:00" in result.output
    assert "fetch_age" in result.output
    assert "never" in result.output
