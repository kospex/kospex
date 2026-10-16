"""`declared_scope` records the section a manifest declared a dependency in.

Migration 0009 created the column for exactly this: "declared_scope holds the
RAW string the file used, verbatim, in that format's own vocabulary... This
column exists because every error in building the booleans happened at write
time, where it is unrecoverable."

The lockfile parsers populate it. The two manifest parsers did not, and in
pyproject.toml's case the information was read and then thrown away --
`for group, deps in optional.items()` never used `group`. On a real estate that
discarded 129 distinct extras group names across 432 declared dependencies, 29%
of which name a development group (`dev`, `test`, `docs`, `lint`) that kospex
has a `package_use` value for.

What is deliberately NOT done here: inferring dev-ness from the group name.
`dev` and `test` are obvious, `check`, `cover`, `enabler` and `type` are
judgement calls, and guessing from a name is the recognition-based gating this
codebase has rejected elsewhere. The raw string is stored; classifying it later
is then a query, not a re-scan.
"""

import json

import pytest

from kospex_dependencies import KospexDependencies


@pytest.fixture
def kdeps():
    return KospexDependencies()


def _write(tmp_path, name, payload):
    """Write `payload` as JSON, or verbatim when it is already source text.

    A str payload is treated as the file's literal contents (so TOML fixtures
    can be written inline), everything else is JSON-encoded -- including bare
    scalars, which is how the non-object-document cases are built.
    """
    path = tmp_path / name
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _by_name(records):
    return {r["package_name"]: r for r in records}


class TestPackageJsonDeclaredScope:
    """The npm section name, verbatim as package.json spells it."""

    @pytest.fixture
    def records(self, kdeps, tmp_path):
        path = _write(tmp_path, "package.json", {
            "dependencies": {"lodash": "^4.17.21"},
            "devDependencies": {"jest": "^29.0.0"},
            "peerDependencies": {"react": ">=18"},
            "optionalDependencies": {"fsevents": "^2.3.0"},
        })
        return _by_name(kdeps.parse_package_json(path))

    def test_runtime_dependency(self, records):
        assert records["lodash"]["declared_scope"] == "dependencies"

    def test_dev_dependency(self, records):
        assert records["jest"]["declared_scope"] == "devDependencies"

    def test_peer_dependency(self, records):
        assert records["react"]["declared_scope"] == "peerDependencies"

    def test_optional_dependency(self, records):
        assert records["fsevents"]["declared_scope"] == "optionalDependencies"

    def test_scope_is_the_npm_spelling_not_the_kospex_one(self, records):
        """Verbatim means npm's camelCase key, not kospex's `package_use`."""
        assert records["jest"]["declared_scope"] != "dev"
        assert records["react"]["declared_scope"] != "peer"

    def test_requirements_type_is_unchanged(self, records):
        """declared_scope is additive -- it does not replace the classification."""
        assert records["lodash"]["requirements_type"] == "direct"
        assert records["jest"]["requirements_type"] == "dev"
        assert records["react"]["requirements_type"] == "peer"
        assert records["fsevents"]["requirements_type"] == "optional"


class TestPyprojectDeclaredScope:
    """The extras group name -- the value that was being discarded."""

    @pytest.fixture
    def records(self, kdeps, tmp_path):
        path = _write(tmp_path, "pyproject.toml", """
[project]
name = "acme"
dependencies = ["requests>=2.0"]

[project.optional-dependencies]
dev = ["pytest>=7.0"]
socks = ["pysocks"]
""")
        return _by_name(kdeps.parse_pyproject_file(path))

    def test_runtime_dependency_scope(self, records):
        assert records["requests"]["declared_scope"] == "dependencies"

    def test_extras_group_name_is_kept(self, records):
        assert records["pytest"]["declared_scope"] == "dev"
        assert records["pysocks"]["declared_scope"] == "socks"

    def test_group_name_is_not_guessed_into_a_classification(self, records):
        """A `dev` extras group is still recorded as `optional`.

        PEP 621 extras are "installed only if requested", which is not the same
        fact as npm's devDependencies. The group name is evidence, kept in
        declared_scope; promoting `dev` to requirements_type="dev" would be an
        inference this parser is not entitled to make, and would silently
        reclassify the 71% of groups that are genuine feature extras.
        """
        assert records["pytest"]["requirements_type"] == "optional"
        assert records["pysocks"]["requirements_type"] == "optional"

    def test_two_groups_are_distinguishable(self, records):
        """The whole point: `dev` and `socks` were previously identical rows."""
        assert (
            records["pytest"]["declared_scope"]
            != records["pysocks"]["declared_scope"]
        )


