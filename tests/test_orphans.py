"""Tests for `kospex orphans`: author identity, bots, and refresh age.

Design: changes/202611-orphans-author-bots-refresh.md
"""
import csv
from datetime import datetime, timedelta, timezone

import pytest
import sqlite_utils
from click.testing import CliRunner

import kospex.authorship as KospexAuthorship
import kospex.orphans as KospexOrphans
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
    return KospexOrphans.orphan_row("r", repo, authors, active, _is_bot, NOW, stale_days)


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
    row = KospexOrphans.orphan_row("r", None, {}, set(), _is_bot, NOW, 30)
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


def test_report_rows_have_every_field():
    kq = _seed()
    repos = kq.repos_table()
    rows = KospexOrphans.report(kq, [*repos, "x~y~z"], repos, _is_bot)
    assert rows
    for row in rows:
        assert set(row) == set(KospexOrphans.FIELDS)


def test_unknown_active_in_is_an_error():
    with pytest.raises(ValueError):
        KospexOrphans.report(_seed(), [], {}, _is_bot, active_in="galaxy")


# --- the queries, against a seeded DB --------------------------------------------

def _owner(repo_id):
    """(server, owner) from a test repo_id; '~~' is a '/' in a nested group."""
    server, rest = repo_id.split("~", 1)
    return server, rest.rsplit("~", 1)[0].replace("~~", "/")


def _commit(repo_id, hash_value, author, author_days, committer="same", committer_days=None):
    committer_days = author_days if committer_days is None else committer_days
    server, owner = _owner(repo_id)
    return {"_repo_id": repo_id, "hash": hash_value,
            "_git_server": server, "_git_owner": owner,
            "author_email": author, "author_when": _ago(author_days),
            "committer_email": author if committer == "same" else committer,
            "committer_when": _ago(committer_days), "parents": 1, "_files": 1}


REPOS = [
    "github.com~acme~webmerged",
    "github.com~acme~rebased",
    "github.com~acme~botonly",
    "github.com~acme~quiet",
    "gitlab.com~beta~svc",
    # A GitLab nested group, and a sibling subgroup of it
    "gitlab.com~group~~old~legacy",
    "gitlab.com~group~~new~app",
]


def _seed(stale=False):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMITS)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    for repo_id in REPOS:
        server, owner = _owner(repo_id)
        repo = repo_id.rsplit("~", 1)[1]
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
        # moved from one subgroup to its sibling
        _commit("gitlab.com~group~~old~legacy", "l1", "mover@acme.com", 200),
        _commit("gitlab.com~group~~new~app", "n1", "mover@acme.com", 10),
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
    assert set(kq.repos_table(server="gitlab.com")) == {
        "gitlab.com~beta~svc", "gitlab.com~group~~old~legacy", "gitlab.com~group~~new~app"}
    assert set(kq.repos_table(id_params={"org_key": "gitlab.com~group~~old"})) == {
        "gitlab.com~group~~old~legacy"}
    assert set(kq.repos_table(id_params={"repo_id": "github.com~acme~quiet"})) == {
        "github.com~acme~quiet"}
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
        "gitlab.com~group~~old~legacy": "Knowledge",
        "gitlab.com~group~~new~app": "Knowledge",
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
    assert "not synced in" not in fresh
    stale = CliRunner().invoke(cli(stale=True), ["orphans"]).output
    assert "7 of 7 repos not synced in 30 days" in stale
    assert "kgit pull --all --stale 30" in stale
    scoped = CliRunner().invoke(cli(stale=True), ["orphans", "-server", "gitlab.com"]).output
    assert "kgit pull --server gitlab.com --stale 30" in scoped


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
    assert list(rows[0]) == KospexOrphans.FIELDS
    by_id = {r["repo_id"]: r for r in rows}
    assert by_id["github.com~acme~botonly"]["bots_active"] == "1"
    assert by_id["github.com~acme~webmerged"]["status"] == "Knowledge"


# --- -active-in: where "still working for us" is looked for --------------------

def _status(kq, repo_id, active_in):
    repos = kq.repos_table()
    rows = KospexOrphans.report(kq, list(repos), repos, _is_bot, active_in=active_in)
    found = {r["repo_id"]: r["status"] for r in rows}
    assert found, rows
    return found[repo_id]


@pytest.mark.parametrize("active_in, expected", [
    ("estate", "Knowledge"),    # dev@acme.com is active on gitlab.com
    ("server", "Orphaned"),     # ...but not on github.com
    ("org", "Orphaned"),
])
def test_active_in_scopes_who_is_still_here(active_in, expected):
    assert _status(_seed(), "github.com~acme~webmerged", active_in) == expected


