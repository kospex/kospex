"""How far apart are two versions, by major / minor / patch?

Two answers:

* Offline (kospex.dependencies.version_diff): the raw per-component difference of the
  version numbers, and the level of the change (major / minor / patch). No
  lookup, so it says nothing about how many releases sit in between.
* Online (KospexDependencies.releases_between): the releases deps.dev lists
  in (from, to], each classified by the level it moved from the release
  before it, so 1.2.3 -> 2.0.1 reads as "1 major, 1 minor, 3 patch releases".
"""
import json
import time
from urllib.parse import quote

import pytest

from kospex.dependencies.version_diff import change_level, parse_release, version_diff
from kospex_dependencies import KospexDependencies

BASE = "https://api.deps.dev/v3alpha/systems"


class TestParseRelease:

    @pytest.mark.parametrize("system, version, expected", [
        ("npm", "1.2.3", (1, 2, 3)),
        ("npm", "v1.2.3", (1, 2, 3)),
        ("npm", "1.2.3-rc.1+build.5", (1, 2, 3)),
        ("go", "v0.0.0-20240829181941-546108da6839", (0, 0, 0)),
        ("pypi", "2026.3.post1", (2026, 3, 0)),
        ("pypi", "1!2.0", (2, 0, 0)),
        ("pypi", "1.0rc1", (1, 0, 0)),
        ("maven", "31.1-jre", (31, 1, 0)),
        ("maven", "2.10.0.pr1", (2, 10, 0)),
        ("maven", "5.4.2.Final", (5, 4, 2)),
        ("nuget", "1.2.3.4", (1, 2, 3, 4)),
        (None, "6", (6, 0, 0)),
        (None, " 1.2 ", (1, 2, 0)),
    ])
    def test_parses_the_release_numbers(self, system, version, expected):
        assert parse_release(version, system) == expected

    @pytest.mark.parametrize("version", ["", None, "latest", "^1.2.3", "~=1.0", ">=2", "abc1.2"])
    def test_none_when_there_is_no_leading_number(self, version):
        assert parse_release(version) is None

    def test_unparseable_pypi_falls_back_to_the_leading_numbers(self):
        # A legacy upload packaging refuses, but its number is still readable.
        assert parse_release("0.10.0.win32-py2.7", "pypi") == (0, 10, 0)

    def test_a_long_digit_run_is_linear(self):
        start = time.perf_counter()
        assert parse_release("1." * 5000 + "x") is not None
        assert time.perf_counter() - start < 0.5


class TestVersionDiff:

    @pytest.mark.parametrize("a, b, expected", [
        ("1.2.3", "1.2.5", {"major": 0, "minor": 0, "patch": 2}),
        ("1.2.3", "1.4.0", {"major": 0, "minor": 2, "patch": -3}),
        ("1.9.9", "3.0.0", {"major": 2, "minor": -9, "patch": -9}),
        ("2.0.0", "1.5.0", {"major": -1, "minor": 5, "patch": 0}),
        ("1.2.3", "1.2.3", {"major": 0, "minor": 0, "patch": 0}),
        ("1.2", "1.2.0", {"major": 0, "minor": 0, "patch": 0}),
    ])
    def test_raw_component_difference(self, a, b, expected):
        assert version_diff(a, b) == expected

    def test_none_when_either_side_is_unparseable(self):
        assert version_diff("1.2.3", "latest") is None
        assert version_diff("latest", "1.2.3") is None


class TestChangeLevel:

    @pytest.mark.parametrize("a, b, level", [
        ("1.2.3", "1.2.4", "patch"),
        ("1.2.3", "1.3.0", "minor"),
        ("1.9.9", "2.0.0", "major"),
        ("1.2.3", "1.2.3", "none"),
        ("1.2", "1.2.0", "none"),
        ("1.2.3-rc.1", "1.2.3", "none"),     # pre-release tags are not compared
        ("2.0.0", "1.9.0", "downgrade"),
        ("1.2.3", "latest", None),
    ])
    def test_level(self, a, b, level):
        assert change_level(a, b) == level

    def test_level_is_not_the_sign_of_the_raw_diff(self):
        # minor is -9 here, but this is a major upgrade.
        assert version_diff("1.9.0", "2.0.0")["minor"] == -9
        assert change_level("1.9.0", "2.0.0") == "major"


class _Query:
    def __init__(self, responses):
        self.responses = responses

    def url_request_with_status(self, url, **_kwargs):
        return self.responses.get(url, (None, 404))

    def url_request(self, url, **_kwargs):
        return self.url_request_with_status(url)[0]


