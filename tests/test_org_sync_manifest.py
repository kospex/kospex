"""The `kgit sync --org` manifest: migration 0012 and kospex.org_sync_manifest.

Design: changes/202610-kgit-sync-org.md (#335)

What these pin down is what makes an org sync safe to interrupt: a run is saved
whole or not at all, `in_progress` survives a crash and is picked up again, a
failed repository isn't retried forever, and an unfinished run isn't replaced by
accident.
"""
import sqlite3

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
import kospex_utils as KospexUtils
from kospex.org_sync_manifest import (
    FAILED,
    GONE,
    IN_PROGRESS,
    PENDING,
    SKIPPED,
    SYNCED,
    TBL_ORG_SYNC_ITEMS,
    TBL_ORG_SYNC_RUNS,
    UnfinishedRunExists,
    close_run,
    close_run_if_done,
    inventory,
    latest_run,
    listed_at,
    mark,
    new_run_id,
    next_items,
    record_provider_ids,
    refresh_listing,
    run_counts,
    save_listing,
    unfinished_run,
    window_counts,
)

ORG = "github.com~acme"
A, B, C = "github.com~acme~a", "github.com~acme~b", "github.com~acme~c"


def _db(path=None):
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(path) if path else sqlite_utils.Database(memory=True)
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    Migrator(db).apply_pending()
    return db


def _item(repo_id, pushed_at="2026-11-01T00:00:00Z", **kw):
    name = repo_id.split("~")[-1]
    return {
        "_repo_id": repo_id,
        "clone_url": f"https://github.com/acme/{name}.git",
        "ssh_url": f"git@github.com:acme/{name}.git",
        "provider_repo_id": f"github.com:{abs(hash(repo_id)) % 10**8}",
        "archived": False,
        "fork": False,
        "private": False,
        "pushed_at": pushed_at,
        **kw,
    }


def _columns(db, table):
    return [c[1] for c in db.execute(f"PRAGMA table_info({table})").fetchall()]


# --- migration ---------------------------------------------------------------

def test_a_migrated_db_has_both_tables_and_the_repos_columns():
    db = _db()
    assert {TBL_ORG_SYNC_RUNS, TBL_ORG_SYNC_ITEMS} <= set(db.table_names())
    repos = _columns(db, "repos")
    assert "provider_repo_id" in repos
    assert "provider_checked_at" in repos


def test_the_new_tables_and_columns_are_not_in_the_baseline_schema():
    """0012 owns them. Declaring them in SQL_CREATE_* too breaks clean installs.

    A new DB runs the baseline CREATEs and then the migrations, so a column in
    both places fails with `duplicate column name` on every clean install.
    """
    baseline = " ".join(KospexSchema.DB_CREATE_STATEMENTS.values())
    for name in ("org_sync_runs", "org_sync_items", "provider_repo_id", "provider_checked_at",
                 "listed_at"):
        assert name not in baseline, name


def test_listed_at_is_backfilled_from_started_at_for_older_runs(tmp_path):
    """0013 on a DB that already has runs from 0012."""
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(str(tmp_path / "k.db"))
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    migrator = Migrator(db)
    pending = [m for m in migrator.pending() if m.sequence <= 12]
    for migration in pending:
        migrator.apply(migration)
    assert "listed_at" not in _columns(db, TBL_ORG_SYNC_RUNS)
    db[TBL_ORG_SYNC_RUNS].insert({"run_id": "20261101T000000Z", "org_key": ORG,
                                  "started_at": "2026-11-01T00:00:00Z"})
    Migrator(db).apply_pending()
    assert listed_at(db, "20261101T000000Z") == "2026-11-01T00:00:00Z"


# --- save_listing ------------------------------------------------------------

def test_a_listing_is_saved_as_a_run_with_every_item_pending():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A), _item(B)], owner_type="org")

    run = unfinished_run(db, ORG)
    assert run["run_id"] == run_id
    assert run["listed"] == 2
    assert run["owner_type"] == "org"
    assert run["provider"] == "github"
    assert run_counts(db, run_id)[PENDING] == 2


