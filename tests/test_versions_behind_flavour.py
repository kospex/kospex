"""versions_behind counts only releases of the flavour in use (#415).

Guava publishes every release twice, `33.8.0-jre` and `33.8.0-android`. Both
are releases, so both were counted, and a project read about twice as far
behind as it is: 32.1.3-jre was 42 behind, of which 21 are -jre releases.
Someone on -jre is not behind an -android release, and the reverse.

Expected counts come from Guava's real deps.dev record, computed outside
kospex by publish date: releases of the same flavour published after the
version in use, up to and including deps.dev's default (33.8.0-jre, whose
-android twin was published at the same moment).
"""
import json
import pathlib
from urllib.parse import quote

import pytest

from kospex_dependencies import KospexDependencies

DATA = pathlib.Path(__file__).parent / "data" / "depsdev"
BASE = "https://api.deps.dev/v3alpha/systems"
GUAVA = "com.google.guava:guava"


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


def _guava():
    return _kd("maven", GUAVA, (DATA / "maven__com.google.guava_guava.json").read_text())


@pytest.mark.parametrize("used, expected", [
    ("32.1.3-jre", 21),          # was 42
    ("33.0.0-android", 20),      # was 41
    ("31.1-jre", 27),            # was 54
    ("33.7.2-android", 1),       # 33.8.0-android only
    ("33.8.0-android", 0),       # the default's twin is current
    ("33.8.0-jre", 0),           # the default
])
def test_only_releases_of_the_same_flavour_are_counted(used, expected):
    assert _guava().get_versions_behind("maven", GUAVA, used)["versions_behind"] == expected


def test_twin_published_before_the_default_is_not_behind():
    """If the -android twin sorts ahead of the -jre default, a project on
    that twin is still current, and one on the previous -android release is
    one behind, not zero."""
    listing = json.dumps({"versions": [
        {"versionKey": {"version": "2.0-android"}, "publishedAt": "2026-02-01T00:00:01Z"},
        {"versionKey": {"version": "2.0-jre"}, "publishedAt": "2026-02-01T00:00:00Z",
         "isDefault": True},
        {"versionKey": {"version": "1.0-android"}, "publishedAt": "2026-01-01T00:00:01Z"},
        {"versionKey": {"version": "1.0-jre"}, "publishedAt": "2026-01-01T00:00:00Z"},
    ]})
    kd = _kd("maven", "acme:lib", listing)

    assert kd.get_versions_behind("maven", "acme:lib", "2.0-android")["versions_behind"] == 0
    assert kd.get_versions_behind("maven", "acme:lib", "1.0-android")["versions_behind"] == 1
    assert kd.get_versions_behind("maven", "acme:lib", "1.0-jre")["versions_behind"] == 1


def test_a_version_without_a_flavour_is_unchanged():
    """`22.0` predates the split; with no flavour to match, every release counts."""
    kd = _guava()
    releases = [r for r in kd.release_list("maven", GUAVA) if not r["is_prerelease"]]
    position = [r["version"] for r in releases].index("22.0")

    assert kd.get_versions_behind("maven", GUAVA, "22.0")["versions_behind"] == position


def test_other_ecosystems_are_untouched():
    """A `-jre` suffix means nothing outside Maven: npm `1.0.0-jre` is a
    pre-release, and an unknown position stays unknown."""
    listing = json.dumps({"versions": [
        {"versionKey": {"version": "2.0.0"}, "publishedAt": "2026-02-01T00:00:00Z",
         "isDefault": True},
        {"versionKey": {"version": "1.0.0"}, "publishedAt": "2026-01-01T00:00:00Z"},
    ]})
    kd = _kd("npm", "acme", listing)

    assert kd.get_versions_behind("npm", "acme", "1.0.0")["versions_behind"] == 1