class TestMalformedSectionStillFailsClosed:
    """A section that is not an object must raise, not be skipped.

    `parse_package_json` has no try/except by design: a malformed file raises,
    both scan paths turn that into a `parse_error` outcome, and the file is
    reported as unread rather than as one with nothing in it.

    These pin that property against a plausible "defensive" refactor. An
    `isinstance(declared, dict)` guard with `continue` looks like hygiene and is
    a fail-open: `{"dependencies": ["lodash"]}` would report zero dependencies
    for a file that declares one, and a file whose every section is malformed
    would report as `empty` -- "examined, nothing to find". That regression was
    introduced and caught here.
    """

    @pytest.mark.parametrize("payload", [
        {"dependencies": ["lodash"]},            # array instead of object
        {"dependencies": "lodash"},              # string instead of object
        {"dependencies": None},                  # explicit null
        {"devDependencies": ["jest"]},
        {"peerDependencies": 42},
        {"optionalDependencies": [{"a": 1}]},
    ], ids=["deps-array", "deps-string", "deps-null",
            "devdeps-array", "peerdeps-int", "optdeps-array"])
    def test_non_object_section_raises(self, kdeps, tmp_path, payload):
        path = _write(tmp_path, "package.json", payload)
        with pytest.raises(Exception):
            kdeps.parse_package_json(path)

    def test_a_valid_section_is_not_lost_to_a_malformed_sibling(self, kdeps, tmp_path):
        """Raising is right even when some sections are readable.

        A partial result is the thing to avoid: 1 of 2 sections silently
        dropped is a scan that looks successful.
        """
        path = _write(tmp_path, "package.json", {
            "dependencies": {"lodash": "1.0.0"},
            "devDependencies": ["jest"],
        })
        with pytest.raises(Exception):
            kdeps.parse_package_json(path)

    @pytest.mark.parametrize("payload", [
        ["lodash"],          # top level is an array
        '"lodash"',          # top level is a JSON string
        "42",                # top level is a JSON number
    ], ids=["doc-array", "doc-string", "doc-number"])
    def test_non_object_document_raises(self, kdeps, tmp_path, payload):
        """A package.json whose top level is not an object is malformed.

        This was a pre-existing fail-open, and an inconsistent one: an array or
        a string returned 0 records silently (`"dependencies" in ["x"]` is a
        valid membership test that answers False), while a number raised
        TypeError. Valid JSON, so `json.load` cannot catch it, and npm would
        reject the file outright -- so 0 records meant "this repo declares no
        npm dependencies" for a file kospex could not read.
        """
        path = _write(tmp_path, "package.json", payload)
        with pytest.raises(Exception):
            kdeps.parse_package_json(path)

    def test_absent_section_is_fine(self, kdeps, tmp_path):
        """Absent is not malformed -- a package.json need not declare them all."""
        path = _write(tmp_path, "package.json", {"dependencies": {"lodash": "1.0.0"}})
        records = _by_name(kdeps.parse_package_json(path))
        assert set(records) == {"lodash"}

    def test_empty_section_is_fine(self, kdeps, tmp_path):
        """An empty object genuinely declares nothing, and is well-formed."""
        path = _write(tmp_path, "package.json", {
            "dependencies": {"lodash": "1.0.0"},
            "devDependencies": {},
        })
        records = _by_name(kdeps.parse_package_json(path))
        assert set(records) == {"lodash"}


