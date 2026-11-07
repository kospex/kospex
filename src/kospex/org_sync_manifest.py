"""The manifest behind `kgit sync --org` / `--user`: what an owner's listing held,
and how far the sync has got through it.

Design: changes/202610-kgit-sync-org.md (#335)
Tables: migration 0012

A run is one complete listing of an owner's repositories. Each repository in it
is an item with a status:

    pending -> in_progress -> synced | skipped | failed

`in_progress` is committed before the work on a repository starts, so a crash or
`kill -9` leaves it `in_progress`, and the next invocation treats it as pending.
Redoing it is cheap: a clone becomes a pull, and the sync is incremental.

A run is finished once nothing in it is pending or in progress. Until then it is
the org's unfinished run, which `-next N` keeps working through and `-inventory`
refuses to replace without `-relist`.

This module only reads and writes the manifest. Listing the provider, cloning
and syncing are the caller's.
"""
from datetime import datetime, timezone

import kospex_utils as KospexUtils

TBL_ORG_SYNC_RUNS = "org_sync_runs"
TBL_ORG_SYNC_ITEMS = "org_sync_items"
TBL_REPOS = "repos"

PENDING = "pending"
IN_PROGRESS = "in_progress"
SYNCED = "synced"
SKIPPED = "skipped"
FAILED = "failed"

STATUSES = (PENDING, IN_PROGRESS, SYNCED, SKIPPED, FAILED)
# Statuses a run still has work in. `in_progress` is here because it means the
# process doing it died: nothing else leaves an item in that state.
OPEN_STATUSES = (PENDING, IN_PROGRESS)
DONE_STATUSES = (SYNCED, SKIPPED, FAILED)

ITEM_FIELDS = (
    "clone_url", "ssh_url", "provider_repo_id", "archived", "fork",
    "fork_parent", "fork_source", "private", "pushed_at", "status", "detail",
)


