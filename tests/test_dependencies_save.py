"""Tests for KospexDependencies.save_dependencies().

save_dependencies() is the reusable DB-write path used by `krunner osi`
(and intended to back the other dependency writers). It must:
  - persist pre-parsed records into TBL_DEPENDENCY_DATA with latest=1,
  - stamp the [source] column with the calling tool,
  - derive _git_server/_git_owner/_git_repo from _repo_id,
  - strip extractor-only template fields that are not schema columns, and
  - demote prior rows for the same (_repo_id, file_path, package_name)
    to latest=0 so re-runs don't accumulate stale "latest" rows.
"""
import os

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_dependencies import KospexDependencies


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    """Constructing KospexQuery/KospexDependencies sets KOSPEX_* env vars as a
    side effect; restore them so we don't leak state into later tests."""
    keys = ("KOSPEX_CODE", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_HOME", "KOSPEX_LOGS")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


_PK = [
    "_repo_id", "hash", "file_path",
    "package_type", "package_name", "package_version",
]


def _make_db():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
    return db


def test_save_dependencies_writes_rows_with_latest_source_and_git_fields():
    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)

    records = [{
        "_repo_id": "github.com~kospex~kospex",
        "hash": "abc123",
        "file_path": "requirements.txt",
        "package_type": "pypi",
        "package_name": "requests",
        "package_version": "2.31.0",
        "versions_behind": 3,
        "advisories": 0,
        # extractor-only template fields that are NOT schema columns:
        "ecosystem": "PyPi",
        "requirements_type": "direct",
    }]

    count = kdeps.save_dependencies(records, source="krunner osi")

    assert count == 1
    rows = list(db[KospexSchema.TBL_DEPENDENCY_DATA].rows)
    assert len(rows) == 1
    row = rows[0]
    assert row["package_name"] == "requests"
    assert row["package_version"] == "2.31.0"
    assert row["latest"] == 1
    assert row["source"] == "krunner osi"
    # _git_* derived from _repo_id
    assert row["_git_server"] == "github.com"
    assert row["_git_owner"] == "kospex"
    assert row["_git_repo"] == "kospex"
    # extractor-only fields must not have been persisted as columns
    assert "ecosystem" not in row
    assert "requirements_type" not in row


def test_save_dependencies_demotes_prior_latest_rows():
    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)

    # An existing "latest" row for an older version of the same package
    # in the same file (e.g. a prior krunner osi run).
    db[KospexSchema.TBL_DEPENDENCY_DATA].insert({
        "_repo_id": "github.com~kospex~kospex",
        "hash": "oldhash",
        "file_path": "requirements.txt",
        "package_type": "pypi",
        "package_name": "requests",
        "package_version": "2.30.0",
        "latest": 1,
    }, pk=_PK)

    kdeps.save_dependencies([{
        "_repo_id": "github.com~kospex~kospex",
        "hash": "newhash",
        "file_path": "requirements.txt",
        "package_type": "pypi",
        "package_name": "requests",
        "package_version": "2.31.0",
    }], source="krunner osi")

    rows = {r["package_version"]: r for r in db[KospexSchema.TBL_DEPENDENCY_DATA].rows}
    assert rows["2.30.0"]["latest"] == 0   # prior version demoted
    assert rows["2.31.0"]["latest"] == 1   # new current version


def test_save_dependencies_empty_is_noop():
    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)
    assert kdeps.save_dependencies([], source="krunner osi") == 0
    assert list(db[KospexSchema.TBL_DEPENDENCY_DATA].rows) == []


def test_save_dependencies_persists_resolution():
    import sqlite_utils, kospex_schema as KospexSchema
    from kospex_dependencies import KospexDependencies
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
    db.execute("ALTER TABLE dependency_data ADD COLUMN resolution TEXT")
    kd = KospexDependencies(kospex_db=db)
    kd.save_dependencies([{
        "_repo_id": "s~o~r", "hash": "h", "file_path": "req.txt", "package_type": "pypi",
        "package_name": "foo", "package_version": "^1.0",
        "versions_behind": None, "resolution": "unresolved_spec",
    }], source="krunner osi")
    row = next(db.query("SELECT resolution, versions_behind FROM dependency_data"))
    assert row["resolution"] == "unresolved_spec" and row["versions_behind"] is None


def _existing_row(db, **overrides):
    """Insert a prior latest=1 row, defaults matching a typical krunner osi write."""
    row = {
        "_repo_id": "github.com~kospex~kospex",
        "hash": "abc123",
        "file_path": "requirements.txt",
        "package_type": "pypi",
        "package_name": "requests",
        "package_version": "2.31.0",
        "latest": 1,
        "source": "krunner osi",
    }
    row.update(overrides)
    db[KospexSchema.TBL_DEPENDENCY_DATA].insert(row, pk=_PK, replace=True)
    return row


