"""Tests for `kospex orphans`: author identity, bots, and refresh age.

Design: changes/202611-orphans-author-bots-refresh.md
"""
import csv
from datetime import datetime, timedelta, timezone

import pytest
import sqlite_utils
from click.testing import CliRunner

import kospex.authorship as KospexAuthorship
import kospex_schema as KospexSchema
from kospex_query import KospexQuery

BOT = "29139614+renovate[bot]@users.noreply.github.com"
NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


def _is_bot(email):
    return email == BOT


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


# --- orphan_row: the pure rule ---------------------------------------------------

def _row(authors, active, repo=None, stale_days=30):
    repo = {"_repo_id": "r", "last_sync": "2026-11-20T00:00:00Z"} if repo is None else repo
    return KospexAuthorship.orphan_row("r", repo, authors, active, _is_bot,
                                       NOW, 365, 90, stale_days)


def test_people_none_active_is_orphaned():
    row = _row({"gone@acme.com": False}, active=set())
    assert (row["status"], row["people"], row["people_active"]) == ("Orphaned", 1, 0)
    assert row["pct_here"] == 0


def test_one_person_still_active_is_knowledge():
    row = _row({"gone@acme.com": False, "here@acme.com": False}, active={"here@acme.com"})
    assert (row["status"], row["people_active"], row["pct_here"]) == ("Knowledge", 1, 50.0)


def test_no_commits_in_window_is_dormant_not_orphaned():
    row = _row({}, active=set())
    assert row["status"] == "Dormant"
    assert row["pct_here"] == ""   # undefined, not 0%


def test_an_active_bot_is_not_working_knowledge():
    """The bot is active, the only person is gone: Orphaned, bot shown separately."""
    row = _row({"gone@acme.com": False, BOT: True}, active={BOT})
    assert (row["status"], row["people"], row["bots"], row["bots_active"]) == (
        "Orphaned", 1, 1, 1)


def test_bots_only_is_dormant_with_bots_active():
    row = _row({BOT: True}, active={BOT})
    assert (row["status"], row["people"], row["bots_active"]) == ("Dormant", 0, 1)


def test_bots_active_means_on_this_repo():
    """A bot busy elsewhere says nothing about this repo."""
    row = _row({BOT: False}, active={BOT})
    assert (row["bots"], row["bots_active"]) == (1, 0)


def test_not_in_db_is_not_synced():
    row = KospexAuthorship.orphan_row("r", None, {}, set(), _is_bot, NOW, 365, 90, 30)
    assert row["status"] == "Not synced"
    assert row["refresh_stale"] == ""


@pytest.mark.parametrize("repo, age, stale", [
    ({"last_fetch": "2026-11-20T00:00:00Z", "last_sync": "2026-01-01T00:00:00Z"}, 1, False),
    ({"last_sync": "2026-10-01T00:00:00+11:00"}, 51, True),     # falls back to last_sync
    ({}, "", True),                                              # never refreshed
])
def test_refresh_age_uses_the_staleness_rule(repo, age, stale):
    row = _row({"a@acme.com": False}, active=set(), repo=repo)
    assert (row["refresh_age_days"], row["refresh_stale"]) == (age, stale)


def test_fields_are_pinned():
    row = _row({}, active=set())
    assert list(row) != []
    assert set(row) == set(KospexAuthorship.ORPHAN_FIELDS)


# --- the queries, against a seeded DB --------------------------------------------

def _commit(repo_id, hash_value, author, author_days, committer="same", committer_days=None):
    committer_days = author_days if committer_days is None else committer_days
    return {"_repo_id": repo_id, "hash": hash_value,
            "author_email": author, "author_when": _ago(author_days),
            "committer_email": author if committer == "same" else committer,
            "committer_when": _ago(committer_days), "parents": 1, "_files": 1}


REPOS = {
    "github.com~acme~webmerged": "github.com",
    "github.com~acme~rebased": "github.com",
    "github.com~acme~botonly": "github.com",
    "github.com~acme~quiet": "github.com",
    "gitlab.com~beta~svc": "gitlab.com",
}


def _seed(stale=False):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMITS)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    for repo_id, server in REPOS.items():
        _, owner, repo = repo_id.split("~")
        db[KospexSchema.TBL_REPOS].insert({
            "_repo_id": repo_id, "_git_server": server, "_git_owner": owner,
            "_git_repo": repo, "last_sync": _ago(100 if stale else 1)})
    rows = [
        # Merged on GitHub's web UI: the committer is GitHub, the author is a
        # developer who is still active (on another server, even).
        _commit("github.com~acme~webmerged", "w1", "dev@acme.com", 200,
                committer="noreply@github.com"),
        _commit("gitlab.com~beta~svc", "g1", "dev@acme.com", 5),
        # Authored long ago, rebased last week: nobody worked on it this year.
        _commit("github.com~acme~rebased", "r1", "gone@acme.com", 900, committer_days=7),
        _commit("github.com~acme~botonly", "b1", BOT, 3),
        _commit("github.com~acme~quiet", "q1", "gone@acme.com", 800),
    ]
    db[KospexSchema.TBL_COMMITS].insert_all(rows, pk=["_repo_id", "hash"])
    return KospexQuery(kospex_db=db)


