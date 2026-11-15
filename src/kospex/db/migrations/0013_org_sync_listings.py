"""Backfill 0013: first_listed_at, archived_seen_at and one org_sync_listings
row for each existing org-sync run.

Before this migration a run was listed exactly once, when it started, and
nothing marked an item gone, so each run is one listing at its `started_at`.
Runs are replayed oldest first per org with the same rules as
`kospex.org_sync_manifest._apply_history`. They're repeated here rather than
imported because a migration must keep doing what it did when it shipped.

Runs inside the migrator's transaction: `db.execute` only, no
`db[table].insert()`, which would commit part way.
"""


def _match(by_id, by_provider, repo_id, provider_repo_id):
    if provider_repo_id and provider_repo_id in by_provider:
        return by_provider[provider_repo_id]
    record = by_id.get(repo_id)
    if record and provider_repo_id and record["provider_repo_id"] \
            and record["provider_repo_id"] != provider_repo_id:
        return None
    return record


def up(db):
    runs = db.execute(
        "SELECT run_id, org_key, started_at FROM org_sync_runs ORDER BY org_key, run_id"
    ).fetchall()
    history = {}  # org_key -> (by_id, by_provider, previous listing's ids)
    for run_id, org_key, started_at in runs:
        by_id, by_provider, previous = history.setdefault(org_key, ({}, {}, set()))
        items = db.execute(
            "SELECT _repo_id, provider_repo_id, archived, fork, private "
            "FROM org_sync_items WHERE run_id = ?", [run_id]).fetchall()
        counts = dict.fromkeys(
            ("new", "back", "renamed", "newly_archived", "unarchived"), 0)
        listed, renamed_from, records = set(), set(), []
        for repo_id, provider_repo_id, archived, _fork, _private in items:
            listed.add(repo_id)
            archived = bool(archived)
            prior = _match(by_id, by_provider, repo_id, provider_repo_id)
            if prior is None:
                counts["new"] += 1
                first, seen = started_at, None
            else:
                first = prior["first"] or started_at
                if prior["_repo_id"] != repo_id:
                    counts["renamed"] += 1
                    renamed_from.add(prior["_repo_id"])
                elif repo_id not in previous:
                    counts["back"] += 1
                if not archived:
                    seen = None
                    counts["unarchived"] += prior["archived"]
                elif prior["archived"]:
                    seen = prior["archived_seen_at"]
                else:
                    seen = started_at
                    counts["newly_archived"] += 1
            db.execute(
                "UPDATE org_sync_items SET first_listed_at = ?, archived_seen_at = ? "
                "WHERE run_id = ? AND _repo_id = ?", [first, seen, run_id, repo_id])
            records.append({"_repo_id": repo_id, "provider_repo_id": provider_repo_id,
                            "first": first, "archived": archived,
                            "archived_seen_at": seen})

        # Updated after the whole run, so the run's items are compared with
        # earlier runs only.
        for record in records:
            by_id[record["_repo_id"]] = record
            if record["provider_repo_id"]:
                by_provider[record["provider_repo_id"]] = record
        gone = len(previous - listed - renamed_from)
        history[org_key] = (by_id, by_provider, listed)

        db.execute(
            "INSERT INTO org_sync_listings (run_id, org_key, listed_at, kind, listed, "
            "[new], gone, back, renamed, newly_archived, unarchived, archived, forks, private) "
            "VALUES (?, ?, ?, 'first', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [run_id, org_key, started_at, len(items), counts["new"], gone, counts["back"],
             counts["renamed"], counts["newly_archived"], counts["unarchived"],
             sum(1 for i in items if i[2]), sum(1 for i in items if i[3]),
             sum(1 for i in items if i[4])])