def test_items_get_their_git_columns_from_the_repo_id():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A)])
    row = next(iter(db.query(f"SELECT * FROM {TBL_ORG_SYNC_ITEMS} WHERE run_id = ?", [run_id])))
    assert (row["_git_server"], row["_git_owner"], row["_git_repo"]) == ("github.com", "acme", "a")


def test_a_repo_listed_twice_is_saved_once():
    """A listing can repeat an item when repositories are created while it pages."""
    db = _db()
    run_id = save_listing(db, ORG, [_item(A), _item(B), _item(A, clone_url="second")])
    assert unfinished_run(db, ORG)["listed"] == 2
    clone_url = db.execute(
        f"SELECT clone_url FROM {TBL_ORG_SYNC_ITEMS} WHERE run_id = ? AND _repo_id = ?",
        [run_id, A]).fetchone()[0]
    assert clone_url == "https://github.com/acme/a.git"  # first occurrence wins


def test_a_failure_part_way_through_saving_leaves_no_run(tmp_path):
    """The run and its items are one transaction, so there's never half a list.

    An unknown status on the second item raises after the run row and the first
    item would have been written. Checked on a file DB through a second
    connection, so it's what's on disk that's empty, not just this connection.
    """
    path = str(tmp_path / "kospex.db")
    db = _db(path)
    with pytest.raises(ValueError):
        save_listing(db, ORG, [_item(A), _item(B, status="bogus")])

    other = sqlite3.connect(path)
    assert other.execute(f"SELECT COUNT(*) FROM {TBL_ORG_SYNC_RUNS}").fetchone()[0] == 0
    assert other.execute(f"SELECT COUNT(*) FROM {TBL_ORG_SYNC_ITEMS}").fetchone()[0] == 0


def test_a_second_listing_is_refused_while_a_run_is_unfinished():
    db = _db()
    first = save_listing(db, ORG, [_item(A)], run_id="20261106T000000Z")
    with pytest.raises(UnfinishedRunExists) as e:
        save_listing(db, ORG, [_item(A), _item(B)], run_id="20261106T000001Z")
    assert e.value.run_id == first
    assert unfinished_run(db, ORG)["run_id"] == first
    assert db[TBL_ORG_SYNC_RUNS].count == 1


def test_replace_closes_the_old_run_and_keeps_its_items():
    """`-restart`: the old run is closed, and its items stay as a record."""
    db = _db()
    first = save_listing(db, ORG, [_item(A), _item(B)], run_id="20261106T000000Z")
    mark(db, first, A, SYNCED)

    second = save_listing(db, ORG, [_item(A), _item(B), _item(C)],
                          run_id="20261106T000001Z", replace=True)

    assert unfinished_run(db, ORG)["run_id"] == second
    old = next(iter(db.query(f"SELECT * FROM {TBL_ORG_SYNC_RUNS} WHERE run_id = ?", [first])))
    assert old["finished_at"] is not None
    assert run_counts(db, first) == {PENDING: 1, IN_PROGRESS: 0, SYNCED: 1, SKIPPED: 0,
                                     FAILED: 0, GONE: 0}


def test_another_orgs_unfinished_run_does_not_block_a_listing():
    db = _db()
    save_listing(db, "github.com~other", [_item("github.com~other~x")])
    save_listing(db, ORG, [_item(A)])
    assert unfinished_run(db, ORG) is not None


def test_two_orgs_listed_in_the_same_second_get_different_run_ids():
    db = _db()
    first = save_listing(db, "github.com~other", [_item("github.com~other~x")],
                         run_id="20261106T031500Z")
    second = save_listing(db, ORG, [_item(A)], run_id="20261106T031500Z")
    assert first == "20261106T031500Z"
    assert second == "20261106T031500Z-2"