def _kd(system, name, *versions):
    """versions: (version, publishedAt, isDefault) tuples."""
    listing = json.dumps({"versions": [
        {"versionKey": {"version": v}, "publishedAt": d, "isDefault": default}
        for v, d, default in versions]})
    url = f"{BASE}/{system}/packages/{quote(name, safe='')}"
    return KospexDependencies(kospex_query=_Query({url: (listing, 200)}))


# Published out of version order on purpose: 1.3.1 is a backport after 2.0.0.
NPM = [
    ("1.2.3", "2024-01-01T00:00:00Z", False),
    ("1.2.4", "2024-02-01T00:00:00Z", False),
    ("1.3.0", "2024-03-01T00:00:00Z", False),
    ("2.0.0-rc.1", "2024-03-15T00:00:00Z", False),
    ("2.0.0", "2024-04-01T00:00:00Z", False),
    ("1.3.1", "2024-04-15T00:00:00Z", False),
    ("2.0.1", "2024-05-01T00:00:00Z", True),
    ("3.0.0-canary.1", "2024-06-01T00:00:00Z", False),
]


# On 4.1.1; 3.9.15 is a fix to the older line, published after 4.1.5; no default.
BACKPORT_NO_DEFAULT = [
    ("3.9.14", "2024-01-01T00:00:00Z", False),
    ("4.1.1", "2024-02-01T00:00:00Z", False),
    ("4.1.2", "2024-03-01T00:00:00Z", False),
    ("4.1.3", "2024-04-01T00:00:00Z", False),
    ("4.1.4", "2024-05-01T00:00:00Z", False),
    ("4.1.5", "2024-06-01T00:00:00Z", False),
    ("3.9.15", "2024-07-01T00:00:00Z", False),
]


class TestVersionsBehindFallback:
    """get_versions_behind() anchors on the same fallback."""

    def test_no_default_anchors_on_the_highest_version(self):
        kd = _kd("npm", "pkg", *BACKPORT_NO_DEFAULT)
        details = kd.get_versions_behind("npm", "pkg", "4.1.1")
        assert details["versions_behind"] == 4
        assert details["versions_before_default"] == 1   # 3.9.15


