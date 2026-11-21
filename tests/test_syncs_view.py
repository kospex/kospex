"""The data behind kweb's /syncs/ (kospex.syncs_view).

The page shows a command next to a table; these pin that the two always agree
with each other and with kgit pull --stale, and that the Org syncs tab reads
runs, their recorded commands and listings without failing on a DB that is
behind or a command that never finished.
"""
from datetime import datetime, timedelta, timezone

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
import kospex.org_sync_manifest as M
from kospex import staleness
from kospex import syncs_view as V

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)


def _ago(days):
    return (NOW - timedelta(days=days)).isoformat()


def _repo(server, owner, name, fetched=None, synced=None):
    return {"_repo_id": f"{server}~{owner.replace('/', '~~')}~{name}" if owner
            else f"{server}~{name}", "_git_server": server, "_git_owner": owner,
            "_git_repo": name, "last_fetch": fetched, "last_sync": synced}


REPOS = [
    _repo("github.com", "acme", "fresh", fetched=_ago(1)),
    _repo("github.com", "acme", "old", fetched=_ago(40)),
    _repo("github.com", "jshttp", "synced-only", synced=_ago(60)),
    _repo("github.com", "jshttp", "never"),
    _repo("go.googlesource.com", "", "tools", synced=_ago(90)),
    _repo("gitlab.example.com", "acme/platform", "api", fetched=_ago(10)),
]


# --- params ------------------------------------------------------------------

def test_defaults():
    assert V.parse_params(None, None, None, None) == {
        "tab": "repos", "org": None, "server": None, "days": 30}


def test_all_days_is_none():
    assert V.parse_params("orgs", "github.com~acme", None, "all")["days"] is None


@pytest.mark.parametrize("tab, org, server, days", [
    ("other", None, None, None),
    (None, "github.com~acme", "github.com", None),
    (None, "not-an-org-key", None, None),
    (None, None, None, "0"),
    (None, None, None, "-1"),
    (None, None, None, "soon"),
])
def test_bad_params_raise(tab, org, server, days):
    with pytest.raises(ValueError):
        V.parse_params(tab, org, server, days)


# --- the command -------------------------------------------------------------

@pytest.mark.parametrize("kw, expected", [
    (dict(org=None, server=None, days=30), "kgit pull --all --stale 30"),
    # Org keys are quoted: zsh with extendedglob reads a ~ inside a word as a
    # glob operator.
    (dict(org="github.com~jshttp", server=None, days=7),
     "kgit pull --org 'github.com~jshttp' --stale 7"),
    (dict(org=None, server="go.googlesource.com", days=1),
     "kgit pull --server go.googlesource.com --stale 1"),
    (dict(org="gitlab.example.com~acme~~platform", server=None, days=None),
     "kgit pull --org 'gitlab.example.com~acme~~platform'"),
    (dict(org=None, server=None, days=30, check=True), "kgit pull --check --all --stale 30"),
])
def test_pull_command(kw, expected):
    assert V.pull_command(**kw) == expected


def test_pull_command_quotes_what_it_interpolates():
    # Review focus: a value from the DB that a shell would read as more.
    assert V.pull_command(org="gitlab.example.com~a;rm -rf ~~b", server=None, days=7) == (
        "kgit pull --org 'gitlab.example.com~a;rm -rf ~~b' --stale 7")


# --- the Repos tab -----------------------------------------------------------

def test_repo_view_matches_kgit_pull_stale():
    view = V.repo_view(REPOS, org=None, server=None, days=30, now=NOW)
    expected = [r["_repo_id"] for r in REPOS if staleness.is_stale(r, 30, NOW)]
    assert sorted(row["repo_id"] for row in view["rows"]) == sorted(expected)
    assert (view["stale"], view["total"]) == (4, 6)
    assert "github.com~jshttp~never" in [row["repo_id"] for row in view["rows"]]
    assert view["fell_back"] == 2
    assert view["command"] == "kgit pull --all --stale 30"
    assert view["check_command"] == "kgit pull --check --all --stale 30"


def test_repo_view_for_an_org_and_a_server():
    view = V.repo_view(REPOS, org="github.com~acme", server=None, days=30, now=NOW)
    assert [row["repo_id"] for row in view["rows"]] == ["github.com~acme~old"]
    assert view["total"] == 2
    view = V.repo_view(REPOS, org=None, server="go.googlesource.com", days=30, now=NOW)
    assert [row["repo_id"] for row in view["rows"]] == ["go.googlesource.com~tools"]


def test_repo_view_all_days_lists_every_repo():
    view = V.repo_view(REPOS, org=None, server=None, days=None, now=NOW)
    assert len(view["rows"]) == 6
    assert view["command"] == "kgit pull --all"


