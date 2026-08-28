"""Tests for timezone-correct date aggregation and ordering (#154).

Commit dates are stored as ISO-8601 text carrying each committer's local UTC
offset. Text comparison of ISO-8601 is only valid when every value shares one
offset; kospex has 33 distinct offsets across 254,997 commits, so
MAX(committer_when) and ORDER BY committer_when return the wrong row whenever
the true latest commit has a more westerly offset than a near tie.

The fix aggregates on the true instant and formats the result back to ISO-8601
in UTC:

    strftime('%Y-%m-%dT%H:%M:%SZ', MAX(unixepoch(committer_when)), 'unixepoch')

MAX(unixepoch(...)) alone would return an integer, breaking days_ago() and
`new Date()` in the templates. The SQLite bare-column trick (SELECT MAX(...), x)
does return the winning row's string, but its behaviour is undefined with more
than one min/max aggregate — and several queries select first_commit and
last_commit together — so it cannot be the general form.
"""
import os

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
import kospex_utils as KospexUtils
from kospex_query import KospexData, KospexQuery

# Same instant ordering trap as production: 16:59:54-04:00 (20:59:54Z) is
# LATER than 18:34:03+01:00 (17:34:03Z), but sorts lower as text.
EARLIER_LOOKS_LATER = "2026-04-16T18:34:03+01:00"   # 17:34:03Z
LATER_LOOKS_EARLIER = "2026-04-16T16:59:54-04:00"   # 20:59:54Z


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    keys = ("KOSPEX_CODE", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_HOME", "KOSPEX_LOGS")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _db(rows):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMITS)
    db[KospexSchema.TBL_COMMITS].insert_all(rows, pk=["_repo_id", "hash"])
    return db


def _two_commits():
    return _db([
        {"_repo_id": "s~o~r", "hash": "looks_later",
         "committer_when": EARLIER_LOOKS_LATER, "author_when": EARLIER_LOOKS_LATER,
         "author_email": "a@e.com"},
        {"_repo_id": "s~o~r", "hash": "really_later",
         "committer_when": LATER_LOOKS_EARLIER, "author_when": LATER_LOOKS_EARLIER,
         "author_email": "a@e.com"},
    ])


def _run(db, kd):
    return list(db.query(kd.generate_sql(), kd.get_bind_parameters()))


def test_text_max_picks_the_wrong_row_without_the_fix():
    """Characterises the bug, so the fix below is demonstrably doing something."""
    db = _two_commits()

    row = next(iter(db.query(
        "SELECT MAX(committer_when) AS last_commit FROM commits")))

    assert row["last_commit"] == EARLIER_LOOKS_LATER


def test_select_latest_date_picks_the_true_latest():
    db = _two_commits()
    kd = KospexData(kospex_db=db)
    kd.from_table(KospexSchema.TBL_COMMITS)
    kd.select_latest_date("committer_when", "last_commit")

    assert _run(db, kd)[0]["last_commit"] == "2026-04-16T20:59:54Z"


def test_select_earliest_date_picks_the_true_earliest():
    db = _two_commits()
    kd = KospexData(kospex_db=db)
    kd.from_table(KospexSchema.TBL_COMMITS)
    kd.select_earliest_date("committer_when", "first_commit")

    assert _run(db, kd)[0]["first_commit"] == "2026-04-16T17:34:03Z"


def test_earliest_and_latest_together_are_both_correct():
    """The reason the SQLite bare-column trick can't be used: its behaviour is
    undefined with more than one min/max aggregate, and repo_summary needs both."""
    db = _two_commits()
    kd = KospexData(kospex_db=db)
    kd.from_table(KospexSchema.TBL_COMMITS)
    kd.select_earliest_date("committer_when", "first_commit")
    kd.select_latest_date("committer_when", "last_commit")

    row = _run(db, kd)[0]

    assert row["first_commit"] == "2026-04-16T17:34:03Z"
    assert row["last_commit"] == "2026-04-16T20:59:54Z"


def test_result_is_a_string_python_can_parse():
    """MAX(unixepoch(...)) alone returns an int, which breaks days_ago()."""
    db = _two_commits()
    kd = KospexData(kospex_db=db)
    kd.from_table(KospexSchema.TBL_COMMITS)
    kd.select_latest_date("committer_when", "last_commit")

    value = _run(db, kd)[0]["last_commit"]

    assert isinstance(value, str)
    assert KospexUtils.days_ago(value) is not None
    assert KospexUtils.development_status(KospexUtils.days_ago(value))


def test_order_by_date_sorts_on_the_true_instant():
    db = _two_commits()
    kd = KospexData(kospex_db=db)
    kd.from_table(KospexSchema.TBL_COMMITS)
    kd.select("hash")
    kd.order_by_date("committer_when", "DESC")

    assert [r["hash"] for r in _run(db, kd)] == ["really_later", "looks_later"]


def test_order_by_date_ascending():
    db = _two_commits()
    kd = KospexData(kospex_db=db)
    kd.from_table(KospexSchema.TBL_COMMITS)
    kd.select("hash")
    kd.order_by_date("committer_when", "ASC")

    assert [r["hash"] for r in _run(db, kd)] == ["looks_later", "really_later"]


def test_unparseable_date_does_not_crash_the_aggregate():
    """git accepts absurd offsets; the live DB has 4 commits at '+518:00'.
    unixepoch() returns NULL for those and MAX ignores NULLs."""
    db = _db([
        {"_repo_id": "s~o~r", "hash": "bad",
         "committer_when": "2011-09-08T02:38:50+518:00", "author_email": "a@e.com"},
        {"_repo_id": "s~o~r", "hash": "good",
         "committer_when": LATER_LOOKS_EARLIER, "author_email": "a@e.com"},
    ])
    kd = KospexData(kospex_db=db)
    kd.from_table(KospexSchema.TBL_COMMITS)
    kd.select_latest_date("committer_when", "last_commit")

    assert _run(db, kd)[0]["last_commit"] == "2026-04-16T20:59:54Z"


def test_invalid_column_is_rejected():
    db = _two_commits()
    kd = KospexData(kospex_db=db)

    with pytest.raises(ValueError):
        kd.select_latest_date("committer_when; DROP TABLE commits", "last_commit")


def test_invalid_alias_is_rejected():
    db = _two_commits()
    kd = KospexData(kospex_db=db)

    with pytest.raises(ValueError):
        kd.select_latest_date("committer_when", "bad alias; DROP TABLE commits")


def test_repos2_returns_the_true_latest():
    """End to end through a real query, not just the helper."""
    db = _two_commits()
    kq = KospexQuery(kospex_db=db)

    rows = kq.repos2(id={"repo_id": "s~o~r"})

    assert rows
    assert rows[0]["last_commit"] == "2026-04-16T20:59:54Z"
    assert rows[0]["first_commit"] == "2026-04-16T17:34:03Z"
