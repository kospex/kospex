"""The generic run lock behind `krunner osi -next` and `kgit sync --org`.

`tests/test_osi_lock.py` covers the behaviours osi relies on (live lock is busy,
a stale or corrupt lock is taken over, release on error). These cover what the
generic lock adds: a dead process's lock is taken over at once, a live one is
never taken over for being slow, the liveness check is only trusted on the same
host, the create and the takeover are atomic, and messages go to the caller's
logger.
"""
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from kospex.run_lock import LockBusy, RunLock, pid_alive, read_lock


class Log:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args)


def _ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path, pid, started_at, host=None):
    path.write_text(json.dumps({"pid": pid, "host": host or socket.gethostname(),
                                "started_at": started_at}))


@pytest.fixture
def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert pid_alive(p.pid) is False
    return p.pid


@pytest.fixture
def live_pid():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield p.pid
    p.kill()
    p.wait()


def lock(path, stale=7 * 24 * 3600, log=None):
    return RunLock(path, "kgit sync --org acme", stale, log or Log())


def test_a_dead_processs_lock_is_taken_over_at_once(tmp_path, dead_pid):
    """A crashed sync must not block the schedule until the age bound."""
    path = tmp_path / "x.lock"
    _write(path, dead_pid, _ago(seconds=5))
    log = Log()
    with lock(path, log=log):
        assert read_lock(path)["pid"] == os.getpid()
    assert any("no longer running" in w for w in log.warnings), log.warnings
    assert not path.exists()


def test_a_live_process_is_never_taken_over_for_being_slow(tmp_path, live_pid):
    """The bug in the osi-only lock: a 10-hour sync was taken over at 6 hours."""
    path = tmp_path / "x.lock"
    _write(path, live_pid, _ago(hours=10))
    with pytest.raises(LockBusy, match="another kgit sync --org acme run") as exc:
        lock(path).acquire()
    assert exc.value.pid == live_pid
    assert read_lock(path)["pid"] == live_pid, "the live run's lock must be untouched"


def test_past_the_bound_a_live_pid_is_taken_over_as_a_reused_pid(tmp_path, live_pid):
    """The age bound is only the backstop for a PID now owned by another process."""
    path = tmp_path / "x.lock"
    _write(path, live_pid, _ago(days=8))
    log = Log()
    with lock(path, log=log):
        assert read_lock(path)["pid"] == os.getpid()
    assert any("stale" in w for w in log.warnings)


def test_on_another_host_a_dead_looking_pid_is_not_trusted(tmp_path, dead_pid):
    """Another container sharing KOSPEX_HOME has its own PIDs: only age counts."""
    path = tmp_path / "x.lock"
    _write(path, dead_pid, _ago(minutes=5), host="some-other-container")
    with pytest.raises(LockBusy):
        lock(path).acquire()


def test_a_lock_from_before_the_host_was_recorded_falls_back_to_age(tmp_path, dead_pid):
    path = tmp_path / "x.lock"
    path.write_text(json.dumps({"pid": dead_pid, "started_at": _ago(minutes=5)}))
    with pytest.raises(LockBusy):
        lock(path).acquire()


def test_the_lock_records_pid_host_and_start_time(tmp_path):
    path = tmp_path / "x.lock"
    with lock(path):
        info = read_lock(path)
    assert info["pid"] == os.getpid()
    assert info["host"] == socket.gethostname()
    assert info["started_at"].startswith("20")


def test_a_second_lock_is_busy_and_does_not_overwrite_the_first(tmp_path):
    path = tmp_path / "x.lock"
    first = lock(path).acquire()
    before = path.read_bytes()
    with pytest.raises(LockBusy):
        lock(path).acquire()
    assert path.read_bytes() == before
    first.release()


def test_a_takeover_gives_up_if_another_run_took_the_lock_first(tmp_path, dead_pid,
                                                              monkeypatch):
    """Two runs taking over the same dead lock must not both get it."""
    path = tmp_path / "x.lock"
    _write(path, dead_pid, _ago(seconds=5))
    loser = lock(path)
    judge = RunLock._check_takeover

    def judge_then_lose_the_race(self, existing):
        judge(self, existing)
        _write(path, 424242, _ago(seconds=0))    # the other run's fresh lock

    monkeypatch.setattr(RunLock, "_check_takeover", judge_then_lose_the_race)
    with pytest.raises(LockBusy) as exc:
        loser.acquire()
    assert exc.value.pid == 424242
    assert read_lock(path)["pid"] == 424242, "the winner's lock must be untouched"


def test_release_leaves_a_lock_another_run_has_taken_over(tmp_path):
    path = tmp_path / "x.lock"
    mine = lock(path).acquire()
    _write(path, 424242, _ago(seconds=0))
    log = Log()
    mine.log = log
    mine.release()
    assert read_lock(path)["pid"] == 424242
    assert any("now held by pid 424242" in w for w in log.warnings)


def test_messages_go_to_the_callers_logger(tmp_path):
    """kgit's lock warnings used to land in krunner.log."""
    path = tmp_path / "x.lock"
    path.write_text("{not json")
    log = Log()
    with lock(path, log=log):
        pass
    assert log.warnings == ["kgit sync --org acme lock is unreadable, taking it over"]


def test_locks_at_different_paths_are_independent(tmp_path):
    a, b = tmp_path / "org-sync-github.com~acme.lock", tmp_path / "org-sync-github.com~other.lock"
    with lock(a):
        with lock(b):
            assert a.exists() and b.exists()
    assert not a.exists() and not b.exists()


@pytest.mark.parametrize("pid", [None, 0, -1, "123", True, 1.5])
def test_pid_alive_is_unknown_for_a_pid_that_isnt_one(pid):
    assert pid_alive(pid) is None


def test_pid_alive_is_unknown_on_windows(monkeypatch):
    """os.kill(pid, 0) on Windows sends CTRL_C_EVENT; it must never be called."""
    import kospex.run_lock as run_lock

    def boom(*a):
        raise AssertionError("os.kill called")

    monkeypatch.setattr(run_lock.os, "name", "nt")
    monkeypatch.setattr(run_lock.os, "kill", boom)
    assert pid_alive(os.getpid()) is None


def test_pid_alive_for_this_process_and_a_finished_one(dead_pid):
    assert pid_alive(os.getpid()) is True
    assert pid_alive(dead_pid) is False
