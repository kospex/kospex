"""Maven/NuGet interval ranges, and npm upper-bound-only ranges, are looked up
at a version they allow (#414).

`clean_version_spec` read Maven/NuGet interval notation as if it were npm:
`[1.0,2.0)` gave `2.0)`, `(,2.0]` gave `2.0]`, and `[1.0,)` and `[1.5]` passed
through unchanged. None of those is a version, so every lookup 404'd and the
row was recorded as `version_yanked`.

Now:
* `[1.5]` is a pin and is looked up as `1.5`.
* An inclusive lower bound, `[1.0,2.0)` or `[1.0,)`, is looked up at `1.0`.
* Anything else -- an exclusive lower bound, no lower bound, or a union of
  intervals -- needs Maven's version ordering to evaluate, which kospex does
  not have. It is recorded as `range_not_evaluated`, with nothing looked up,
  rather than a broken version sent to deps.dev.
* If an inclusive lower bound is not a published version, the range can still
  allow others, so a 404 is `range_not_evaluated`, not `version_yanked`. A
  range cannot be yanked.

npm: `<4.0.0` has only a ceiling, and that ceiling, 4.0.0, was kept as the
lookup version whenever it was published -- the one version the range
excludes. The written floor is now kept only if the range allows it, as PyPI
already does (#388).

Both scan paths are driven with real deps.dev package records from
tests/data/depsdev/.
"""
import pytest

from kospex_dependencies import KospexDependencies

from test_npm_range_floor import PATHS


NETTY = "io.netty:netty-handler"
NEWTONSOFT = "Newtonsoft.Json"


@PATHS
@pytest.mark.parametrize("system, name, declared, expected", [
    # Exact pin in interval notation.
    ("maven", NETTY, "[4.1.0.Final]", "4.1.0.Final"),
    # Inclusive lower bound, with and without a ceiling.
    ("maven", NETTY, "[4.1.0.Final,5.0)", "4.1.0.Final"),
    ("maven", NETTY, "[4.1.0.Final,)", "4.1.0.Final"),
    ("maven", NETTY, "[ 4.1.0.Final , 5.0 )", "4.1.0.Final"),
    ("nuget", NEWTONSOFT, "[12.0.1,13.0)", "12.0.1"),
    ("nuget", NEWTONSOFT, "[13.0.1]", "13.0.1"),
])
def test_interval_is_looked_up_at_its_inclusive_lower_bound(scan, system, name,
                                                           declared, expected):
    row, query = scan(system, name, declared)

    assert query.asked == [expected]
    assert row["resolution"] == "resolved"
    assert row["resolved_version"] == expected
    assert row["package_version"] == declared          # the declaration is untouched


@PATHS
@pytest.mark.parametrize("system, name, declared", [
    ("maven", NETTY, "(4.1.0.Final,5.0)"),            # exclusive lower bound
    ("maven", NETTY, "(,4.2.0.Final]"),               # no lower bound
    ("maven", NETTY, "[4.1.0.Final,4.2),[4.3,)"),     # union of intervals
    ("nuget", NEWTONSOFT, "(12.0.1,13.0)"),
])
def test_interval_without_an_inclusive_floor_is_not_evaluated(scan, system, name, declared):
    row, query = scan(system, name, declared)

    assert query.asked == []                            # nothing broken sent to deps.dev
    assert row["resolution"] == "range_not_evaluated"
    assert row["resolved_version"] == ""
    assert row["versions_behind"] is None


@PATHS
def test_interval_floor_not_published_is_not_yanked(scan):
    """4.0.99.Final was never published, but the range allows 4.1.0.Final."""
    row, query = scan("maven", NETTY, "[4.0.99.Final,5.0)")

    assert query.asked == ["4.0.99.Final"]
    assert row["resolution"] == "range_not_evaluated"
    assert row["versions_behind"] is None


@PATHS
def test_unpublished_interval_pin_is_still_a_lookup_miss(scan):
    """`[x]` is a pin: nothing else is allowed, so a 404 is the pin's own."""
    row, query = scan("maven", NETTY, "[4.0.99.Final]")

    assert query.asked == ["4.0.99.Final"]
    assert row["resolution"] == "version_yanked"


@PATHS
def test_bare_maven_version_is_unchanged(scan):
    row, query = scan("maven", NETTY, "4.1.0.Final")

    assert query.asked == ["4.1.0.Final"]
    assert row["resolution"] == "resolved"


@pytest.mark.parametrize("system, declared, expected", [
    ("maven", "[1.5]", "1.5"),
    ("maven", "[1.0,2.0)", "1.0"),
    ("maven", "[1.0,)", "1.0"),
    ("nuget", "[1.0,2.0)", "1.0"),
    # No inclusive floor: returned unchanged, never a fragment like `2.0)`.
    ("maven", "(,2.0]", "(,2.0]"),
    ("maven", "(1.0,2.0)", "(1.0,2.0)"),
    ("maven", "[1.0,2.0),[3.0,)", "[1.0,2.0),[3.0,)"),
])
def test_clean_version_spec_reads_intervals(system, declared, expected):
    assert KospexDependencies().clean_version_spec(declared, system) == expected


@PATHS
def test_npm_upper_bound_only_is_not_checked_at_its_ceiling(scan):
    """`<18.0.0` excludes 18.0.0, which is published. The lookup is the lowest
    published stable version the range allows, as for `<4` (#379)."""
    row, query = scan("npm", "react", "<18.0.0")

    assert query.asked == ["0.0.1"]
    assert row["resolved_version"] == "0.0.1"


@PATHS
def test_npm_written_floor_the_range_allows_is_kept(scan):
    """`<=18.0.0` allows 18.0.0, so the written version is kept, as PyPI keeps
    `<=2.0` at 2.0 (#388)."""
    row, query = scan("npm", "react", "<=18.0.0")

    assert query.asked == ["18.0.0"]


def test_new_resolution_is_labelled_in_kweb():
    import pathlib
    from kweb2 import UNRESOLVED_STATUS_LABELS
    template = (pathlib.Path(__file__).parent.parent / "src" / "templates"
                / "dependencies.html").read_text()

    assert UNRESOLVED_STATUS_LABELS["range_not_evaluated"] == "Range not evaluated"
    assert "'range_not_evaluated': 'range not evaluated'" in template


@pytest.mark.parametrize("system", ["maven", "nuget", "gradle"])
def test_interval_parsing_is_linear_cwe_1333(system):
    """Declared versions come from untrusted manifests. The interval pattern
    this replaced backtracked polynomially on runs of spaces around a comma:
    163 characters took 5 s, and each doubling cost about 27 times more."""
    import time
    from kospex.extractors.constraints import classify_constraint, interval_lookup_version

    spaces = " " * 5000
    hostile = ["[" + spaces + "," + spaces + "x", "[" + spaces + "," + spaces + ")x",
               "(" + spaces + "," + spaces + "]"]
    start = time.perf_counter()
    for text in hostile:
        classify_constraint(text, system)
        interval_lookup_version(text)
        KospexDependencies().clean_version_spec(text, "maven")
    assert time.perf_counter() - start < 1.0
