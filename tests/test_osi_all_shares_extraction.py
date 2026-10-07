"""`osi -all` and `osi -next` must share one extraction loop.

Two loops is the drift the comment inside osi's own loop warns about:

    so `krunner osi` and `kospex sca` cannot drift apart the way they did when
    each kept its own filename checks -- go.mod and *.csproj were silently
    skipped here for exactly that reason

Adding extract_repo() for -next recreated that inside osi: -all kept an inline
loop that swallowed parse failures with `except (json.JSONDecodeError, OSError):
continue`, so the same manifest produced a different record depending on which
command read it.

Three consequences, each tested here:

* -all silently skipped malformed manifests -- the #148 defect, on the path people
  actually use.
* -all recorded no outcomes, so a full run did not advance the -next queue. You
  could scan the whole estate with -all and -next would scan it all again.
* -all's except was narrow, so a TOMLDecodeError took down the whole run rather
  than marking one file.
"""
import importlib

import pytest
import sqlite_utils
from click.testing import CliRunner

import kospex_schema as KospexSchema
from kospex.osi_outcomes import EXTRACTED, PARSE_ERROR
from kospex.osi_queue import get_outcome

A = "github.com~acme~a"


@pytest.fixture
def estate(tmp_path, monkeypatch):
    """One repo with a good manifest and a broken one."""
    from kospex.db.migrator import Migrator
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    repo_dir = tmp_path / "code" / "a"
    repo_dir.mkdir(parents=True)
    (repo_dir / "requirements.txt").write_text("requests==2.31.0\n")
    (repo_dir / "package.json").write_text("{ not json")

    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path / "code"))
    HabitatConfig.reset_instance()

    db = KospexSchema.connect_or_create_kospex_db()
    Migrator(db).apply_pending()
    db[KospexSchema.TBL_REPOS].insert(
        {"_repo_id": A, "_git_server": "github.com", "_git_owner": "acme",
         "_git_repo": "a", "file_path": str(repo_dir)}, pk="_repo_id")
    for i, f in enumerate(["requirements.txt", "package.json"]):
        db[KospexSchema.TBL_FILE_METADATA].insert(
            {"_repo_id": A, "Provider": f, "Filename": f, "hash": f"h{i}",
             "latest": 1, "tech_type": "|dependencies|"},
            pk=["Provider", "hash", "_repo_id"])
    db.close()

    yield home / "kospex.db"
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
    importlib.reload(krunner)
    return CliRunner().invoke(krunner.cli, ["osi", *args])


def test_all_records_outcomes_so_a_full_run_advances_the_next_queue(estate):
    """Otherwise -all scans the estate and -next scans it all over again."""
    result = _invoke("-all")
    assert result.exit_code == 0, result.output

    db = sqlite_utils.Database(str(estate))
    outcome = get_outcome(db, A)
    db.close()

    assert outcome is not None, "-all examined this repo but recorded nothing"
    assert outcome["files"]["requirements.txt"] == EXTRACTED


def test_all_records_a_parse_error_instead_of_silently_skipping(estate):
    """The #148 defect, on the path people actually use."""
    _invoke("-all")

    db = sqlite_utils.Database(str(estate))
    outcome = get_outcome(db, A)
    db.close()

    assert outcome["files"]["package.json"] == PARSE_ERROR


def test_all_still_extracts_the_good_manifest_alongside_the_broken_one(estate):
    """One bad file must not cost the repo its good ones."""
    _invoke("-all")

    db = sqlite_utils.Database(str(estate))
    rows = list(db.query(
        "SELECT package_name FROM dependency_data WHERE latest = 1"))
    db.close()

    assert [r["package_name"] for r in rows] == ["requests"]


def test_a_scoped_run_records_outcomes_too(estate):
    """`osi REPO_ID` is the same path, so it must behave the same way."""
    _invoke(A)

    db = sqlite_utils.Database(str(estate))
    outcome = get_outcome(db, A)
    db.close()

    assert outcome is not None
    assert outcome["files"]["package.json"] == PARSE_ERROR


def test_all_still_writes_its_estate_wide_csv(estate):
    """Existing behaviour -- -all's exports are unchanged by this refactor."""
    from kospex.assessment_types import AssessmentTypes

    _invoke("-all")

    assert AssessmentTypes.get_assessments_path(AssessmentTypes.OSI, "all").exists()


def test_osi_has_no_second_extraction_loop(estate):
    """Structural: the inline parse must be gone, not merely bypassed.

    Asserted against the source because the whole point is that one loop exists.
    A reviewer cannot see a duplicate loop in a passing behavioural test.
    """
    import inspect

    import krunner
    # krunner.osi is a click Command; the function is its callback.
    src = inspect.getsource(krunner.osi.callback)

    # Comments stripped: the replacement explains in prose what it removed, and a
    # naive substring match would hit that explanation rather than any code.
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#"))

    assert "extract_dependency_file(" not in code, (
        "osi still parses inline; it must go through kospex.osi_extract")
    assert "JSONDecodeError" not in code, (
        "osi still has its own narrow parse-error handling")
    assert "extract_repo(" in code, "osi must use the shared extraction loop"
