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
        result.append(entry)
    return result


def repo_view(repos, *, org, server, days, now):
    """The Repos tab: the stale repos in scope, the counts, and the commands."""
    scoped = _in_scope(repos, org, server)
    shown = [r for r in scoped if _is_shown(r, days, now)]
    rows = staleness.staleness_rows(shown, now)
    return {"rows": rows, "total": len(scoped), "stale": len(shown),
            "fell_back": sum(1 for row in rows if row["fell_back"]),
            "command": pull_command(org=org, server=server, days=days),
            "check_command": pull_command(org=org, server=server, days=days, check=True)}


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


def org_runs(db, *, org, server, now):
    """The Org syncs tab: runs latest first, with their commands and listings.

    `available` is False on a DB from before migration 0013, which kweb never
    applies itself: the page says to run `kospex upgrade-db` instead.
    """
    tables = (OrgSyncManifest.TBL_ORG_SYNC_RUNS, OrgSyncManifest.TBL_ORG_SYNC_ITEMS,
              OrgSyncManifest.TBL_ORG_SYNC_LISTINGS, OrgSyncManifest.TBL_ORG_SYNC_COMMANDS)
    if not all(db[name].exists() for name in tables):
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