@pytest.mark.parametrize("days, label", [
    (10, "Active"), (90, "Active"), (91, "Aging"), (181, "Stale"), (366, "Dormant"),
])
def test_repo_status_comes_from_the_last_commit(days, label):
    repo = _repo("github.com", "acme", "x", fetched=_ago(1)) | {"last_seen": _ago(days)}
    [row] = V.repo_view([repo], org=None, server=None, days=None, now=NOW)["rows"]
    assert row["status"] == label


def test_repo_status_without_a_commit_is_unknown():
    [row] = V.repo_view([_repo("github.com", "acme", "x", fetched=_ago(1))],
                        org=None, server=None, days=None, now=NOW)["rows"]
    assert row["status"] == "Unknown"


def test_repo_statuses_sort_from_active_to_unknown():
    ranks = [V.STATUS_BADGES[s][0] for s in ("Active", "Aging", "Stale", "Dormant", "Unknown")]
    assert ranks == sorted(ranks)


def test_picker():
    servers = {s["server"]: s for s in V.picker(REPOS, 30, NOW)}
    assert [s["server"] for s in V.picker(REPOS, 30, NOW)] == [
        "github.com", "gitlab.example.com", "go.googlesource.com"]
    github = servers["github.com"]
    assert (github["repos"], github["stale"]) == (4, 3)
    assert [(o["org_key"], o["label"], o["repos"], o["stale"]) for o in github["orgs"]] == [
        ("github.com~acme", "acme", 2, 1), ("github.com~jshttp", "jshttp", 2, 2)]
    assert servers["go.googlesource.com"]["orgs"] == []
    assert servers["go.googlesource.com"]["repos"] == 1
    [nested] = servers["gitlab.example.com"]["orgs"]
    assert (nested["org_key"], nested["label"]) == (
        "gitlab.example.com~acme~~platform", "acme/platform")


# --- the Org syncs tab -------------------------------------------------------

def _db():
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(memory=True)
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    Migrator(db).apply_pending()
    return db


ORG = "github.com~acme"
AUTH = {"login": "octocat", "kind": "fine-grained", "source": "GH_TOKEN",
        "expires_at": "2027-05-20T00:00:00Z"}


def _item(name, **kw):
    return {"_repo_id": f"{ORG}~{name}", "clone_url": f"https://github.com/acme/{name}.git",
            "ssh_url": f"git@github.com:acme/{name}.git",
            "provider_repo_id": f"github.com:{name}", "archived": False, "fork": False,
            "private": False, "visibility": "public", "pushed_at": "2026-11-01T00:00:00Z",
            **kw}


def _command(db, run_id, options, finished=True, auth=AUTH, started="2026-11-15T01:00:00Z"):
    command_id = M.start_command(db, ORG, f"kgit sync --org acme {options}".strip(),
                                 options, auth, now=started)
    M.finish_command(db, command_id, run_id=run_id, listing=None, synced_any=True,
                     synced=1, failed=0, skipped=0, new=None, gone=None, finished=finished)


def test_org_runs_unfinished_run_continues_with_its_options():
    db = _db()
    run_id = M.save_listing(db, ORG, [_item("a"), _item("b", archived=True),
                                      _item("c", fork=True)])
    _command(db, run_id, "-inventory -ssh -no-auth", started="2026-11-15T01:00:00Z")
    _command(db, run_id, "-next 1 -retry-failed -ssh -no-auth",
             started="2026-11-15T02:00:00Z")
    data = V.org_runs(db, org=None, server=None, now=NOW)
    assert data["available"] is True
    [run] = data["runs"]
    assert run["run_id"] == run_id
    assert run["continue_command"] == "kgit sync --org acme -next 1 -ssh -no-auth"
    assert (run["archived"], run["forks"]) == (1, 1)
    assert [c["state"] for c in run["commands"]] == ["finished", "finished"]
    assert run["commands"][0]["token_text"] == (
        "octocat · fine-grained · GH_TOKEN · expires 2027-05-20")
    assert len(run["listings"]) == 1


def test_command_duration():
    db = _db()
    run_id = M.save_listing(db, ORG, [_item("a")])
    command_id = M.start_command(db, ORG, "kgit sync --org acme", "", AUTH,
                                 now="2026-11-15T01:00:00Z")
    M.finish_command(db, command_id, run_id=run_id, listing=None, synced_any=True, synced=1,
                     failed=0, skipped=0, new=None, gone=None, now="2026-11-15T01:36:05Z")
    stopped = M.start_command(db, ORG, "kgit sync --org acme", "", AUTH,
                              now="2026-11-15T02:00:00Z")
    M.finish_command(db, stopped, run_id=run_id, listing=None, synced_any=True, synced=0,
                     failed=0, skipped=0, new=None, gone=None, finished=False)
    [run] = V.org_runs(db, org=None, server=None, now=NOW)["runs"]
    assert [c["took"] for c in run["commands"]] == ["36m", ""]


