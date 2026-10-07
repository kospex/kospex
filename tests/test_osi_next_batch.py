"""Running one `krunner osi -next` batch end to end.

Design: changes/202610-krunner-osi-next.md

Ties together the queue, the lock, the outcome record and the per-repo
extraction. deps.dev is stubbed; everything else is real -- real parsers, real
files on disk, real SQLite.

Ordering within a repository is extract -> enrich -> save -> record, and the
record comes last on purpose. If the save fails, the outcome is not written and
the repository stays queued, so work is retried rather than silently skipped.
"""
import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex.osi_lock import LOCK_FILENAME, OsiLock
from kospex.osi_next import run_next_batch
from kospex.osi_outcomes import EMPTY, EXTRACTED, PARSE_ERROR
from kospex.osi_queue import get_outcome

A, B, C = "github.com~acme~a", "github.com~acme~b", "github.com~acme~c"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig
    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    HabitatConfig.reset_instance()
    yield home
    HabitatConfig.reset_instance()


@pytest.fixture(autouse=True)
def _no_depsdev(monkeypatch):
    """deps.dev stubbed -- no network in tests."""
    from kospex_dependencies import KospexDependencies
    monkeypatch.setattr(
        KospexDependencies, "depsdev_record",
        lambda self, ptype, name, version: {
            "package_name": name, "package_version": version,
            "package_type": ptype, "versions_behind": 1, "advisories": 0,
            "resolution": "resolved", "published_at": "2026-01-01T00:00:00Z",
        })


@pytest.fixture
def estate(tmp_path):
    """Three repos: A has a manifest, B has none, C has a broken one."""
    # Built the way a real database is: every baseline table, then the shipped
    # migrations. Hand-ALTERing the columns a specific migration added is what
    # broke three test fixtures when 0007 landed -- the subset is always one
    # migration out of date.
    from kospex.db.migrator import Migrator

    db = sqlite_utils.Database(memory=True)
    for create_sql in KospexSchema.DB_CREATE_STATEMENTS.values():
        db.execute(create_sql)
    Migrator(db).apply_pending()

    for rid, files in ((A, ["requirements.txt"]), (B, []), (C, ["package.json"])):
        d = tmp_path / rid.replace("~", "_")
        d.mkdir()
        db[KospexSchema.TBL_REPOS].insert(
            {"_repo_id": rid, "_git_server": "github.com", "_git_owner": "acme",
             "_git_repo": rid.split("~")[-1], "file_path": str(d)}, pk="_repo_id")
        for i, f in enumerate(files):
            db[KospexSchema.TBL_FILE_METADATA].insert(
                {"_repo_id": rid, "Provider": f, "Filename": f, "hash": f"h{i}",
                 "latest": 1, "tech_type": "|dependencies|"},
                pk=["Provider", "hash", "_repo_id"])
    (tmp_path / A.replace("~", "_") / "requirements.txt").write_text("requests==2.31.0\n")
    (tmp_path / C.replace("~", "_") / "package.json").write_text("{ broken")
    return db


def test_a_batch_processes_exactly_n_repos(estate):
    result = run_next_batch(estate, limit=2)

    assert len(result.repos) == 2
    assert not result.skipped


def test_every_processed_repo_gets_an_outcome_record(estate):
    result = run_next_batch(estate, limit=3)

    for r in result.repos:
        assert get_outcome(estate, r.repo_id) is not None, f"{r.repo_id} unrecorded"


def test_a_repo_with_no_dependency_files_is_recorded_as_examined(estate):
    """The fact that makes the queue terminate."""
    run_next_batch(estate, limit=3)

    outcome = get_outcome(estate, B)
    assert outcome is not None
    assert outcome["files"] == {}


def test_a_second_batch_advances_past_the_first(estate):
    first = {r.repo_id for r in run_next_batch(estate, limit=1).repos}
    second = {r.repo_id for r in run_next_batch(estate, limit=1).repos}

    assert first and second and first != second


def test_the_whole_estate_is_covered_by_successive_batches(estate):
    """Round-robin must terminate, including over the zero-dependency repo."""
    seen = set()
    for _ in range(3):
        seen |= {r.repo_id for r in run_next_batch(estate, limit=1).repos}

    assert seen == {A, B, C}


def test_dependency_rows_are_saved(estate):
    run_next_batch(estate, limit=3)

    rows = list(estate.query(
        "SELECT package_name, source, versions_behind FROM dependency_data WHERE latest=1"))
    assert [r["package_name"] for r in rows] == ["requests"]
    assert rows[0]["source"] == "krunner osi -next"
    assert rows[0]["versions_behind"] == 1, "enrichment must have run before the save"


def test_outcomes_distinguish_extracted_empty_and_parse_error(estate):
    run_next_batch(estate, limit=3)

    assert get_outcome(estate, A)["files"] == {"requirements.txt": EXTRACTED}
    assert get_outcome(estate, B)["files"] == {}
    assert get_outcome(estate, C)["files"] == {"package.json": PARSE_ERROR}


def test_per_repo_results_carry_timing_and_counts(estate):
    result = run_next_batch(estate, limit=3)
    by_id = {r.repo_id: r for r in result.repos}

    assert by_id[A].packages == 1
    assert by_id[A].files == 1
    assert by_id[A].outcome == EXTRACTED
    assert by_id[B].files == 0
    assert by_id[B].outcome == EMPTY
    assert by_id[C].outcome == PARSE_ERROR
    for r in result.repos:
        assert r.duration_ms >= 0
    assert result.duration_ms >= 0
    assert result.run_id


def test_a_busy_lock_skips_the_batch_without_raising(estate):
    """Cron must see success, not a failure, when a previous tick is still going."""
    held = OsiLock()
    held.acquire()
    try:
        result = run_next_batch(estate, limit=2)
    finally:
        held.release()

    assert result.skipped is True
    assert result.repos == []


def test_an_empty_queue_is_not_an_error(estate):
    run_next_batch(estate, limit=3)          # exhaust it
    result = run_next_batch(estate, limit=3) # everything examined, oldest-first again

    # Nothing is "due" in the sense of unexamined, but round-robin still returns
    # the oldest -- what must not happen is an exception or a skipped flag.
    assert result.skipped is False


def test_an_estate_with_no_repos_yields_an_empty_batch(_home):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db.execute(KospexSchema.SQL_CREATE_OBSERVATIONS)

    result = run_next_batch(db, limit=5)

    assert result.repos == []
    assert result.skipped is False


def test_the_lock_is_released_after_a_batch(estate, _home):
    run_next_batch(estate, limit=1)
    assert not (_home / LOCK_FILENAME).exists()


def test_no_csv_is_written_by_default(estate, tmp_path):
    """A scheduled -next must not touch the assessments directory."""
    from kospex.assessment_types import AssessmentTypes
    run_next_batch(estate, limit=3)

    assessments = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, "all").parent
    written = list(assessments.glob("*.csv")) if assessments.exists() else []
    assert written == [], f"expected no CSVs, got {written}"


def test_csv_opt_in_writes_one_file_per_repo(estate):
    """Per-repo filenames, matching what `osi REPO_ID` already produces."""
    from kospex.assessment_types import AssessmentTypes
    run_next_batch(estate, limit=3, write_csv=True)

    assessments = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, "all").parent
    names = sorted(p.name for p in assessments.glob("*.csv"))

    # Only A produced rows; B had nothing and C failed to parse, so neither gets a
    # file -- an empty CSV would be indistinguishable from "not scanned".
    assert names == [f"OSI-{A}.csv"], f"got {names}"
