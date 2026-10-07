"""`krunner osi -next N` at the command line: guards, exit codes, CSV default.

Design: changes/202610-krunner-osi-next.md

The batch logic is covered in test_osi_next_batch.py. What matters here is the
command contract a cron entry depends on:

* **Exit 0 when nothing is due.** `osi -all` exits 1 on "No results", which for a
  scheduled run would report a failure every time the estate is current.
* **Exit 0 when another run holds the lock.** Overlap is normal under a short
  interval.
* **No CSV unless asked.** A scheduled `-next` must not overwrite the estate-wide
  export with one batch.
"""
import importlib

import pytest
import sqlite_utils
from click.testing import CliRunner

import kospex_schema as KospexSchema

A = "github.com~acme~a"


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """A file-backed kospex home with one repo carrying one manifest."""
    from kospex.db.migrator import Migrator
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    db_path = home / "kospex.db"
    repo_dir = tmp_path / "code" / "a"
    repo_dir.mkdir(parents=True)
    (repo_dir / "requirements.txt").write_text("requests==2.31.0\n")

    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(db_path))
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path / "code"))
    HabitatConfig.reset_instance()

    db = KospexSchema.connect_or_create_kospex_db()
    Migrator(db).apply_pending()
    db[KospexSchema.TBL_REPOS].insert(
        {"_repo_id": A, "_git_server": "github.com", "_git_owner": "acme",
         "_git_repo": "a", "file_path": str(repo_dir)}, pk="_repo_id")
    db[KospexSchema.TBL_FILE_METADATA].insert(
        {"_repo_id": A, "Provider": "requirements.txt", "Filename": "requirements.txt",
         "hash": "h0", "latest": 1, "tech_type": "|dependencies|"},
        pk=["Provider", "hash", "_repo_id"])
    db.close()

    yield home
    HabitatConfig.reset_instance()


@pytest.fixture(autouse=True)
def _no_depsdev(monkeypatch):
    from kospex_dependencies import KospexDependencies
    monkeypatch.setattr(
        KospexDependencies, "depsdev_record",
        lambda self, ptype, name, version: {
            "package_name": name, "package_version": version,
            "package_type": ptype, "versions_behind": 0, "advisories": 0,
            "resolution": "resolved", "published_at": "2026-01-01T00:00:00Z",
        })


def _invoke(*args):
    import krunner
    importlib.reload(krunner)      # rebind module-level Kospex() to the test DB
    return CliRunner().invoke(krunner.cli, ["osi", *args])


def test_next_and_all_are_mutually_exclusive(cli_env):
    result = _invoke("-next", "2", "-all")

    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_next_runs_a_batch_and_exits_zero(cli_env):
    result = _invoke("-next", "5")

    assert result.exit_code == 0, result.output
    assert A in result.output


def test_next_composes_with_a_scope(cli_env):
    result = _invoke("-next", "5", "github.com~acme")

    assert result.exit_code == 0, result.output
    assert A in result.output


def test_next_exits_zero_when_the_estate_is_already_current(cli_env):
    """The cron contract: nothing due is success, not failure.

    `osi -all` exits 1 on "No results", which would report a failure on every
    tick once the estate is scanned.
    """
    assert _invoke("-next", "5").exit_code == 0
    second = _invoke("-next", "5")

    assert second.exit_code == 0, second.output


def test_next_exits_zero_when_another_run_holds_the_lock(cli_env):
    from kospex.osi_lock import OsiLock

    held = OsiLock()
    held.acquire()
    try:
        result = _invoke("-next", "2")
    finally:
        held.release()

    assert result.exit_code == 0, result.output
    assert "in progress" in result.output


def test_next_writes_no_csv_by_default(cli_env):
    from kospex.assessment_types import AssessmentTypes

    _invoke("-next", "5")

    assessments = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, "all").parent
    found = sorted(p.name for p in assessments.glob("*.csv")) if assessments.exists() else []
    assert found == [], f"expected no CSVs, got {found}"


def test_next_csv_opts_in_to_one_file_per_repo(cli_env):
    from kospex.assessment_types import AssessmentTypes

    _invoke("-next", "5", "-csv")

    assessments = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, "all").parent
    found = sorted(p.name for p in assessments.glob("*.csv"))
    assert found == [f"OSI-{A}.csv"], f"got {found}"


def test_next_does_not_clobber_an_existing_estate_wide_csv(cli_env):
    """The regression that matters: a scheduled -next must leave OSI-all.csv alone."""
    from kospex.assessment_types import AssessmentTypes

    AssessmentTypes.ensure_assessments_dir()
    all_csv = AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, "all")
    all_csv.write_text("pre-existing,full,estate,export\n")

    _invoke("-next", "5", "-csv")

    assert all_csv.read_text() == "pre-existing,full,estate,export\n"
