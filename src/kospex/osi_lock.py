"""Run lock for `krunner osi -next`, so overlapping cron ticks cannot double-scan.

Design: changes/202610-krunner-osi-next.md

Three deliberate behaviours:

* A tick finding a **live** lock raises LockBusy, which the command turns into a
  log line and **exit 0**. Overlap is normal under a short interval, not a
  failure, and reporting it as a cron error makes a correctly configured frequent
  schedule noisy. The staleness bound the run prints is the better signal for
  "the batch is too big for the interval".
* A lock older than `stale_after_seconds` is **taken over** with a warning. One
  crashed run blocking the schedule indefinitely is a worse failure than a double
  scan.
* The lock records PID and start time so a stale lock is diagnosable.

Scope: this guards concurrent `-next` runs on one host writing one database. It is
advisory, not a mutex -- two machines sharing a database over a network filesystem
are out of scope, as is kospex generally.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import kospex_utils as KospexUtils

log = KospexUtils.get_kospex_logger("krunner")

LOCK_FILENAME = "osi-next.lock"

# A batch is bounded by deps.dev round-trips, so a long one is slow rather than
# wedged. Six hours is well past any plausible batch and well short of leaving a
# crashed run to block a daily schedule.
DEFAULT_STALE_AFTER_SECONDS = 6 * 60 * 60


class LockBusy(Exception):
    """Raised when a live lock is held by another run."""

    def __init__(self, pid=None, started_at=None, age_seconds=None):
        self.pid = pid
        self.started_at = started_at
        self.age_seconds = age_seconds
        super().__init__(
            f"another osi -next run is in progress (pid {pid}, started {started_at})"
        )


def lock_path() -> Path:
    """Where the lock lives. Under KOSPEX_HOME, beside the database it guards."""
    from kospex.habitat_config import HabitatConfig
    return Path(HabitatConfig.get_instance().home) / LOCK_FILENAME


def read_lock() -> Optional[dict]:
    """The current lock's contents, or None if not held or unreadable.

    Returns None for a corrupt lock as well as a missing one: both mean "no
    usable lock", and the caller's next step is the same.
    """
    path = lock_path()
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


class OsiLock:
    """Context manager holding the run lock.

    Raises LockBusy if a live lock is held. Takes over a lock older than
    `stale_after_seconds`, or one that cannot be parsed.
    """

    def __init__(self, stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS):
        self.stale_after_seconds = stale_after_seconds
        self.path = lock_path()

    def acquire(self):
        existing = read_lock()

        if existing is not None:
            age = self._age_seconds(existing.get("started_at"))
            if age is None:
                # Parseable JSON but no usable start time, so it cannot be aged.
                # Treating it as live would block every tick with no recovery but
                # manual deletion -- the failure the staleness window exists for.
                log.warning("osi -next lock has no usable start time, taking it over")
            elif age < self.stale_after_seconds:
                raise LockBusy(
                    pid=existing.get("pid"),
                    started_at=existing.get("started_at"),
                    age_seconds=age,
                )
            else:
                log.warning(
                    "osi -next lock is stale (pid %s, started %s, %.0fs old > %.0fs) "
                    "-- taking it over",
                    existing.get("pid"), existing.get("started_at"),
                    age, self.stale_after_seconds,
                )
        elif self.path.exists():
            # Present but unreadable: same reasoning as above.
            log.warning("osi -next lock is unreadable, taking it over")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "started_monotonic": None,
        }))
        return self

    def release(self):
        """Remove the lock. Tolerates the file already being gone.

        release() runs on the way out of a failing run too, and a missing lock
        there is not the interesting error -- raising would mask whatever actually
        went wrong.
        """
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("could not remove osi -next lock at %s: %s", self.path, e)

    def _age_seconds(self, started_at):
        """Age of a lock in seconds, or None if its start time is unusable."""
        if not started_at:
            return None
        try:
            started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
        return (datetime.now(timezone.utc) - started).total_seconds()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
