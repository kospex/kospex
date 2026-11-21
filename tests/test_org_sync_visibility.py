"""Each listed repo's visibility: public, private or internal (migration 0014).

GitHub Enterprise orgs can have internal repos, readable across the enterprise
but not public. They used to be recorded as private = 0, like public ones.

Design: changes/202611-org-sync-visibility.md
"""
import pytest
import sqlite_utils

import kospex_schema as KospexSchema
import kospex.org_sync as OrgSync
import kospex.org_sync_command as OrgSyncCommand
import kospex.org_sync_manifest as M

ORG = "github.com~acme"


def _db(up_to=None):
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(memory=True)
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    migrator = Migrator(db)
    for migration in migrator.pending():
        if up_to is None or migration.sequence <= up_to:
            migrator.apply(migration)
    return db


def _repo(name, **kw):
    return {"id": len(name), "name": f"acme/{name}",
            "clone_url": f"https://github.com/acme/{name}.git",
            "ssh_url": f"git@github.com:acme/{name}.git", "archived": False, "fork": False,
            "pushed_at": "2026-11-01T00:00:00Z", **kw}


# --- reading GitHub's listing --------------------------------------------------

@pytest.mark.parametrize("repo, expected", [
    ({"visibility": "public", "private": False}, "public"),
    ({"visibility": "private", "private": True}, "private"),
    # What a GitHub Enterprise org returned for every one of its repos.
    ({"visibility": "internal", "private": True}, "internal"),
    ({"visibility": "Internal"}, "internal"),
    # A value GitHub adds later is kept as sent, not dropped.
    ({"visibility": "restricted", "private": True}, "restricted"),
    # Older GitHub Enterprise Server: no visibility, so the boolean decides.
    ({"private": True}, "private"),
    ({"private": False}, "public"),
    # Neither: not known, and not guessed.
    ({}, None),
])
def test_visibility(repo, expected):
    assert OrgSync.visibility(repo) == expected


def test_internal_repos_are_not_private():
    items, _ = OrgSync.build_items(
        [_repo("a", visibility="internal", private=True),
         _repo("b", visibility="private", private=True),
         _repo("c", visibility="public", private=False)], "acme")
    assert [(i["visibility"], i["private"]) for i in items] == [
        ("internal", False), ("private", True), ("public", False)]


# --- the migration -------------------------------------------------------------

def test_0014_backfills_only_what_is_known():
    # private = 1 means GitHub said private. private = 0 could be public or
    # internal, so it stays NULL until the org is listed again.
    from kospex.db.migrator import Migrator
    db = _db(up_to=13)
    assert "visibility" not in db[M.TBL_ORG_SYNC_ITEMS].columns_dict
    db[M.TBL_ORG_SYNC_RUNS].insert({"run_id": "R1", "org_key": ORG,
                                     "started_at": "2026-11-01T00:00:00Z"})
    for name, private in (("a", 1), ("b", 0), ("c", None)):
        db[M.TBL_ORG_SYNC_ITEMS].insert({"run_id": "R1", "_repo_id": f"{ORG}~{name}",
                                          "private": private, "status": "pending"})
    db[M.TBL_ORG_SYNC_LISTINGS].insert({"run_id": "R1", "org_key": ORG, "private": 1})
    Migrator(db).apply_pending()
    rows = db.execute(f"SELECT _repo_id, visibility FROM {M.TBL_ORG_SYNC_ITEMS} "
                      "ORDER BY _repo_id").fetchall()
    assert rows == [(f"{ORG}~a", "private"), (f"{ORG}~b", None), (f"{ORG}~c", None)]
    # Listings from before 0014 didn't count internal: NULL, not a false 0.
    assert db.execute(f"SELECT internal FROM {M.TBL_ORG_SYNC_LISTINGS}").fetchone() == (None,)


# --- the manifest ----------------------------------------------------------------

def _items(*visibilities):
    repos = [_repo(f"r{n}", visibility=v, private=v != "public")
             for n, v in enumerate(visibilities)]
    return OrgSync.build_items(repos, "acme")[0]


def test_a_listing_counts_internal_repos():
    db = _db()
    M.save_listing(db, ORG, _items("internal", "internal", "private", "public"))
    [listing] = M.listings(db, ORG)
    assert (listing["private"], listing["internal"]) == (1, 2)


def test_inventory_counts_each_visibility():
    db = _db()
    run_id = M.save_listing(db, ORG, _items("internal", "internal", "private", "public",
                                            "restricted"))
    db.execute(f"UPDATE {M.TBL_ORG_SYNC_ITEMS} SET visibility = NULL WHERE _repo_id = ?",
               [f"{ORG}~r3"])
    inv = M.inventory(db, run_id)
    assert {k: inv[k] for k in ("listed", "public", "private", "internal",
                                "visibility_other", "visibility_unknown")} == {
        "listed": 5, "public": 0, "private": 1, "internal": 2,
        "visibility_other": 1, "visibility_unknown": 1}


def test_a_refresh_fills_in_visibility():
    db = _db()
    run_id = M.save_listing(db, ORG, _items("internal", "public"))
    db.execute(f"UPDATE {M.TBL_ORG_SYNC_ITEMS} SET visibility = NULL")
    assert M.inventory(db, run_id)["visibility_unknown"] == 2
    M.refresh_listing(db, run_id, _items("internal", "public"))
    inv = M.inventory(db, run_id)
    assert (inv["visibility_unknown"], inv["internal"], inv["public"]) == (0, 1, 1)


# --- the re-listing command --------------------------------------------------------

@pytest.mark.parametrize("opts, expected", [
    ([], ["-inventory", "-refresh"]),
    (["-active", "90", "-next", "10", "-no-auth"], ["-inventory", "-refresh", "-no-auth"]),
    (["-inventory", "-refresh", "-skip-archived"],
     ["-inventory", "-refresh", "-skip-archived"]),
    (["-next", "3", "-retry-failed", "-include-forks", "-ssh"],
     ["-inventory", "-refresh", "-include-forks", "-ssh"]),
])
def test_relisting(opts, expected):
    assert OrgSyncCommand.relisting(opts) == expected
