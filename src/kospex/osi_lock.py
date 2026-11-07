"""Run lock for `krunner osi -next`, so overlapping cron ticks cannot double-scan.

Design: changes/202610-krunner-osi-next.md

The mechanism is `kospex.run_lock.RunLock` (live lock means exit 0, a dead or
stale lock is taken over, atomic create, PID / host / start time recorded). This
module fixes osi's file, name, logger and staleness bound.
"""
from pathlib import Path
from typing import Optional

import kospex_utils as KospexUtils
from kospex.run_lock import LockBusy, RunLock
from kospex.run_lock import read_lock as _read_lock

__all__ = ["LOCK_FILENAME", "DEFAULT_STALE_AFTER_SECONDS", "LockBusy", "OsiLock",
           "lock_path", "read_lock"]

log = KospexUtils.get_kospex_logger("krunner")

LOCK_FILENAME = "osi-next.lock"

# A batch is bounded by deps.dev round-trips, so a long one is slow rather than
# wedged. Six hours is well past any plausible batch and well short of leaving a
# crashed run to block a daily schedule. It is only the backstop: a lock whose
# process has died on this host is taken over at once.
DEFAULT_STALE_AFTER_SECONDS = 6 * 60 * 60


def lock_path() -> Path:
    """Where the lock lives. Under KOSPEX_HOME, beside the database it guards."""
    from kospex.habitat_config import HabitatConfig
    return Path(HabitatConfig.get_instance().home) / LOCK_FILENAME


def read_lock() -> Optional[dict]:
    """The current lock's contents, or None if not held or unreadable."""
    return _read_lock(lock_path())


class OsiLock(RunLock):
    """The `krunner osi -next` lock. See `kospex.run_lock.RunLock`."""

    def __init__(self, stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS):
        super().__init__(lock_path(), "osi -next", stale_after_seconds, log)
