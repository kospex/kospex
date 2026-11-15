"""kgit pull --stale DAYS: only the repos not refreshed in DAYS days.

The repos come from an in-memory DB through the real scope resolution; git and
the sync are replaced by recorders, so the test sees which repos would be pulled.
"""
from datetime import datetime, timedelta, timezone

import pytest
import sqlite_utils
from click.testing import CliRunner

import kgit
import kospex_schema as KospexSchema
from kospex_query import KospexQuery


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _repo(server, owner, name, fetched_days_ago):
    return {"_repo_id": f"{server}~{owner}~{name}", "_git_server": server,
            "_git_owner": owner, "_git_repo": name, "file_path": f"/code/{name}",
            "last_fetch": _ago(fetched_days_ago), "last_sync": _ago(fetched_days_ago)}


REPOS = [
    _repo("github.com", "acme", "fresh", 1),
    _repo("github.com", "acme", "week-old", 10),
    _repo("gitlab.example.com", "beta", "old", 40),
]


@pytest.fixture
def pulled(monkeypatch):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db.execute("ALTER TABLE repos ADD COLUMN last_fetch TEXT")
    db["repos"].insert_all(REPOS, pk="_repo_id")
    monkeypatch.setattr(kgit.kospex, "kospex_query", KospexQuery(kospex_db=db))
    paths = []

    def git_pull(path, no_prompt=False):
        paths.append(path)
        return True, "up to date", 0

    monkeypatch.setattr(kgit, "_git_pull", git_pull)
    monkeypatch.setattr(kgit.kospex, "sync_repo", lambda path: [])
    return paths


def run(*args):
    return CliRunner().invoke(kgit.cli, ["pull", *args])


def test_stale_pulls_only_repos_older_than_days(pulled):
    result = run("--all", "--stale", "7")
    assert result.exit_code == 0, result.output
    assert sorted(pulled) == ["/code/old", "/code/week-old"]
    assert "Pulling 2 of 3 repos not refreshed in 7 days." in result.output


@pytest.mark.parametrize("scope,expected", [
    (["--org", "github.com~acme"], ["/code/week-old"]),
    (["--server", "gitlab.example.com"], ["/code/old"]),
    (["github.com~acme~week-old"], ["/code/week-old"]),
    (["github.com~acme~fresh"], []),
])
def test_stale_narrows_org_and_server_and_repo_id(pulled, scope, expected):
    result = run(*scope, "--stale", "7")
    assert result.exit_code == 0, result.output
    assert pulled == expected


def test_stale_with_check_filters_the_report(pulled):
    result = run("--check", "--all", "--stale", "7")
    assert result.exit_code == 0, result.output
    assert pulled == []
    assert "github.com~acme~week-old" in result.output
    assert "gitlab.example.com~beta~old" in result.output
    assert "github.com~acme~fresh" not in result.output


def test_nothing_stale(pulled):
    result = run("--all", "--stale", "100")
    assert result.exit_code == 0, result.output
    assert pulled == []
    assert "No repos in scope were last refreshed more than 100 days ago." in result.output


def test_one_day_is_singular(pulled):
    result = run("--all", "--stale", "1")
    assert "not refreshed in 1 day." in result.output


@pytest.mark.parametrize("days", ["0", "-3"])
def test_stale_must_be_positive(pulled, days):
    result = run("--all", "--stale", days)
    assert result.exit_code == 2
    assert "--stale needs a whole number of days, 1 or more." in result.output
    assert pulled == []