@pytest.mark.parametrize("active_in, expected", [
    ("estate", "Knowledge"),
    ("server", "Knowledge"),
    # a nested group's org is the whole path: group/new is not group/old
    ("org", "Orphaned"),
])
def test_active_in_org_uses_the_kospex_org_key_for_nested_groups(active_in, expected):
    assert _status(_seed(), "gitlab.com~group~~old~legacy", active_in) == expected


def test_scope_key_matches_org_key():
    assert KospexOrphans.scope_key("org", "GitLab.com", "Group/Sub") == "gitlab.com~group~~sub"
    assert KospexOrphans.scope_key("server", "GitLab.com", "x") == "gitlab.com"
    assert KospexOrphans.scope_key("estate", "a", "b") is None


def test_cli_active_in(cli):
    result = CliRunner().invoke(cli(), ["orphans", "-active-in", "org"])
    assert result.exit_code == 0, result.output
    assert _statuses(result.output)["github.com~acme~webmerged"] == "Orphaned"
    bad = CliRunner().invoke(cli(), ["orphans", "-active-in", "galaxy"])
    assert bad.exit_code != 0


# --- kweb /orphans/: the same rows as the CLI -------------------------------------

def test_get_orphans_matches_the_cli_rows():
    kq = _seed()
    repos = kq.repos_table()
    for active_in in KospexOrphans.ACTIVE_IN:
        web = kq.get_orphans(id={}, active_in=active_in, is_bot=_is_bot)
        cli_rows = KospexOrphans.report(kq, list(repos), repos, _is_bot, active_in=active_in)
        assert web and web == cli_rows


def test_get_orphans_scopes_to_an_org():
    kq = _seed()
    rows = kq.get_orphans(id={"org_key": "github.com~acme"}, is_bot=_is_bot)
    assert {r["repo_id"] for r in rows} == {
        "github.com~acme~webmerged", "github.com~acme~rebased",
        "github.com~acme~botonly", "github.com~acme~quiet"}
    # active is still estate-wide by default
    assert {r["repo_id"]: r["status"] for r in rows}["github.com~acme~webmerged"] == "Knowledge"


def _request(path, query_string=b""):
    from starlette.requests import Request
    import kweb2
    return Request({
        "type": "http", "method": "GET", "path": path, "server": ("localhost", 8000),
        "scheme": "http", "headers": [], "query_string": query_string, "app": kweb2.app,
    })


@pytest.fixture
def kweb(monkeypatch):
    import kweb2
    kq = _seed()
    monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: kq)
    monkeypatch.setattr(KospexAuthorship, "bot_checker", lambda: _is_bot)
    kweb2.request_cache.clear()
    return kweb2


def test_page_renders_statuses_bots_and_scope_choice(kweb):
    response = kweb.orphans(_request("/orphans/"), id=None, active_in="org")
    assert response.status_code == 200
    html = response.body.decode()
    for text in ("Orphaned", "Knowledge", "Dormant", "Bots active", "Last sync",
                 "explain column headings and table", "kgit pull --all --stale 30",
                 "gitlab.com~group~~old~legacy",
                 '<option value="org" selected>'):
        assert text in html, text


def test_page_refuses_an_unknown_active_in(kweb):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as err:
        kweb.orphans(_request("/orphans/"), id=None, active_in="galaxy")
    assert err.value.status_code == 400


def test_summary_leaves_out_empty_statuses_without_a_gap():
    rows = [{"status": "Orphaned"}, {"status": "Dormant"}]
    assert KospexOrphans.summary(rows) == "Orphaned: 1 | Knowledge: 0 | Dormant: 1 | Total: 2"


@pytest.mark.parametrize("scope, expected", [
    ({}, "kgit pull --all --stale 30"),
    ({"org_key": "gitlab.com~group~~sub"}, "kgit pull --org gitlab.com~group~~sub --stale 30"),
    ({"server": "github.com"}, "kgit pull --server github.com --stale 30"),
    ({"repo_id": "github.com~acme~api"}, "kgit pull github.com~acme~api"),
])
def test_sync_command_always_has_a_scope(scope, expected):
    assert KospexOrphans.sync_command(30, **scope) == expected


def test_page_sync_command_follows_the_scope(kweb):
    html = kweb.orphans(_request("/orphans/github.com~acme"), id="github.com~acme").body.decode()
    assert "kgit pull --org github.com~acme --stale 30" in html