def test_renamed_package_does_not_orphan_its_predecessor():
    """A parser change that derives a different name must not leave two latest rows.

    package_name is part of the primary key, so a re-parse that derives a new
    name inserts a new row rather than updating the old one. Demoting by
    (_repo_id, file_path, package_name) keys the demote on the very thing that
    changed, so the old row keeps latest=1 forever.

    Observed live: `mkdocstrings[python]` (package_not_found) and `mkdocstrings`
    (resolved) both sat at latest=1 for the same file after the PEP 508 parser
    began separating extras from the name.
    """
    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)

    _existing_row(db, package_name="mkdocstrings[python]", package_version="0.24.0",
                  file_path="requirements-docs.txt")

    # Re-parse derives the name without the extras.
    kdeps.save_dependencies([{
        "_repo_id": "github.com~kospex~kospex",
        "hash": "abc123",
        "file_path": "requirements-docs.txt",
        "package_type": "pypi",
        "package_name": "mkdocstrings",
        "package_version": "0.24.0",
    }], source="krunner osi")

    current = [r["package_name"] for r in db[KospexSchema.TBL_DEPENDENCY_DATA].rows
               if r["latest"] == 1]
    assert current == ["mkdocstrings"], (
        f"expected only the re-parsed name to be current, got {current} — "
        "the predecessor was not demoted"
    )


def test_dependency_removed_from_a_manifest_is_demoted():
    """A package deleted from a manifest must stop being reported as current.

    The per-package demote only ran for packages present in the incoming batch,
    so a dependency removed from the file had no incoming record, was never
    demoted, and stayed latest=1 indefinitely.
    """
    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)

    _existing_row(db, package_name="click", package_version="8.1.0")
    _existing_row(db, package_name="requests", package_version="2.31.0")

    # `click` has since been deleted from requirements.txt; only requests remains.
    kdeps.save_dependencies([{
        "_repo_id": "github.com~kospex~kospex",
        "hash": "abc123",
        "file_path": "requirements.txt",
        "package_type": "pypi",
        "package_name": "requests",
        "package_version": "2.31.0",
    }], source="krunner osi")

    rows = {r["package_name"]: r["latest"] for r in db[KospexSchema.TBL_DEPENDENCY_DATA].rows}
    assert rows["requests"] == 1
    assert rows["click"] == 0, "a dependency no longer in the manifest must not stay current"


def test_demote_is_scoped_to_the_files_being_written():
    """Demoting by file must not touch other files in the same repo."""
    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)

    _existing_row(db, file_path="requirements.txt", package_name="requests")
    _existing_row(db, file_path="requirements-dev.txt", package_name="pytest",
                  package_version="8.0.0")

    kdeps.save_dependencies([{
        "_repo_id": "github.com~kospex~kospex",
        "hash": "abc123",
        "file_path": "requirements.txt",
        "package_type": "pypi",
        "package_name": "requests",
        "package_version": "2.32.0",
    }], source="krunner osi")

    latest = {(r["file_path"], r["package_name"], r["package_version"]): r["latest"]
              for r in db[KospexSchema.TBL_DEPENDENCY_DATA].rows}
    # untouched file keeps its current row
    assert latest[("requirements-dev.txt", "pytest", "8.0.0")] == 1
    # rewritten file: old version demoted, new version current
    assert latest[("requirements.txt", "requests", "2.31.0")] == 0
    assert latest[("requirements.txt", "requests", "2.32.0")] == 1


def test_demote_is_scoped_to_the_repo_being_written():
    """Another repo with the same file path must be unaffected."""
    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)

    _existing_row(db, _repo_id="github.com~other~repo", package_name="requests")

    kdeps.save_dependencies([{
        "_repo_id": "github.com~kospex~kospex",
        "hash": "abc123",
        "file_path": "requirements.txt",
        "package_type": "pypi",
        "package_name": "requests",
        "package_version": "2.32.0",
    }], source="krunner osi")

    other = [r for r in db[KospexSchema.TBL_DEPENDENCY_DATA].rows
             if r["_repo_id"] == "github.com~other~repo"]
    assert len(other) == 1
    assert other[0]["latest"] == 1, "another repo's rows must not be demoted"


def test_upsert_failure_leaves_prior_rows_current():
    """A failed upsert must not blank the manifest's current dependencies.

    The demote and the upsert are one unit of work. Run as two statements with
    no transaction, a failure between them leaves the demote committed and the
    replacements never written, so the file ends up with zero rows at latest=1
    -- silently, because the demote itself succeeded.
    """
    import sqlite3

    db = _make_db()
    kdeps = KospexDependencies(kospex_db=db)
    for name in ("requests", "click", "urllib3"):
        _existing_row(db, package_name=name, package_version="1.0.0")

    # `not_a_column` is not in the schema and is not stripped as a template
    # field, so the upsert raises after the demote has already run. The trigger
    # is deliberately generic -- any DB error between the two statements does
    # this; a pre-0005 DB hitting `resolution` is just the case seen in the wild.
    with pytest.raises(sqlite3.OperationalError):
        kdeps.save_dependencies([{
            "_repo_id": "github.com~kospex~kospex",
            "hash": "newhash",
            "file_path": "requirements.txt",
            "package_type": "pypi",
            "package_name": "requests",
            "package_version": "2.31.0",
            "not_a_column": "boom",
        }], source="krunner osi")

    current = [r for r in db[KospexSchema.TBL_DEPENDENCY_DATA].rows if r["latest"] == 1]
    assert {r["package_name"] for r in current} == {"requests", "click", "urllib3"}, (
        "the demote must roll back with the failed upsert, leaving the previous "
        "dependency set current"
    )