def test_active_authors_are_author_emails_by_author_date():
    active = _seed().active_author_emails(days=90)
    assert "dev@acme.com" in active
    assert BOT in active
    assert "noreply@github.com" not in active
    assert "gone@acme.com" not in active   # its commit was only re-committed recently


def test_repo_authors_use_author_email_and_author_date():
    got = _seed().repo_author_emails(list(REPOS), window=365, days=90)
    assert got["github.com~acme~webmerged"] == {"dev@acme.com": False}
    assert got["github.com~acme~rebased"] == {}
    assert got["github.com~acme~botonly"] == {BOT: True}
    assert got["github.com~acme~quiet"] == {}


def test_repo_authors_chunk_large_scopes():
    kq = _seed()
    assert kq.repo_author_emails(list(REPOS), chunk_size=1) == kq.repo_author_emails(
        list(REPOS))


def test_repos_table_scopes():
    kq = _seed()
    assert set(kq.repos_table()) == set(REPOS)
    assert set(kq.repos_table(server="gitlab.com")) == {"gitlab.com~beta~svc"}
    assert set(kq.repos_table(repo_ids=["gitlab.com~beta~svc", "x~y~z"])) == {
        "gitlab.com~beta~svc"}


# --- the CLI -----------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch):
    import kospex_cli

    def use(stale=False):
        monkeypatch.setattr(kospex_cli.kospex, "kospex_query", _seed(stale))
        monkeypatch.setattr(KospexAuthorship, "bot_checker", lambda: _is_bot)
        return kospex_cli.cli

    return use


def _statuses(output):
    """{repo_id: status} from the one-line status messages."""
    found = {}
    for line in output.splitlines():
        if line.startswith("repo_id: "):
            repo_id, status = line[len("repo_id: "):].split(", ")[:2]
            found[repo_id] = status.rstrip(".")
    assert found, output
    return found


def test_cli_statuses(cli):
    result = CliRunner().invoke(cli(), ["orphans"])
    assert result.exit_code == 0, result.output
    assert _statuses(result.output) == {
        "github.com~acme~botonly": "Dormant",
        "github.com~acme~quiet": "Dormant",
        "github.com~acme~rebased": "Dormant",
        "github.com~acme~webmerged": "Knowledge",
        "gitlab.com~beta~svc": "Knowledge",
    }


def test_cli_status_line_is_one_line(cli):
    result = CliRunner().invoke(cli(), ["orphans", "-server", "github.com"])
    assert ("repo_id: github.com~acme~webmerged, Knowledge, Present: 1, Total: 1 in 365 days."
            in result.output.splitlines())
    assert "gitlab.com~beta~svc" not in result.output   # scoped, but dev is still present


def test_cli_does_not_print_the_author_table(cli):
    result = CliRunner().invoke(cli(), ["orphans"])
    assert "username" not in result.output
    assert "active developers" not in result.output


def test_cli_window_changes_who_counts(cli):
    result = CliRunner().invoke(cli(), ["orphans", "-window", "1000"])
    statuses = _statuses(result.output)
    assert statuses["github.com~acme~quiet"] == "Orphaned"
    assert "in 1000 days." in result.output


def test_cli_target_list_reports_repos_not_in_db(cli, tmp_path):
    target = tmp_path / "targets.txt"
    target.write_text("# comment\n\nhttps://github.com/acme/webmerged.git\n"
                      "github.com~acme~quiet\nhttps://github.com/acme/missing\nnot a url\n")
    result = CliRunner().invoke(cli(), ["orphans", "-target-list", str(target)])
    assert result.exit_code == 0, result.output
    assert _statuses(result.output) == {
        "github.com~acme~webmerged": "Knowledge",
        "github.com~acme~quiet": "Dormant",
        "github.com~acme~missing": "Not synced",
    }
    assert "Skipping 'not a url'" in result.output


def test_cli_flags_stale_refresh(cli):
    fresh = CliRunner().invoke(cli(), ["orphans"]).output
    assert "not refreshed" not in fresh
    stale = CliRunner().invoke(cli(stale=True), ["orphans"]).output
    assert "5 of 5 repos not refreshed in 30 days" in stale
    assert "kgit pull --stale 30" in stale


def test_cli_empty_scope(cli):
    result = CliRunner().invoke(cli(), ["orphans", "-server", "nope.example.com"])
    assert result.exit_code == 0, result.output
    assert "No repos to check." in result.output


def test_cli_csv(cli, tmp_path):
    out = tmp_path / "orphans.csv"
    result = CliRunner().invoke(cli(), ["orphans", "-csv", str(out)])
    assert result.exit_code == 0, result.output
    with open(out, newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == KospexAuthorship.ORPHAN_FIELDS
    by_id = {r["repo_id"]: r for r in rows}
    assert by_id["github.com~acme~botonly"]["bots_active"] == "1"
    assert by_id["github.com~acme~webmerged"]["status"] == "Knowledge"
