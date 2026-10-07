"""Run diagnostics for `osi -next` — migration 0008 and the rows it holds.

Design: changes/202610-krunner-osi-next.md

The log lines say what happened in one run. They cannot answer the questions that
matter over time, because log rotation discards them:

* **What N actually fits my interval?** Per-repo cost on the live estate spans 2ms
  to 109s, so this cannot be reasoned about — only measured.
* **Is a repository getting slower?**
* **Is the response cache doing its job?** `lookups` counts deps.dev requests
  actually made, separately from `packages` written, which is the only way to see
  cache effectiveness over time.
* **Is the schedule completing passes, or quietly falling behind?**

`observations` cannot hold this: its primary key includes `latest`, so it keeps one
current row per key and the demote pattern raises on the third write. Hence a table
of its own, keyed on `(run_id, _repo_id)` so history accumulates.
"""
import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex.osi_run_log import (
    TBL_OSI_RUNS,
    record_run,
    run_summary,
)

A, B = "github.com~acme~a", "github.com~acme~b"


def _db():
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(memory=True)
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    Migrator(db).apply_pending()
    return db


def _result(run_id="20261007T090000Z", repos=None):
    from kospex.osi_next import BatchResult, RepoResult
    r = BatchResult(run_id=run_id)
    r.repos = repos if repos is not None else [
        RepoResult(repo_id=A, files=2, packages=37, duration_ms=2400, outcome="extracted"),
        RepoResult(repo_id=B, files=0, packages=0, duration_ms=2, outcome="empty"),
    ]
    r.duration_ms = sum(x.duration_ms for x in r.repos)
    return r


# --- migration ---------------------------------------------------------------

def test_a_migrated_db_has_the_runs_table():
    db = _db()
    assert TBL_OSI_RUNS in db.table_names()


def test_the_table_is_keyed_on_run_and_repo_so_history_accumulates():
    """One row per (run, repo). Two runs over one repo must be two rows.

    This is the whole reason it is not in observations, whose PK keeps only the
    current row per key.
    """
    db = _db()

    record_run(db, _result(run_id="run1"))
    record_run(db, _result(run_id="run2"))

    rows = list(db.query(f"SELECT run_id, _repo_id FROM {TBL_OSI_RUNS} ORDER BY run_id"))
    assert len(rows) == 4, rows
    assert {r["run_id"] for r in rows} == {"run1", "run2"}


def test_the_index_is_not_declared_in_the_baseline_schema():
    """0008 owns its table. Declaring it in SQL_CREATE_* too breaks clean installs.

    A new DB runs the baseline CREATEs and then bootstraps migrations, so anything
    declared in both places collides -- the trap the 0006 columns are kept out of
    SQL_CREATE_DEPENDENCY_DATA to avoid.
    """
    for sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        assert TBL_OSI_RUNS not in sql


# --- what gets recorded ------------------------------------------------------

def test_a_run_records_one_row_per_repo_with_its_cost():
    db = _db()

    record_run(db, _result())

    rows = {r["_repo_id"]: r for r in db.query(f"SELECT * FROM {TBL_OSI_RUNS}")}
    assert rows[A]["files"] == 2
    assert rows[A]["packages"] == 37
    assert rows[A]["duration_ms"] == 2400
    assert rows[A]["outcome"] == "extracted"
    assert rows[B]["packages"] == 0
    assert rows[B]["outcome"] == "empty"


def test_the_git_columns_are_derived():
    db = _db()
    record_run(db, _result())

    row = next(iter(db.query(
        f"SELECT * FROM {TBL_OSI_RUNS} WHERE _repo_id = ?", [A])))
    assert row["_git_server"] == "github.com"
    assert row["_git_owner"] == "acme"
    assert row["_git_repo"] == "a"


def test_a_skipped_run_records_nothing():
    """A tick that found a live lock did no work -- there is nothing to log."""
    from kospex.osi_next import BatchResult
    db = _db()
    skipped = BatchResult(run_id="run1", skipped=True)

    record_run(db, skipped)

    assert list(db.query(f"SELECT * FROM {TBL_OSI_RUNS}")) == []


def test_an_empty_batch_records_nothing():
    db = _db()
    record_run(db, _result(repos=[]))

    assert list(db.query(f"SELECT * FROM {TBL_OSI_RUNS}")) == []


# --- what it makes answerable ------------------------------------------------

def test_a_run_can_be_summarised_for_sizing():
    """The question the log lines cannot answer: does N fit my interval?"""
    db = _db()
    record_run(db, _result(run_id="run1"))

    summary = run_summary(db, "run1")

    assert summary["repos"] == 2
    assert summary["packages"] == 37
    assert summary["total_ms"] == 2402
    assert summary["slowest_repo_id"] == A
    assert summary["slowest_ms"] == 2400


def test_summarising_an_unknown_run_returns_none():
    assert run_summary(_db(), "never-happened") is None


def test_lookups_are_recorded_separately_from_packages():
    """Cache effectiveness is lookups/packages, and needs both over time."""
    from kospex.osi_next import BatchResult, RepoResult
    db = _db()
    result = BatchResult(run_id="run1")
    result.repos = [RepoResult(repo_id=A, files=1, packages=40, duration_ms=10,
                               outcome="extracted", lookups=12)]
    result.duration_ms = 10

    record_run(db, result)

    row = next(iter(db.query(f"SELECT packages, lookups FROM {TBL_OSI_RUNS}")))
    assert row["packages"] == 40
    assert row["lookups"] == 12, "a cached lookup is not an HTTP request"