class UnfinishedRunExists(Exception):
    """An org already has an unfinished run, and the caller didn't ask to replace it."""

    def __init__(self, org_key, run_id):
        self.org_key = org_key
        self.run_id = run_id
        super().__init__(f"{org_key} already has an unfinished run: {run_id}")


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(now=None):
    """A run id in the same form as osi_runs: `20261106T031500Z`."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def _free_run_id(db, run_id):
    """`run_id`, or `run_id-2`, `-3`... if another org's run took that second.

    Run ids are timestamps and the lock is per org, so two orgs listed in the
    same second would otherwise collide on the primary key.
    """
    candidate, n = run_id, 1
    while db.execute(
        f"SELECT 1 FROM {TBL_ORG_SYNC_RUNS} WHERE run_id = ?", [candidate]
    ).fetchone():
        n += 1
        candidate = f"{run_id}-{n}"
    return candidate


def _item_row(run_id, item):
    row = {"run_id": run_id, "_repo_id": item["_repo_id"]}
    for field in ITEM_FIELDS:
        if field in item:
            row[field] = item[field]
    row.setdefault("status", PENDING)
    if row["status"] not in STATUSES:
        raise ValueError(f"Unknown status {row['status']!r} for {item['_repo_id']}")
    for flag in ("archived", "fork", "private"):
        if row.get(flag) is not None:
            row[flag] = int(bool(row[flag]))
    # parse_repo_id returns None for an id it can't parse, so the _git_*
    # columns are left unset rather than guessed. They're derived convenience;
    # _repo_id is the identity.
    if parts := KospexUtils.parse_repo_id(item["_repo_id"]):
        row["_git_server"] = parts["git_server"]
        row["_git_owner"] = parts["org"]
        row["_git_repo"] = parts["repo"]
    return row


def save_listing(db, org_key, items, provider="github", owner_type="org",
                 run_id=None, replace=False):
    """Save a complete listing as a new run. Returns the run id.

    The run and all of its items are written in one transaction, so a failure
    part way leaves no run at all. Call this only with a complete listing: an
    incomplete one raises `GitHubListingIncomplete` before it gets here.

    `items` are dicts with `_repo_id` and any of ITEM_FIELDS. An item's status
    defaults to `pending`. The caller sets `skipped` (with a `detail`) for
    repositories it won't sync, such as forks and archived repositories, so the
    manifest still records everything the owner returned.

    A repository listed twice (a listing can repeat an item when repositories
    are created while it pages) is kept once, first occurrence wins.

    If the org has an unfinished run, raises `UnfinishedRunExists` unless
    `replace` is set, in which case that run is closed in the same transaction.
    Its items are left as they were, as a record of how far it got.
    """
    seen, rows = set(), []
    run_id = run_id or new_run_id()
    for item in items:
        if item["_repo_id"] in seen:
            continue
        seen.add(item["_repo_id"])
        rows.append(item)

    now = _utc_now_iso()
    with db.atomic():
        existing = unfinished_run(db, org_key)
        if existing:
            if not replace:
                raise UnfinishedRunExists(org_key, existing["run_id"])
            _close(db, existing["run_id"], now)

        run_id = _free_run_id(db, run_id)
        db[TBL_ORG_SYNC_RUNS].insert({
            "run_id": run_id,
            "org_key": org_key,
            "provider": provider,
            "owner_type": owner_type,
            "listed": len(rows),
            "started_at": now,
            "finished_at": None,
        }, pk="run_id")
        if rows:
            db[TBL_ORG_SYNC_ITEMS].insert_all(
                [_item_row(run_id, item) for item in rows],
                pk=["run_id", "_repo_id"],
            )
        record_provider_ids(db, run_id, checked_at=now)
        # A listing where every repository was skipped has nothing to do.
        close_run_if_done(db, run_id)
    return run_id


def record_provider_ids(db, run_id, repo_id=None, checked_at=None):
    """Copy provider ids from a run's items to `repos`, for repos already there.

    Only fills an empty `repos.provider_repo_id`. An id that differs from the one
    already recorded means a renamed, transferred or reused name, which is for
    the lineage work (#348) to report, not for this to overwrite. Sets
    `provider_checked_at` on every repository the listing covered.

    Repositories not yet in `repos` are filled in when they are synced: call
    this again with `repo_id` once `sync_repo` has created the row.

    Returns the number of repos rows updated.
    """
    checked_at = checked_at or _utc_now_iso()
    where, params = "i.run_id = ?", [run_id]
    if repo_id:
        where += " AND i._repo_id = ?"
        params.append(repo_id)
    with db.atomic():
        cur = db.execute(f"""
        UPDATE {TBL_REPOS}
           SET provider_repo_id = COALESCE(provider_repo_id, (
                   SELECT i.provider_repo_id FROM {TBL_ORG_SYNC_ITEMS} i
                    WHERE {where} AND i._repo_id = {TBL_REPOS}._repo_id)),
               provider_checked_at = ?
         WHERE _repo_id IN (
                   SELECT i._repo_id FROM {TBL_ORG_SYNC_ITEMS} i WHERE {where})
    """, params + [checked_at] + params)
    return cur.rowcount


def unfinished_run(db, org_key):
    """The org's unfinished run as a dict, or None.

    There should be at most one, because save_listing refuses a second. If an
    older database somehow holds two, the newest is the one to continue.
    """
    return next(iter(db.query(f"""
        SELECT * FROM {TBL_ORG_SYNC_RUNS}
         WHERE org_key = ? AND finished_at IS NULL
         ORDER BY run_id DESC LIMIT 1
    """, [org_key])), None)


def latest_run(db, org_key):
    """The org's most recent run, finished or not, as a dict, or None."""
    return next(iter(db.query(f"""
        SELECT * FROM {TBL_ORG_SYNC_RUNS}
         WHERE org_key = ? ORDER BY run_id DESC LIMIT 1
    """, [org_key])), None)


def next_items(db, run_id, n=None, retry_failed=False):
    """The next items to work on, most recently pushed first.

    Pending and in-progress items, plus failed ones with `retry_failed`.
    Without it, a permanently broken repository would be retried on every
    invocation. Most recently pushed first, so that `-next 10` on a large org
    reaches its active repositories early. Items with no `pushed_at` go last,
    then ties break on `_repo_id` so the order is stable.

    `n=None` returns all of them.
    """
    statuses = list(OPEN_STATUSES) + ([FAILED] if retry_failed else [])
    marks = ", ".join("?" for _ in statuses)
    # `pushed_at IS NULL` first in the ORDER BY puts NULLs last. NULLS LAST
    # itself needs SQLite 3.30, and kospex has to run on 3.26.
    sql = f"""
        SELECT * FROM {TBL_ORG_SYNC_ITEMS}
         WHERE run_id = ? AND status IN ({marks})
         ORDER BY pushed_at IS NULL,
                  CAST(strftime('%s', pushed_at) AS INTEGER) DESC,
                  _repo_id
    """
    params = [run_id] + statuses
    if n is not None:
        if n < 1:
            raise ValueError("n must be a positive number of repositories")
        sql += " LIMIT ?"
        params.append(n)
    return list(db.query(sql, params))


def mark(db, run_id, repo_id, status, detail=None):
    """Set one item's status, committed straight away.

    `in_progress` records the start time and clears any previous finish and
    detail, so a retried item doesn't keep its old failure. The other statuses
    record the finish time. Raises ValueError for an unknown status, and
    KeyError if the item isn't in the run.
    """
    if status not in STATUSES:
        raise ValueError(f"Unknown status {status!r}")
    now = _utc_now_iso()
    if status == IN_PROGRESS:
        sets, params = "status = ?, detail = NULL, started_at = ?, finished_at = NULL", [status, now]
    elif status == PENDING:
        sets, params = "status = ?, detail = ?, started_at = NULL, finished_at = NULL", [status, detail]
    else:
        sets, params = "status = ?, detail = ?, finished_at = ?", [status, detail, now]
    # atomic() commits on exit, so the status is on disk before the caller
    # starts the work. Inside a caller's own atomic() it's a savepoint instead.
    with db.atomic():
        cur = db.execute(
            f"UPDATE {TBL_ORG_SYNC_ITEMS} SET {sets} WHERE run_id = ? AND _repo_id = ?",
            params + [run_id, repo_id],
        )
        if cur.rowcount == 0:
            raise KeyError(f"{repo_id} is not in run {run_id}")


def _close(db, run_id, finished_at):
    db.execute(
        f"UPDATE {TBL_ORG_SYNC_RUNS} SET finished_at = ? "
        "WHERE run_id = ? AND finished_at IS NULL",
        [finished_at, run_id],
    )


def close_run(db, run_id):
    """Close a run whatever its items say. For `-relist`, which abandons it."""
    with db.atomic():
        _close(db, run_id, _utc_now_iso())


def close_run_if_done(db, run_id):
    """Close the run if nothing is pending or in progress. Returns True if closed.

    Failed items don't keep a run open, or one broken repository would keep
    it open forever. They're retried within an unfinished run with
    `-retry-failed`, and on the next listing, where they start as pending again.
    """
    marks = ", ".join("?" for _ in OPEN_STATUSES)
    with db.atomic():
        still_open = db.execute(
            f"SELECT 1 FROM {TBL_ORG_SYNC_ITEMS} WHERE run_id = ? AND status IN ({marks}) LIMIT 1",
            [run_id] + list(OPEN_STATUSES),
        ).fetchone()
        if still_open:
            return False
        _close(db, run_id, _utc_now_iso())
    return True


def run_counts(db, run_id):
    """Items per status for a run, with every status present (0 if none)."""
    counts = dict.fromkeys(STATUSES, 0)
    for row in db.query(
        f"SELECT status, COUNT(*) AS n FROM {TBL_ORG_SYNC_ITEMS} "
        "WHERE run_id = ? GROUP BY status", [run_id]
    ):
        counts[row["status"]] = row["n"]
    return counts


def inventory(db, run_id):
    """What a run's listing holds, compared with what kospex already has.

    The numbers behind `-inventory` and `-status`:

    * `listed`, `active`, `archived`, `forks`, `private`: from the listing.
    * `in_kospex`: listed repositories already in `repos`; `not_in_kospex` the rest.
    * `changed_since_fetch`: in kospex, and pushed to after `repos.last_fetch`
      (or never fetched). Compared as epoch seconds, because the provider's
      `...Z` and kospex's `...+00:00` timestamps don't compare as strings.
    * `gone`: repositories kospex has under this org that the listing doesn't
      include, as a list of `_repo_id`s (the drift report, #12).
    """
    run = next(iter(db.query(
        f"SELECT * FROM {TBL_ORG_SYNC_RUNS} WHERE run_id = ?", [run_id])), None)
    if run is None:
        raise KeyError(f"No run {run_id}")

    totals = next(iter(db.query(f"""
        SELECT COUNT(*) AS listed,
               COALESCE(SUM(archived = 1), 0) AS archived,
               COALESCE(SUM(COALESCE(archived, 0) = 0), 0) AS active,
               COALESCE(SUM(fork = 1), 0) AS forks,
               COALESCE(SUM(private = 1), 0) AS private,
               COALESCE(SUM(r._repo_id IS NOT NULL), 0) AS in_kospex,
               COALESCE(SUM(r._repo_id IS NOT NULL AND (
                   r.last_fetch IS NULL OR
                   CAST(strftime('%s', i.pushed_at) AS INTEGER) >
                   CAST(strftime('%s', r.last_fetch) AS INTEGER))), 0) AS changed_since_fetch
          FROM {TBL_ORG_SYNC_ITEMS} i
          LEFT JOIN {TBL_REPOS} r ON r._repo_id = i._repo_id
         WHERE i.run_id = ?
    """, [run_id])))

    # The org's repos by their parsed columns, as KospexQuery.where_org_key
    # scopes an org, not by slicing _repo_id. A nested group under this org has
    # a different _git_owner (`acme/sub`), so it isn't counted: this listing
    # doesn't cover it. Lowercased on both sides, for rows from before #147.
    parsed = KospexUtils.parse_org_key(run["org_key"])
    gone = [] if parsed is None else [row["_repo_id"] for row in db.query(f"""
        SELECT r._repo_id FROM {TBL_REPOS} r
         WHERE lower(r._git_server) = ? AND lower(r._git_owner) = ?
           AND r._repo_id NOT IN (
               SELECT _repo_id FROM {TBL_ORG_SYNC_ITEMS} WHERE run_id = ?)
         ORDER BY r._repo_id
    """, [parsed["git_server"].lower(), parsed["org"].lower(), run_id])]

    result = dict(totals)
    result["not_in_kospex"] = result["listed"] - result["in_kospex"]
    result["gone"] = gone
    result["run_id"] = run_id
    result["org_key"] = run["org_key"]
    result["finished"] = run["finished_at"] is not None
    return result
