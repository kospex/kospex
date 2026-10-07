"""The `krunner osi -next` run lock.

Design: changes/202610-krunner-osi-next.md

A tick that starts while a previous one is still running must not double-scan.
Three behaviours, each deliberate:

* A tick that finds a **live** lock exits 0. Overlap is normal operation under a
  short interval, not a failure, and surfacing it as a cron error makes a
  correctly configured frequent schedule noisy. The staleness bound printed by the
  run is the better signal for "the batch is too big for the interval".
* A lock older than the staleness window is **taken over**, with a warning. One
  crashed run blocking the schedule indefinitely is a worse failure than a double
  scan.
* The lock records PID and start time, so a stale lock is diagnosable rather than
  mysterious.
"""
import os
import time

import pytest

from kospex.osi_lock import (
    LOCK_FILENAME,
    LockBusy,
    OsiLock,
    read_lock,
)


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig
    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    HabitatConfig.reset_instance()
    yield home
    HabitatConfig.reset_instance()


def test_the_lock_is_created_and_removed(lock_dir):
    path = lock_dir / LOCK_FILENAME
    assert not path.exists()

    with OsiLock():
        assert path.exists(), "lock file must exist while held"

    assert not path.exists(), "lock file must be removed on clean exit"


def test_the_lock_records_pid_and_start_time(lock_dir):
    """A stale lock must be diagnosable, not mysterious."""
    with OsiLock():
        info = read_lock()

    assert info["pid"] == os.getpid()
    assert info["started_at"].startswith("20")


def test_a_second_run_cannot_take_a_live_lock(lock_dir):
    with OsiLock():
        with pytest.raises(LockBusy) as exc:
            with OsiLock():
                pass

    # The caller needs the held lock's details to log something useful.
    assert exc.value.pid == os.getpid()
    assert exc.value.started_at


def test_a_stale_lock_is_taken_over(lock_dir):
    """One crashed run must not block the schedule forever."""
    stale = OsiLock(stale_after_seconds=0.01)
    stale.acquire()
    time.sleep(0.05)

    taken_over = OsiLock(stale_after_seconds=0.01)
    with taken_over:                      # must not raise
        info = read_lock()
        assert info["pid"] == os.getpid()

    assert not (lock_dir / LOCK_FILENAME).exists()


def test_a_lock_within_the_staleness_window_is_still_live(lock_dir):
    """The takeover must be bounded by the window, not unconditional."""
    held = OsiLock(stale_after_seconds=3600)
    held.acquire()
    try:
        with pytest.raises(LockBusy):
            with OsiLock(stale_after_seconds=3600):
                pass
    finally:
        held.release()


def test_the_lock_is_released_when_the_body_raises(lock_dir):
    """A failing batch must not leave the schedule blocked until the timeout."""
    path = lock_dir / LOCK_FILENAME

    with pytest.raises(RuntimeError):
        with OsiLock():
            raise RuntimeError("batch blew up")

    assert not path.exists(), "lock must be released even when the run fails"


def test_release_tolerates_a_lock_file_already_gone(lock_dir):
    """Removing the file by hand mid-run must not mask the real error."""
    lock = OsiLock()
    lock.acquire()
    (lock_dir / LOCK_FILENAME).unlink()

    lock.release()          # must not raise


def test_read_lock_returns_none_when_not_held(lock_dir):
    assert read_lock() is None


def test_a_corrupt_lock_file_is_treated_as_stale(lock_dir):
    """A truncated or hand-edited lock must not wedge the schedule.

    An unparseable lock carries no start time, so it cannot be aged. Treating it
    as live would block every subsequent tick with no way to recover except manual
    deletion, which is the failure the staleness window exists to avoid.
    """
    (lock_dir / LOCK_FILENAME).write_text("{not json")

    with OsiLock():                       # must not raise
        assert read_lock()["pid"] == os.getpid()
