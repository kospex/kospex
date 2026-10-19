"""`osi_runs.started_at` must hold the start, and `finished_at` the finish (#259).

The column was declared "UTC, when this repository began" and held the moment the
repository *finished*: `record_repo_run()` defaulted it to now, and the only
production caller runs after extraction, enrichment, save and outcome are all
done.

Both ends of the window have to be stored, because neither is derivable from the
other. `duration_ms` comes from `time.monotonic()`, which does not advance while
the machine is asleep -- on the first whole-estate sweep that was 1h51m of a 5h34m
wall clock -- so `finished_at - duration_ms` is not the start and
`started_at + duration_ms` is not the finish. Storing one end plus a duration
measures cost, not occupancy.

Resolution is one second, so a test that needs to tell start from finish has to
make the repository take longer than that. `_slow_extract` is why this module
sleeps.
"""
import time

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex.osi_run_log import TBL_OSI_RUNS, record_repo_run

A = "github.com~acme~a"

# Long enough to separate two timestamps at one-second resolution, with margin
# for a second boundary landing mid-call.
SLOW_SECONDS = 1.2


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig
    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    HabitatConfig.reset_instance()
    yield home
    HabitatConfig.reset_instance()


def _db():
    """Every baseline table, then the shipped migrations -- as a real DB is built."""
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(memory=True)
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    Migrator(db).apply_pending()
    return db


@pytest.fixture
def estate(tmp_path):
    db = _db()
    d = tmp_path / "a"
    d.mkdir()
    db[KospexSchema.TBL_REPOS].insert(
        {"_repo_id": A, "_git_server": "github.com", "_git_owner": "acme",
         "_git_repo": "a", "file_path": str(d)}, pk="_repo_id")
    db[KospexSchema.TBL_FILE_METADATA].insert(
        {"_repo_id": A, "Provider": "requirements.txt", "Filename": "requirements.txt",
         "hash": "h0", "latest": 1, "tech_type": "|dependencies|"},
        pk=["Provider", "hash", "_repo_id"])
    (d / "requirements.txt").write_text("requests==2.31.0\n")
    return db


@pytest.fixture
def slow_repo(monkeypatch):
    """Make one repository take longer than the timestamp resolution.

    Patched at `osi_next.extract_repo`, the name `_process_repo` actually calls,
    so the sleep lands inside the window being measured rather than beside it.
    """
    from kospex import osi_next
    real = osi_next.extract_repo

    def _slow_extract(*args, **kwargs):
        time.sleep(SLOW_SECONDS)
        return real(*args, **kwargs)

    monkeypatch.setattr(osi_next, "extract_repo", _slow_extract)


@pytest.fixture(autouse=True)
def _no_depsdev(monkeypatch):
    from kospex_dependencies import KospexDependencies
    monkeypatch.setattr(
        KospexDependencies, "depsdev_record",
        lambda self, ptype, name, version: {
            "package_name": name, "package_version": version,
            "package_type": ptype, "versions_behind": 1, "advisories": 0,
            "resolution": "resolved", "published_at": "2026-01-01T00:00:00Z",
        })


def _row(db):
    return next(iter(db.query(f"SELECT * FROM {TBL_OSI_RUNS}")))


# --- the schema --------------------------------------------------------------

def test_the_runs_table_has_a_finished_at_column():
    """Both ends of the window, because neither can be derived from the other."""
    assert "finished_at" in _db()[TBL_OSI_RUNS].columns_dict


# --- the defect, through the production path ---------------------------------

def test_started_at_is_the_start_not_the_finish(estate, slow_repo):
    """The defect: a repository taking over a second recorded its finish time.

    Driven through run_next_batch, so it fails if the fix lands in
    record_repo_run but the call site still omits the start.
    """
    from kospex.osi_next import run_next_batch
    from kospex.osi_run_log import _utc_now_iso

    before = _utc_now_iso()
    run_next_batch(estate, limit=1)
    after = _utc_now_iso()

    row = _row(estate)
    assert row["started_at"] is not None, "no start recorded at all"
    # The repository occupied nearly all of before..after, so a start recorded at
    # completion is strictly later than `before`.
    assert row["started_at"] == before, (
        f"started_at {row['started_at']!r} is not the start "
        f"(window was {before} .. {after})")


def test_finished_at_follows_started_at(estate, slow_repo):
    from kospex.osi_next import run_next_batch

    run_next_batch(estate, limit=1)

    row = _row(estate)
    assert row["started_at"] < row["finished_at"], (
        f"start {row['started_at']!r} must precede finish {row['finished_at']!r} "
        f"for a repository that took over {SLOW_SECONDS}s")


def test_the_recorded_window_covers_the_measured_duration(estate, slow_repo):
    """Wall-clock occupancy is at least the monotonic cost, never less.

    Catches a start and finish that are each plausible but not the same window --
    for instance a start captured per batch rather than per repository.
    """
    from datetime import datetime, timezone
    from kospex.osi_next import run_next_batch

    run_next_batch(estate, limit=1)

    row = _row(estate)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    start = datetime.strptime(row["started_at"], fmt).replace(tzinfo=timezone.utc)
    finish = datetime.strptime(row["finished_at"], fmt).replace(tzinfo=timezone.utc)
    wall_ms = (finish - start).total_seconds() * 1000

    # One second of slack: the timestamps are truncated to the second, so a
    # genuine window can measure up to 1s short of the duration it contains.
    assert wall_ms >= row["duration_ms"] - 1000, (
        f"wall window {wall_ms}ms cannot be shorter than the measured "
        f"{row['duration_ms']}ms it is supposed to contain")


# --- the default that was doing the lying ------------------------------------

def test_an_unknown_start_is_recorded_as_unknown():
    """NULL, not now. A caller that does not know the start must not invent one.

    This was the root cause: `started_at or _utc_now_iso()` meant every caller
    that omitted it got a confident wrong answer instead of a missing one.
    """
    from kospex.osi_next import RepoResult
    db = _db()

    record_repo_run(db, "run1", RepoResult(
        repo_id=A, files=1, packages=1, duration_ms=5, outcome="extracted"))

    assert _row(db)["started_at"] is None


def test_a_supplied_start_is_stored_verbatim():
    from kospex.osi_next import RepoResult
    db = _db()

    record_repo_run(db, "run1", RepoResult(
        repo_id=A, files=1, packages=1, duration_ms=5, outcome="extracted"),
        started_at="2026-10-19T01:02:03Z")

    assert _row(db)["started_at"] == "2026-10-19T01:02:03Z"


def test_the_finish_is_stamped_when_not_supplied():
    """Unlike the start, `finished_at` legitimately defaults to now -- the row is
    written at the finish, so now *is* the finish."""
    from kospex.osi_next import RepoResult
    from kospex.osi_run_log import _utc_now_iso
    db = _db()

    before = _utc_now_iso()
    record_repo_run(db, "run1", RepoResult(
        repo_id=A, files=1, packages=1, duration_ms=5, outcome="extracted"))
    after = _utc_now_iso()

    assert before <= _row(db)["finished_at"] <= after
