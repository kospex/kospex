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
from kospex.org_sync_manifest import (
    FAILED,
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
    mark,
    new_run_id,
    next_items,
    record_provider_ids,
    run_counts,
    save_listing,
    unfinished_run,
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
    for name in ("org_sync_runs", "org_sync_items", "provider_repo_id", "provider_checked_at"):
        assert name not in baseline, name


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
    """`-relist`: the old run is closed, and its items stay as a record."""
    db = _db()
    first = save_listing(db, ORG, [_item(A), _item(B)], run_id="20261106T000000Z")
    mark(db, first, A, SYNCED)

    second = save_listing(db, ORG, [_item(A), _item(B), _item(C)],
                          run_id="20261106T000001Z", replace=True)

    assert unfinished_run(db, ORG)["run_id"] == second
    old = next(iter(db.query(f"SELECT * FROM {TBL_ORG_SYNC_RUNS} WHERE run_id = ?", [first])))
    assert old["finished_at"] is not None
    assert run_counts(db, first) == {PENDING: 1, IN_PROGRESS: 0, SYNCED: 1, SKIPPED: 0, FAILED: 0}


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

def test_inventory_compares_the_listing_with_kospex():
    db = _db()
    db["repos"].insert_all([
        {"_repo_id": A, "last_fetch": "2026-11-02T00:00:00+00:00"},  # fetched after the push
        {"_repo_id": B, "last_fetch": "2026-10-01T00:00:00+00:00"},  # pushed since
        {"_repo_id": "github.com~acme~deleted"},                     # gone from the org
        {"_repo_id": "github.com~acme~~sub~nested"},                 # a nested group, not this org's
        {"_repo_id": "github.com~acmeco~x"},                         # a different org, same prefix
    ], pk="_repo_id")
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
