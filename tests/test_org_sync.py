"""kospex.org_sync: targets, fork policy, listing -> items, and the per-repo loop.

Design: changes/202610-kgit-sync-org.md (#335)
"""
import pytest
import sqlite_utils

import kospex_schema as KospexSchema
import kospex.org_sync as OrgSync
import kospex.org_sync_manifest as M

ORG = "github.com~acme"


def _db():
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(memory=True)
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    Migrator(db).apply_pending()
    return db


def _repo(full_name, **kw):
    owner, name = full_name.split("/")
    return {
        "id": abs(hash(full_name)) % 10**8,
        "name": full_name,
        "clone_url": f"https://github.com/{full_name}.git",
        "ssh_url": f"git@github.com:{full_name}.git",
        "visibility": "public",
        "archived": False,
        "fork": False,
        "pushed_at": "2026-11-01T00:00:00Z",
        **kw,
    }


# --- parse_target ------------------------------------------------------------

@pytest.mark.parametrize("target", [
    "acme", "Acme", "github.com/acme", "https://github.com/acme",
    "https://github.com/acme/", "github.com~acme", "GitHub.com/acme",
])
def test_every_target_form_names_the_owner(target):
    host, owner = OrgSync.parse_target(target)
    assert host == "github.com"
    assert owner.lower() == "acme"


@pytest.mark.parametrize("target, message", [
    ("", "No organisation"),
    ("https://gitlab.com/acme", "Only GitHub"),
    ("bitbucket.org/acme", "Only GitHub"),
    ("github.com/acme/app", "looks like a repository"),
    ("acme/app", "looks like a repository"),
    ("https://github.com/acme/app", "looks like a repository"),
    ("github.com~acme~app", "looks like a repository"),
    ("-acme", "isn't a valid"),
    ("ac me", "isn't a valid"),
])
def test_targets_that_are_not_a_github_owner_are_refused(target, message):
    with pytest.raises(ValueError, match=message):
        OrgSync.parse_target(target)


def test_org_key_is_lowercase_like_repo_ids():
    assert OrgSync.org_key("github.com", "Acme") == "github.com~acme"


# --- forks_default -----------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (None, False), ("", False), ("false", False), ("0", False), ("No", False),
    ("true", True), ("TRUE", True), ("1", True), (" yes ", True), ("on", True),
])
def test_clone_forks_default(value, expected):
    env = {} if value is None else {OrgSync.ENV_CLONE_FORKS_DEFAULT: value}
    assert OrgSync.forks_default(env) is expected


def test_a_clone_forks_default_typo_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="isn't true or false"):
        OrgSync.forks_default({OrgSync.ENV_CLONE_FORKS_DEFAULT: "ture"})


# --- build_items -------------------------------------------------------------

def test_a_listing_becomes_pending_items_with_ids_and_urls():
    items, others = OrgSync.build_items([_repo("acme/App", id=42, visibility="private")], "acme")
    assert others == 0
    (item,) = items
    assert item["_repo_id"] == "github.com~acme~app"
    assert item["provider_repo_id"] == "github.com:42"
    assert item["private"] is True
    assert item["clone_url"] == "https://github.com/acme/App.git"
    assert item["ssh_url"] == "git@github.com:acme/App.git"
    assert "status" not in item  # pending, by default in save_listing


def test_forks_are_skipped_by_default_but_kept_with_their_parent():
    fork = _repo("acme/lib", fork=True, fork_parent="up/lib", fork_source="root/lib")
    (item,), _ = OrgSync.build_items([fork], "acme")
    assert (item["status"], item["detail"]) == (M.SKIPPED, OrgSync.SKIP_FORK)
    assert (item["fork_parent"], item["fork_source"]) == ("up/lib", "root/lib")

    (item,), _ = OrgSync.build_items([fork], "acme", include_forks=True)
    assert "status" not in item


def test_archived_repos_are_synced_unless_skip_archived():
    old = _repo("acme/old", archived=True)
    (item,), _ = OrgSync.build_items([old], "acme")
    assert "status" not in item and item["archived"] is True
    (item,), _ = OrgSync.build_items([old], "acme", skip_archived=True)
    assert (item["status"], item["detail"]) == (M.SKIPPED, OrgSync.SKIP_ARCHIVED)


def test_repos_owned_by_another_account_are_left_out_and_counted():
    """Listing your own account includes repos you only collaborate on."""
    items, others = OrgSync.build_items(
        [_repo("acme/a"), _repo("someone-else/b"), _repo("ACME/c")], "acme")
    assert [i["_repo_id"] for i in items] == ["github.com~acme~a", "github.com~acme~c"]
    assert others == 1


def test_a_repo_without_an_id_has_no_provider_id():
    (item,), _ = OrgSync.build_items([_repo("acme/a", id=None)], "acme")
    assert item["provider_repo_id"] is None


# --- compare_with_run, lock_path, no_prompt_env ------------------------------

