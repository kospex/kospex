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
from kospex.osi_run_log import record_run

log = KospexUtils.get_kospex_logger("krunner")

SOURCE = "krunner osi -next"


@dataclass
class RepoResult:
    """What one repository cost and produced.

    `lookups` counts deps.dev requests that actually left the machine, as opposed
    to `packages`, which counts rows written. The difference is the response cache
    doing its job, and tracking the ratio over time is the only way to see whether
    the TTL is set sensibly. None when not counted.
    """
    repo_id: str
    files: int
    packages: int
    duration_ms: int
    outcome: str
    lookups: Optional[int] = None


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
    budget_exhausted: bool = False

    @property
    def packages(self):
        return sum(r.packages for r in self.repos)


def _run_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_next_batch(db, limit, request_id=None, write_csv=False, max_seconds=None,
                   kospex_query=None, kdeps=None, echo=None):
    """Process the `limit` repositories most due for extraction.

    `max_seconds` bounds the batch in the dimension `limit` cannot. Measured on a
    real estate, per-repo cost spans four orders of magnitude -- 2ms for a
    repository with no dependency files against 109s for a monorepo with 215 of
    them and 1101 packages -- so a fixed count gives no predictable tick duration.

    The budget is checked *between* repositories, not during one. A repository in
    flight always finishes: abandoning it part-way would either save its rows with
    no outcome recorded, or record an outcome for work not done. Overshooting by
    one repository is the price of never half-processing one, and is why
    max_seconds=0 still does exactly one.

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

            # Checked after, never before: the first repository always runs, so a
            # budget can slow the schedule but can never stall it to zero
            # progress. A tight budget then overshoots by at most one repository.
            if max_seconds is not None and (time.monotonic() - started) >= max_seconds:
                remaining = len(queue) - len(result.repos)
                if remaining:
                    result.budget_exhausted = True
                    log.info(
                        "osi -next: time budget of %ss spent after %s repo(s), "
                        "%s left queued for the next run",
                        max_seconds, len(result.repos), remaining)
                    if echo:
                        echo(f"  time budget spent -- {remaining} repo(s) left queued")
                    break
                # The budget ran out exactly as the queue did. Nothing was left
                # behind, so this is a complete batch rather than a truncated one.
                result.budget_exhausted = True
    finally:
        lock.release()

    result.duration_ms = int((time.monotonic() - started) * 1000)
    log.info("osi -next: run %s done, %s repos, %s packages, %sms",
             result.run_id, len(result.repos), result.packages, result.duration_ms)

    # Diagnostics last, and never allowed to fail the run. The scan and its
    # outcome records are the product; this table exists to answer questions
    # about cost later, and losing a row of it must not cost a tick of work.
    try:
        record_run(db, result)
    except Exception as e:                       # noqa: BLE001
        log.warning("osi -next: could not record run diagnostics: %s", e)

    _warn_on_budget_overshoot(result, max_seconds, echo)
    return result


def _warn_on_budget_overshoot(result, max_seconds, echo):
    """Say so when one repository blew the budget on its own.

    `-max-seconds` bounds when new work *starts*, not total runtime, because a
    repository in flight is never abandoned. The overshoot is therefore the cost of
    the last repository, and that can be large: a 10s budget on the live estate
    produced a 109s tick, because babel/babel alone takes ~100s.

    Reported rather than left to be discovered, since a scheduler sized on the
    budget will otherwise see ticks it cannot explain.
    """
    if max_seconds is None or not result.repos:
        return

    actual = result.duration_ms / 1000
    if actual <= max_seconds:
        return

    slowest = max(result.repos, key=lambda r: r.duration_ms)
    log.warning(
        "osi -next: run %s took %.0fs against a %.0fs budget -- %s alone took %.0fs. "
        "The budget bounds when repositories START, and one in flight always finishes.",
        result.run_id, actual, max_seconds, slowest.repo_id, slowest.duration_ms / 1000)
    if echo:
        echo(f"  note: took {actual:.0f}s against a {max_seconds:.0f}s budget -- "
             f"{slowest.repo_id} alone took {slowest.duration_ms / 1000:.0f}s")


def _process_repo(db, repo_id, kospex_query, kdeps, write_csv, echo):
    """One repository: extract, enrich, save, then record the outcome."""
    from krunner import enrich_dependency_records

    started = time.monotonic()
    fetches_before = getattr(kospex_query, "url_fetches", 0)
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

    # kdeps shares kospex_query, so this counts the deps.dev requests enrichment
    # actually made for THIS repository -- packages served from url_cache do not
    # increment it. packages minus lookups is the cache earning its keep.
    lookups = getattr(kospex_query, "url_fetches", 0) - fetches_before

    return RepoResult(
        repo_id=repo_id, files=len(outcomes), packages=len(rows),
        duration_ms=duration_ms, outcome=outcome, lookups=lookups,
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
