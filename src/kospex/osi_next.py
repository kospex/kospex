"""One `krunner osi -next` batch: queue, lock, extract, enrich, save, record.

Design: changes/202610-krunner-osi-next.md

`osi -all` is a single sweep taking hours on a large estate, so it cannot run from
cron. This runs N repositories per invocation, and a schedule of small runs
converges on full coverage.

Per repository the order is **extract -> enrich -> save -> record**, and the record
comes last deliberately. If the save fails, no outcome is written and the
repository stays at the head of the queue, so the work is retried rather than
silently skipped.

Per repository rather than per batch, so a failure part-way through leaves the
completed repositories recorded instead of losing the lot. Enrich and save stay
adjacent within a repository because `save_dependencies` stamps `last_checked` on
every row it writes and never calls deps.dev -- splitting those two is what would
advance the timestamp over rows whose advisory data was never refreshed.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import kospex_utils as KospexUtils
from kospex.osi_extract import extract_repo
from kospex.osi_lock import LockBusy, OsiLock
from kospex.osi_outcomes import repo_outcome
from kospex.osi_queue import next_repos, record_outcome

log = KospexUtils.get_kospex_logger("krunner")

SOURCE = "krunner osi -next"


@dataclass
class RepoResult:
    """What one repository cost and produced."""
    repo_id: str
    files: int
    packages: int
    duration_ms: int
    outcome: str


@dataclass
class BatchResult:
    """What one `-next` invocation did.

    `skipped` means a live lock was held by another run, so this tick did nothing.
    That is success, not failure -- overlap is normal under a short interval.
    """
    run_id: str
    repos: list = field(default_factory=list)
    skipped: bool = False
    duration_ms: int = 0

    @property
    def packages(self):
        return sum(r.packages for r in self.repos)


def _run_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_next_batch(db, limit, request_id=None, write_csv=False,
                   kospex_query=None, kdeps=None, echo=None):
    """Process the `limit` repositories most due for extraction.

    `db` is the kospex database. `kospex_query` and `kdeps` are injectable for
    testing; by default they are built on `db`.

    Returns a BatchResult. Never raises for an empty queue or a busy lock -- both
    are ordinary outcomes a scheduler should see as success.
    """
    from kospex_dependencies import KospexDependencies
    from kospex_query import KospexQuery

    kospex_query = kospex_query or KospexQuery(kospex_db=db)
    kdeps = kdeps or KospexDependencies(kospex_db=db, kospex_query=kospex_query)

    result = BatchResult(run_id=_run_id())
    started = time.monotonic()

    try:
        lock = OsiLock().acquire()
    except LockBusy as busy:
        log.info("osi -next: %s -- skipping this tick", busy)
        if echo:
            echo(f"another run is in progress (pid {busy.pid}), skipping")
        result.skipped = True
        return result

    try:
        queue = next_repos(db, limit, request_id=request_id)
        log.info("osi -next: run %s starting, limit=%s, queued=%s",
                 result.run_id, limit, len(queue))

        for repo_id in queue:
            result.repos.append(
                _process_repo(db, repo_id, kospex_query, kdeps, write_csv, echo))
    finally:
        lock.release()

    result.duration_ms = int((time.monotonic() - started) * 1000)
    log.info("osi -next: run %s done, %s repos, %s packages, %sms",
             result.run_id, len(result.repos), result.packages, result.duration_ms)
    return result


def _process_repo(db, repo_id, kospex_query, kdeps, write_csv, echo):
    """One repository: extract, enrich, save, then record the outcome."""
    from krunner import enrich_dependency_records

    started = time.monotonic()
    rows, outcomes = extract_repo(repo_id, kospex_query, kdeps, echo=echo)

    if rows:
        # Enrich and save adjacently. save_dependencies() stamps last_checked and
        # never calls deps.dev, so anything between these two would let the
        # timestamp attest a check that did not happen.
        enrich_dependency_records(rows, kdeps)
        kdeps.save_dependencies(rows, source=SOURCE)

        if write_csv:
            _write_repo_csv(repo_id, rows, echo)

    # Last: a failed save above leaves this unwritten, so the repository stays
    # queued and the work is retried rather than being marked done.
    record_outcome(db, repo_id, outcomes)

    duration_ms = int((time.monotonic() - started) * 1000)
    outcome = repo_outcome(outcomes)
    log.info("osi -next:   %-45s %2d files %4d packages %6dms  %s",
             repo_id, len(outcomes), len(rows), duration_ms, outcome)
    if echo:
        echo(f"  {repo_id}  {len(outcomes)} files  {len(rows)} packages  "
             f"{duration_ms}ms  {outcome}")

    return RepoResult(
        repo_id=repo_id, files=len(outcomes), packages=len(rows),
        duration_ms=duration_ms, outcome=outcome,
    )


def _write_repo_csv(repo_id, rows, echo=None):
    """Write one repository's rows to OSI-{repo_id}.csv in the assessments dir.

    Per repository rather than per batch, reusing the name `krunner osi REPO_ID`
    already produces. A batch of N arbitrary repositories has no scope to name, so
    a batch file would either lie about its coverage or accumulate one file per
    run.

    Assessments directory only, unlike `osi -all`, which also writes to the
    current working directory. For a scheduled run that second write would scatter
    N files per tick into whatever directory the scheduler started in.
    """
    import krunner_utils as KrunnerUtils
    from kospex.assessment_types import AssessmentTypes

    AssessmentTypes.ensure_assessments_dir()
    path = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, repo_id)
    KrunnerUtils.write_dict_to_csv(str(path), rows)
    if echo:
        echo(f"    wrote {path}")