def test_run_ids_have_the_osi_runs_form():
    from datetime import datetime, timezone
    assert new_run_id(datetime(2026, 11, 6, 3, 15, 0, tzinfo=timezone.utc)) == "20261106T031500Z"


def test_skipped_items_are_recorded_with_their_reason():
    """Forks and archived repos are skipped by default but still in the manifest."""
    db = _db()
    run_id = save_listing(db, ORG, [
        _item(A),
        _item(B, fork=True, fork_parent="upstream/b", fork_source="root/b",
              status=SKIPPED, detail="fork"),
    ])
    row = next(iter(db.query(
        f"SELECT * FROM {TBL_ORG_SYNC_ITEMS} WHERE run_id = ? AND _repo_id = ?", [run_id, B])))
    assert (row["status"], row["detail"], row["fork"]) == (SKIPPED, "fork", 1)
    assert (row["fork_parent"], row["fork_source"]) == ("upstream/b", "root/b")
    assert [i["_repo_id"] for i in next_items(db, run_id)] == [A]


def test_a_listing_with_nothing_to_sync_is_finished_straight_away():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A, status=SKIPPED, detail="fork")])
    assert unfinished_run(db, ORG) is None
    assert latest_run(db, ORG)["run_id"] == run_id


def test_an_empty_listing_is_saved_and_finished():
    """An org with no repositories is a real answer, and the drift report needs it."""
    db = _db()
    run_id = save_listing(db, ORG, [])
    assert latest_run(db, ORG)["listed"] == 0
    assert latest_run(db, ORG)["run_id"] == run_id
    assert unfinished_run(db, ORG) is None


# --- next_items and mark -----------------------------------------------------

def test_next_items_are_most_recently_pushed_first_with_unknown_last():
    db = _db()
    run_id = save_listing(db, ORG, [
        _item(A, pushed_at="2026-01-01T00:00:00Z"),
        _item(B, pushed_at=None),
        _item(C, pushed_at="2026-11-01T00:00:00Z"),
    ])
    assert [i["_repo_id"] for i in next_items(db, run_id)] == [C, A, B]
    assert [i["_repo_id"] for i in next_items(db, run_id, n=1)] == [C]


def test_pushed_at_is_compared_as_time_not_as_text():
    """`+10:00` sorts below `Z` as text but is the earlier time here."""
    db = _db()
    run_id = save_listing(db, ORG, [
        _item(A, pushed_at="2026-11-01T09:00:00+10:00"),   # 2026-10-31T23:00Z
        _item(B, pushed_at="2026-11-01T00:00:00Z"),
    ])
    assert [i["_repo_id"] for i in next_items(db, run_id)] == [B, A]


def test_next_items_rejects_a_non_positive_n():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A)])
    with pytest.raises(ValueError):
        next_items(db, run_id, n=0)


def test_an_item_left_in_progress_by_a_crash_is_picked_up_again(tmp_path):
    """`in_progress` is committed before the work, so a crash leaves it behind.

    Simulated by marking it and then opening the file afresh, as the next
    invocation would. It must come back as work to do.
    """
    path = str(tmp_path / "kospex.db")
    db = _db(path)
    run_id = save_listing(db, ORG, [_item(A), _item(B)])
    mark(db, run_id, A, IN_PROGRESS)
    db.conn.close()

    after = sqlite_utils.Database(path)
    assert unfinished_run(after, ORG)["run_id"] == run_id
    assert {i["_repo_id"] for i in next_items(after, run_id)} == {A, B}
    status = after.execute(
        f"SELECT status FROM {TBL_ORG_SYNC_ITEMS} WHERE _repo_id = ?", [A]).fetchone()[0]
    assert status == IN_PROGRESS


def test_failed_items_are_retried_only_when_asked():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A), _item(B)])
    mark(db, run_id, A, FAILED, detail="fatal: repository not found")
    assert [i["_repo_id"] for i in next_items(db, run_id)] == [B]
    assert {i["_repo_id"] for i in next_items(db, run_id, retry_failed=True)} == {A, B}


