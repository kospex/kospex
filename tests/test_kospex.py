""" Tests for kospex """
import json
from pathlib import Path
from unittest.mock import patch
from kospex_core import Kospex
import krunner_utils as KrunnerUtils
import kospex_utils as KospexUtils
from kospex_query import KospexQuery, KospexData
from kospex_dependencies import KospexDependencies
import kospex_schema as KospexSchema

def test_kospex():
    """ Test object creation works"""
    k = Kospex()
    assert k is not None

# Tests for krunner_utils

def test_extract_grep_parameters():
    """ Test the extract_grep_parameters function """
    # Test with a valid input string
    input_string = "./kospex_utils.py:12:    # Split the input string at colons"
    expected = ["kospex_utils.py", "12", "    # Split the input string at colons"]
    assert KrunnerUtils.extract_grep_parameters(input_string) == expected

    # Test with a valid input string, additional colon
    input_string = "./kospex_utils.py:12:    # Split the input string at colons:"
    expected = ["kospex_utils.py", "12", "    # Split the input string at colons:"]
    assert KrunnerUtils.extract_grep_parameters(input_string) == expected

    # Test with a valid input string, many colons
    input_string = "one:two:three:four:five"
    expected = ["one", "two", "three:four:five"]
    assert KrunnerUtils.extract_grep_parameters(input_string) == expected

# Tests for kospex_utils

def test_git_rename_event():
    """ Test the parse_git_rename_event function """
    # KospexUtils.parse_git_rename_event()

    # Test with a simple rename
    input_string = "{bin/act => utilities/developer_utils/act}"
    expected = "utilities/developer_utils/act"
    assert KospexUtils.parse_git_rename_event(input_string) == expected

    # Test a plain string is just returned as a string
    input_string = "bin/act/utilities"
    assert input_string == KospexUtils.parse_git_rename_event(input_string)

    # Check a double braces weird filename is just returned as a string
    no_rename_with_braces = "/cookiecutter/app_name/static/css/{{cookiecutter.app_name}}.css"
    assert no_rename_with_braces == KospexUtils.parse_git_rename_event(no_rename_with_braces)

    # Check a replacement with another double braces weird filename
    # pylint: disable=line-too-long
    replace_and_2braces = '{bin/act => utilities/developer_utils}/cookiecutter/static/css/{{cookiecutter.app_name}}.css'
    expected = 'utilities/developer_utils/cookiecutter/static/css/{{cookiecutter.app_name}}.css'
    assert expected == KospexUtils.parse_git_rename_event(replace_and_2braces)


def test_git_rename_event_empty_brace_side():
    """A directory-level rename renders in git --numstat with an empty brace
    side (the file moved up/down a level). Expanding the empty side must not
    leave a doubled or leading slash, or the resulting path won't match the
    working-tree path and file_metadata.committer_when ends up NULL."""
    # File moved up a level: ".github/workflows/dependabot.yml" -> ".github/dependabot.yml"
    assert KospexUtils.parse_git_rename_event(
        ".github/{workflows => }/dependabot.yml") == ".github/dependabot.yml"
    # File moved to repo root: "docs/README.md" -> "README.md"
    assert KospexUtils.parse_git_rename_event("{docs => }/README.md") == "README.md"
    # Empty side at the start (added dir level) still resolves cleanly
    assert KospexUtils.parse_git_rename_event("{ => src}/foo.js") == "src/foo.js"
    # Both-sides-present renames are unaffected
    assert KospexUtils.parse_git_rename_event("pkg/{old => new}/x.py") == "pkg/new/x.py"

def test_git_rename_event_no_braces():
    """git only uses the brace form when the old and new paths share a common
    leading directory or trailing component. With nothing in common it emits a
    bare "old => new", which must resolve to the new path — otherwise the raw
    arrow string is stored as commit_files.file_path, never matches the
    working-tree path, and file_metadata.committer_when ends up NULL."""
    # Root-level file moved into a subdirectory (no shared prefix or suffix)
    assert KospexUtils.parse_git_rename_event(
        "FASTAPI_MIGRATION.md => changes/FASTAPI_MIGRATION.md") == "changes/FASTAPI_MIGRATION.md"
    # Extension change at the repo root
    assert KospexUtils.parse_git_rename_event("LICENSE.rst => LICENSE.txt") == "LICENSE.txt"
    # Directory-to-directory move with nothing in common
    assert KospexUtils.parse_git_rename_event("old/a.py => new/b.py") == "new/b.py"
    # A path that merely contains "=>" without the git rename spacing is untouched
    assert KospexUtils.parse_git_rename_event("src/a=>b.txt") == "src/a=>b.txt"


