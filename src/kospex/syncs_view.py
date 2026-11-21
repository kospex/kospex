"""The data behind kweb's /syncs/: stale repos and org sync runs.

Read-only. Every rule comes from elsewhere, so the page can't disagree with the
command it shows: staleness from kospex.staleness (as `kgit pull --stale`), the
continue command from kospex.org_sync_command (as kgit's own suggestions).

Design: changes/202611-kweb-syncs.md
"""
import shlex
from datetime import timedelta

import kospex_utils as KospexUtils
import kospex.org_sync_command as OrgSyncCommand
import kospex.org_sync_manifest as OrgSyncManifest
from kospex import staleness
from kospex_git import KospexGit

TABS = ("repos", "orgs")
DAYS_CHOICES = (1, 7, 30)
DEFAULT_DAYS = 30
# A token expiring within this many days is highlighted.
EXPIRY_WARNING_DAYS = 14

# A repo's development status from its last commit, as /summary/ counts it
# (KospexUtils.development_status). Label: (sort rank, pill classes). The hues
# are the summary circles' (kospex_web.SUMMARY_STATUSES), tinted like /osi/'s
# pills so the text stays readable; "Unmaintained" shows as "Dormant" there too.
STATUS_BADGES = {
    "Active": (1, "bg-green-100 text-green-800"),
    "Aging": (2, "bg-yellow-100 text-yellow-800"),
    "Stale": (3, "bg-orange-100 text-orange-800"),
    "Dormant": (4, "bg-gray-100 text-gray-700"),
    "Unknown": (5, "bg-gray-100 text-gray-500"),
}
_STATUS_LABELS = {"Unmaintained": "Dormant"}


def parse_params(tab, org, server, days):
    """Validate the query string. Raises ValueError with a message for a 400."""
    tab = tab or "repos"
    if tab not in TABS:
        raise ValueError(f"tab must be one of {', '.join(TABS)}.")
    if org and server:
        raise ValueError("Use org or server, not both.")
    if org and not KospexUtils.parse_org_key(org):
        raise ValueError(f"{org!r} isn't an org key (<server>~<owner>).")
    if days in (None, ""):
        days = DEFAULT_DAYS
    elif days == "all":
        days = None
    else:
        try:
            days = int(days)
        except ValueError:
            raise ValueError("days must be a whole number of days, or all.") from None
        if days < 1:
            raise ValueError("days must be 1 or more, or all.")
    return {"tab": tab, "org": org or None, "server": server or None, "days": days}


def pull_command(*, org, server, days, check=False):
    """The `kgit pull` that refreshes exactly what the Repos tab shows."""
    parts = ["kgit", "pull"] + (["--check"] if check else [])
    if org:
        parts += ["--org", shlex.quote(org)]
    elif server:
        parts += ["--server", shlex.quote(server)]
    else:
        parts.append("--all")
    if days is not None:
        parts += ["--stale", str(days)]
    return " ".join(parts)


def _org_key(repo):
    owner = repo.get("_git_owner") or ""
    return KospexGit.generate_org_key(repo.get("_git_server") or "", owner) if owner else None


def _in_scope(repos, org, server):
    if org:
        return [r for r in repos if _org_key(r) == org.lower()]
    if server:
        return [r for r in repos if (r.get("_git_server") or "") == server]
    return list(repos)


def _is_shown(repo, days, now):
    return days is None or staleness.is_stale(repo, days, now)


def picker(repos, days, now):
    """Servers, each with its orgs, and how many repos and stale repos each has.

    Owner-less repos (go.googlesource.com) count towards their server only: no
    `kgit pull` scope selects just them, and the command must match the table.
    """
    servers = {}
    for repo in repos:
        server = repo.get("_git_server") or ""
        entry = servers.setdefault(server, {"server": server, "repos": 0, "stale": 0,
                                            "orgs": {}})
        shown = _is_shown(repo, days, now)
        entry["repos"] += 1
        entry["stale"] += shown
        key = _org_key(repo)
        if key:
            org = entry["orgs"].setdefault(key, {"org_key": key, "label": repo["_git_owner"],
                                                 "repos": 0, "stale": 0})
            org["repos"] += 1
            org["stale"] += shown
    result = []
    for server in sorted(servers):
        entry = servers[server]
        entry["orgs"] = [entry["orgs"][key] for key in sorted(entry["orgs"])]
        if days is None:  # every repo is shown: a stale count would just repeat it
            entry["stale"] = None
            for org in entry["orgs"]:
                org["stale"] = None
        result.append(entry)
    return result


