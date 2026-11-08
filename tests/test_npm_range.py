"""npm range evaluation, checked against node-semver itself (#379).

kospex needs one question answered for an npm declaration: which published
stable versions does this range permit? It is used to find the lowest one,
which is what a range's `resolved_version` records. Zero-filling the declared
floor is not enough: `<4` has no floor, `18 || 19` has two, and `^1`'s `1.0.0`
is often never published (`@playwright/test` starts at 1.12.0).

The golden file is node-semver 7.6.3's verdict, not ours. For each range, a
pool of candidate versions is built around the numbers in the range (by
`candidate_pool` in tests/npm_range_pool.py, shared with the builder) and node-semver marks which
satisfy it. The ranges are real npm declarations, one or two per syntactic
shape, with anything containing `:` or `/` (URLs, paths, aliases) left out.

Regenerate with tests/manual/build_npm_range_golden.py, which needs node and
the `semver` package; it prints how to run it.
"""
import json
import pathlib
import sys

import pytest

from kospex.npm_range import min_satisfying, parse_range, satisfies

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from npm_range_pool import candidate_pool  # noqa: E402

GOLDEN = pathlib.Path(__file__).parent / "data" / "npm_range_golden.json"


def _golden():
    return json.loads(GOLDEN.read_text())


def test_golden_is_present_and_substantial():
    """Guards the parametrised test below from passing over an empty corpus."""
    cases = _golden()
    assert len(cases) > 250
    assert sum(1 for c in cases if c["satisfying"] is None) > 5      # invalid ranges
    assert sum(1 for c in cases if "||" in c["range"]) > 5


@pytest.mark.parametrize("case", _golden(), ids=lambda c: c["range"])
def test_matches_node_semver(case):
    pool = candidate_pool(case["range"])
    if case["satisfying"] is None:
        # node-semver rejects it; so must we, rather than guess a meaning.
        assert parse_range(case["range"]) is None
        return
    parsed = parse_range(case["range"])
    assert parsed is not None, "node-semver accepts this range"
    ours = "".join("1" if satisfies(v, parsed) else "0" for v in pool)
    assert ours == case["satisfying"]


class TestMinSatisfying:
    """The lowest published stable version, which is what #379 records."""

    def test_unpublished_zero_floor_moves_to_the_first_release(self):
        assert min_satisfying(["0.9.0", "1.12.0", "1.13.1", "2.0.0"], "^1") == "1.12.0"

    def test_prereleases_are_never_chosen(self):
        assert min_satisfying(["1.0.0-alpha.1", "1.0.0-beta.2", "1.0.1"], "^1") == "1.0.1"

    def test_a_ceiling_only_range_starts_at_the_first_release(self):
        assert min_satisfying(["2.11.0", "3.0.0", "4.0.0"], "<4") == "2.11.0"

    def test_alternatives_take_the_lowest_of_either(self):
        assert min_satisfying(["17.0.2", "18.0.0", "19.0.0"], "18 || 19") == "18.0.0"

    def test_numbers_beyond_2_53_make_the_range_invalid(self):
        """node-semver rejects `react: "1000000000000000000"`, a sentinel used in
        peer-dependency setups; so does npm, which reads it as a dist-tag. The
        corpus has no such number, so this guards the limit on its own."""
        assert parse_range("1000000000000000000") is None
        assert parse_range("9007199254740991") is None    # upper bound overflows
        assert parse_range("9007199254740990") is not None

    def test_an_oversized_range_is_rejected_before_any_regex_runs(self):
        """Manifests are untrusted input. A long whitespace run made the
        operator / hyphen regexes quadratic (8,000 spaces: 110 ms, doubling to
        4x per doubling), so ~100k spaces in a version string cost seconds per
        row. The longest real npm range on a development estate is 104 chars;
        anything over the cap is not a range anyone wrote."""
        assert parse_range("1" + " " * 100_000 + "2") is None

    def test_whitespace_runs_are_collapsed(self):
        assert min_satisfying(["1.5.0", "2.1.0"], "^1   ||    ^2") == "1.5.0"
        assert parse_range(">=   1.2") is not None

    def test_nothing_satisfies(self):
        assert min_satisfying(["18.2.0", "19.0.0"], "1000000000000000000") is None

    def test_an_unparseable_range_is_none(self):
        assert min_satisfying(["1.0.0"], "workspace:^1") is None

    def test_order_of_the_input_does_not_matter(self):
        assert min_satisfying(["1.10.0", "1.9.0", "1.2.0"], "^1.2") == "1.2.0"
