"""Tests for the RunErrors tracker used by krunner scans.

A krunner scan walks many repos and any one of them can fail without the run
being a failure. RunErrors collects those per-repo failures, logs each one at
ERROR level, and reports a count-by-type summary at the end of the scan.
"""
import logging

import pytest

import krunner_utils as KrunnerUtils
from krunner_utils import RunErrors


class _RecordingConsole:
    """Stand-in for a rich Console that records what was printed."""

    def __init__(self):
        self.lines = []

    def log(self, message, style=None):
        self.lines.append(message)


def test_no_errors_is_falsey_and_has_no_summary():
    errors = RunErrors()
    assert not errors
    assert len(errors) == 0
    assert errors.counts_by_type() == {}
    assert errors.summary_table() is None


def test_counts_by_type_orders_by_count_then_name():
    errors = RunErrors()
    errors.add(KrunnerUtils.GIT_ERROR, "repo~a", "boom")
    errors.add(KrunnerUtils.MISSING_CLONE, "repo~b", "gone")
    errors.add(KrunnerUtils.MISSING_CLONE, "repo~c", "gone")

    assert len(errors) == 3
    assert bool(errors) is True
    # MISSING_CLONE first (2 > 1), so the biggest problem leads the summary.
    assert list(errors.counts_by_type().items()) == [
        (KrunnerUtils.MISSING_CLONE, 2),
        (KrunnerUtils.GIT_ERROR, 1),
    ]


def test_counts_by_type_breaks_ties_alphabetically():
    errors = RunErrors()
    errors.add(KrunnerUtils.MISSING_CLONE, "repo~a", "gone")
    errors.add(KrunnerUtils.GIT_ERROR, "repo~b", "boom")

    assert list(errors.counts_by_type()) == [KrunnerUtils.GIT_ERROR, KrunnerUtils.MISSING_CLONE]


def test_add_logs_each_failure_at_error_level(caplog):
    log = logging.getLogger("test_run_errors")
    errors = RunErrors(logger=log)

    with caplog.at_level(logging.ERROR, logger="test_run_errors"):
        errors.add(KrunnerUtils.MISSING_CLONE, "github.com~kospex~panopticas", "no local clone at /tmp/x")

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert "MISSING_CLONE" in record.message
    assert "github.com~kospex~panopticas" in record.message
    assert "no local clone at /tmp/x" in record.message


def test_add_prints_to_the_console_when_one_is_given():
    console = _RecordingConsole()
    errors = RunErrors(console=console)

    errors.add(KrunnerUtils.GIT_ERROR, "github.com~org~repo", "exit status 128")

    assert len(console.lines) == 1
    assert "GIT_ERROR" in console.lines[0]
    assert "github.com~org~repo" in console.lines[0]
    assert "exit status 128" in console.lines[0]


def test_add_works_without_a_logger_or_console():
    errors = RunErrors()
    errors.add(KrunnerUtils.GIT_ERROR, "repo~a", "boom")  # must not raise
    assert len(errors) == 1


def test_summary_table_lists_each_type_and_count():
    errors = RunErrors()
    errors.add(KrunnerUtils.MISSING_CLONE, "repo~a", "gone")
    errors.add(KrunnerUtils.MISSING_CLONE, "repo~b", "gone")
    errors.add(KrunnerUtils.GIT_ERROR, "repo~c", "boom")

    table = errors.summary_table()
    assert table is not None
    rendered = "".join(str(cell) for column in table.columns for cell in column.cells)
    assert "MISSING_CLONE" in rendered
    assert "GIT_ERROR" in rendered
    assert "2" in rendered and "1" in rendered