def test_mark_records_times_and_a_retry_clears_the_old_failure():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A)])

    def row():
        return next(iter(db.query(
            f"SELECT status, detail, started_at, finished_at FROM {TBL_ORG_SYNC_ITEMS} "
            "WHERE run_id = ? AND _repo_id = ?", [run_id, A])))

    mark(db, run_id, A, IN_PROGRESS)
    assert row()["started_at"] and row()["finished_at"] is None
    mark(db, run_id, A, FAILED, detail="timeout")
    assert row()["finished_at"] and row()["detail"] == "timeout"
    mark(db, run_id, A, IN_PROGRESS)
    assert row()["detail"] is None and row()["finished_at"] is None
    mark(db, run_id, A, SYNCED)
    assert row()["status"] == SYNCED and row()["finished_at"]


def test_mark_rejects_an_unknown_status_and_an_unknown_item():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A)])
    with pytest.raises(ValueError):
        mark(db, run_id, A, "done")
    with pytest.raises(KeyError):
        mark(db, run_id, B, SYNCED)


# --- closing a run -----------------------------------------------------------

def test_a_run_closes_only_when_nothing_is_pending_or_in_progress():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A), _item(B)])
    mark(db, run_id, A, SYNCED)
    mark(db, run_id, B, IN_PROGRESS)
    assert close_run_if_done(db, run_id) is False
    assert unfinished_run(db, ORG) is not None

    mark(db, run_id, B, FAILED, detail="timeout")
    assert close_run_if_done(db, run_id) is True
    assert unfinished_run(db, ORG) is None


def test_close_run_closes_regardless_of_items():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A)])
    close_run(db, run_id)
    assert unfinished_run(db, ORG) is None
    assert run_counts(db, run_id)[PENDING] == 1


# --- provider ids ------------------------------------------------------------

def test_a_listing_fills_provider_ids_for_repos_already_in_kospex():
    db = _db()
    db["repos"].insert({"_repo_id": A}, pk="_repo_id")
    save_listing(db, ORG, [_item(A, provider_repo_id="github.com:111"), _item(B)])

    row = next(iter(db.query("SELECT * FROM repos WHERE _repo_id = ?", [A])))
    assert row["provider_repo_id"] == "github.com:111"
    assert row["provider_checked_at"]
    assert db["repos"].count == 1  # B isn't created, only filled in once synced


def test_an_existing_provider_id_is_not_overwritten():
    """A different id under the same name is a rename or reused name (#348)."""
    db = _db()
    db["repos"].insert({"_repo_id": A, "provider_repo_id": "github.com:999"}, pk="_repo_id")
    save_listing(db, ORG, [_item(A, provider_repo_id="github.com:111")])
    assert db.execute("SELECT provider_repo_id FROM repos").fetchone()[0] == "github.com:999"


def test_a_repo_synced_later_gets_its_id_from_the_run():
    db = _db()
    run_id = save_listing(db, ORG, [_item(B, provider_repo_id="github.com:222")])
    db["repos"].insert({"_repo_id": B}, pk="_repo_id")  # sync_repo created it
    assert record_provider_ids(db, run_id, repo_id=B) == 1
    assert db.execute("SELECT provider_repo_id FROM repos").fetchone()[0] == "github.com:222"


# --- inventory ---------------------------------------------------------------

def _repo_row(row):
    """A repos row with the _git_* columns sync_repo writes, from its _repo_id."""
    parts = KospexUtils.parse_repo_id(row["_repo_id"])
    return {**row, "_git_server": parts["git_server"], "_git_owner": parts["org"],
            "_git_repo": parts["repo"]}


def test_inventory_finds_a_gone_repo_from_before_ids_were_lowercased():
    """Rows synced before #147 can be mixed case; the org is still matched."""
    db = _db()
    db["repos"].insert({"_repo_id": "github.com~Acme~Old", "_git_server": "github.com",
                        "_git_owner": "Acme", "_git_repo": "Old"}, pk="_repo_id")
    run_id = save_listing(db, ORG, [_item(A)])
    assert inventory(db, run_id)["gone"] == ["github.com~Acme~Old"]

