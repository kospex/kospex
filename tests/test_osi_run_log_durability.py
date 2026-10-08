"""Diagnostics must survive an interrupted batch.

`record_outcome` is called per repository, so an interrupted batch keeps what it
did and those repositories stay out of the queue. `record_run` was called once at
the end, so the same interruption threw away every diagnostics row for the batch.

Observed on a live run: 7 repositories had outcomes while `osi_runs` still showed
only the previous run's 5 rows.

That asymmetry is backwards. The runs most likely to be interrupted are the long
ones -- a `-next 25` that draws two monorepos and gets killed at four minutes --
and those are exactly the runs whose cost you most want recorded. Losing the
measurement precisely when it would be most useful makes the table untrustworthy
for the sizing question it exists to answer.

So diagnostics are now written per repository, alongside the outcome.
"""
import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex.osi_next import run_next_batch
from kospex.osi_run_log import TBL_OSI_RUNS

REPOS = [f"github.com~acme~r{i}" for i in range(5)]


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig
    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    HabitatConfig.reset_instance()
    yield home
    HabitatConfig.reset_instance()


@pytest.fixture
def estate(tmp_path):
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(memory=True)
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    Migrator(db).apply_pending()
    for rid in REPOS:
        d = tmp_path / rid.replace("~", "_")
        d.mkdir()
        db[KospexSchema.TBL_REPOS].insert(
            {"_repo_id": rid, "_git_server": "github.com", "_git_owner": "acme",
             "_git_repo": rid.split("~")[-1], "file_path": str(d)}, pk="_repo_id")
    return db


def _rows(db):
    return list(db.query(f"SELECT _repo_id FROM {TBL_OSI_RUNS}"))


def test_a_completed_batch_records_every_repo(estate):
    run_next_batch(estate, limit=5)
    assert len(_rows(estate)) == 5


def test_an_interrupted_batch_keeps_the_diagnostics_it_earned(estate, monkeypatch):
    """A KeyboardInterrupt part-way must not discard the completed repositories.

    This is the whole point. Before, record_run ran only after the loop, so an
    interruption lost diagnostics for work that had actually been done -- and the
    outcome records for those same repositories survived, so they were removed
    from the queue with no record of what they cost.
    """
    import kospex.osi_next as osi_next
    real = osi_next._process_repo
    calls = {"n": 0}

    def blow_up_on_the_third(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(osi_next, "_process_repo", blow_up_on_the_third)

    with pytest.raises(KeyboardInterrupt):
        run_next_batch(estate, limit=5)

    assert len(_rows(estate)) == 2, (
        "the two completed repositories must keep their diagnostics")


def test_an_interrupted_batch_leaves_outcomes_and_diagnostics_consistent(estate, monkeypatch):
    """Every repo with an outcome must have a diagnostics row, and vice versa.

    The asymmetry that prompted this: outcomes were durable per repository while
    diagnostics were not, so an interrupted run left repositories marked examined
    with no record of their cost.
    """
    import kospex.osi_next as osi_next
    from kospex.osi_queue import OBSERVATION_KEY
    real = osi_next._process_repo
    calls = {"n": 0}

    def blow_up_on_the_fourth(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("killed mid-batch")
        return real(*args, **kwargs)

    monkeypatch.setattr(osi_next, "_process_repo", blow_up_on_the_fourth)

    with pytest.raises(RuntimeError):
        run_next_batch(estate, limit=5)

    examined = {r["_repo_id"] for r in estate.query(
        "SELECT _repo_id FROM observations WHERE observation_key = ?",
        [OBSERVATION_KEY])}
    measured = {r["_repo_id"] for r in _rows(estate)}

    assert examined == measured, f"examined {examined}, measured {measured}"
    assert len(examined) == 3


def test_the_lock_is_released_when_a_batch_is_interrupted(estate, monkeypatch, _home):
    """A crashed run must not leave the schedule blocked until the stale timeout."""
    import kospex.osi_next as osi_next
    from kospex.osi_lock import LOCK_FILENAME

    monkeypatch.setattr(osi_next, "_process_repo",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        run_next_batch(estate, limit=5)

    assert not (_home / LOCK_FILENAME).exists()


def test_a_diagnostics_write_failure_does_not_lose_the_scan(estate, monkeypatch):
    """The scan is the product; diagnostics are bookkeeping.

    A repository whose diagnostics row cannot be written must still be recorded as
    examined, or it would be rescanned forever.
    """
    import kospex.osi_next as osi_next
    from kospex.osi_queue import get_outcome

    monkeypatch.setattr(osi_next, "record_repo_run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))

    result = run_next_batch(estate, limit=2)

    assert len(result.repos) == 2
    for r in result.repos:
        assert get_outcome(estate, r.repo_id) is not None