@pytest.mark.parametrize("seconds, text", [(3, "3s"), (59, "59s"), (60, "1m"),
                                           (3599, "59m"), (3600, "1h 0m"), (5400, "1h 30m")])
def test_duration_text(seconds, text):
    assert V.duration_text(seconds) == text


def test_picker_has_no_stale_count_when_showing_all():
    servers = V.picker(REPOS, None, NOW)
    assert all(s["stale"] is None for s in servers)
    assert all(o["stale"] is None for s in servers for o in s["orgs"])


def test_org_runs_without_commands_continue_plainly():
    db = _db()
    M.save_listing(db, ORG, [_item("a")])
    [run] = V.org_runs(db, org=ORG, server=None, now=NOW)["runs"]
    assert run["continue_command"] == "kgit sync --org acme"
    assert run["commands"] == []


def test_a_finished_run_has_no_continue_command():
    db = _db()
    run_id = M.save_listing(db, ORG, [_item("a")])
    M.close_run(db, run_id)
    [run] = V.org_runs(db, org=None, server=None, now=NOW)["runs"]
    assert run["continue_command"] is None


def test_a_stopped_command_shows_as_stopped_or_running():
    # Review focus: finished_at NULL with counts unknown must not break the page.
    db = _db()
    run_id = M.save_listing(db, ORG, [_item("a")])
    command_id = M.start_command(db, ORG, "kgit sync --org acme", "", AUTH)
    M.finish_command(db, command_id, run_id=run_id, listing=None, synced_any=True,
                     synced=None, failed=None, skipped=None, new=None, gone=None,
                     finished=False)
    [run] = V.org_runs(db, org=None, server=None, now=NOW)["runs"]
    assert run["commands"][0]["state"] == "stopped or running"


@pytest.mark.parametrize("expires_at, expiring", [
    ("2026-11-25T00:00:00Z", True),      # 10 days away
    ("2026-11-01T00:00:00Z", True),      # already expired
    ("2027-05-20T00:00:00Z", False),
    (None, False),
])
def test_expiring_tokens_are_flagged(expires_at, expiring):
    db = _db()
    run_id = M.save_listing(db, ORG, [_item("a")])
    _command(db, run_id, "", auth={**AUTH, "expires_at": expires_at})
    [run] = V.org_runs(db, org=None, server=None, now=NOW)["runs"]
    assert run["commands"][0]["expiring"] is expiring


def test_no_auth_token_text():
    db = _db()
    run_id = M.save_listing(db, ORG, [_item("a")])
    _command(db, run_id, "-no-auth",
             auth={"login": None, "kind": "none", "source": None, "expires_at": None})
    [run] = V.org_runs(db, org=None, server=None, now=NOW)["runs"]
    assert run["commands"][0]["token_text"] == "no auth"


def test_org_runs_filter_by_org_and_server():
    db = _db()
    M.save_listing(db, ORG, [_item("a")])
    M.save_listing(db, "github.com~other", [_item("z")])
    assert len(V.org_runs(db, org=ORG, server=None, now=NOW)["runs"]) == 1
    assert len(V.org_runs(db, org=None, server="github.com", now=NOW)["runs"]) == 2
    assert V.org_runs(db, org=None, server="gitlab.com", now=NOW)["runs"] == []


def test_a_db_behind_0013_is_not_available():
    # Review focus: kweb never migrates; the page must explain, not fail.
    db = _db()
    db.execute(f"DROP TABLE {M.TBL_ORG_SYNC_COMMANDS}")
    db.execute(f"DROP TABLE {M.TBL_ORG_SYNC_LISTINGS}")
    assert V.org_runs(db, org=None, server=None, now=NOW) == {"available": False,
                                                              "runs": []}


# --- one run: /syncs/run/{run_id} --------------------------------------------

