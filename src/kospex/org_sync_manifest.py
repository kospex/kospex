"""The manifest behind `kgit sync --org` / `--user`: what an owner's listing held,
and how far the sync has got through it.

Design: changes/202610-kgit-sync-org.md (#335),
        changes/202611-kgit-sync-active.md (-active, -refresh, `gone`)
Tables: migrations 0012, 0013

Every saved listing, first or refresh, also gets a row in `org_sync_listings`
saying what changed since the previous listing of the org, and each item
carries `first_listed_at` and `archived_seen_at` across runs, so a weekly
listing builds a history even though a refresh overwrites the run's items.

A run is one complete listing of an owner's repositories. Each repository in it
is an item with a status:

    pending -> in_progress -> synced | skipped | failed

and `gone` for a repository a later listing of the same run no longer has
(`refresh_listing`). `gone` isn't a step in the sync: nothing picks it.

`in_progress` is committed before the work on a repository starts, so a crash or
`kill -9` leaves it `in_progress`, and the next invocation treats it as pending.
Redoing it is cheap: a clone becomes a pull, and the sync is incremental.

A run is finished once nothing in it is pending or in progress. Until then it is
the org's unfinished run, which `-next N` keeps working through and `-inventory`
refuses to replace without `-restart`. `-refresh` lists the org again into the
same run, keeping each item's progress.

This module only reads and writes the manifest. Listing the provider, cloning
and syncing are the caller's.
"""
from datetime import datetime, timezone

import kospex_utils as KospexUtils

TBL_ORG_SYNC_RUNS = "org_sync_runs"
TBL_ORG_SYNC_ITEMS = "org_sync_items"
TBL_ORG_SYNC_LISTINGS = "org_sync_listings"
TBL_ORG_SYNC_COMMANDS = "org_sync_commands"
TBL_REPOS = "repos"

PENDING = "pending"
IN_PROGRESS = "in_progress"
SYNCED = "synced"
SKIPPED = "skipped"
FAILED = "failed"
# In the run, but missing from a later complete listing: deleted, renamed,
# transferred, or no longer visible to the listing token.
GONE = "gone"

STATUSES = (PENDING, IN_PROGRESS, SYNCED, SKIPPED, FAILED, GONE)
# Statuses a run still has work in. `in_progress` is here because it means the
# process doing it died: nothing else leaves an item in that state.
OPEN_STATUSES = (PENDING, IN_PROGRESS)
DONE_STATUSES = (SYNCED, SKIPPED, FAILED)

# What a listing says about a repository, as opposed to how far the sync got
# with it. A refresh overwrites these and leaves the status alone.
LISTING_FIELDS = (
    "clone_url", "ssh_url", "provider_repo_id", "archived", "fork",
    "fork_parent", "fork_source", "private", "visibility", "pushed_at", "created_at",
)

# An item's `visibility` (migration 0014), as GitHub reports it. NULL = not
# recorded: listed before 0014, or GitHub sent neither visibility nor private.
PUBLIC = "public"
PRIVATE = "private"
INTERNAL = "internal"

ITEM_FIELDS = LISTING_FIELDS + ("status", "detail")

# A listing's `kind` in org_sync_listings.
FIRST = "first"
REFRESH = "refresh"
# A recorded command's listing when it replaced the run (-restart). The new
# run's org_sync_listings row is still kind FIRST.
RESTART = "restart"


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

        # Compared with the org's previous run, finished or just replaced.
        previous_run = latest_run(db, org_key)
        previous = set() if previous_run is None else _listed_ids(db, previous_run["run_id"])
        item_rows = [_item_row(None, item) for item in rows]
        changes, _ = _apply_history(item_rows, *_history(db, org_key), previous, now)

        run_id = _free_run_id(db, run_id)
        for row in item_rows:
            row["run_id"] = run_id
        db[TBL_ORG_SYNC_RUNS].insert({
            "run_id": run_id,
            "org_key": org_key,
            "provider": provider,
            "owner_type": owner_type,
            "listed": len(rows),
            "started_at": now,
            "listed_at": now,
            "finished_at": None,
        }, pk="run_id")
        if item_rows:
            db[TBL_ORG_SYNC_ITEMS].insert_all(item_rows, pk=["run_id", "_repo_id"])
        _record_listing(db, run_id, org_key, FIRST, now, item_rows, changes)
        record_provider_ids(db, run_id, checked_at=now)
        # A listing where every repository was skipped has nothing to do.
        close_run_if_done(db, run_id)
    return run_id


