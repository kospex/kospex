"""Rebuild derived data for repos already in the kospex DB, without a re-sync.

A sync ingests commits and then derives other tables from them. Each derived
table that can be recomputed on its own is a RebuildTask here, and
`kospex rebuild <task>` runs it over a scope of repos: one REPO_ID, or -all,
-org ORG_KEY or -server SERVER. Exactly one scope is required, so nothing is
rebuilt estate-wide by accident.

Adding a task means one entry in TASKS. Scope resolution, the per-repo loop,
skipping repos whose clone is missing (for tasks that read the working tree),
per-repo error isolation and the summary are shared.
"""
import os
import time
from dataclasses import dataclass
from typing import Callable

import kospex_schema as KospexSchema
from kospex_utils import get_kospex_logger

# The CLI's logger, so a rebuild lands in kospex.log next to the command's
# other output.
log = get_kospex_logger("kospex")


@dataclass(frozen=True)
class RebuildTask:
    name: str
    description: str
    writes: tuple
    needs_clone: bool
    # (kospex_query, repo_row) -> a dict of numbers to report (e.g.
    # {"developers": 4, "commits": 969}), a row count, or None.
    run: Callable


@dataclass
class RepoResult:
    repo_id: str
    status: str  # "ok", "skipped" or "failed"
    seconds: float = 0.0
    detail: str = ""


def _developer_stats(kospex_query, repo):
    # The same method sync_repo() calls, so the two can't drift apart (#352).
    kospex_query.refresh_developer_stats(repo["_repo_id"])
    return kospex_query.developer_stats_totals(repo["_repo_id"])


def _describe(outcome):
    """What a task returned, as text for the progress line and the log."""
    if isinstance(outcome, dict):
        return " ".join(f"{key}={value}" for key, value in outcome.items())
    if isinstance(outcome, int):
        return f"{outcome} rows"
    return ""


TASKS = {
    task.name: task
    for task in (
        RebuildTask(
            name="developer-stats",
            description="Per-developer commit counts, first/last commit and 90-day activity, "
                        "from the commits table.",
            writes=(KospexSchema.TBL_DEVELOPER_STATS,),
            needs_clone=False,
            run=_developer_stats,
        ),
    )
}


def resolve_scope(kospex_query, repo_id=None, all_repos=False, org_key=None, server=None):
    """The repo rows in scope. Exactly one of the four must be given."""
    if sum([bool(repo_id), bool(all_repos), bool(org_key), bool(server)]) != 1:
        raise ValueError("Specify exactly one scope: a REPO_ID, or one of -all / -org / -server.")
    if all_repos:
        return kospex_query.get_repos()
    if repo_id:
        return kospex_query.get_repos(repo_id=repo_id)
    if org_key:
        return kospex_query.get_repos(org_key=org_key)
    return kospex_query.get_repos(server=server)


def run_task(task, repos, kospex_query, on_repo=None, scope=None):
    """Run task for each repo, isolating failures. Returns a list of RepoResult.

    on_repo(index, total, result) is called after each repo, for progress output.
    scope is how the repos were chosen (e.g. "-org github.com~acme"), for the log.

    Logs to kospex.log: a start line, one line per repo (INFO when it worked,
    WARNING when skipped, ERROR with the traceback when it failed) and a summary.
    """
    prefix = f"rebuild {task.name}"
    log.info("%s start: scope=%s, %d repos", prefix, scope or "(not given)", len(repos))
    started_all = time.perf_counter()
    results = []
    for i, repo in enumerate(repos, start=1):
        repo_id = repo["_repo_id"]
        path = repo.get("file_path")
        if task.needs_clone and not (path and os.path.isdir(path)):
            result = RepoResult(repo_id, "skipped", detail=f"no clone at {path}")
            log.warning("%s %s skipped: %s", prefix, repo_id, result.detail)
        else:
            started = time.perf_counter()
            try:
                detail = _describe(task.run(kospex_query, repo))
                result = RepoResult(repo_id, "ok", time.perf_counter() - started, detail)
                log.info("%s %s ok %.2fs%s", prefix, repo_id, result.seconds,
                         f" {detail}" if detail else "")
            except Exception as e:  # one bad repo must not stop the rest
                result = RepoResult(repo_id, "failed", time.perf_counter() - started, str(e))
                log.exception("%s %s failed %.2fs: %s", prefix, repo_id, result.seconds, e)
        results.append(result)
        if on_repo:
            on_repo(i, len(repos), result)

    counts = {s: sum(1 for r in results if r.status == s) for s in ("ok", "skipped", "failed")}
    log.info("%s done: %d ok, %d skipped, %d failed in %.1fs", prefix,
             counts["ok"], counts["skipped"], counts["failed"], time.perf_counter() - started_all)
    return results