def _run_db():
    """A run of four items in various states, plus kospex repos for drift."""
    db = _db()
    db["repos"].insert_all([
        {"_repo_id": f"{ORG}~a", "_git_server": "github.com", "_git_owner": "acme",
         "_git_repo": "a"},
        {"_repo_id": f"{ORG}~old", "_git_server": "github.com", "_git_owner": "acme",
         "_git_repo": "old"},
        {"_repo_id": "github.com~other~x", "_git_server": "github.com",
         "_git_owner": "other", "_git_repo": "x"},
    ], pk="_repo_id", alter=True)
    run_id = M.save_listing(db, ORG, [
        _item("a"),
        _item("b", private=True, visibility="private"),
        _item("c", fork=True, fork_parent="up/c", fork_source="root/c"),
        _item("d", fork=True, fork_parent="up/d", fork_source="up/d"),
    ])
    M.mark(db, run_id, f"{ORG}~a", M.SYNCED)
    M.mark(db, run_id, f"{ORG}~b", M.FAILED, "clone failed: auth")
    M.mark(db, run_id, f"{ORG}~c", M.SKIPPED, "fork")
    return db, run_id


def test_run_view_header_items_and_counts():
    db, run_id = _run_db()
    view = V.run_view(db, run_id)
    assert view["available"] is True
    assert view["run"]["org_key"] == ORG and view["owner"] == "acme"
    assert view["counts"]["failed"] == 1 and view["counts"]["pending"] == 1
    assert view["filters"] == [("failed", 1), ("skipped", 1), ("pending", 1), ("synced", 1)]
    items = {i["_repo_id"].split("~")[-1]: i for i in view["rows"]}
    assert list(items) == ["a", "b", "c", "d"]
    assert items["a"]["in_kospex"] == 1 and items["b"]["in_kospex"] == 0
    assert items["b"]["detail"] == "clone failed: auth" and items["b"]["private"] == 1
    assert items["a"]["pushed_date"] == "2026-11-01"
    # The source shows only when it isn't the parent.
    assert items["c"]["fork_source_shown"] == "root/c"
    assert items["d"]["fork_source_shown"] is None
    assert view["continue_command"] == "kgit sync --org acme"


def test_run_view_drift_and_visibility_come_from_inventory():
    db, run_id = _run_db()
    view = V.run_view(db, run_id)
    # `old` is in kospex under acme but not listed; `other~x` is another org.
    assert view["drift"] == M.inventory(db, run_id)["gone"] == [f"{ORG}~old"]
    assert view["visibility_banner"] == "This run includes 1 private repo out of 4."
    assert view["visibility_note"] is None


def test_run_view_gone_items_are_shown_but_not_counted_as_listed():
    db, run_id = _run_db()
    M.refresh_listing(db, run_id, [_item("a"), _item("c", fork=True), _item("d", fork=True)])
    view = V.run_view(db, run_id)
    assert [i["_repo_id"] for i in view["rows"] if i["gone"]] == [f"{ORG}~b"]
    assert ("gone", 1) in view["filters"]
    # b was the private one, and isn't in the current listing any more.
    assert view["visibility_banner"] is None and view["listed"] == 3


@pytest.mark.parametrize("counts, text", [
    ({"internal": 2500}, "This run includes 2,500 internal repos out of 2,500."),
    ({"private": 1}, "This run includes 1 private repo out of 2,500."),
    ({"internal": 3, "private": 1},
     "This run includes 3 internal and 1 private repos out of 2,500."),
    ({"internal": 3, "private": 1, "visibility_other": 2},
     "This run includes 3 internal, 1 private and 2 other non-public repos out of 2,500."),
    ({}, None),
])
def test_visibility_banner(counts, text):
    inventory = {"internal": 0, "private": 0, "visibility_other": 0, "listed": 2500, **counts}
    assert V.visibility_banner(inventory) == text


def test_visibility_note_carries_the_options_that_decide_the_listing():
    db, run_id = _run_db()
    db.execute(f"UPDATE {M.TBL_ORG_SYNC_ITEMS} SET visibility = NULL WHERE run_id = ?",
               [run_id])
    _command(db, run_id, "-active 90 -next 10 -skip-forks -no-auth")
    note = V.run_view(db, run_id)["visibility_note"]
    assert note["unknown"] == 4 and note["finished"] is False
    # -inventory refuses -active and -next, so they're dropped.
    assert note["command"] == "kgit sync --org acme -inventory -refresh -skip-forks -no-auth"


def test_run_view_on_a_db_behind_0014():
    db = _db()
    db.execute(f"ALTER TABLE {M.TBL_ORG_SYNC_ITEMS} DROP COLUMN visibility")
    assert V.run_view(db, "anything") == {"available": False}


def test_run_view_unknown_run_raises_key_error():
    db, _ = _run_db()
    with pytest.raises(KeyError):
        V.run_view(db, "19990101T000000Z")


def test_run_view_on_a_db_behind_0013():
    db = _db()
    db.execute(f"DROP TABLE {M.TBL_ORG_SYNC_COMMANDS}")
    assert V.run_view(db, "anything") == {"available": False}
