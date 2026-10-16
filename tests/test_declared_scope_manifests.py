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
    path = tmp_path / name
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path.write_text(payload, encoding="utf-8")
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
