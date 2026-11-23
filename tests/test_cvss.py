"""CVSS v3.x base scoring (kospex.scoring.cvss).

Three layers:
* worked examples whose scores are published (NVD / FIRST) -- these are written
  out by hand, not generated;
* strictness: a malformed or unsupported vector gives None, never a guess;
* agreement with an independent implementation over every v3 base vector,
  from tests/data/cvss_v3_golden.json (regenerate with tests/manual/cvss_oracle.py).
  The golden file is data: this test never imports the library that made it.
"""
import json
from pathlib import Path

import pytest

from kospex.scoring.cvss import CvssScore, parse, rating, score

GOLDEN = Path(__file__).parent / "data" / "cvss_v3_golden.json"


class TestWorkedExamples:
    @pytest.mark.parametrize("vector,expected,label", [
        # Maximum, scope unchanged: unauthenticated network RCE.
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8, "CRITICAL"),
        # Scope changed, full impact: the ceiling.
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0, "CRITICAL"),
        # Heartbleed-shaped: confidentiality only.
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N", 7.5, "HIGH"),
        # Reflected XSS: user interaction, scope changed.
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1, "MEDIUM"),
        ("CVSS:3.1/AV:L/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H", 7.8, "HIGH"),
        ("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H", 8.8, "HIGH"),
        ("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8, "CRITICAL"),
    ])
    def test_published_scores(self, vector, expected, label):
        result = score(vector)
        assert result is not None
        assert result.base_score == expected
        assert result.rating == label

    def test_no_impact_is_zero_and_none(self):
        result = score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N")
        assert result == CvssScore("3.1", 0.0, "NONE")

    def test_result_names_its_version(self):
        assert score("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H").version == "3.0"
        assert score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H").version == "3.1"


class TestRating:
    @pytest.mark.parametrize("value,expected", [
        (0.0, "NONE"), (0.1, "LOW"), (3.9, "LOW"), (4.0, "MEDIUM"), (6.9, "MEDIUM"),
        (7.0, "HIGH"), (8.9, "HIGH"), (9.0, "CRITICAL"), (10.0, "CRITICAL"),
    ])
    def test_bands(self, value, expected):
        assert rating(value) == expected


class TestTemporalAndEnvironmentalMetrics:
    def test_are_accepted_and_do_not_change_the_base_score(self):
        base = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
        extended = base + "/E:P/RL:O/RC:U/CR:L/MAV:P"
        assert score(extended) == score(base)

    def test_an_invalid_value_for_one_is_still_refused(self):
        assert score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:Z") is None


class TestStrictness:
    BASE = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"

    @pytest.mark.parametrize("vector", [
        "",
        "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",                     # no prefix
        "CVSS:3.1",                                                 # nothing else
        "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H",                 # missing A
        "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/A:H",         # repeated
        "CVSS:3.1/AV:X/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",             # bad value
        "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/ZZ:Q",        # unknown metric
        "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/",            # trailing slash
        "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H ",            # whitespace
        "cvss:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",             # case matters
        "CVSS:3.1/av:n/ac:l/pr:n/ui:n/s:u/c:h/i:h/a:h",
        "CVSS:3.2/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",             # unknown minor
        "CVSS:2.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "AV:N/AC:L/Au:N/C:P/I:P/A:P",                               # CVSS 2
        "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",  # CVSS 4
        "CVSS:3.1//AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",            # empty segment
        "CVSS:3.1/AV/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",               # no value
    ])
    def test_refused(self, vector):
        assert parse(vector) is None
        assert score(vector) is None

    @pytest.mark.parametrize("not_a_string", [None, 9.8, 0, [], b"CVSS:3.1/AV:N"])
    def test_not_a_string_is_refused_not_raised(self, not_a_string):
        assert score(not_a_string) is None

    def test_a_valid_vector_parses(self):
        assert parse(self.BASE) is not None


class TestAgreesWithAnIndependentImplementation:
    @staticmethod
    def golden():
        with open(GOLDEN) as handle:
            return json.load(handle)

    def test_golden_file_is_present_and_is_not_trivially_small(self):
        data = self.golden()
        # 2,592 base vectors x 2 versions, so a truncated file fails here
        # rather than passing a loop that has nothing to loop over.
        assert len(data["scored"]) >= 5184
        assert data["generator"]["package"] == "cvss"

    def test_every_scored_vector_matches(self):
        mismatches = []
        for vector, (expected, expected_rating) in self.golden()["scored"].items():
            got = score(vector)
            if got is None or (got.base_score, got.rating) != (expected, expected_rating):
                mismatches.append((vector, (expected, expected_rating), got))
        assert not mismatches, f"{len(mismatches)} differ, first: {mismatches[:3]}"

    def test_every_vector_the_other_implementation_refuses_is_refused_here(self):
        refused = self.golden()["refused"]
        assert refused, "no refused vectors recorded"
        accepted = [vector for vector in refused if score(vector) is not None]
        assert not accepted, f"scored vectors the oracle refused: {accepted[:3]}"
