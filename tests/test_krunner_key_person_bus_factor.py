"""`krunner key-person` and `krunner bus-factor`.

Both run over a scope (REPO_ID | -all | -org | -server) from one grouped query,
KospexQuery.author_commit_aggregates(), and turn its rows into CSV rows with
the pure functions in kospex.authorship. Each shows a table by default;
-out FILENAME also writes the CSV, and -stdout writes only the CSV.

The CSV headers are a contract with downstream processes, so their order is
pinned here as literal lists.

Design: changes/202611-krunner-key-person-bus-factor.md
"""
import csv
import io
import os
from datetime import datetime, timedelta, timezone

import pytest
import sqlite_utils
from click.testing import CliRunner

import kospex.authorship as KospexAuthorship
import kospex_schema as KospexSchema
from kospex_query import KospexQuery


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    keys = ("KOSPEX_CODE", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_HOME", "KOSPEX_LOGS")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


GENERATED = "2026-11-13T00:00:00Z"
NOT_BOT = lambda email: False  # noqa: E731
BOT = "dependabot[bot]@users.noreply.github.com"


def _is_bot(email):
    return email == BOT


def _author(author, commits, active=0, merges=0,
            first="2024-01-01T00:00:00Z", last="2026-01-01T00:00:00Z"):
    return {"author": author, "commits": commits, "merges": merges,
            "active_commits": active, "first_commit": first, "last_commit": last}


REPO = {"_repo_id": "gitlab.com~group~~sub~svc", "_git_server": "gitlab.com",
        "_git_owner": "group/sub", "_git_repo": "svc"}


# --- thresholds ----------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("50", [50]),
    ("80,50", [50, 80]),
    ("50,50", [50]),
    ("50, 80 ,90", [50, 80, 90]),
    ("1,100", [1, 100]),
])
def test_parse_thresholds_accepts(value, expected):
    assert KospexAuthorship.parse_thresholds(value) == expected


@pytest.mark.parametrize("value", ["50,60,70,80", "0", "101", "50.5", "-5", "", "50,", "abc"])
def test_parse_thresholds_refuses(value):
    with pytest.raises(ValueError):
        KospexAuthorship.parse_thresholds(value)


# --- bus factor ----------------------------------------------------------------

@pytest.mark.parametrize("counts,thresholds,expected", [
    ([], [50], {50: None}),                         # undefined, not 0
    ([0, 0], [50, 80], {50: None, 80: None}),
    ([10], [50, 100], {50: 1, 100: 1}),
    ([5, 5], [50], {50: 1}),                        # exactly hitting the threshold counts
    ([1, 1, 1], [50], {50: 2}),                     # 1/3 < 50%, 2/3 >= 50%
    ([1, 1, 1, 1], [50, 51, 100], {50: 2, 51: 3, 100: 4}),
    ([2, 7, 1], [50, 80, 90], {50: 1, 80: 2, 90: 2}),  # order of input does not matter
])
def test_bus_factors(counts, thresholds, expected):
    assert KospexAuthorship.bus_factors(counts, thresholds) == expected


BUS_FACTOR_FIELDS_50_80 = [
    "repo_id", "git_server", "git_owner", "git_repo", "active_days",
    "authors", "commits",
    "bus_factor_50", "bus_factor_80", "human_bus_factor_50", "human_bus_factor_80",
    "bot_commits", "bot_commit_pct", "top_author", "top_author_pct",
    "active_authors", "active_commits",
    "active_bus_factor_50", "active_bus_factor_80",
    "active_human_bus_factor_50", "active_human_bus_factor_80",
    "active_top_author", "active_top_author_pct", "last_commit", "generated_at",
]


def test_bus_factor_header_is_pinned():
    assert KospexAuthorship.bus_factor_fields([50, 80]) == BUS_FACTOR_FIELDS_50_80


@pytest.mark.parametrize("thresholds", [[50], [50, 80], [50, 80, 90]])
def test_bus_factor_header_has_two_columns_per_threshold_per_window(thresholds):
    fields = KospexAuthorship.bus_factor_fields(thresholds)
    assert len(fields) == 17 + 4 * len(thresholds)
    assert len(set(fields)) == len(fields)


