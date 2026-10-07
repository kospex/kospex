"""Run diagnostics for `krunner osi -next`: what each repository cost, over time.

Design: changes/202610-krunner-osi-next.md
Table:  migration 0008

The run's log lines say what happened once, and rotation discards them. This keeps
rows, so the questions that only make sense across runs become answerable:

* **What N fits my interval?** Per-repo cost on a real estate spans 2ms to 109s,
  so this cannot be reasoned about, only measured.
* **Is a repository getting slower?**
* **Is the response cache working?** `lookups` counts deps.dev requests actually
  made, separately from `packages` written -- cache effectiveness is the ratio,
  tracked over time.
* **Is the schedule completing passes, or falling behind?**

Not in `observations`, because its primary key includes `latest`: it holds one
current row per key, and the demote-then-insert pattern raises on the third write.
Keyed here on `(run_id, _repo_id)` so history accumulates instead of colliding.
"""
from datetime import datetime, timezone

import kospex_utils as KospexUtils

TBL_OSI_RUNS = "osi_runs"


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_run(db, result, started_at=None):
    """Write one row per repository in `result`.

    A skipped run (a live lock was held) and an empty batch both did no work, so
    neither writes anything -- a row implying a repository was processed when it
    was not would corrupt exactly the measurements this table exists for.

    Returns the number of rows written.
    """
    if result.skipped or not result.repos:
        return 0

    started_at = started_at or _utc_now_iso()
    rows = []

    for repo in result.repos:
        row = {
            "run_id": result.run_id,
            "_repo_id": repo.repo_id,
            "started_at": started_at,
            "duration_ms": repo.duration_ms,
            "files": repo.files,
            "packages": repo.packages,
            "lookups": getattr(repo, "lookups", None),
            "outcome": repo.outcome,
        }
        # parse_repo_id returns None for a nested-group id (#94), so the _git_*
        # columns are left unset rather than guessed -- they are derived
        # convenience, and _repo_id is the identity.
        if parts := KospexUtils.parse_repo_id(repo.repo_id):
            row["_git_server"] = parts["git_server"]
            row["_git_owner"] = parts["org"]
            row["_git_repo"] = parts["repo"]
        rows.append(row)

    db[TBL_OSI_RUNS].upsert_all(rows, pk=["run_id", "_repo_id"])
    return len(rows)


def run_summary(db, run_id):
    """Totals for one run, or None if it recorded nothing.

    `slowest_repo_id` is the sizing signal: a batch is as long as its worst
    repository, so an average hides the case that actually overruns an interval.
    """
    sql = f"""
        SELECT count(*) AS repos,
               sum(packages) AS packages,
               sum(lookups) AS lookups,
               sum(duration_ms) AS total_ms,
               max(duration_ms) AS slowest_ms
          FROM {TBL_OSI_RUNS}
         WHERE run_id = ?
    """
    row = next(iter(db.query(sql, [run_id])), None)
    if row is None or not row["repos"]:
        return None

    slowest = next(iter(db.query(
        f"SELECT _repo_id FROM {TBL_OSI_RUNS} WHERE run_id = ? "
        "ORDER BY duration_ms DESC, _repo_id ASC LIMIT 1", [run_id])), None)

    return {
        "run_id": run_id,
        "repos": row["repos"],
        "packages": row["packages"] or 0,
        "lookups": row["lookups"] or 0,
        "total_ms": row["total_ms"] or 0,
        "slowest_ms": row["slowest_ms"] or 0,
        "slowest_repo_id": slowest["_repo_id"] if slowest else None,
    }
