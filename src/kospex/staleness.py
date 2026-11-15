"""How long since kospex last refreshed a repo, and whether that's stale.

One rule for `kgit pull --stale`, `kgit pull --check` and kweb's /syncs/, so the
page and the command it shows always pick the same repos.

A repo's last refresh is `last_fetch`, or `last_sync` when it was never
fetched: `last_fetch` only exists since migration 0004 and is only written by
kgit pull / clone / sync, so repos synced before then, or with
`kospex sync-directory`, have none. A repo with neither is always stale.

Design: changes/202611-kweb-syncs.md
"""
from datetime import datetime, timezone


def parse_time(value):
    """An aware UTC datetime from a stored timestamp, or None.

    The repos table holds mixed offsets (`+11:00`, `Z`) and some naive values,
    which are taken as UTC. Anything unparseable is None, never an error.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def last_refreshed(repo):
    """`last_fetch`, else `last_sync`, as stored; None when neither is set."""
    return repo.get("last_fetch") or repo.get("last_sync") or None


def age_days(repo, now):
    """Days since the last refresh, exactly; None when there isn't one."""
    when = parse_time(last_refreshed(repo))
    if when is None:
        return None
    return (now - when).total_seconds() / 86400


def is_stale(repo, days, now):
    """True when the last refresh is more than `days` days before `now`."""
    age = age_days(repo, now)
    return age is None or age > days


def staleness_rows(repos, now=None):
    """Display rows for `kgit pull --check` and /syncs/, stalest first.

    Never-refreshed repos come first, then the largest age. `last_fetch` reads
    "never" when it is empty, so the repos aged by `last_sync` can be told
    apart; `fell_back` says the same thing for code.
    """
    now = now or datetime.now(timezone.utc)
    rows = []
    for repo in repos:
        age = age_days(repo, now)
        rows.append({
            "repo_id": repo.get("_repo_id"),
            "last_fetch": repo.get("last_fetch") or "never",
            "last_sync": repo.get("last_sync") or "-",
            "last_commit": repo.get("last_seen") or "-",
            "fetch_age": "never" if age is None else f"{int(age)}d",
            "age_days": age,
            "fell_back": not repo.get("last_fetch") and bool(repo.get("last_sync")),
        })
    rows.sort(key=lambda row: float("inf") if row["age_days"] is None else row["age_days"],
              reverse=True)
    return rows