def test_bus_factor_row_separates_bots():
    authors = [_author(BOT, 60, active=30), _author("a@x.com", 30, active=0),
               _author("b@x.com", 10, active=5, last="2026-11-01T00:00:00Z")]

    row = KospexAuthorship.bus_factor_row(REPO, authors, [50, 80], 90, GENERATED, _is_bot)

    assert list(row) == BUS_FACTOR_FIELDS_50_80
    assert row["repo_id"] == "gitlab.com~group~~sub~svc"
    assert row["git_owner"] == "group/sub"   # from the repos row, not split from repo_id
    assert row["commits"] == 100 and row["authors"] == 3
    assert row["bus_factor_50"] == 1          # the bot alone has 60%
    assert row["human_bus_factor_50"] == 1    # a@ has 30 of the 40 human commits
    assert row["human_bus_factor_80"] == 2
    assert row["bot_commits"] == 60 and row["bot_commit_pct"] == 60.0
    assert (row["top_author"], row["top_author_pct"]) == (BOT, 60.0)
    assert row["active_authors"] == 2 and row["active_commits"] == 35
    assert row["active_human_bus_factor_50"] == 1
    assert row["last_commit"] == "2026-11-01T00:00:00Z"
    assert row["generated_at"] == GENERATED


def test_bus_factor_row_with_no_active_commits_is_blank_not_zero():
    row = KospexAuthorship.bus_factor_row(REPO, [_author("a@x.com", 3)], [50], 90,
                                          GENERATED, NOT_BOT)
    assert row["bus_factor_50"] == 1
    assert row["active_commits"] == 0
    assert row["active_bus_factor_50"] == ""
    assert row["active_human_bus_factor_50"] == ""
    assert row["active_top_author"] == "" and row["active_top_author_pct"] == ""


def test_bus_factor_row_with_no_commits_at_all():
    row = KospexAuthorship.bus_factor_row(REPO, [], [50], 90, GENERATED, NOT_BOT)
    assert row["commits"] == 0 and row["bus_factor_50"] == ""
    assert row["bot_commit_pct"] == "" and row["last_commit"] == ""


# --- key person ----------------------------------------------------------------

KEY_PERSON_FIELDS = [
    "repo_id", "git_server", "git_owner", "git_repo",
    "rank", "author", "is_bot",
    "commits", "merges", "pct_commits",
    "active_commits", "pct_active",
    "in_top_all_time", "in_top_active",
    "first_commit", "last_commit", "tenure_years",
    "repo_commits", "repo_active_commits",
    "active_days", "generated_at",
]


def test_key_person_header_is_pinned():
    assert KospexAuthorship.KEY_PERSON_FIELDS == KEY_PERSON_FIELDS


AUTHORS = [_author("a@x.com", 50, active=0), _author("b@x.com", 30, active=1),
           _author("c@x.com", 15, active=0), _author("d@x.com", 5, active=9, merges=2)]


def test_key_person_top_adds_active_authors_not_already_listed():
    rows = KospexAuthorship.key_person_rows(REPO, AUTHORS, 2, 90, GENERATED, NOT_BOT)

    assert [r["author"] for r in rows] == ["a@x.com", "b@x.com", "d@x.com"]
    assert all(list(r) == KEY_PERSON_FIELDS for r in rows)
    d = rows[2]
    assert d["rank"] == 4 and d["merges"] == 2
    assert (d["in_top_all_time"], d["in_top_active"]) == (0, 1)
    assert d["pct_commits"] == 5.0 and d["pct_active"] == 90.0
    assert rows[0]["in_top_active"] == 0     # no active commits, so not an active author
    assert rows[0]["repo_commits"] == 100 and rows[0]["repo_active_commits"] == 10


def test_key_person_all_authors():
    rows = KospexAuthorship.key_person_rows(REPO, AUTHORS, None, 90, GENERATED, NOT_BOT)
    assert [r["rank"] for r in rows] == [1, 2, 3, 4]


def test_key_person_ties_are_deterministic():
    authors = [_author("z@x.com", 5), _author("a@x.com", 5)]
    rows = KospexAuthorship.key_person_rows(REPO, authors, 1, 90, GENERATED, NOT_BOT)
    assert [r["author"] for r in rows] == ["a@x.com"]


def test_key_person_no_active_commits_leaves_pct_active_blank():
    rows = KospexAuthorship.key_person_rows(REPO, [_author("a@x.com", 3)], 4, 90,
                                            GENERATED, NOT_BOT)
    assert rows[0]["pct_active"] == ""


def test_key_person_flags_bots_and_keeps_them():
    rows = KospexAuthorship.key_person_rows(REPO, [_author(BOT, 3)], 4, 90, GENERATED, _is_bot)
    assert rows[0]["is_bot"] == 1


def test_key_person_no_commits_gives_no_rows():
    assert KospexAuthorship.key_person_rows(REPO, [], 4, 90, GENERATED, NOT_BOT) == []


