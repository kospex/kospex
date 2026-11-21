"""Orphaned repos: shared by `kospex orphans` and kweb's /orphans/ page.

A repo is Orphaned when everyone who worked on it is no longer working for
you: people committed to it in the window (default 365 days), and none of
them has committed in the last `days` (default 90) anywhere in the
`active_in` scope. Bots are never people; they get their own column.

`active_in` is where "still working for us" is looked for:

- ``estate`` (default): any synced repo. Right for a company that syncs its
  own servers; a person who moved to another project is still here.
- ``server``: repos on the same git server as the repo.
- ``org``: repos in the same kospex org (its org_key, `server~owner`). For a
  GitLab nested group the owner is the whole path, so `group/sub-a` and
  `group/sub-b` are different orgs.

People are author email and author date: a GitHub web merge is committed by
noreply@github.com, and a rebase gives an old commit a new committer date.

Design: changes/202611-orphans-author-bots-refresh.md
"""
from datetime import datetime, timezone

from kospex import staleness
from kospex_git import KospexGit

ORPHANED = "Orphaned"
KNOWLEDGE = "Knowledge"
DORMANT = "Dormant"
NOT_SYNCED = "Not synced"
STATUSES = (ORPHANED, KNOWLEDGE, DORMANT, NOT_SYNCED)

ACTIVE_IN = ("estate", "server", "org")

FIELDS = [
    "repo_id", "status", "people", "people_active", "pct_here",
    "bots", "bots_active", "last_refresh", "refresh_age_days", "refresh_stale",
    "window_days", "active_days", "active_in",
]


def scope_key(active_in, server, owner):
    """The key a repo's people are looked up under, for an active_in scope."""
    if active_in == "estate":
        return None
    if active_in == "server":
        return (server or "").lower()
    if active_in == "org":
        return KospexGit.generate_org_key(server or "", owner or "")
    raise ValueError(f"active_in must be one of {', '.join(ACTIVE_IN)}, not '{active_in}'.")


def _pct(part, total):
    return round(100 * part / total, 2) if total else ""


def orphan_row(repo_id, repo, authors, active, is_bot, now, stale_days):
    """One row: the rule, as a pure function.

    repo: the repos-table row, or None when the repo is not in the DB.
    authors: {author_email: recent} for the window (recent = committed to this
    repo in the last `days`), from KospexQuery.repo_author_emails().
    active: the emails still active in this repo's active_in scope.

    status is people only. A repo whose only commits in the window are by bots
    is Dormant (no person has worked on it); bots_active shows the bots still at
    work. refresh_stale uses the kgit pull / kweb /syncs/ rule (kospex.staleness).
    """
    row = {"repo_id": repo_id}
    if repo is None:
        row.update({"status": NOT_SYNCED, "people": "", "people_active": "", "pct_here": "",
                    "bots": "", "bots_active": "", "last_refresh": "",
                    "refresh_age_days": "", "refresh_stale": ""})
        return row

    people = [a for a in authors if not is_bot(a)]
    bots = [a for a in authors if is_bot(a)]
    people_active = sum(1 for a in people if a in active)
    if not people:
        status = DORMANT
    elif people_active == 0:
        status = ORPHANED
    else:
        status = KNOWLEDGE

    age = staleness.age_days(repo, now)
    row.update({
        "status": status,
        "people": len(people),
        "people_active": people_active,
        "pct_here": _pct(people_active, len(people)),
        "bots": len(bots),
        # bots on this repo recently, not anywhere: a bot elsewhere says nothing here
        "bots_active": sum(1 for a in bots if authors[a]),
        "last_refresh": staleness.last_refreshed(repo) or "",
        "refresh_age_days": "" if age is None else int(age),
        "refresh_stale": staleness.is_stale(repo, stale_days, now),
    })
    return row


def report(kq, repo_ids, repos, is_bot, days=90, window=365, stale_days=30,
           active_in="estate", now=None):
    """Rows for repo_ids, in order.

    repos is {repo_id: repos-table row} from KospexQuery.repos_table(); an id
    missing from it is Not synced. kq is a KospexQuery.
    """
    if active_in not in ACTIVE_IN:
        raise ValueError(f"active_in must be one of {', '.join(ACTIVE_IN)}, not '{active_in}'.")
    now = now or datetime.now(timezone.utc)
    if active_in == "estate":
        everywhere = kq.active_author_emails(days=days)
    else:
        by_scope = kq.active_author_emails_by_scope(
            days=days, key=lambda server, owner: scope_key(active_in, server, owner))
    authors = kq.repo_author_emails([r for r in repo_ids if r in repos],
                                    window=window, days=days)
    rows = []
    for repo_id in repo_ids:
        repo = repos.get(repo_id)
        if active_in == "estate":
            active = everywhere
        elif repo is None:
            active = set()
        else:
            active = by_scope.get(
                scope_key(active_in, repo.get("_git_server"), repo.get("_git_owner")), set())
        row = orphan_row(repo_id, repo, authors.get(repo_id, {}), active, is_bot, now,
                         stale_days)
        row.update({"window_days": window, "active_days": days, "active_in": active_in})
        rows.append(row)
    return rows


def counts(rows):
    """{status: n} for every status, in STATUSES order."""
    return {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}


def summary(rows):
    """'Orphaned: n | Knowledge: n | ... | Total: n'; a status with none is left
    out, except Orphaned and Knowledge, which are always shown."""
    parts = [f"{s}: {n}" for s, n in counts(rows).items() if n or s in (ORPHANED, KNOWLEDGE)]
    return " | ".join(parts + [f"Total: {len(rows)}"])
