"""Orphaned repos: shared by `kospex orphans` and kweb's /orphans/ page.

A repo is Orphaned when no one who authored its code is still here: none of
its authors has committed in the last `here_days` (default 365) anywhere in
the `active_in` scope. Authors are everyone who authored a commit to the repo, all
time (or the last `window` days, when given), merges and bots excluded: a
merge's author merged the code rather than wrote it, and bots get their own
column. A repo with no commits for years still gets a verdict; its last
commit date is shown alongside.

Two presence windows, because they answer different questions:

- **Present** (`here_days`, default 365): still here. One year is what the
  studies that tested the threshold chose for "has left" (Avelino et al. ESEM
  2019, Calefato et al. EMSE 2022); 3 months wrongly marked most core
  developers as gone at least once. Orphaned is decided on this.
- **Active** (`days`, default 90): recently active, so their knowledge is
  fresh, and the usual seat-licensing rule. Shown, not used for the status.

`active_in` is where "still working for us" is looked for:

- ``estate`` (default): any synced repo. Right for a company that syncs its
  own servers; a person who moved to another project is still here.
- ``server``: repos on the same git server as the repo.
- ``org``: repos in the same kospex org (its org_key, `server~owner`). For a
  GitLab nested group the owner is the whole path, so `group/sub-a` and
  `group/sub-b` are different orgs.

People are author email and author date: a GitHub web merge is committed by
noreply@github.com, and a rebase gives an old commit a new committer date.
Presence ("still here") counts any commit, merges included: merging is still
being here.

Design: changes/202611-orphans-author-bots-refresh.md
"""
from datetime import datetime, timedelta, timezone

from kospex import staleness
from kospex_git import KospexGit

ORPHANED = "Orphaned"
KNOWLEDGE = "Knowledge"
NO_AUTHORS = "No authors"
NOT_SYNCED = "Not synced"
STATUSES = (ORPHANED, KNOWLEDGE, NO_AUTHORS, NOT_SYNCED)

ACTIVE_IN = ("estate", "server", "org")

FIELDS = [
    "repo_id", "status", "authors", "present", "pct_here", "active",
    "bots", "bots_active", "last_commit", "last_refresh", "refresh_age_days",
    "refresh_stale", "window_days", "here_days", "active_days", "active_in",
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


def orphan_row(repo_id, repo, authors, here, active, is_bot, now, stale_days):
    """One row: the rule, as a pure function.

    repo: the repos-table row, or None when the repo is not in the DB.
    authors: {author_email: recent} (recent = authored a commit here in the
    last `days`), from KospexQuery.repo_author_emails().
    here: the emails present (last here_days) in this repo's active_in scope.
    active: the emails active (last days) in the same scope.

    status is people only. A repo whose only authors are bots (or that has no
    authorship commits at all) is No authors; bots_active shows bots still at
    work. last_commit is the repos table's last_seen. refresh_stale uses the
    kgit pull / kweb /syncs/ rule (kospex.staleness).
    """
    row = {"repo_id": repo_id}
    if repo is None:
        row.update({"status": NOT_SYNCED, "authors": "", "present": "", "pct_here": "",
                    "active": "",
                    "bots": "", "bots_active": "", "last_commit": "", "last_refresh": "",
                    "refresh_age_days": "", "refresh_stale": ""})
        return row

    people = [a for a in authors if not is_bot(a)]
    bots = [a for a in authors if is_bot(a)]
    present = sum(1 for a in people if a in here)
    if not people:
        status = NO_AUTHORS
    elif present == 0:
        status = ORPHANED
    else:
        status = KNOWLEDGE

    age = staleness.age_days(repo, now)
    row.update({
        "status": status,
        "authors": len(people),
        "present": present,
        "pct_here": _pct(present, len(people)),
        "active": sum(1 for a in people if a in active),
        "bots": len(bots),
        # bots on this repo recently, not anywhere: a bot elsewhere says nothing here
        "bots_active": sum(1 for a in bots if authors[a]),
        "last_commit": repo.get("last_seen") or "",
        "last_refresh": staleness.last_refreshed(repo) or "",
        "refresh_age_days": "" if age is None else int(age),
        "refresh_stale": staleness.is_stale(repo, stale_days, now),
    })
    return row


def report(kq, repo_ids, repos, is_bot, days=90, here_days=365, window=None,
           stale_days=30, active_in="estate", now=None):
    """Rows for repo_ids, in order.

    repos is {repo_id: repos-table row} from KospexQuery.repos_table(); an id
    missing from it is Not synced. kq is a KospexQuery.
    """
    if active_in not in ACTIVE_IN:
        raise ValueError(f"active_in must be one of {', '.join(ACTIVE_IN)}, not '{active_in}'.")
    now = now or datetime.now(timezone.utc)
    # Last active date per author, once, for both windows. The cut-offs use the
    # clock, as the SQL's date('now') does, not `now` (which ages the syncs).
    key = None if active_in == "estate" else (
        lambda server, owner: scope_key(active_in, server, owner))
    last_active = kq.author_last_active(max(days, here_days), key=key)
    today = datetime.now(timezone.utc).date()

    def emails_since(seen, window_days):
        cutoff = (today - timedelta(days=window_days)).isoformat()
        return {email for email, last in seen.items() if last > cutoff}

    if key is None:
        everywhere = (emails_since(last_active, here_days), emails_since(last_active, days))
    by_scope = {}
    authors = kq.repo_author_emails([r for r in repo_ids if r in repos],
                                    window=window, days=days)
    rows = []
    for repo_id in repo_ids:
        repo = repos.get(repo_id)
        if key is None:
            here, active = everywhere
        elif repo is None:
            here, active = set(), set()
        else:
            scope = scope_key(active_in, repo.get("_git_server"), repo.get("_git_owner"))
            if scope not in by_scope:
                seen = last_active.get(scope, {})
                by_scope[scope] = (emails_since(seen, here_days), emails_since(seen, days))
            here, active = by_scope[scope]
        row = orphan_row(repo_id, repo, authors.get(repo_id, {}), here, active, is_bot,
                         now, stale_days)
        row.update({"window_days": "" if window is None else window, "here_days": here_days,
                    "active_days": days, "active_in": active_in})
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


def sync_command(stale_days, repo_id=None, org_key=None, server=None):
    """The `kgit pull` that refreshes the stale repos in a scope.

    kgit pull needs exactly one scope (a repo_id, --org, --server or --all), so
    the suggestion names the one being looked at, or --all.
    """
    if repo_id:
        return f"kgit pull {repo_id}"
    if org_key:
        return f"kgit pull --org {org_key} --stale {stale_days}"
    if server:
        return f"kgit pull --server {server} --stale {stale_days}"
    return f"kgit pull --all --stale {stale_days}"