def _listed_ids(db, run_id):
    """The repositories a run's latest listing had: everything but `gone`."""
    return {row["_repo_id"] for row in db.query(
        f"SELECT _repo_id FROM {TBL_ORG_SYNC_ITEMS} WHERE run_id = ? AND status != ?",
        [run_id, GONE])}


def _history(db, org_key):
    """What the org's listings so far said about each repository.

    Returns `(by_repo_id, by_provider_id)`, two views of the same records:
    `_repo_id`, `provider_repo_id`, `first` (the earliest `first_listed_at`),
    `archived` and `archived_seen_at`, each from the repository's most recent
    item. Runs are read oldest first, so a later run's item wins, and a
    renamed repository's record carries over to its new name through
    `provider_repo_id`. Within a run, `gone` items are read first, so a
    renamed repository's old name doesn't stand in for its current one.
    """
    by_id, by_provider = {}, {}
    for row in db.query(f"""
        SELECT i._repo_id, i.provider_repo_id, i.archived, i.first_listed_at,
               i.archived_seen_at, r.started_at
          FROM {TBL_ORG_SYNC_ITEMS} i
          JOIN {TBL_ORG_SYNC_RUNS} r ON r.run_id = i.run_id
         WHERE r.org_key = ?
         ORDER BY r.run_id, i.status = ? DESC
    """, [org_key, GONE]):
        earlier = _match(by_id, by_provider, row["_repo_id"], row["provider_repo_id"])
        first = row["first_listed_at"] or row["started_at"]
        if earlier and earlier["first"] and (first is None or earlier["first"] < first):
            first = earlier["first"]
        record = {"_repo_id": row["_repo_id"], "provider_repo_id": row["provider_repo_id"],
                  "first": first, "archived": bool(row["archived"]),
                  "archived_seen_at": row["archived_seen_at"]}
        by_id[row["_repo_id"]] = record
        if row["provider_repo_id"]:
            by_provider[row["provider_repo_id"]] = record
    return by_id, by_provider


def _match(by_id, by_provider, repo_id, provider_repo_id):
    """The earlier record for a repository: by provider id, then by name.

    A name whose earlier record has a different provider id is a new
    repository reusing the name, not the same one.
    """
    if provider_repo_id and provider_repo_id in by_provider:
        return by_provider[provider_repo_id]
    record = by_id.get(repo_id)
    if record and provider_repo_id and record["provider_repo_id"] \
            and record["provider_repo_id"] != provider_repo_id:
        return None
    return record


def _apply_history(rows, by_id, by_provider, previous, now):
    """Set `first_listed_at` and `archived_seen_at` on a listing's item rows,
    and count what changed since the `previous` listing (a set of `_repo_id`s).
    Returns `(counts, renamed)`, where `renamed` maps each old `_repo_id` to
    its new one.

    * `new`: in no earlier listing of the org. `first_listed_at` is now.
    * `back`: listed before, but not in the previous listing.
    * `renamed`: the same `provider_repo_id` under a new name. Its old name
      isn't counted as gone.
    * `newly_archived` / `unarchived`: the flag changed since the repository's
      last listing. `archived_seen_at` is set to now when it becomes archived,
      kept while it stays archived, and cleared when it's unarchived. One
      already archived when first listed gets none: kospex can't know when.
    * `gone`: in the previous listing and not in this one.
    """
    counts = dict.fromkeys(("new", "back", "renamed", "newly_archived", "unarchived"), 0)
    listed, renamed = set(), {}
    for row in rows:
        repo_id = row["_repo_id"]
        listed.add(repo_id)
        prior = _match(by_id, by_provider, repo_id, row.get("provider_repo_id"))
        archived = bool(row.get("archived"))
        if prior is None:
            counts["new"] += 1
            row["first_listed_at"] = now
            row["archived_seen_at"] = None
            continue
        row["first_listed_at"] = prior["first"] or now
        if prior["_repo_id"] != repo_id:
            counts["renamed"] += 1
            renamed[prior["_repo_id"]] = repo_id
        elif repo_id not in previous:
            counts["back"] += 1
        if not archived:
            row["archived_seen_at"] = None
            counts["unarchived"] += prior["archived"]
        elif prior["archived"]:
            row["archived_seen_at"] = prior["archived_seen_at"]
        else:
            row["archived_seen_at"] = now
            counts["newly_archived"] += 1
    counts["gone"] = len(previous - listed - set(renamed))
    return counts, renamed


