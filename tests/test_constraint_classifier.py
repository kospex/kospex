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
        # NuGet bare is a MINIMUM (`>= 3.1.1`), not an equality — an exact
        # pin needs `[3.1.1]`. See TestBareVersionIsEcosystemDependent.
        ("3.1.1", "nuget", "gte", ""),
        # PEP 440 arbitrary equality: a raw string comparison, not normalised
        # version equality — ===1.0 does not match 1.0.0, ===2020.1 does not
        # match 2020.01, and it has no wildcard support. The escape hatch for
        # versions that are not PEP 440 compliant. Distinct operator from ==.
        ("=== 23.1.0", "pypi", "pinned", "==="),
        ("===23.1.0", "pypi", "pinned", "==="),
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

    def test_ordinary_go_version_is_gte_not_commit(self):
        """An ordinary Go version must not match the pseudo-version regex.

        It classifies `gte` rather than `pinned` because a Go `require` is a
        Minimal Version Selection floor — see
        TestBareVersionIsEcosystemDependent. What this test guards is the
        commit/not-commit boundary.
        """
        assert classify_constraint("v1.8.0", "go") == ("gte", "")


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
        # A BARE version, deliberately: `^1.0.0` returns at the caret branch
        # and never reaches the package_type lookup, so it would test nothing
        # about the argument this parametrisation exists to vary.
        kind, operator = classify_constraint("1.0.0", package_type)
        assert isinstance(kind, str) and isinstance(operator, str)

    @pytest.mark.parametrize("package_type", [123, 4.5, object(), ["npm"]])
    def test_non_string_package_type_does_not_raise(self, package_type):
        """package_type is now read, so it must be coerced like the version."""
        kind, operator = classify_constraint("1.0.0", package_type)
        assert isinstance(kind, str) and isinstance(operator, str)


class TestPinnedMeansExactlyOneVersion:
    """`pinned` means exactly one version, nothing can drift.

    Everything here previously landed in `pinned` via the "starts with a
    digit" fallback, which asked what a string looked like rather than what
    it permitted. Each of these permits more than one version, so reporting
    them as pinned overstates how locked-down a dependency is — the wrong
    direction for the question this column exists to answer.
    """

    @pytest.mark.parametrize("declared,package_type,kind", [
        # Wildcards: a floor and a ceiling, so `bounded`.
        # `1.x` is `>=1.0.0 <2.0.0`; `==1.*` is `>=1.0 <2.0`.
        ("1.x", "npm", "bounded"),
        ("2.*", "npm", "bounded"),
        ("1.2.x", "npm", "bounded"),
        ("==1.*", "pypi", "bounded"),
        ("==8.*", "pypi", "bounded"),
        # npm hyphen range: `1.2.3 - 2.3.4` is `>=1.2.3 <=2.3.4`.
        # The spaces are required by npm, which is what separates it from a
        # prerelease like `1.2.3-beta`.
        ("1.2.3 - 2.3.4", "npm", "bounded"),
    ])
    def test_ranges_are_not_pins(self, declared, package_type, kind):
        assert classify_constraint(declared, package_type)[0] == kind

    def test_exclusion_is_its_own_kind(self):
        """`!=` is the opposite of a pin: everything EXCEPT one version.

        It fits nothing else — no floor, no ceiling. It earns its own kind
        because it is also the only declaration that encodes "we know this
        specific release is bad", which is worth being able to query for.
        One line in the 109-repo reference estate (`attrs!=21.1.0`).
        """
        assert classify_constraint("!=21.1.0", "pypi") == ("excluded", "!=")

    def test_exclusion_inside_an_open_range_stays_gte(self):
        """`Sphinx>=1.7.3,!=1.8.0` — the carve-out is not the headline.

        The open floor is what matters for drift, so this stays `gte`. Only
        a STANDALONE `!=` is `excluded`. 14 estate lines are this shape.
        """
        assert classify_constraint(">=1.7.3,!=1.8.0", "pypi") == ("gte", ">=")

    @pytest.mark.parametrize("declared,package_type,kind", [
        ("1.4.3", "npm", "pinned"),
        ("==2.31.0", "pypi", "pinned"),
        ("===23.1.0", "pypi", "pinned"),
        ("[3.1.1]", "nuget", "pinned"),
    ])
    def test_real_pins_still_pin(self, declared, package_type, kind):
        assert classify_constraint(declared, package_type)[0] == kind


class TestBareVersionIsEcosystemDependent:
    """The same bare string means different things in different ecosystems.

    This is the one rule that genuinely needs `package_type`; it cannot be
    decided from the shape of the string.

    npm treats a bare version as strict equality. Go's `require foo v1.2.3`
    is a Minimal Version Selection *floor* — the build picks the maximum of
    all minimums across the module graph, so an unrelated dependency
    requiring v1.5.0 bumps you without that line changing. NuGet documents a
    bare `PackageReference Version="3.1.1"` as `>= 3.1.1`; an exact pin
    needs bracket notation.
    """

    def test_npm_bare_version_is_an_exact_pin(self):
        assert classify_constraint("1.4.3", "npm") == ("pinned", "")

    def test_go_bare_version_is_a_minimum(self):
        assert classify_constraint("v1.8.0", "go") == ("gte", "")

    def test_nuget_bare_version_is_a_minimum(self):
        assert classify_constraint("3.1.1", "nuget") == ("gte", "")

    def test_unknown_ecosystem_defaults_to_pinned(self):
        """Absent a reason to think otherwise, a bare version is a pin."""
        assert classify_constraint("1.4.3", None) == ("pinned", "")
        assert classify_constraint("1.4.3", "unknown") == ("pinned", "")

    def test_go_pseudo_version_stays_commit(self):
        """MVS makes a pseudo-version a floor too, but `commit` says more.

        It records that the dependency points at a commit with no published
        release, which is why deps.dev has nothing to match. Demoting it to
        `gte` would lose that; the MVS caveat is documented on the kind.
        """
        assert classify_constraint(
            "v0.0.0-20230828082145-3c4c8a2d2371", "go") == ("commit", "")


