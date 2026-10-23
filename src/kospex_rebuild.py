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


@dataclass(frozen=True)
class RebuildTask:
    name: str
    description: str
    writes: tuple
    needs_clone: bool
    run: Callable  # (kospex_query, repo_row) -> optional count


@dataclass
class RepoResult:
    repo_id: str
    status: str  # "ok", "skipped" or "failed"
    seconds: float = 0.0
    detail: str = ""


def _developer_stats(kospex_query, repo):
    return kospex_query.update_developer_stats(repo["_repo_id"])


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


def run_task(task, repos, kospex_query, on_repo=None):
    """Run task for each repo, isolating failures. Returns a list of RepoResult.

    on_repo(index, total, result) is called after each repo, for progress output.
    """
    results = []
    for i, repo in enumerate(repos, start=1):
        repo_id = repo["_repo_id"]
        path = repo.get("file_path")
        if task.needs_clone and not (path and os.path.isdir(path)):
            result = RepoResult(repo_id, "skipped", detail=f"no clone at {path}")
        else:
            started = time.perf_counter()
            try:
                count = task.run(kospex_query, repo)
                detail = f"{count} rows" if isinstance(count, int) else ""
                result = RepoResult(repo_id, "ok", time.perf_counter() - started, detail)
            except Exception as e:  # one bad repo must not stop the rest
                result = RepoResult(repo_id, "failed", time.perf_counter() - started, str(e))
        results.append(result)
        if on_repo:
            on_repo(i, len(repos), result)
    return results