def _record_listing(db, run_id, org_key, kind, now, rows, changes):
    """Add the listing's row to org_sync_listings."""
    db.execute(f"""
        INSERT INTO {TBL_ORG_SYNC_LISTINGS}
               (run_id, org_key, listed_at, kind, listed, [new], gone, back, renamed,
                newly_archived, unarchived, archived, forks, private, internal)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [run_id, org_key, now, kind, len(rows), changes["new"], changes["gone"],
          changes["back"], changes["renamed"], changes["newly_archived"],
          changes["unarchived"], sum(1 for r in rows if r.get("archived")),
          sum(1 for r in rows if r.get("fork")), sum(1 for r in rows if r.get("private")),
          sum(1 for r in rows if r.get("visibility") == INTERNAL)])


def listings(db, org_key):
    """Every saved listing of the org, oldest first, as dicts (`-history`)."""
    return list(db.query(f"""
        SELECT * FROM {TBL_ORG_SYNC_LISTINGS}
         WHERE org_key = ? ORDER BY listed_at, rowid
    """, [org_key]))


def start_command(db, org_key, command, options, auth, now=None):
    """Record that a `kgit sync` command has started; returns its command_id.

    auth: `login`, `kind`, `source` and `expires_at` of the GitHub token, or
    their -no-auth equivalents. Never the token.
    """
    cur = db.execute(f"""
        INSERT INTO {TBL_ORG_SYNC_COMMANDS}
               (org_key, started_at, command, options, auth_login, auth_kind,
                auth_source, auth_expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [org_key, now or _utc_now_iso(), command, options, auth.get("login"),
          auth.get("kind"), auth.get("source"), auth.get("expires_at")])
    db.conn.commit()
    return cur.lastrowid


def finish_command(db, command_id, *, run_id, listing, synced_any, synced, failed, skipped,
                   new, gone, error=None, finished=True, now=None):
    """Record what the command did. `finished=False` (stopped) leaves finished_at NULL."""
    db.execute(f"""
        UPDATE {TBL_ORG_SYNC_COMMANDS}
           SET run_id = ?, listing = ?, synced_any = ?, synced = ?, failed = ?,
               skipped = ?, [new] = ?, gone = ?, error = ?, finished_at = ?
         WHERE command_id = ?
    """, [run_id, listing, 1 if synced_any else 0, synced, failed, skipped, new, gone, error,
          (now or _utc_now_iso()) if finished else None, command_id])
    db.conn.commit()


def commands(db, run_id):
    """The run's recorded commands, oldest first, as dicts."""
    return list(db.query(f"""
        SELECT * FROM {TBL_ORG_SYNC_COMMANDS}
         WHERE run_id = ? ORDER BY started_at, command_id
    """, [run_id]))


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


def _window(active_days, now):
    """SQL and params limiting items to ones pushed in the last `active_days`.

    Counted back from `now` (default: the current time), not from when the run
    was listed. An item with no `pushed_at` (an empty repository) is outside
    every window. Epoch seconds, so `...Z` and `...+00:00` compare correctly,
    and strftime, so it runs on SQLite 3.26.
    """
    if active_days is None:
        return "", []
    if active_days < 1:
        raise ValueError("active_days must be a positive number of days")
    return (
        " AND CAST(strftime('%s', pushed_at) AS INTEGER) >= "
        "CAST(strftime('%s', ?, ?) AS INTEGER)",
        [now or _utc_now_iso(), f"-{int(active_days)} days"],
    )


def next_items(db, run_id, n=None, retry_failed=False, active_days=None, now=None):
    """The next items to work on, most recently pushed first.

    Pending and in-progress items, plus failed ones with `retry_failed`.
    Without it, a permanently broken repository would be retried on every
    invocation. Most recently pushed first, so that `-next 10` on a large org
    reaches its active repositories early. Items with no `pushed_at` go last,
    then ties break on `_repo_id` so the order is stable.

    `active_days` keeps only items pushed in the last N days (`-active`). The
    rest stay pending: a filter on what's picked, not a status, so a later run
    with a wider window, or none, still finds them.

    `n=None` returns all of them.
    """
    statuses = list(OPEN_STATUSES) + ([FAILED] if retry_failed else [])
    marks = ", ".join("?" for _ in statuses)
    window_sql, window_params = _window(active_days, now)
    # `pushed_at IS NULL` first in the ORDER BY puts NULLs last. NULLS LAST
    # itself needs SQLite 3.30, and kospex has to run on 3.26.
    sql = f"""
        SELECT * FROM {TBL_ORG_SYNC_ITEMS}
         WHERE run_id = ? AND status IN ({marks}){window_sql}
         ORDER BY pushed_at IS NULL,
                  CAST(strftime('%s', pushed_at) AS INTEGER) DESC,
                  _repo_id
    """
    params = [run_id] + statuses + window_params
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
    """Close a run whatever its items say. For `-restart`, which abandons it."""
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


def window_counts(db, run_id, active_days, retry_failed=False, now=None):
    """`(inside, outside)`: items still to do, inside and outside the window.

    Inside is what `next_items` with the same arguments would return; outside
    is the rest of the open items, which the window leaves pending.
    """
    statuses = list(OPEN_STATUSES) + ([FAILED] if retry_failed else [])
    marks = ", ".join("?" for _ in statuses)
    window_sql, window_params = _window(active_days, now)
    base = (f"SELECT COUNT(*) FROM {TBL_ORG_SYNC_ITEMS} "
            f"WHERE run_id = ? AND status IN ({marks})")
    total = db.execute(base, [run_id] + statuses).fetchone()[0]
    inside = db.execute(base + window_sql, [run_id] + statuses + window_params).fetchone()[0]
    return inside, total - inside


def listed_at(db, run_id):
    """When the run's listing was last taken (`started_at` for a run from
    before migration 0013 that wasn't backfilled)."""
    row = db.execute(
        f"SELECT COALESCE(listed_at, started_at) FROM {TBL_ORG_SYNC_RUNS} WHERE run_id = ?",
        [run_id]).fetchone()
    if row is None:
        raise KeyError(f"No run {run_id}")
    return row[0]


def refresh_listing(db, run_id, items):
    """Merge a new complete listing into an existing run, keeping its progress.

    * A repository new to the run is added with the status the caller gave it
      (`pending`, or `skipped` for a fork or archived repository).
    * One already in the run gets the listing's fields (`pushed_at`,
      `archived`, URLs...) and keeps its status. A fork or archived flag that
      changed is recorded but doesn't change the status.
    * One that had gone and is listed again goes back to the status the caller
      gave it.
    * One the listing no longer has becomes `gone`, with its previous status
      in `detail` (`was synced`), and its new name if it was renamed
      (`was synced, renamed to github.com~acme~new`). Its row stays.

    Call this only with a complete listing: a partial one would mark real
    repositories gone. Everything is written in one transaction: the items,
    the run's `listed` and `listed_at`, and a `refresh` row in
    org_sync_listings. Returns that row's counts (see `_apply_history`) plus
    `updated`, the items already in the run.
    """
    listed, seen = [], set()
    for item in items:
        if item["_repo_id"] not in seen:
            seen.add(item["_repo_id"])
            listed.append(item)

    now = _utc_now_iso()
    with db.atomic():
        run = db.execute(f"SELECT org_key FROM {TBL_ORG_SYNC_RUNS} WHERE run_id = ?",
                         [run_id]).fetchone()
        if run is None:
            raise KeyError(f"No run {run_id}")
        org_key = run[0]
        existing = {row["_repo_id"]: row["status"] for row in db.query(
            f"SELECT _repo_id, status FROM {TBL_ORG_SYNC_ITEMS} WHERE run_id = ?", [run_id])}
        previous = {repo_id for repo_id, status in existing.items() if status != GONE}
        rows = [_item_row(run_id, item) for item in listed]
        counts, renamed = _apply_history(rows, *_history(db, org_key), previous, now)
        counts["updated"] = 0

        for row in rows:
            repo_id = row["_repo_id"]
            if repo_id not in existing:
                db[TBL_ORG_SYNC_ITEMS].insert(row, pk=["run_id", "_repo_id"])
                continue
            fields = {f: row[f] for f in LISTING_FIELDS if f in row}
            fields.update(first_listed_at=row["first_listed_at"],
                          archived_seen_at=row["archived_seen_at"])
            if existing[repo_id] == GONE:
                fields.update(status=row["status"], detail=row.get("detail"),
                              started_at=None, finished_at=None)
            else:
                counts["updated"] += 1
            sets = ", ".join(f"{f} = ?" for f in fields)
            db.execute(
                f"UPDATE {TBL_ORG_SYNC_ITEMS} SET {sets} WHERE run_id = ? AND _repo_id = ?",
                list(fields.values()) + [run_id, repo_id])

        for repo_id, status in existing.items():
            if repo_id in seen or status == GONE:
                continue
            detail = f"was {status}" + (f", renamed to {renamed[repo_id]}"
                                        if repo_id in renamed else "")
            db.execute(
                f"UPDATE {TBL_ORG_SYNC_ITEMS} SET status = ?, detail = ?, finished_at = ? "
                "WHERE run_id = ? AND _repo_id = ?",
                [GONE, detail, now, run_id, repo_id])

        db.execute(
            f"UPDATE {TBL_ORG_SYNC_RUNS} SET listed = ?, listed_at = ? WHERE run_id = ?",
            [len(listed), now, run_id])
        _record_listing(db, run_id, org_key, REFRESH, now, rows, counts)
        record_provider_ids(db, run_id, checked_at=now)
    return counts


def items(db, run_id):
    """A run's items, by repo id, each with `in_kospex` (1 when `repos` has it).

    The per-row form of the join `inventory()` totals. Includes `gone` items:
    callers that only want the current listing filter on status.
    """
    return list(db.query(f"""
        SELECT i.*, (r._repo_id IS NOT NULL) AS in_kospex
          FROM {TBL_ORG_SYNC_ITEMS} i
          LEFT JOIN {TBL_REPOS} r ON r._repo_id = i._repo_id
         WHERE i.run_id = ?
         ORDER BY i._repo_id
    """, [run_id]))


def inventory(db, run_id):
    """What a run's listing holds, compared with what kospex already has.

    The numbers behind `-inventory` and `-status`:

    * `listed`, `active`, `archived`, `forks`, `private`: from the listing.
    * `public`, `internal`: by `visibility`, with `private` the third value.
      `visibility_unknown` is NULL (listed before migration 0014), and
      `visibility_other` a value GitHub added since, kept as sent.
    * `in_kospex`: listed repositories already in `repos`; `not_in_kospex` the rest.
    * `changed_since_fetch`: in kospex, and pushed to after `repos.last_fetch`
      (or never fetched). Compared as epoch seconds, because the provider's
      `...Z` and kospex's `...+00:00` timestamps don't compare as strings.
    * `gone`: repositories kospex has under this org that the listing doesn't
      include, as a list of `_repo_id`s (the drift report, #12).

    Items a refresh marked `gone` aren't in the listing any more, so they're
    left out of the totals and counted in `gone` if kospex has them.
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
               COALESCE(SUM(i.private = 1), 0) AS private,
               COALESCE(SUM(i.visibility = ?), 0) AS public,
               COALESCE(SUM(i.visibility = ?), 0) AS internal,
               COALESCE(SUM(i.visibility IS NULL), 0) AS visibility_unknown,
               COALESCE(SUM(i.visibility NOT IN (?, ?, ?)), 0) AS visibility_other,
               COALESCE(SUM(r._repo_id IS NOT NULL), 0) AS in_kospex,
               COALESCE(SUM(r._repo_id IS NOT NULL AND (
                   r.last_fetch IS NULL OR
                   CAST(strftime('%s', i.pushed_at) AS INTEGER) >
                   CAST(strftime('%s', r.last_fetch) AS INTEGER))), 0) AS changed_since_fetch
          FROM {TBL_ORG_SYNC_ITEMS} i
          LEFT JOIN {TBL_REPOS} r ON r._repo_id = i._repo_id
         WHERE i.run_id = ? AND i.status != ?
    """, [PUBLIC, INTERNAL, PUBLIC, PRIVATE, INTERNAL, run_id, GONE])))

    # The org's repos by their parsed columns, as KospexQuery.where_org_key
    # scopes an org, not by slicing _repo_id. A nested group under this org has
    # a different _git_owner (`acme/sub`), so it isn't counted: this listing
    # doesn't cover it. Lowercased on both sides, for rows from before #147.
    parsed = KospexUtils.parse_org_key(run["org_key"])
    gone = [] if parsed is None else [row["_repo_id"] for row in db.query(f"""
        SELECT r._repo_id FROM {TBL_REPOS} r
         WHERE lower(r._git_server) = ? AND lower(r._git_owner) = ?
           AND r._repo_id NOT IN (
               SELECT _repo_id FROM {TBL_ORG_SYNC_ITEMS} WHERE run_id = ? AND status != ?)
         ORDER BY r._repo_id
    """, [parsed["git_server"].lower(), parsed["org"].lower(), run_id, GONE])]

    result = dict(totals)
    result["not_in_kospex"] = result["listed"] - result["in_kospex"]
    result["gone"] = gone
    result["run_id"] = run_id
    result["org_key"] = run["org_key"]
    result["finished"] = run["finished_at"] is not None
    return result
