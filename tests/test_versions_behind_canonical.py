"""versions_behind must find the version in use by deps.dev's canonical spelling (#378).

`get_versions_behind()` walks a package's releases from the default down to
the version in use. It used to locate that version with `version_fuzzy_match`,
which missed common spellings -- `3.0` vs `3.0.0` (a `0` segment was stripped to
empty), a single segment `6`, PEP 440's `2026.3.post1` vs `2026.3.0.post1`. A
miss ran the walk to the end of the list, so the count became the package's
whole release history: `sphinx>=9` was stored as 347 behind.

deps.dev's exact-version lookup already applies each ecosystem's own equality
rules and returns the canonical spelling in `versionKey.version` (PyPI `9` ->
`9.0.0`; npm, Maven and Go have no such rules and stay exact). depsdev_record()
holds that response, so the walk matches it exactly instead of guessing.

Fixtures are real deps.dev package records (tests/data/depsdev/), trimmed to the
fields kospex reads. Expected counts were computed outside kospex with a PEP 440
equality walk over the same records.
"""
import json
import pathlib

import pytest

from kospex_dependencies import KospexDependencies

DATA = pathlib.Path(__file__).parent / "data" / "depsdev"
BASE = "https://api.deps.dev/v3alpha/systems"


def _package_url(system, name):
    from urllib.parse import quote
    return f"{BASE}/{system}/packages/{quote(name, safe='')}"


class _Query:
    """Serves canned (content, status) per URL, like a url_cache hit or miss."""

    def __init__(self, responses):
        self.responses = responses

    def url_request_with_status(self, url, **_kwargs):
        return self.responses.get(url, (None, 404))

    def url_request(self, url, **_kwargs):
        return self.url_request_with_status(url)[0]


def _fixture(system, name):
    fname = f"{system}__{name}".replace("@", "_").replace("/", "_") + ".json"
    return (DATA / fname).read_text()


def _version_record(system, name, canonical):
    """What deps.dev's exact-version endpoint returns, reduced to what is read."""
    return json.dumps({"versionKey": {"system": system.upper(), "name": name,
                                      "version": canonical},
                       "publishedAt": "2026-01-01T00:00:00Z", "isDefault": False})


def _kdeps(system, name, asked, canonical):
    responses = {
        _package_url(system, name): (_fixture(system, name), 200),
        f"{_package_url(system, name)}/versions/{asked}":
            (_version_record(system, name, canonical), 200),
    }
    return KospexDependencies(kospex_query=_Query(responses))


@pytest.mark.parametrize("asked, canonical, expected", [
    # A range floor: `sphinx>=9` is looked up as `9`, which deps.dev resolves.
    ("9", "9.0.0", 7),
    ("9.0", "9.0.0", 7),
    # PEP 440 spelling variant: the pin and deps.dev's list differ as strings.
    ("2026.3.post1", "2026.3.0.post1", 2),
])
def test_short_or_variant_spelling_is_found(asked, canonical, expected):
    system = "pypi"
    name = "pytz" if "post" in asked else "sphinx"
    kd = _kdeps(system, name, asked, canonical)

    rec = kd.depsdev_record(system, name, asked)

    assert rec["resolution"] == "resolved"
    assert rec["versions_behind"] == expected


def test_a_version_already_spelled_canonically_is_unchanged():
    """The common case must not move: a full pin counts exactly as before."""
    kd = _kdeps("pypi", "sphinx", "8.1.3", "8.1.3")

    assert kd.depsdev_record("pypi", "sphinx", "8.1.3")["versions_behind"] == 19


def test_version_missing_from_the_list_is_unknown_not_the_whole_history():
    """If the version still isn't listed, say so: None, not a count to the end.

    Counting to the end asserted the package's entire history as 'behind' --
    the largest possible value, for a row whose real position is unknown.
    """
    kd = _kdeps("pypi", "sphinx", "9.9.9", "9.9.9")

    rec = kd.depsdev_record("pypi", "sphinx", "9.9.9")

    assert rec["resolution"] == "resolved"
    assert rec["versions_behind"] is None
