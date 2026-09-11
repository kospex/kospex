"""The suite runs as if on the oldest SQLite kospex supports.

tests/conftest.py gives every connection an authorizer that refuses each SQL
function newer than the floor. These tests pin that it reaches the connections
kospex actually opens, so the guard cannot silently stop applying -- for
example if sqlite_utils switched to a different sqlite3 driver.
"""
import sqlite3

import pytest
import sqlite_utils


def test_a_function_newer_than_the_floor_is_unavailable():
    db = sqlite_utils.Database(memory=True)
    with pytest.raises(sqlite3.DatabaseError, match="unixepoch"):
        db.execute("SELECT unixepoch('2026-08-31T11:22:37Z')").fetchone()


def test_it_is_refused_even_when_no_row_would_reach_it():
    """A missing function fails when the statement is compiled, not per row,
    so an aggregate over an empty table must fail too."""
    db = sqlite_utils.Database(memory=True)
    db.execute("CREATE TABLE commits (committer_when TEXT)")
    with pytest.raises(sqlite3.DatabaseError, match="unixepoch"):
        db.execute("SELECT MAX(unixepoch(committer_when)) FROM commits").fetchone()


def test_the_unixepoch_modifier_is_not_the_function_and_still_works():
    """strftime(fmt, x, 'unixepoch') is a modifier available in every SQLite 3."""
    db = sqlite_utils.Database(memory=True)
    got = db.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', 1788175357, 'unixepoch')").fetchone()
    assert got == ("2026-08-31T11:22:37Z",)


def test_epoch_seconds_honour_the_utc_offset():
    """CAST(strftime('%s', x) AS INTEGER) is unixepoch(x) on any SQLite 3: the
    same instant written with two different offsets is the same second."""
    db = sqlite_utils.Database(memory=True)
    got = db.execute(
        "SELECT CAST(strftime('%s', '2026-08-31T21:22:37+10:00') AS INTEGER),"
        "       CAST(strftime('%s', '2026-08-31T11:22:37Z') AS INTEGER),"
        "       CAST(strftime('%s', 'not a date') AS INTEGER)").fetchone()
    assert got == (1788175357, 1788175357, None)