# --- the query, against a seeded DB ---------------------------------------------

def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _commit(repo_id, hash_value, author, days_ago, parents=1, files=1):
    return {"_repo_id": repo_id, "hash": hash_value, "author_email": author,
            "author_when": _iso(days_ago), "committer_when": _iso(days_ago),
            "parents": parents, "_files": files}


REPOS = {
    "github.com~acme~api": ("github.com", "acme", "api"),
    "github.com~acme~web": ("github.com", "acme", "web"),
    "gitlab.com~beta~svc": ("gitlab.com", "beta", "svc"),
}


def _seed():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMITS)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    for repo_id, (server, owner, repo) in REPOS.items():
        db[KospexSchema.TBL_REPOS].insert({
            "_repo_id": repo_id, "_git_server": server, "_git_owner": owner,
            "_git_repo": repo, "first_seen": _iso(400), "last_seen": _iso(1)})
    rows = [
        _commit("github.com~acme~api", "a1", "lead@acme.com", 300),
        _commit("github.com~acme~api", "a2", "lead@acme.com", 200),
        _commit("github.com~acme~api", "a3", "lead@acme.com", 10),
        _commit("github.com~acme~api", "a4", "dev@acme.com", 5),
        _commit("github.com~acme~api", "m1", "lead@acme.com", 4, parents=2, files=0),
        _commit("github.com~acme~api", "b1", BOT, 3),
        _commit("github.com~acme~web", "w1", "dev@acme.com", 500),
        # gitlab.com~beta~svc has no commits
    ]
    db[KospexSchema.TBL_COMMITS].insert_all(rows, pk=["_repo_id", "hash"])
    return KospexQuery(kospex_db=db)


def test_aggregates_count_authorship_merges_and_the_active_window():
    kq = _seed()
    got = kq.author_commit_aggregates(list(REPOS), days=90)

    assert got["gitlab.com~beta~svc"] == []
    api = {r["author"]: r for r in got["github.com~acme~api"]}
    assert (api["lead@acme.com"]["commits"], api["lead@acme.com"]["merges"],
            api["lead@acme.com"]["active_commits"]) == (3, 1, 1)
    assert api["dev@acme.com"]["active_commits"] == 1
    assert api["lead@acme.com"]["first_commit"].endswith("Z")
    assert got["github.com~acme~web"][0]["active_commits"] == 0


def test_aggregates_chunk_large_scopes():
    kq = _seed()
    got = kq.author_commit_aggregates(list(REPOS), days=90, chunk_size=1)
    assert len(got["github.com~acme~api"]) == 3 and len(got["github.com~acme~web"]) == 1


def test_key_person_rows_agree_with_kospex_key_person():
    """`kospex key-person` and `krunner key-person` must not drift apart."""
    kq = _seed()
    repo_id = "github.com~acme~api"
    authors = kq.author_commit_aggregates([repo_id], days=90)[repo_id]
    rows = KospexAuthorship.key_person_rows({"_repo_id": repo_id}, authors, 4, 90,
                                            GENERATED, NOT_BOT)
    existing = {a["author"]: a for a in kq.key_person(repo_id=repo_id, top=4)}

    assert rows and len(rows) == len(existing)
    for r in rows:
        e = existing[r["author"]]
        assert (r["commits"], r["merges"], r["active_commits"]) == (
            e["commits"], e["merges"], e["active_commits"])
        assert f"{r['pct_commits']:.1f}%" == e["% commits"]
        assert f"{r['tenure_years']:.3f}" == e["tenure"]
        assert r["first_commit"].split("T")[0] == e["first_commit"]


def test_key_person_honours_days():
    kq = _seed()
    repo_id = "github.com~acme~api"
    lead = {a["author"]: a for a in kq.key_person(repo_id=repo_id, days=250)}["lead@acme.com"]
    assert lead["active_commits"] == 2   # 200 and 10 days ago; was always 90 days


# --- the CLI ---------------------------------------------------------------------

@pytest.fixture
def krunner(monkeypatch):
    import krunner as krunner_module
    monkeypatch.setattr(krunner_module.kospex, "kospex_query", _seed())
    return krunner_module


def _invoke(krunner, *args):
    return CliRunner().invoke(krunner.cli, list(args))


def _csv(text):
    return list(csv.DictReader(io.StringIO(text)))


@pytest.mark.parametrize("command", ["key-person", "bus-factor"])
def test_scope_is_required(krunner, command):
    result = _invoke(krunner, command)
    assert result.exit_code == 2
    assert "exactly one scope" in result.output


