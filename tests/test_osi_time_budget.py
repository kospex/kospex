"""`-max-seconds`: bound a batch by time, not only by repository count.

Design: changes/202610-krunner-osi-next.md

Measured on the live estate, per-repo cost spans four orders of magnitude:

    benjaminp/six        0 files     0 packages        2 ms
    babel/babel        215 files  1101 packages  109,011 ms

So a fixed `N` cannot give a predictable tick duration. A batch that happens to
draw two monorepos overruns any short interval, the next tick skips on the lock,
and throughput falls below whatever the operator planned for.

`-max-seconds` bounds the batch in the one dimension `N` cannot. It is a budget
checked *between* repositories, not a timeout: a repository in flight always
finishes, because abandoning it part-way would leave its rows saved with no
outcome recorded, or an outcome recorded for work not done. Overshooting the
budget by one repository is the cost of never half-processing one.
"""
import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex.osi_next import run_next_batch

REPOS = [f"github.com~acme~r{i}" for i in range(6)]


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


def test_without_a_budget_the_whole_batch_runs(estate):
    result = run_next_batch(estate, limit=6)
    assert len(result.repos) == 6


def test_a_budget_stops_the_batch_early(estate, monkeypatch):
    """Each repo is made to cost ~40ms; a 100ms budget must not run all six."""
    import kospex.osi_next as osi_next
    real = osi_next._process_repo

    def slow(*args, **kwargs):
        import time
        time.sleep(0.04)
        return real(*args, **kwargs)

    monkeypatch.setattr(osi_next, "_process_repo", slow)

    result = run_next_batch(estate, limit=6, max_seconds=0.1)

    assert 0 < len(result.repos) < 6, f"ran {len(result.repos)} of 6"
    assert result.budget_exhausted is True


def test_a_repo_in_flight_is_never_abandoned(estate, monkeypatch):
    """A zero budget still completes one repository, fully recorded.

    Stopping mid-repository would either save rows with no outcome, or record an
    outcome for work not done. Both are worse than overshooting by one.
    """
    from kospex.osi_queue import get_outcome

    result = run_next_batch(estate, limit=6, max_seconds=0)

    assert len(result.repos) == 1, "a budget of zero must still do one repo"
    assert get_outcome(estate, result.repos[0].repo_id) is not None
    assert result.budget_exhausted is True


def test_an_unexhausted_budget_is_not_flagged(estate):
    result = run_next_batch(estate, limit=2, max_seconds=3600)

    assert len(result.repos) == 2
    assert result.budget_exhausted is False


def test_a_stopped_batch_leaves_the_rest_queued(estate):
    """What was not reached must come up next, ahead of what was done."""
    first = run_next_batch(estate, limit=6, max_seconds=0)
    done = {r.repo_id for r in first.repos}
    assert len(done) == 1

    # Exactly the five not reached. Asking for 6 would legitimately return the
    # examined one too, since round-robin serves it last rather than excluding it.
    second = run_next_batch(estate, limit=5)
    seen = [r.repo_id for r in second.repos]

    assert set(seen) == set(REPOS) - done
    assert done | set(seen) == set(REPOS)


def test_an_examined_repo_sorts_behind_the_unexamined_ones(estate):
    """Round-robin excludes nothing; it orders. The examined repo comes last."""
    first = run_next_batch(estate, limit=6, max_seconds=0)
    done = first.repos[0].repo_id

    order = [r.repo_id for r in run_next_batch(estate, limit=6).repos]

    assert order[-1] == done, f"expected {done} last, got {order}"


def test_an_overshoot_is_reported_rather_than_left_to_be_discovered(estate, monkeypatch):
    """The budget bounds when repos START, so the overshoot is the last repo's cost.

    On the live estate a 10s budget produced a 109s tick, because babel/babel alone
    takes ~100s. A scheduler sized on the budget would otherwise see ticks it
    cannot explain.
    """
    import kospex.osi_next as osi_next
    real = osi_next._process_repo

    def slow(*args, **kwargs):
        import time
        time.sleep(0.15)
        return real(*args, **kwargs)

    monkeypatch.setattr(osi_next, "_process_repo", slow)
    said = []

    result = run_next_batch(estate, limit=6, max_seconds=0.05, echo=said.append)

    assert result.duration_ms / 1000 > 0.05
    assert any("against a" in str(line) for line in said), said


def test_no_overshoot_note_when_the_budget_was_respected(estate):
    said = []
    run_next_batch(estate, limit=2, max_seconds=3600, echo=said.append)

    assert not any("against a" in str(line) for line in said), said


def test_the_lock_is_released_when_the_budget_stops_the_batch(estate, _home):
    from kospex.osi_lock import LOCK_FILENAME

    run_next_batch(estate, limit=6, max_seconds=0)

    assert not (_home / LOCK_FILENAME).exists()
