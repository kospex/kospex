"""A lock file that stops two scheduled runs of the same job overlapping.

Used by `krunner osi -next` (through `kospex.osi_lock.OsiLock`) and by
`kgit sync --org` / `--user`, one file per org. Behaviours, each deliberate:

* A run finding a **live** lock raises LockBusy, which the command turns into a
  message and **exit 0**. Overlap is normal under a short cron interval, not a
  failure, and reporting it as an error makes a correctly configured schedule
  noisy.
* **A lock whose process has died is taken over at once**, with a warning. One
  crashed run must not block the schedule.
* **A lock whose process is still running is never taken over for being slow**,
  only once it is older than `stale_after_seconds`. That bound is a backstop for
  a reused PID (a crashed run's PID now belonging to an unrelated process), not a
  guess at how long a run takes, so callers set it well past their longest run.
* **Whether the process is alive is only checked on the same host** (the lock
  records the hostname) and not on Windows, where `os.kill(pid, 0)` would send a
  Ctrl-C. Elsewhere, including a lock written from another container sharing
  KOSPEX_HOME, the age bound is all there is.
* **Taking the lock is atomic** (`O_CREAT | O_EXCL`), so two runs starting in the
  same instant can't both get it. Taking over a dead or stale lock re-reads it
  first and gives up if it changed, so two runs taking over the same stale lock
  don't both succeed.
* The lock records PID, hostname and start time, so a stuck lock is diagnosable.

Scope: this guards concurrent runs on one host, or containers on one host,
writing one database. It is advisory, not a mutex: two machines sharing a
database over a network filesystem are out of scope, as is kospex generally.
"""
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class LockBusy(Exception):
    """Raised when a live lock is held by another run."""

    def __init__(self, pid=None, started_at=None, age_seconds=None, name="run"):
        self.pid = pid
        self.started_at = started_at
        self.age_seconds = age_seconds
        super().__init__(
            f"another {name} run is in progress (pid {pid}, started {started_at})"
        )


def read_lock(path) -> Optional[dict]:
    """The lock's contents, or None if not held or unreadable.

    Returns None for a corrupt lock as well as a missing one: both mean "no
    usable lock", and the caller's next step is the same.
    """
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def pid_alive(pid) -> Optional[bool]:
    """True if a process with this PID exists here, False if not, None if unknown.

    Unknown on Windows (signal 0 there is CTRL_C_EVENT, not a probe) and for a
    PID that isn't a positive integer.
    """
    if os.name == "nt" or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True             # exists, owned by another user
    except OSError:
        return None
    return True


def _age_seconds(started_at):
    """Age of a lock in seconds, or None if its start time is unusable."""
    if not started_at:
        return None
    try:
        started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - started).total_seconds()


class RunLock:
    """Context manager holding a run lock at `path`.

    Raises LockBusy if a live lock is held. Takes over a lock whose process has
    died, one older than `stale_after_seconds`, or one that cannot be parsed.
    `name` is used in messages; `logger` receives the takeover warnings.
    """

    def __init__(self, path, name, stale_after_seconds, logger):
        self.path = Path(path)
        self.name = name
        self.stale_after_seconds = stale_after_seconds
        self.log = logger

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._create():
            return self

        raw = self._read_raw()
        existing = read_lock(self.path)
        if raw is None:
            # Removed between our create and our read: the holder just finished.
            if self._create():
                return self
            raise LockBusy(name=self.name)

        self._check_takeover(existing)

        # Take over only the lock we judged. If another run replaced it in the
        # meantime, that run holds a fresh lock.
        if self._read_raw() != raw:
            current = read_lock(self.path) or {}
            raise LockBusy(pid=current.get("pid"), started_at=current.get("started_at"),
                           name=self.name)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        if self._create():
            return self
        current = read_lock(self.path) or {}
        raise LockBusy(pid=current.get("pid"), started_at=current.get("started_at"),
                       name=self.name)

    def _check_takeover(self, existing):
        """Raise LockBusy unless `existing` may be taken over; log why it may."""
        if existing is None:
            self.log.warning("%s lock is unreadable, taking it over", self.name)
            return

        pid, started_at = existing.get("pid"), existing.get("started_at")
        age = _age_seconds(started_at)
        same_host = existing.get("host") == socket.gethostname()
        alive = pid_alive(pid) if same_host else None

        if alive is False:
            self.log.warning("%s lock is held by pid %s, which is no longer running "
                             "(started %s) -- taking it over", self.name, pid, started_at)
            return
        if age is None:
            # No usable start time, so it can't be aged. Treating it as live would
            # block every run with no recovery but deleting the file by hand.
            self.log.warning("%s lock has no usable start time, taking it over", self.name)
            return
        if age < self.stale_after_seconds:
            raise LockBusy(pid=pid, started_at=started_at, age_seconds=age, name=self.name)
        self.log.warning(
            "%s lock is stale (pid %s, started %s, %.0fs old > %.0fs) "
            "-- taking it over", self.name, pid, started_at, age, self.stale_after_seconds)

    def _create(self):
        """Create the lock file only if none exists. True if this run now holds it."""
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }))
        return True

    def _read_raw(self):
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            return b""

    def release(self):
        """Remove the lock if this run still holds it. Tolerates it being gone.

        release() runs on the way out of a failing run too, and a missing lock
        there is not the interesting error -- raising would mask whatever actually
        went wrong. A lock another run has taken over (this one was judged stale)
        is left alone.
        """
        current = read_lock(self.path)
        if current is not None and current.get("pid") != os.getpid():
            self.log.warning("%s lock at %s is now held by pid %s; leaving it",
                             self.name, self.path, current.get("pid"))
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            self.log.warning("could not remove %s lock at %s: %s", self.name, self.path, e)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