def test_inventory_compares_the_listing_with_kospex():
    db = _db()
    db["repos"].insert_all([_repo_row(r) for r in [
        {"_repo_id": A, "last_fetch": "2026-11-02T00:00:00+00:00"},  # fetched after the push
        {"_repo_id": B, "last_fetch": "2026-10-01T00:00:00+00:00"},  # pushed since
        {"_repo_id": "github.com~acme~deleted"},                     # gone from the org
        {"_repo_id": "github.com~acme~~sub~nested"},                 # a nested group, not this org's
        {"_repo_id": "github.com~acmeco~x"},                         # a different org, same prefix
    ]], pk="_repo_id")
    run_id = save_listing(db, ORG, [
        _item(A), _item(B),
        _item(C, archived=True, private=True),
        _item("github.com~acme~f", fork=True, status=SKIPPED, detail="fork"),
    ])

    inv = inventory(db, run_id)
    assert (inv["listed"], inv["active"], inv["archived"], inv["forks"], inv["private"]) == (4, 3, 1, 1, 1)
    assert (inv["in_kospex"], inv["not_in_kospex"]) == (2, 2)
    assert inv["changed_since_fetch"] == 1
    assert inv["gone"] == ["github.com~acme~deleted"]
    assert inv["finished"] is False


def test_inventory_counts_a_never_fetched_repo_as_changed():
    db = _db()
    db["repos"].insert({"_repo_id": A}, pk="_repo_id")
    run_id = save_listing(db, ORG, [_item(A)])
    assert inventory(db, run_id)["changed_since_fetch"] == 1


def test_inventory_of_an_unknown_run_raises():
    with pytest.raises(KeyError):
        inventory(_db(), "nope")


# --- -active: the window --------------------------------------------------------

NOW = "2026-11-15T00:00:00Z"


def _windowed_run(db):
    return save_listing(db, ORG, [
        _item(A, "2026-11-10T00:00:00Z"),       # 5 days before NOW
        _item(B, "2026-06-01T00:00:00+00:00"),  # 167 days, in kospex's own form
        _item(C, None),                         # an empty repository
    ], run_id="20261106T000000Z")


def test_next_items_with_a_window_keeps_only_recently_pushed():
    db = _db()
    run_id = _windowed_run(db)
    ids = lambda **kw: [r["_repo_id"] for r in next_items(db, run_id, now=NOW, **kw)]
    assert ids(active_days=90) == [A]
    assert ids(active_days=365) == [A, B]
    assert ids() == [A, B, C]                # no window: everything, empty last
    assert ids(active_days=5) == [A]         # the boundary is inclusive
    assert ids(active_days=4) == []


def test_the_window_counts_back_from_now_not_from_the_listing():
    db = _db()
    run_id = _windowed_run(db)
    assert [r["_repo_id"] for r in next_items(db, run_id, active_days=90,
                                               now="2027-11-15T00:00:00Z")] == []


def test_next_items_rejects_a_non_positive_window():
    db = _db()
    run_id = _windowed_run(db)
    with pytest.raises(ValueError):
        next_items(db, run_id, active_days=0)


def test_window_counts_split_open_items_and_respect_retry_failed():
    db = _db()
    run_id = _windowed_run(db)
    assert window_counts(db, run_id, 90, now=NOW) == (1, 2)
    mark(db, run_id, A, FAILED, "boom")
    assert window_counts(db, run_id, 90, now=NOW) == (0, 2)
    assert window_counts(db, run_id, 90, retry_failed=True, now=NOW) == (1, 2)


