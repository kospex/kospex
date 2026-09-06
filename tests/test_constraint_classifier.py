"""Classify how a dependency is constrained (#187).

Every value here occurs in a real 109-repo estate — none are invented. The
classifier runs during extraction, so it must never raise: an exception would
lose a whole manifest, not one row.
"""
import pytest

from kospex.extractors.constraints import classify_constraint


class TestVersionConstraints:
    @pytest.mark.parametrize("declared,package_type,kind,operator", [
        # exact pins
        ("1.4.3", "npm", "pinned", ""),
        ("==2.31.0", "pypi", "pinned", "=="),
        ("3.1.1", "nuget", "pinned", ""),
        # npm range prefixes — tilde is NOT caret: patch-only vs minor+patch
        ("^4.18.0", "npm", "caret", "^"),
        ("~29.0.0", "npm", "tilde", "~"),
        # open floor
        (">=2.0", "pypi", "gte", ">="),
        (">2.0", "pypi", "gte", ">"),
        # windowed — an upper bound exists
        (">=1.0,<2.0", "pypi", "bounded", ">=,<"),
        (">=23.0, <24.3", "pypi", "bounded", ">=,<"),
        ("^1.0.0 || ^2.0.0", "npm", "bounded", "||"),
        (">=1.0 <2.0", "npm", "bounded", ">=,<"),
        ("~=8.1", "pypi", "tilde", "~="),
        ("<4", "pypi", "bounded", "<"),
        # Upper bound declared FIRST — the common shape in the live estate
        # (79 rows, all of this form). The operator label is normalised to
        # ">=,<" regardless of declared order; the raw text stays in
        # package_version if anyone needs the ordering back.
        ("<1,>=0.23.0", "pypi", "bounded", ">=,<"),
        ("<5,>=4.14", "pypi", "bounded", ">=,<"),
    ])
    def test_version_constraints(self, declared, package_type, kind, operator):
        assert classify_constraint(declared, package_type) == (kind, operator)


class TestGoPseudoVersions:
    """A Go pseudo-version is MAXIMALLY pinned — a specific commit — yet today
    it lands in unresolved_spec alongside `>=1.0,<2.0`, which is the opposite.
    12 of mergestat's 88 Go modules are this shape."""

    @pytest.mark.parametrize("declared", [
        "v0.0.0-20230828082145-3c4c8a2d2371",
        "v0.0.0-20221005151137-0ff49e3f5413",
        "v1.5.1-0.20230307220236-3a3c6141e376",
    ])
    def test_pseudo_versions_are_commit_pins(self, declared):
        assert classify_constraint(declared, "go") == ("commit", "")

    def test_ordinary_go_version_is_pinned_not_commit(self):
        assert classify_constraint("v1.8.0", "go") == ("pinned", "")


class TestNonVersionValues:
    """Resolution mechanisms, not constraints. Each means something different."""

    @pytest.mark.parametrize("declared,kind,operator", [
        ("latest", "latest", ""),
        ("workspace:^", "workspace", "workspace:"),
        ("workspace:*", "workspace", "workspace:"),
        ("link:../scripts/repo-utils", "link", "link:"),
        # file: is a local path like link:, and occurs in the live estate.
        ("file:./packages/helpers", "link", "file:"),
        ("catalog:", "catalog", "catalog:"),
        ("catalog:dev", "catalog", "catalog:"),
        ("npm:@babel/core@7.24.4", "alias", "npm:"),
        ("patch:rollup-plugin-dts@npm%3A6.1.0#~/.yarn/patches/x", "patch", "patch:"),
    ])
    def test_non_version_values(self, declared, kind, operator):
        assert classify_constraint(declared, "npm") == (kind, operator)


class TestNoDeclaration:
    @pytest.mark.parametrize("declared", ["", None])
    def test_absent_version_is_none_kind(self, declared):
        assert classify_constraint(declared, "pypi") == ("none", "")


class TestTotality:
    """It runs during extraction — raising loses a whole manifest."""

    @pytest.mark.parametrize("declared", [
        "", None, "   ", "\n", "!!!", "@@@@", "." * 300,
        "éèê", ">=", "^", "~", "||", ":", "1.2.3.4.5.6",
    ])
    def test_never_raises_and_always_returns_a_pair(self, declared):
        result = classify_constraint(declared, "npm")
        assert isinstance(result, tuple) and len(result) == 2
        assert all(isinstance(x, str) for x in result)

    @pytest.mark.parametrize("package_type", ["npm", "pypi", "go", "nuget", None, "", "unknown"])
    def test_unknown_ecosystem_does_not_raise(self, package_type):
        kind, operator = classify_constraint("^1.0.0", package_type)
        assert isinstance(kind, str) and isinstance(operator, str)


class TestKindVocabularyIsClosed:
    """Guards against a typo introducing a fourteenth kind by accident."""

    VALID = {
        "pinned", "commit", "caret", "tilde", "gte", "bounded", "latest",
        "workspace", "link", "catalog", "alias", "patch", "none",
    }

    @pytest.mark.parametrize("declared,package_type", [
        ("1.4.3", "npm"), ("^4.18.0", "npm"), ("~1.0", "npm"),
        (">=1.0,<2.0", "pypi"), ("latest", "npm"), ("catalog:dev", "npm"),
        ("v0.0.0-20230828082145-3c4c8a2d2371", "go"), ("", "pypi"),
        ("npm:x@1.0.0", "npm"), ("workspace:^", "npm"), ("junk!!", "npm"),
    ])
    def test_kind_is_always_from_the_vocabulary(self, declared, package_type):
        kind, _op = classify_constraint(declared, package_type)
        assert kind in self.VALID, f"{declared!r} produced unknown kind {kind!r}"