class TestNuGetBracketNotation:
    """NuGet uses interval notation, and each end is independent.

    `[` / `]` are inclusive, `(` / `)` exclusive, so `[1.0,2.0)` is
    `>=1.0 <2.0` — the idiomatic "any 1.x". None of these classified at all
    before: a leading `[` fails the digit check and fell through to `none`.
    """

    @pytest.mark.parametrize("declared,kind", [
        ("[3.1.1]", "pinned"),      # single value, exact
        ("[1.0,)", "gte"),          # floor only
        ("(1.0,)", "gte"),          # floor only, exclusive
        ("[1.0,2.0)", "bounded"),   # floor + ceiling
        ("[1.0,2.0]", "bounded"),   # both inclusive
        ("(,2.0]", "bounded"),      # ceiling only
    ])
    def test_bracket_ranges(self, declared, kind):
        assert classify_constraint(declared, "nuget")[0] == kind


class TestEcosystemRulesDoNotLeak:
    """A rule added for one ecosystem must not fire in another.

    The NuGet interval rule was originally ungated and silently reclassified
    valid PEP 508 declarations: `requests (>=2.0)` matched the bracket
    pattern and came back ("pinned", "") — an open floor reported as a pin
    with the operator erased, which is worse than the defect it replaced
    because nothing on the row records what was actually declared.
    """

    @pytest.mark.parametrize("declared,kind,operator", [
        # PEP 508 permits parentheses around a specifier.
        ("(>=2.0)", "gte", ">="),
        ("(==2.31.0)", "pinned", "=="),
        ("(>=1.0,<2.0)", "bounded", ">=,<"),
    ])
    def test_parenthesised_pypi_specifiers_are_not_nuget_intervals(
            self, declared, kind, operator):
        assert classify_constraint(declared, "pypi") == (kind, operator)

    def test_hyphen_rule_does_not_fire_for_pypi(self):
        """`\\S+` is unconstrained, so this rule must stay npm-scoped."""
        assert classify_constraint("foo - bar", "pypi")[0] != "bounded"

    def test_nuget_intervals_still_work_for_nuget(self):
        assert classify_constraint("[1.0,2.0)", "nuget")[0] == "bounded"
        assert classify_constraint("[3.1.1]", "nuget")[0] == "pinned"


class TestNpmPartialVersions:
    """npm reads a partial version as a range, with no wildcard to show it.

    `"react": "16"` is `>=16.0.0 <17.0.0`; `"16.8"` is `>=16.8.0 <16.9.0`.
    12 rows in the reference estate are this shape — three times the `==N.*`
    family — and every one of them looked like a pin.
    """

    @pytest.mark.parametrize("declared", ["16", "16.8", "4", "19.2", "0.14"])
    def test_npm_partial_is_a_range(self, declared):
        assert classify_constraint(declared, "npm")[0] == "bounded"

    def test_npm_full_version_is_still_a_pin(self):
        assert classify_constraint("16.8.0", "npm") == ("pinned", "")

    def test_partial_rule_is_npm_only(self):
        """A bare `2.0` elsewhere is a full version, not a partial one."""
        assert classify_constraint("2.0", "pypi") == ("pinned", "")
        assert classify_constraint("2.0", None) == ("pinned", "")


class TestMalformedNuGetIntervals:
    """Malformed input must not come back as a confident pin.

    NuGet documents `(1.0)` as invalid; `[1.0)` and `(1.0]` are mismatched.
    Reporting any of them as `pinned` is the wrong direction of error.
    """

    @pytest.mark.parametrize("declared", ["(1.0)", "[1.0)", "(1.0]"])
    def test_invalid_intervals_are_none(self, declared):
        assert classify_constraint(declared, "nuget") == ("none", "")


class TestKindVocabularyIsClosed:
    """Guards against a typo introducing a fifteenth kind by accident."""

    VALID = {
        "pinned", "commit", "caret", "tilde", "gte", "bounded", "latest",
        "workspace", "link", "catalog", "alias", "patch", "none", "excluded",
    }

    def test_valid_set_matches_the_module_constant(self):
        from kospex.extractors.constraints import KINDS
        assert set(KINDS) == self.VALID
        assert len(KINDS) == 14

    @pytest.mark.parametrize("declared,package_type", [
        ("1.4.3", "npm"), ("^4.18.0", "npm"), ("~1.0", "npm"),
        (">=1.0,<2.0", "pypi"), ("latest", "npm"), ("catalog:dev", "npm"),
        ("v0.0.0-20230828082145-3c4c8a2d2371", "go"), ("", "pypi"),
        ("npm:x@1.0.0", "npm"), ("workspace:^", "npm"), ("junk!!", "npm"),
        ("!=1.0", "pypi"), ("1.x", "npm"), ("[1.0,2.0)", "nuget"),
        ("v1.8.0", "go"), ("3.1.1", "nuget"),
    ])
    def test_kind_is_always_from_the_vocabulary(self, declared, package_type):
        kind, _op = classify_constraint(declared, package_type)
        assert kind in self.VALID, f"{declared!r} produced unknown kind {kind!r}"