def test_items_outside_the_window_keep_the_run_open():
    db = _db()
    run_id = _windowed_run(db)
    for row in next_items(db, run_id, active_days=90, now=NOW):
        mark(db, run_id, row["_repo_id"], SYNCED)
    assert not close_run_if_done(db, run_id)
    assert unfinished_run(db, ORG)["run_id"] == run_id


# --- refresh_listing ------------------------------------------------------------

def test_refresh_keeps_progress_adds_new_and_marks_gone():
    db = _db()
    D = "github.com~acme~d"
    run_id = save_listing(db, ORG, [_item(A), _item(B), _item(C)], run_id="20261106T000000Z")
    mark(db, run_id, A, SYNCED)
    mark(db, run_id, B, FAILED, "clone failed")

    counts = refresh_listing(db, run_id, [
        _item(A, "2026-11-12T00:00:00Z", archived=True),
        _item(B), _item(D), _item(D)])

    assert counts == {"new": 1, "updated": 2, "back": 0, "gone": 1}
    rows = {r["_repo_id"]: r for r in db.query(
        f"SELECT * FROM {TBL_ORG_SYNC_ITEMS} WHERE run_id = ?", [run_id])}
    assert (rows[A]["status"], rows[A]["pushed_at"], rows[A]["archived"]) == \
        (SYNCED, "2026-11-12T00:00:00Z", 1)        # archived since: flag only
    assert (rows[B]["status"], rows[B]["detail"]) == (FAILED, "clone failed")
    assert (rows[C]["status"], rows[C]["detail"]) == (GONE, "was pending")
    assert rows[D]["status"] == PENDING
    run = latest_run(db, ORG)
    assert run["listed"] == 3
    assert run["listed_at"] >= run["started_at"]
    assert db[TBL_ORG_SYNC_RUNS].count == 1


def test_a_gone_repo_listed_again_comes_back_and_gone_is_never_picked():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A), _item(B)], run_id="20261106T000000Z")
    refresh_listing(db, run_id, [_item(A)])
    assert [r["_repo_id"] for r in next_items(db, run_id)] == [A]
    assert run_counts(db, run_id)[GONE] == 1
    assert refresh_listing(db, run_id, [_item(A)])["gone"] == 0   # not counted twice

    counts = refresh_listing(db, run_id, [_item(A), _item(B)])
    assert counts["back"] == 1
    assert [r["_repo_id"] for r in next_items(db, run_id)] == [A, B]


def test_a_run_whose_open_items_are_all_gone_can_close():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A), _item(B)], run_id="20261106T000000Z")
    mark(db, run_id, A, SYNCED)
    refresh_listing(db, run_id, [_item(A)])
    assert close_run_if_done(db, run_id)


def test_a_failure_part_way_through_a_refresh_changes_nothing(monkeypatch):
    import kospex.org_sync_manifest as M
    db = _db()
    run_id = save_listing(db, ORG, [_item(A), _item(B)], run_id="20261106T000000Z")
    before = list(db.query(f"SELECT * FROM {TBL_ORG_SYNC_ITEMS}"))

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(M, "record_provider_ids", boom)
    with pytest.raises(RuntimeError):
        refresh_listing(db, run_id, [_item(A, "2026-11-12T00:00:00Z"), _item(C)])
    assert list(db.query(f"SELECT * FROM {TBL_ORG_SYNC_ITEMS}")) == before
    assert latest_run(db, ORG)["listed"] == 2


def test_refresh_of_an_unknown_run_raises():
    with pytest.raises(KeyError):
        refresh_listing(_db(), "nope", [_item(A)])


def test_inventory_leaves_gone_items_out_and_reports_them_if_in_kospex():
    db = _db()
    run_id = save_listing(db, ORG, [_item(A), _item(B)], run_id="20261106T000000Z")
    db["repos"].insert({"_repo_id": B, "_git_server": "github.com",
                        "_git_owner": "acme", "_git_repo": "b"})
    refresh_listing(db, run_id, [_item(A)])
    inv = inventory(db, run_id)
    assert inv["listed"] == 1
    assert inv["gone"] == [B]