@pytest.mark.parametrize("command", ["key-person", "bus-factor"])
def test_two_scopes_are_refused(krunner, command):
    result = _invoke(krunner, command, "-all", "-server", "github.com")
    assert result.exit_code == 2


@pytest.mark.parametrize("value,hint", [
    ("github.com", "-server github.com"),
    ("github.com~acme", "-org github.com~acme"),
])
def test_positional_server_or_org_gets_a_hint(krunner, value, hint):
    result = _invoke(krunner, "key-person", value)
    assert result.exit_code == 2
    assert hint in result.output


def test_unknown_repo_id_is_an_error(krunner):
    result = _invoke(krunner, "bus-factor", "github.com~acme~nope")
    assert result.exit_code == 2
    assert "not a repo_id in the kospex DB" in result.output


def test_empty_org_is_an_error(krunner):
    result = _invoke(krunner, "bus-factor", "-org", "github.com~nobody")
    assert result.exit_code == 2
    assert "No repos in scope" in result.output


@pytest.mark.parametrize("command", ["key-person", "bus-factor"])
def test_out_and_stdout_are_exclusive(krunner, command, tmp_path):
    result = _invoke(krunner, command, "-all", "-stdout", "-out", str(tmp_path / "x.csv"))
    assert result.exit_code == 2


def test_out_refuses_to_overwrite(krunner, tmp_path):
    out = tmp_path / "bf.csv"
    out.write_text("keep me")
    result = _invoke(krunner, "bus-factor", "-all", "-out", str(out))
    assert result.exit_code == 2
    assert out.read_text() == "keep me"

    result = _invoke(krunner, "bus-factor", "-all", "-out", str(out), "-overwrite")
    assert result.exit_code == 0, result.output
    assert len(_csv(out.read_text())) == 3


def test_overwrite_without_out_is_refused(krunner):
    assert _invoke(krunner, "bus-factor", "-all", "-overwrite").exit_code == 2


def test_too_many_thresholds_is_a_usage_error(krunner):
    result = _invoke(krunner, "bus-factor", "-all", "-threshold", "50,60,70,80")
    assert result.exit_code == 2
    assert "At most 3" in result.output


def test_bus_factor_stdout_is_only_csv(krunner):
    result = CliRunner().invoke(
        krunner.cli, ["bus-factor", "-org", "github.com~acme", "-threshold", "50,80", "-stdout"])

    assert result.exit_code == 0, result.stderr
    rows = _csv(result.stdout)
    assert list(rows[0]) == BUS_FACTOR_FIELDS_50_80
    assert sorted(r["repo_id"] for r in rows) == ["github.com~acme~api", "github.com~acme~web"]
    api = next(r for r in rows if r["repo_id"] == "github.com~acme~api")
    assert api["commits"] == "5" and api["bot_commits"] == "1"
    assert "repos in scope" in result.stderr


def test_key_person_stdout_is_only_csv(krunner):
    result = CliRunner().invoke(
        krunner.cli, ["key-person", "-all", "-stdout"])

    assert result.exit_code == 0, result.stderr
    rows = _csv(result.stdout)
    assert list(rows[0]) == KEY_PERSON_FIELDS
    assert {r["repo_id"] for r in rows} == {"github.com~acme~api", "github.com~acme~web"}
    assert "1 with no commits" in result.stderr


def test_key_person_stdout_with_no_commits_is_header_only(krunner):
    result = CliRunner().invoke(
        krunner.cli, ["key-person", "gitlab.com~beta~svc", "-stdout"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == ",".join(KEY_PERSON_FIELDS)


def test_default_is_a_table_and_writes_no_file(krunner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _invoke(krunner, "bus-factor", "-all", "-threshold", "50,80")
    assert result.exit_code == 0, result.output
    assert "bf 80%" in result.output and "github.com~acme~api" in result.output
    assert "repo_id,git_server" not in result.output
    assert list(tmp_path.iterdir()) == []


def test_table_shows_author_markup_literally(krunner, monkeypatch):
    """Authors are git metadata; Rich must not parse them as markup (CWE-150)."""
    kq = _seed()
    kq.kospex_db[KospexSchema.TBL_COMMITS].insert(
        _commit("github.com~acme~web", "x1", "[bold]x[/]@acme.com", 1),
        pk=["_repo_id", "hash"])
    monkeypatch.setattr(krunner.kospex, "kospex_query", kq)
    result = _invoke(krunner, "key-person", "github.com~acme~web")
    assert result.exit_code == 0, result.output
    assert "[bold]x[/]@acme.com" in result.output
