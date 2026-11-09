"""versions_behind counts releases, not pre-releases (#276).

deps.dev lists every published version and has no pre-release flag, so
canaries, nightlies, release candidates and Go pseudo-versions were counted as
releases behind. Two more distortions follow from the default:

* deps.dev can mark a pre-release as the default (duckdb's nightly
  `2.1.0.dev...`), which moved the start of the count.
* It can mark one older than the version in use: netty-handler's default is
  `5.0.0.Alpha2` from 2015, so a project on 4.2.18 was never reached after
  the default and read 0 behind.

Now the walk counts stable releases only, starting at the default or, when the
default is a pre-release, at the newest stable release. A project on a
pre-release reads None (unknown): neither 0 nor a count describes it.

release_list() is the version list both this and #390's first-release date
read, so they agree on what a release is.

Fixtures are real deps.dev records (tests/data/depsdev/). Expected counts were
computed outside kospex, with a separately written classifier and walk.
"""
import json
import pathlib
from urllib.parse import quote

import pytest

from kospex_dependencies import KospexDependencies

DATA = pathlib.Path(__file__).parent / "data" / "depsdev"
BASE = "https://api.deps.dev/v3alpha/systems"


class _Query:
    def __init__(self, responses):
        self.responses = responses

    def url_request_with_status(self, url, **_kwargs):
        return self.responses.get(url, (None, 404))

    def url_request(self, url, **_kwargs):
        return self.url_request_with_status(url)[0]


def _kd(system, name, listing):
    url = f"{BASE}/{system}/packages/{quote(name, safe='')}"
    return KospexDependencies(kospex_query=_Query({url: (listing, 200)}))


def _fixture_kd(system, name, fixture):
    return _kd(system, name, (DATA / fixture).read_text())


def _listing(*versions):
    """A deps.dev package record from (version, publishedAt, isDefault) tuples."""
    return json.dumps({"versions": [
        {"versionKey": {"version": v}, "publishedAt": d, "isDefault": default}
        for v, d, default in versions]})


CASES = [
    # system, name, fixture, version in use, expected (was)
    ("pypi", "duckdb", "pypi__duckdb.json", "1.4.3", 10),                     # 101
    ("maven", "io.netty:netty-handler", "maven__io.netty_netty-handler.json",
     "4.2.18.Final", 5),                                                         # 0
    ("go", "github.com/gin-gonic/gin", "go__github.com_gin-gonic_gin.json",
     "v1.10.0", 3),                                                              # 42
    ("npm", "react", "npm__react.json", "16.12.0", 43),                         # 49
    ("pypi", "sphinx", "pypi__sphinx.json", "6.0.0", 53),                       # 63
]


@pytest.mark.parametrize("system, name, fixture, used, expected", CASES,
                         ids=[c[1] for c in CASES])
def test_only_releases_are_counted(system, name, fixture, used, expected):
    kd = _fixture_kd(system, name, fixture)

    assert kd.get_versions_behind(system, name, used)["versions_behind"] == expected


def test_a_package_without_prereleases_is_unchanged():
    kd = _fixture_kd("npm", "@types/react", "npm___types_react.json")

    assert kd.get_versions_behind("npm", "@types/react", "18.0.0")["versions_behind"] == 329


def test_a_prerelease_default_older_than_the_version_in_use():
    """netty-handler: the default is a 2015 alpha. Counting from it reached
    nothing and read 0. Counting from the newest stable release (4.1.140)
    gives 5 -- of which three are 4.1.x backports, not newer than 4.2.18;
    that is #377 (maintenance lines), not a pre-release question."""
    kd = _fixture_kd("maven", "io.netty:netty-handler", "maven__io.netty_netty-handler.json")

    details = kd.get_versions_behind("maven", "io.netty:netty-handler", "4.2.18.Final")

    assert details["versions_behind"] == 5


@pytest.mark.parametrize("system, name, fixture, used", [
    ("pypi", "duckdb", "pypi__duckdb.json", "1.4.5.dev27"),       # a nightly
    ("npm", "react", "npm__react.json", "0.0.0-00d4f95c2"),       # a commit build
])
def test_a_project_on_a_prerelease_is_unknown(system, name, fixture, used):
    """Neither 0 ("current") nor a count of releases describes being on a
    nightly or canary. None is how versions_behind says unknown."""
    kd = _fixture_kd(system, name, fixture)
    listed = {r["version"]: r for r in kd.release_list(system, name)}
    assert listed[used]["is_prerelease"] is True                  # the premise

    assert kd.get_versions_behind(system, name, used)["versions_behind"] is None


class TestReleaseList:
    """The shared version list (#276, #390)."""

    def test_none_when_deps_dev_has_no_record(self):
        kd = KospexDependencies(kospex_query=_Query({}))
        assert kd.release_list("npm", "left-pad") is None

    def test_every_version_with_its_flags(self):
        kd = _fixture_kd("pypi", "duckdb", "pypi__duckdb.json")
        releases = kd.release_list("pypi", "duckdb")
        listed = json.loads((DATA / "pypi__duckdb.json").read_text())["versions"]

        assert len(releases) == len(listed)                  # pre-releases included
        assert set(releases[0]) == {"version", "published_at", "is_default", "is_prerelease"}
        by_version = {r["version"]: r for r in releases}
        assert by_version["1.4.3"]["is_prerelease"] is False
        assert by_version["2.1.0.dev2610310630"]["is_prerelease"] is True
        assert by_version["2.1.0.dev2610310630"]["is_default"] is True

    def test_newest_first(self):
        kd = _kd("npm", "x", _listing(("1.0.0", "2020-01-01T00:00:00Z", False),
                                      ("2.0.0", "2022-01-01T00:00:00Z", True),
                                      ("1.5.0", "2021-01-01T00:00:00Z", False)))
        assert [r["version"] for r in kd.release_list("npm", "x")] == ["2.0.0", "1.5.0", "1.0.0"]

    def test_a_shared_timestamp_is_ordered_by_version(self):
        """Two releases published together (netty 4.2.20 and 4.1.140 on one
        day) must come out in the same order every time. Version order, not
        string order: 1.0.10 is newer than 1.0.9."""
        same = "2026-11-03T00:00:00Z"
        for system in ("npm", "pypi"):
            kd = _kd(system, "x", _listing(("1.0.9", same, False), ("1.0.10", same, True),
                                           ("1.0.10-rc.1" if system == "npm" else "1.0.10rc1",
                                            same, False)))
            order = [r["version"] for r in kd.release_list(system, "x")]
            assert order[0] == "1.0.10", system
            assert order[-1] == "1.0.9", system

    def test_undated_versions_come_last_and_are_not_the_first_release(self):
        """A version with no publishedAt is kept, with "" as its date, and sorted
        last -- the oldest position of a newest-first list. So list[-1] is not
        the first release: a caller wanting that must skip undated entries."""
        kd = _kd("pypi", "requests", _listing(
            ("2.23.0-py2.7", "", False),
            ("2.31.0", "2023-05-22T00:00:00Z", True),
            ("0.2.0", "2011-02-14T08:49:42Z", False)))
        releases = kd.release_list("pypi", "requests")

        assert releases[-1]["version"] == "2.23.0-py2.7"
        assert releases[-1]["published_at"] == ""
        dated = [r for r in releases if r["published_at"]]
        assert dated[-1]["version"] == "0.2.0"              # the first release