class TestPyprojectDoesNotFabricatePackages:
    """A string where PEP 621 requires an array must raise, not be iterated.

    `dependencies` and each `optional-dependencies` group are arrays. A string
    is also iterable, so iterating one yields its *characters*:

        dependencies = "requests"   ->  rows named r, e, q, u, e, s, t, s

    which were written to `dependency_data` and looked up on deps.dev. This is
    not a silent miss, it is fabrication: `e`, `q`, `u`, `s`, `t` and `p` are
    real PyPI packages, so a malformed manifest made kospex report a repository
    as depending on packages it has nothing to do with, with their real
    advisories attached. `package_name` is part of the primary key, so the rows
    persist.
    """

    @pytest.mark.parametrize("body", [
        '[project]\nname="acme"\ndependencies="requests"\n',
        '[project]\nname="acme"\ndependencies="flask>=2.0"\n',
    ], ids=["bare-name", "with-specifier"])
    def test_string_dependencies_raises(self, kdeps, tmp_path, body):
        path = _write(tmp_path, "pyproject.toml", body)
        with pytest.raises(Exception):
            kdeps.parse_pyproject_file(path)

    def test_string_extras_group_raises(self, kdeps, tmp_path):
        path = _write(tmp_path, "pyproject.toml",
                      '[project]\nname="acme"\n'
                      '[project.optional-dependencies]\ndev="pytest"\n')
        with pytest.raises(Exception):
            kdeps.parse_pyproject_file(path)

    def test_no_single_character_packages_are_ever_produced(self, kdeps, tmp_path):
        """The property, stated directly: character-wise iteration is the bug."""
        path = _write(tmp_path, "pyproject.toml",
                      '[project]\nname="acme"\ndependencies="requests"\n')
        try:
            records = kdeps.parse_pyproject_file(path)
        except Exception:
            return  # raising is the fix
        assert not [r for r in records if len(r["package_name"]) == 1], (
            "single-character package names indicate a string was iterated "
            "as a sequence of characters"
        )

    def test_table_instead_of_array_raises(self, kdeps, tmp_path):
        """`[project.dependencies]` as a table is not PEP 621 either.

        It happened to produce the right answer -- iterating a dict yields its
        keys -- which is a coincidence, not correctness, and masked the shape of
        the file from anyone reading the output.
        """
        path = _write(tmp_path, "pyproject.toml",
                      '[project]\nname="acme"\n'
                      '[project.dependencies]\nrequests="*"\n')
        with pytest.raises(Exception):
            kdeps.parse_pyproject_file(path)

    def test_well_formed_arrays_still_parse(self, kdeps, tmp_path):
        path = _write(tmp_path, "pyproject.toml", """
[project]
name = "acme"
dependencies = ["requests>=2.0", "flask"]

[project.optional-dependencies]
dev = ["pytest"]
""")
        records = _by_name(kdeps.parse_pyproject_file(path))
        assert set(records) == {"requests", "flask", "pytest"}

    def test_empty_arrays_are_well_formed(self, kdeps, tmp_path):
        path = _write(tmp_path, "pyproject.toml",
                      '[project]\nname="acme"\ndependencies=[]\n')
        assert kdeps.parse_pyproject_file(path) == []


class TestNoExtras:
    def test_pyproject_without_extras_still_records_scope(self, kdeps, tmp_path):
        path = _write(tmp_path, "pyproject.toml", """
[project]
name = "acme"
dependencies = ["requests"]
""")
        records = _by_name(kdeps.parse_pyproject_file(path))
        assert records["requests"]["declared_scope"] == "dependencies"

    def test_package_json_with_only_runtime_deps(self, kdeps, tmp_path):
        path = _write(tmp_path, "package.json", {"dependencies": {"lodash": "1.0.0"}})
        records = _by_name(kdeps.parse_package_json(path))
        assert records["lodash"]["declared_scope"] == "dependencies"
