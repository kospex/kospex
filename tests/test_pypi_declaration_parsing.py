"""Regression tests for `parse_pypi_package_declaration` — issue #29.

The original implementation substring-tested version operators against the
**whole declaration, including the environment marker**, then split on the first
operator it found:

    elif ">=" in package_declaration:   version_spec = ">="   # tested before ~=
    ...
    package["package_name"]    = package_declaration.split(version_spec)[0]
    package["package_version"] = package_declaration.split(version_spec)[1]

Four consequences, all covered below:

1. Environment markers were not stripped, so `requests; sys_platform == 'win32'`
   split on the *marker's* `==` and produced a package named
   `"requests; sys_platform "`.
2. Operator precedence was wrong — `>=` was tested before `~=`, so
   `numpy~=2.3.3; python_version >= "3.14"` split on the marker's `>=` and the
   name absorbed the real specifier.
3. Neither field was stripped, leaving `"hypothesis "` / `" 3.30"`.
4. Unpinned declarations and `<`, `<=`, `!=`, `===` specs returned ``None``.
   `parse_pip_requirements_file` drops falsy results, so those packages never
   reached `dependency_data` at all.

Defect 4 is the damaging one. Observed on a real synced repo,
`theskumar/python-dotenv/requirements.txt` declares ten packages, nine of them
unpinned. `dependency_data` held exactly one — `pytest`, the only line carrying
a specifier. A 90% loss, silent.

**Names are not canonicalised.** `package_name` and `package_version` are both
part of the `dependency_data` primary key, so rewriting `MarkupSafe` to
`markupsafe` would insert duplicate rows on re-sync rather than update existing
ones. `packaging` is used to *parse* the declaration, never to rename it.
"""
import pytest

from kospex_dependencies import KospexDependencies


@pytest.fixture
def kd():
    return KospexDependencies.__new__(KospexDependencies)


class TestEnvironmentMarkers:
    """Markers must be stripped before the version is parsed (defects 1 and 2)."""

    def test_marker_with_no_version_does_not_leak_into_the_name(self, kd):
        result = kd.parse_pypi_package_declaration("requests; sys_platform == 'win32'")
        assert result is not None
        assert result["package_name"] == "requests"
        assert result["package_version"] == ""

    def test_marker_operator_does_not_win_over_the_real_specifier(self, kd):
        result = kd.parse_pypi_package_declaration(
            'numpy~=2.3.3; python_version >= "3.14"'
        )
        assert result is not None
        assert result["package_name"] == "numpy"
        assert result["package_version"] == "2.3.3"
        assert result["version_type"] == "~="


class TestWhitespace:
    """Neither field may carry surrounding whitespace (defect 3)."""

    def test_spaces_around_the_operator_are_stripped(self, kd):
        result = kd.parse_pypi_package_declaration("hypothesis >= 3.30")
        assert result is not None
        assert result["package_name"] == "hypothesis"
        assert result["package_version"] == "3.30"
        assert result["version_type"] == ">="


class TestUnpinnedDeclarations:
    """A declaration with no version must be recorded, not dropped (defect 4)."""

    def test_bare_package_is_recorded_with_an_empty_version(self, kd):
        result = kd.parse_pypi_package_declaration("requests")
        assert result is not None, (
            "a bare, unpinned requirement must still be recorded — dropping it "
            "hides the least reproducible way to declare a dependency"
        )
        assert result["package_name"] == "requests"
        assert result["package_version"] == ""

    def test_extras_without_a_version_are_recorded(self, kd):
        result = kd.parse_pypi_package_declaration("httpx[cli,http2]")
        assert result is not None
        assert result["package_name"] == "httpx"
        assert result["package_version"] == ""


class TestOperatorCoverage:
    """Every PEP 440 operator must parse, not just >=, ~= and == (defect 4)."""

    @pytest.mark.parametrize(
        "declaration,name,version,operator",
        [
            ("urllib3 < 3", "urllib3", "3", "<"),
            ("flask != 2.0.0", "flask", "2.0.0", "!="),
            ("django <= 4.2", "django", "4.2", "<="),
            ("boto3 > 1.0", "boto3", "1.0", ">"),
            ("attrs === 23.1.0", "attrs", "23.1.0", "==="),
            ("duckdb==1.4.3", "duckdb", "1.4.3", "=="),
        ],
    )
    def test_operator_parses(self, kd, declaration, name, version, operator):
        result = kd.parse_pypi_package_declaration(declaration)
        assert result is not None, f"{declaration!r} must not be dropped"
        assert result["package_name"] == name
        assert result["package_version"] == version
        assert result["version_type"] == operator


class TestExistingBehaviourPreserved:
    """Behaviour other code and tests already rely on must not regress."""

    def test_multi_specifier_still_classifies_as_multiple(self, kd):
        """Issue #108 — the whole spec is retained and typed 'multiple'."""
        result = kd.parse_pypi_package_declaration("requests>=1.0,<2.0")
        assert result is not None
        assert result["package_name"] == "requests"
        assert result["package_version"] == ">=1.0,<2.0"
        assert result["version_type"] == "multiple"

    def test_names_are_not_canonicalised(self, kd):
        """package_name is part of the dependency_data primary key.

        Rewriting the declared name would insert duplicate rows on re-sync
        instead of updating the existing ones.
        """
        result = kd.parse_pypi_package_declaration("MarkupSafe==3.0.3")
        assert result is not None
        assert result["package_name"] == "MarkupSafe"


class TestRequirementsFileIsNotLossy:
    """End-to-end: the file parser must not silently drop unpinned lines."""

    def test_unpinned_packages_survive_the_file_parser(self, kd, tmp_path):
        """Mirrors theskumar/python-dotenv/requirements.txt, which lost 9 of 10."""
        req = tmp_path / "requirements.txt"
        req.write_text(
            "bumpversion\nclick\nipython\npytest-cov\npytest>=9.0.3\n"
            "tox\nwheel\nruff\nbuild\npre-commit\n"
        )

        packages = kd.parse_pip_requirements_file(str(req))

        names = [p["package_name"] for p in packages]
        assert len(packages) == 10, (
            f"expected all 10 declarations, got {len(packages)}: {names}"
        )
        assert "bumpversion" in names
        assert "pre-commit" in names

    def test_comments_and_blank_lines_are_still_skipped(self, kd, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("# a comment\n\nrequests\n\n# another\nclick==8.1\n")

        packages = kd.parse_pip_requirements_file(str(req))

        assert [p["package_name"] for p in packages] == ["requests", "click"]