class TestReleasesBetween:

    def test_counts_releases_by_level(self):
        kd = _kd("npm", "pkg", *NPM)
        result = kd.releases_between("npm", "pkg", "1.2.3", "2.0.1")
        assert result["versions"] == ["1.2.4", "1.3.0", "1.3.1", "2.0.0", "2.0.1"]
        assert result["releases"] == 5
        # 1.2.4 patches the line in use, which 1.3 superseded: not on the path.
        assert result["path"] == ["1.3.0", "1.3.1", "2.0.0", "2.0.1"]
        assert (result["major"], result["minor"], result["patch"], result["other"]) == (1, 1, 2, 0)
        assert result["level"] == "major"
        assert result["diff"] == {"major": 1, "minor": -2, "patch": -2}
        assert (result["on_major"], result["latest_on_major"]) == (3, "1.3.1")

    def test_fixes_to_superseded_minor_lines_are_not_on_the_path(self):
        # react's shape: after 19.2.0, fixes went out to 19.0.x and 19.1.x too.
        kd = _kd("npm", "pkg",
                 ("19.2.1", "2025-12-03T00:00:00Z", True),
                 ("19.1.2", "2025-12-03T00:00:00Z", False),
                 ("19.0.1", "2025-12-03T00:00:00Z", False),
                 ("19.2.0", "2025-10-01T00:00:00Z", False),
                 ("19.1.1", "2025-07-28T00:00:00Z", False),
                 ("19.1.0", "2025-03-28T00:00:00Z", False),
                 ("19.0.0", "2024-12-05T00:00:00Z", False),
                 ("18.3.1", "2024-04-26T00:00:00Z", False),
                 ("18.3.0", "2024-04-25T00:00:00Z", False))
        result = kd.releases_between("npm", "pkg", "18.3.0")
        assert result["releases"] == 8
        assert result["path"] == ["18.3.1", "19.0.0", "19.1.0", "19.2.0", "19.2.1"]
        assert (result["major"], result["minor"], result["patch"]) == (1, 2, 2)
        assert (result["on_major"], result["latest_on_major"]) == (1, "18.3.1")

    def test_a_line_that_starts_without_a_dot_zero_is_still_a_step(self):
        kd = _kd("npm", "pkg",
                 ("2.1.3", "2024-03-01T00:00:00Z", True),
                 ("2.1.2", "2024-02-01T00:00:00Z", False),
                 ("2.0.0", "2024-01-01T00:00:00Z", False))
        result = kd.releases_between("npm", "pkg", "2.0.0")
        assert result["path"] == ["2.1.2", "2.1.3"]
        assert (result["minor"], result["patch"]) == (1, 1)

    def test_a_fourth_component_step_counts_as_other(self):
        kd = _kd("nuget", "pkg",
                 ("1.2.3.1", "2024-02-01T00:00:00Z", True),
                 ("1.2.3", "2024-01-01T00:00:00Z", False))
        result = kd.releases_between("nuget", "pkg", "1.2.3")
        assert result["path"] == ["1.2.3.1"]
        assert (result["major"], result["minor"], result["patch"], result["other"]) == (0, 0, 0, 1)

    def test_to_defaults_to_the_deps_dev_default(self):
        kd = _kd("npm", "pkg", *NPM)
        result = kd.releases_between("npm", "pkg", "1.3.0")
        assert result["to"] == "2.0.1"
        assert result["versions"] == ["1.3.1", "2.0.0", "2.0.1"]

    def test_default_that_is_a_prerelease_falls_back_to_the_newest_release(self):
        rows = [(v, d, v == "3.0.0-canary.1") for v, d, _ in NPM]
        kd = _kd("npm", "pkg", *rows)
        assert kd.releases_between("npm", "pkg", "2.0.0")["to"] == "2.0.1"

    def test_no_default_falls_back_to_the_highest_version_not_the_newest_published(self):
        # 3.9.15 is a backport published after 4.1.5. With no default, the end
        # point is still 4.1.5 -- not a "downgrade" to 3.9.15.
        kd = _kd("npm", "pkg", *BACKPORT_NO_DEFAULT)
        result = kd.releases_between("npm", "pkg", "4.1.1")
        assert result["to"] == "4.1.5"
        assert result["versions"] == ["4.1.2", "4.1.3", "4.1.4", "4.1.5"]
        assert (result["major"], result["minor"], result["patch"]) == (0, 0, 4)

    def test_prerelease_default_falls_back_to_the_highest_version(self):
        rows = [(v, d, False) for v, d, _ in BACKPORT_NO_DEFAULT]
        rows.append(("5.0.0-rc.1", "2024-08-01T00:00:00Z", True))
        kd = _kd("npm", "pkg", *rows)
        assert kd.releases_between("npm", "pkg", "4.1.1")["to"] == "4.1.5"

    def test_same_version_is_zero(self):
        kd = _kd("npm", "pkg", *NPM)
        result = kd.releases_between("npm", "pkg", "2.0.1", "2.0.1")
        assert (result["major"], result["minor"], result["patch"], result["releases"]) == (0, 0, 0, 0)
        assert result["level"] == "none"

    def test_downgrade_is_reported_not_counted(self):
        kd = _kd("npm", "pkg", *NPM)
        result = kd.releases_between("npm", "pkg", "2.0.1", "1.2.3")
        assert result["level"] == "downgrade"
        assert result["releases"] == 0

    def test_maven_flavour_counts_only_its_own_flavour(self):
        kd = _kd("maven", "com.google.guava:guava",
                 ("31.0-jre", "2024-01-01T00:00:00Z", False),
                 ("31.0-android", "2024-01-01T00:00:00Z", False),
                 ("31.1-jre", "2024-02-01T00:00:00Z", False),
                 ("31.1-android", "2024-02-01T00:00:00Z", False),
                 ("32.0.0-jre", "2024-03-01T00:00:00Z", True),
                 ("32.0.0-android", "2024-03-01T00:00:00Z", False))
        result = kd.releases_between("maven", "com.google.guava:guava", "31.0-jre")
        assert result["to"] == "32.0.0-jre"
        assert result["versions"] == ["31.1-jre", "32.0.0-jre"]
        assert (result["major"], result["minor"], result["patch"]) == (1, 1, 0)

    def test_none_when_deps_dev_has_no_record(self):
        kd = KospexDependencies(kospex_query=_Query({}))
        assert kd.releases_between("npm", "nope", "1.0.0", "2.0.0") is None

    def test_none_when_a_version_is_unparseable(self):
        kd = _kd("npm", "pkg", *NPM)
        assert kd.releases_between("npm", "pkg", "latest", "2.0.1") is None