def test_days_functions():
    """ Test the days calculation functions """

    d1 = "2023-08-14T17:06:01+10:00"
    d2 = "2021-03-01T01:10:23+00:00"
    assert KospexUtils.days_between_datetimes(d1, d2) == 896.2

    # The same date should return 0.0
    # (i.e. no days between them and 1 decimal places)
    d1 = "2024-05-06T15:50:01+10:00"
    d2 = d1
    assert KospexUtils.days_between_datetimes(d1, d2) == 0.0

# Tests for KospexData

def test_has_complex_sql():
    """ Test validators for more complex SQL expressions """

    import sqlite_utils
    kd = KospexData(kospex_db=sqlite_utils.Database(":memory:"))
    assert kd.is_valid_sql_name("SUM") is True

    assert kd.has_parentheses("SUM") is False

    # Test a simple SQL expression
    simple_sql = "count(author_email)"
    assert kd.has_parentheses(simple_sql) is True
    assert kd.validate_nested_expressions(simple_sql) is True

    #complex_sql = "Count(Distinct(author_email))"
    complex_sql = "count(distinct(author_email))"
    assert kd.has_parentheses(complex_sql) is True
    assert kd.validate_nested_expressions(complex_sql) is True

    invalid_complex = "cou!nt(distinct(author_email))"
    assert kd.validate_nested_expressions(invalid_complex) is False


def test_find_dependency_files_includes_pnpm(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    kdeps = KospexDependencies()
    found = kdeps.find_dependency_files(str(tmp_path))
    assert any("pnpm-lock.yaml" in f for f in found), \
        f"pnpm-lock.yaml not found in {found}"


def test_npm_assess_stamps_package_use(tmp_path):
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({
        "dependencies": {"lodash": "4.17.21"},
        "devDependencies": {"jest": "29.0.0"},
    }))

    kdeps = KospexDependencies()

    def fake_depsdev(pkg_type, name, version):
        return {"package_name": name, "package_version": version, "package_type": pkg_type}

    with patch.object(kdeps, "depsdev_record", side_effect=fake_depsdev):
        results = kdeps.npm_assess(str(pkg_json), dev_deps=True)

    by_name = {r["package_name"]: r for r in results}
    assert by_name["lodash"]["package_use"] == KospexSchema.PACKAGE_USE_DIRECT
    assert by_name["jest"]["package_use"] == KospexSchema.PACKAGE_USE_DEV


PNPM_V9_FIXTURE = Path(__file__).parent / "fixtures" / "pnpm" / "v9-simple.yaml"


def test_assess_pnpm_returns_records_with_package_use(tmp_path):
    # assess() dispatches on the literal filename; write fixture as pnpm-lock.yaml
    lock_file = tmp_path / "pnpm-lock.yaml"
    lock_file.write_text(PNPM_V9_FIXTURE.read_text())

    kdeps = KospexDependencies()

    def fake_depsdev(pkg_type, name, version):
        return {"package_name": name, "package_version": version, "package_type": pkg_type}

    with patch.object(kdeps, "depsdev_record", side_effect=fake_depsdev):
        results = kdeps.assess(str(lock_file))

    assert results is not None, "assess() returned None for pnpm-lock.yaml"
    assert len(results) > 0

    by_name = {r["package_name"]: r for r in results}

    assert by_name["lodash"]["package_use"] == KospexSchema.PACKAGE_USE_DIRECT
    assert by_name["jest"]["package_use"] == KospexSchema.PACKAGE_USE_DEV

    transitive = [r for r in results if r["package_use"] == KospexSchema.PACKAGE_USE_TRANSITIVE]
    assert len(transitive) >= 1, "Expected at least one transitive package"


def test_assess_pnpm_not_none(tmp_path):
    """assess() must not return None for a pnpm-lock.yaml (would print error and exit in CLI)."""
    lock_file = tmp_path / "pnpm-lock.yaml"
    lock_file.write_text(PNPM_V9_FIXTURE.read_text())

    kdeps = KospexDependencies()
    with patch.object(kdeps, "depsdev_record", return_value={}):
        result = kdeps.assess(str(lock_file))
    assert result is not None