def repo_status(repo, now):
    """Active, Aging, Stale, Dormant, or Unknown when there's no last commit."""
    last_commit = staleness.parse_time(repo.get("last_seen"))
    if not last_commit:
        return "Unknown"
    status = KospexUtils.development_status((now - last_commit).total_seconds() / 86400)
    return _STATUS_LABELS.get(status, status)


def repo_view(repos, *, org, server, days, now):
    """The Repos tab: the stale repos in scope, the counts, and the commands."""
    scoped = _in_scope(repos, org, server)
    shown = [r for r in scoped if _is_shown(r, days, now)]
    rows = staleness.staleness_rows(shown, now)
    by_id = {r["_repo_id"]: r for r in shown}
    for row in rows:
        row["status"] = repo_status(by_id[row["repo_id"]], now)
        row["status_rank"], row["status_classes"] = STATUS_BADGES[row["status"]]
    return {"rows": rows, "total": len(scoped), "stale": len(shown),
            "fell_back": sum(1 for row in rows if row["fell_back"]),
            "command": pull_command(org=org, server=server, days=days),
            "check_command": pull_command(org=org, server=server, days=days, check=True)}


def duration_text(seconds):
    """`3s`, `36m`, `1h 30m`: how long a command took."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"


def _took(command):
    started = staleness.parse_time(command.get("started_at"))
    finished = staleness.parse_time(command.get("finished_at"))
    if not (started and finished):
        return ""
    return duration_text((finished - started).total_seconds())


def _token_text(command):
    if command.get("auth_kind") == "none":
        return "no auth"
    parts = [command.get("auth_login"), command.get("auth_kind"), command.get("auth_source")]
    if command.get("auth_expires_at"):
        parts.append(f"expires {command['auth_expires_at'][:10]}")
    return " · ".join(p for p in parts if p)


def _expiring(command, now):
    expires = staleness.parse_time(command.get("auth_expires_at"))
    return bool(expires and expires - now <= timedelta(days=EXPIRY_WARNING_DAYS))


def _continue_command(run, commands):
    if run["finished_at"]:
        return None
    parsed = KospexUtils.parse_org_key(run["org_key"])
    flag = "--user" if run.get("owner_type") == "user" else "--org"
    opts = OrgSyncCommand.parse(commands[-1]["options"]) if commands else []
    return OrgSyncCommand.command(flag, parsed["org"], OrgSyncCommand.continuing(opts))


def _available(db):
    """False on a DB from before migration 0013 (kweb never applies it)."""
    tables = (OrgSyncManifest.TBL_ORG_SYNC_RUNS, OrgSyncManifest.TBL_ORG_SYNC_ITEMS,
              OrgSyncManifest.TBL_ORG_SYNC_LISTINGS, OrgSyncManifest.TBL_ORG_SYNC_COMMANDS)
    return all(db[name].exists() for name in tables)


def org_runs(db, *, org, server, now):
    """The Org syncs tab: runs latest first, with their commands and listings.

    `available` is False on a DB from before migration 0013, which kweb never
    applies itself: the page says to run `kospex upgrade-db` instead.
    """
    if not _available(db):
        return {"available": False, "runs": []}
    sql = f"SELECT * FROM {OrgSyncManifest.TBL_ORG_SYNC_RUNS}"
    params = []
    if org:
        sql += " WHERE org_key = ?"
        params.append(org.lower())
    elif server:
        sql += " WHERE org_key LIKE ? ESCAPE '\\'"
        params.append(server.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                      + "~%")
    sql += " ORDER BY started_at DESC, run_id DESC"
    runs = []
    for run in db.query(sql, params):
        run_id = run["run_id"]
        commands = OrgSyncManifest.commands(db, run_id)
        for command in commands:
            command["state"] = "finished" if command["finished_at"] else "stopped or running"
            command["took"] = _took(command)
            command["token_text"] = _token_text(command)
            command["expiring"] = _expiring(command, now)
        totals = db.execute(f"""
            SELECT COALESCE(SUM(archived), 0), COALESCE(SUM(fork), 0)
              FROM {OrgSyncManifest.TBL_ORG_SYNC_ITEMS}
             WHERE run_id = ? AND status != ?
        """, [run_id, OrgSyncManifest.GONE]).fetchone()
        runs.append({**run, "counts": OrgSyncManifest.run_counts(db, run_id),
                     "archived": totals[0], "forks": totals[1], "commands": commands,
                     "listings": [row for row in OrgSyncManifest.listings(db, run["org_key"])
                                  if row["run_id"] == run_id],
                     "continue_command": _continue_command(run, commands)})
    return {"available": True, "runs": runs}


# --- one run: /syncs/run/{run_id} --------------------------------------------
# Design: changes/202611-kweb-sync-run.md

# The order statuses appear in the page's filter.
FILTER_STATUSES = (OrgSyncManifest.FAILED, OrgSyncManifest.SKIPPED,
                   OrgSyncManifest.PENDING, OrgSyncManifest.IN_PROGRESS,
                   OrgSyncManifest.SYNCED, OrgSyncManifest.GONE)


def _has_visibility(db):
    """False on a DB from before migration 0014 (org_sync_items.visibility)."""
    return "visibility" in db[OrgSyncManifest.TBL_ORG_SYNC_ITEMS].columns_dict


def visibility_banner(inventory):
    """"This run includes 3 internal and 1 private repo out of 40.", or None
    when every listed repo is public or not recorded. Zero parts are left out.
    """
    parts = [(inventory["internal"], "internal"), (inventory["private"], "private"),
             (inventory["visibility_other"], "other non-public")]
    parts = [f"{n:,} {label}" for n, label in parts if n]
    if not parts:
        return None
    shown = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    total = inventory["internal"] + inventory["private"] + inventory["visibility_other"]
    return (f"This run includes {shown} repo{'' if total == 1 else 's'} "
            f"out of {inventory['listed']:,}.")


def _visibility_note(run, inventory, commands):
    """What to run when some items have no recorded visibility, or None.

    An unfinished run can be listed again into itself, cloning nothing. A
    finished one can't (-refresh needs an unfinished run): the org's next
    listing, a new run, records it.
    """
    unknown = inventory["visibility_unknown"]
    if not unknown:
        return None
    parsed = KospexUtils.parse_org_key(run["org_key"])
    flag = "--user" if run.get("owner_type") == "user" else "--org"
    opts = OrgSyncCommand.parse(commands[-1]["options"]) if commands else []
    relist = OrgSyncCommand.relisting(opts)
    if run["finished_at"]:
        relist = [opt for opt in relist if opt != "-refresh"]
    return {"unknown": unknown, "finished": bool(run["finished_at"]),
            "command": OrgSyncCommand.command(flag, parsed["org"], relist)}


def run_view(db, run_id):
    """One run: its header, counts, items and drift.

    Returns `{"available": False}` on a DB from before migration 0014, which
    added visibility. Raises KeyError for an unknown run. Drift and the
    visibility counts come from `inventory()`, as `kgit sync -inventory`
    reports them, so they count only the current listing, not `gone` items.
    """
    if not (_available(db) and _has_visibility(db)):
        return {"available": False}
    run = next(iter(db.query(
        f"SELECT * FROM {OrgSyncManifest.TBL_ORG_SYNC_RUNS} WHERE run_id = ?", [run_id])),
        None)
    if run is None:
        raise KeyError(f"No run {run_id}")
    inventory = OrgSyncManifest.inventory(db, run_id)
    counts = OrgSyncManifest.run_counts(db, run_id)
    rows = OrgSyncManifest.items(db, run_id)
    for row in rows:
        row["gone"] = row["status"] == OrgSyncManifest.GONE
        row["pushed_date"] = (row.get("pushed_at") or "")[:10]
        source = row.get("fork_source")
        row["fork_source_shown"] = (source if source and source != row.get("fork_parent")
                                    else None)
    parsed = KospexUtils.parse_org_key(run["org_key"])
    commands = OrgSyncManifest.commands(db, run_id)
    return {"available": True, "run": run,
            "owner": parsed["org"] if parsed else run["org_key"],
            "listed_at": OrgSyncManifest.listed_at(db, run_id),
            "counts": counts,
            "filters": [(s, counts[s]) for s in FILTER_STATUSES if counts[s]],
            "rows": rows,  # not "items": Jinja would read page.items as dict.items
            "drift": inventory["gone"],
            "listed": inventory["listed"],
            "visibility_banner": visibility_banner(inventory),
            "visibility_note": _visibility_note(run, inventory, commands),
            "continue_command": _continue_command(run, commands)}