def test_compare_with_run_reports_new_and_gone():
    db = _db()
    items, _ = OrgSync.build_items([_repo("acme/a"), _repo("acme/b")], "acme")
    run_id = M.save_listing(db, ORG, items)
    fresh, _ = OrgSync.build_items([_repo("acme/b"), _repo("acme/c")], "acme")
    assert OrgSync.compare_with_run(db, run_id, fresh) == (
        ["github.com~acme~c"], ["github.com~acme~a"])


def test_each_org_gets_its_own_lock_file(tmp_path):
    a = OrgSync.lock_path(tmp_path, "github.com~acme")
    b = OrgSync.lock_path(tmp_path, "github.com~other")
    assert a != b and a.parent == tmp_path
    assert a.name == "org-sync-github.com~acme.lock"
    assert "/" not in OrgSync.lock_path(tmp_path, "x/../y").name


def test_no_prompt_env_turns_off_both_credential_prompts():
    env = OrgSync.no_prompt_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("credential.interactive", "false")


# --- sync_items --------------------------------------------------------------

def _run(db, *names, **kw):
    items, _ = OrgSync.build_items([_repo(f"acme/{n}") for n in names], "acme", **kw)
    run_id = M.save_listing(db, ORG, items)
    return run_id, M.next_items(db, run_id)


def _status(db, run_id, name):
    return db.execute(
        f"SELECT status, detail FROM {M.TBL_ORG_SYNC_ITEMS} WHERE run_id = ? AND _repo_id = ?",
        [run_id, f"github.com~acme~{name}"]).fetchone()


def test_each_repo_is_cloned_synced_and_marked():
    db = _db()
    run_id, todo = _run(db, "a", "b")
    cloned, synced = [], []
    counts = OrgSync.sync_items(
        db, run_id, todo,
        clone=lambda url: cloned.append(url) or f"/code/{url}",
        sync=lambda path, repo_id: synced.append(repo_id))
    assert counts == {M.SYNCED: 2, M.SKIPPED: 0, M.FAILED: 0}
    assert cloned == ["https://github.com/acme/a.git", "https://github.com/acme/b.git"]
    assert synced == ["github.com~acme~a", "github.com~acme~b"]
    assert M.close_run_if_done(db, run_id) is True


def test_ssh_uses_the_ssh_url():
    db = _db()
    run_id, todo = _run(db, "a")
    cloned = []
    OrgSync.sync_items(db, run_id, todo, clone=lambda url: cloned.append(url) or "/p",
                       sync=lambda *a: None, use_ssh=True)
    assert cloned == ["git@github.com:acme/a.git"]


def test_one_failure_never_stops_the_rest():
    """A failed clone, a sync that raises and a path conflict each mark one repo."""
    db = _db()
    run_id, todo = _run(db, "a", "b", "c", "d")

    def clone(url):
        return None if "/b.git" in url else "/p/" + url

    def sync(path, repo_id):
        if repo_id.endswith("~c"):
            raise RuntimeError("database is locked")

    counts = OrgSync.sync_items(db, run_id, todo, clone=clone, sync=sync,
                                conflict=lambda url: "/d.git" in url)
    assert counts == {M.SYNCED: 1, M.SKIPPED: 1, M.FAILED: 2}
    assert _status(db, run_id, "a") == (M.SYNCED, None)
    assert _status(db, run_id, "b") == (M.FAILED, "clone or pull failed")
    assert _status(db, run_id, "c") == (M.FAILED, "RuntimeError: database is locked")
    assert _status(db, run_id, "d") == (M.SKIPPED, "already synced from another directory")


def test_an_interrupt_stops_the_run_and_leaves_the_repo_to_resume():
    db = _db()
    run_id, todo = _run(db, "a", "b")

    def clone(url):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        OrgSync.sync_items(db, run_id, todo, clone=clone, sync=lambda *a: None)
    assert _status(db, run_id, todo[0]["_repo_id"].split("~")[-1])[0] == M.IN_PROGRESS
    assert len(M.next_items(db, run_id)) == 2  # both still to do


def test_a_synced_repo_gets_its_provider_id():
    """The repos row doesn't exist until sync creates it, so the id is copied then."""
    db = _db()
    run_id, todo = _run(db, "a")

    def sync(path, repo_id):
        db["repos"].insert({"_repo_id": repo_id}, pk="_repo_id")

    OrgSync.sync_items(db, run_id, todo, clone=lambda url: "/p", sync=sync)
    row = db.execute("SELECT provider_repo_id, provider_checked_at FROM repos").fetchone()
    assert row[0] == todo[0]["provider_repo_id"] and row[1]


def test_on_item_reports_each_result():
    db = _db()
    run_id, todo = _run(db, "a")
    seen = []
    OrgSync.sync_items(db, run_id, todo, clone=lambda url: None, sync=lambda *a: None,
                       on_item=lambda item, status, detail: seen.append((status, detail)))
    assert seen == [(M.FAILED, "clone or pull failed")]
