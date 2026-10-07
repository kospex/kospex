"""Extracting one repository, and reporting what happened to each of its files.

Design: changes/202610-krunner-osi-next.md

`extract_repo()` is the per-repo unit `-next` calls once per queued repository. It
exists as a function rather than inline in the command because the outcome
reporting has to be testable against real parsers -- a test that re-implements the
loop proves nothing about the code that runs, which is why
`extract_dependency_file` and `enrich_dependency_records` were pulled out of
`osi` in the first place.

It must swallow nothing silently: a parser that raises becomes a `parse_error`
outcome rather than a `continue`, which is the defect #148 is about.
"""
import sqlite_utils

import kospex_schema as KospexSchema
from kospex.osi_outcomes import (
    EMPTY,
    EXTRACTED,
    NOT_A_MANIFEST,
    PARSE_ERROR,
    UNSUPPORTED,
)
from kospex.osi_extract import extract_repo

REPO_ID = "github.com~acme~svc"


def _query_db(repo_dir, providers):
    """A kospex_query stand-in holding one repo and its tagged files."""
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db.execute(KospexSchema.SQL_CREATE_FILE_METADATA)
    db[KospexSchema.TBL_REPOS].insert(
        {"_repo_id": REPO_ID, "_git_server": "github.com", "_git_owner": "acme",
         "_git_repo": "svc", "file_path": str(repo_dir)}, pk="_repo_id")
    for i, provider in enumerate(providers):
        db[KospexSchema.TBL_FILE_METADATA].insert(
            {"_repo_id": REPO_ID, "Provider": provider, "Filename": provider,
             "hash": f"h{i}", "latest": 1, "tech_type": "|dependencies|"},
            pk=["Provider", "hash", "_repo_id"])

    from kospex_query import KospexQuery
    return KospexQuery(kospex_db=db)


def _kdeps():
    from kospex_dependencies import KospexDependencies
    return KospexDependencies(kospex_db=sqlite_utils.Database(memory=True))


def test_a_parsed_manifest_reports_extracted_and_returns_rows(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\nclick==8.1.7\n")
    kq = _query_db(tmp_path, ["requirements.txt"])

    rows, outcomes = extract_repo(REPO_ID, kq, _kdeps())

    assert outcomes == {"requirements.txt": EXTRACTED}
    assert sorted(r["package_name"] for r in rows) == ["click", "requests"]


def test_a_manifest_declaring_nothing_reports_empty(tmp_path):
    """Must be distinguishable from a parser that blew up."""
    (tmp_path / "requirements.txt").write_text("# nothing here\n")
    kq = _query_db(tmp_path, ["requirements.txt"])

    rows, outcomes = extract_repo(REPO_ID, kq, _kdeps())

    assert outcomes == {"requirements.txt": EMPTY}
    assert rows == []


def test_a_malformed_manifest_reports_parse_error_instead_of_being_skipped(tmp_path):
    """Today this is `except (JSONDecodeError, OSError): continue` — invisible."""
    (tmp_path / "package.json").write_text("{ this is not json")
    kq = _query_db(tmp_path, ["package.json"])

    rows, outcomes = extract_repo(REPO_ID, kq, _kdeps())

    assert outcomes == {"package.json": PARSE_ERROR}
    assert rows == []


def test_a_missing_file_reports_parse_error(tmp_path):
    """file_metadata says it exists, disk disagrees — a stale sync, not a crash."""
    kq = _query_db(tmp_path, ["requirements.txt"])      # never written

    rows, outcomes = extract_repo(REPO_ID, kq, _kdeps())

    assert outcomes == {"requirements.txt": PARSE_ERROR}
    assert rows == []


def test_an_unparseable_manifest_does_not_stop_the_other_files(tmp_path):
    """One bad file must not cost the repo its other manifests."""
    (tmp_path / "package.json").write_text("{ broken")
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    kq = _query_db(tmp_path, ["package.json", "requirements.txt"])

    rows, outcomes = extract_repo(REPO_ID, kq, _kdeps())

    assert outcomes == {"package.json": PARSE_ERROR,
                        "requirements.txt": EXTRACTED}
    assert [r["package_name"] for r in rows] == ["requests"]


def test_unsupported_and_non_manifest_files_are_distinguished(tmp_path):
    (tmp_path / "yarn.lock").write_text("# lockfile\n")
    (tmp_path / "dependabot.yml").write_text("version: 2\n")
    kq = _query_db(tmp_path, ["yarn.lock", "dependabot.yml"])

    rows, outcomes = extract_repo(REPO_ID, kq, _kdeps())

    assert outcomes == {"yarn.lock": UNSUPPORTED,
                        "dependabot.yml": NOT_A_MANIFEST}
    assert rows == []


def test_a_repo_with_no_dependency_files_returns_an_empty_mapping(tmp_path):
    """Not an error. The caller records it as 'examined, nothing to find'."""
    kq = _query_db(tmp_path, [])

    rows, outcomes = extract_repo(REPO_ID, kq, _kdeps())

    assert outcomes == {}
    assert rows == []


def test_rows_carry_the_repo_and_file_identity(tmp_path):
    """save_dependencies needs the full primary key on every row."""
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    kq = _query_db(tmp_path, ["requirements.txt"])

    rows, _ = extract_repo(REPO_ID, kq, _kdeps())

    row = rows[0]
    assert row["_repo_id"] == REPO_ID
    assert row["file_path"] == "requirements.txt"
    assert row["hash"] == "h0"
    assert row["package_type"] == "pypi"
