"""An npm range is looked up at the lowest published stable version it permits (#379).

`clean_version_spec("^19")` gives `19`, which deps.dev 404s for npm (semver
needs x.y.z), and a 404 for a package that exists was recorded as
`version_yanked`: 1,227 of 1,417 such npm rows in a reference estate, none of
them yanked. Zero-filling the floor fixes about 70%: `^1`'s `1.0.0` is often
never published, and `<4` has no floor at all.

So for an npm range, the version sent to deps.dev -- and recorded in
`resolved_version` -- is the lowest published stable version the range permits,
taken from the package's deps.dev version list. If none does, the row is
`no_satisfying_version`: a range cannot be yanked.

Both scan paths are driven (`kospex sca` and `krunner osi`) with real deps.dev
package records from tests/data/depsdev/. Expected versions are node-semver's
`minSatisfying` over the same records.
"""
import json
import pathlib
from urllib.parse import quote

import pytest

import krunner
from kospex.extractors.registry import REGISTRY
from kospex_dependencies import KospexDependencies

DATA = pathlib.Path(__file__).parent / "data" / "depsdev"
BASE = "https://api.deps.dev/v3alpha/systems"


def _fixture(system, name):
    return (DATA / (f"{system}__{name}".replace("@", "_").replace("/", "_") + ".json")).read_text()


class _Query:
    """deps.dev from fixtures: a package list, and an exact lookup that answers
    only for versions in that list -- as the real endpoint does."""

    def __init__(self, system, name):
        self.listing = _fixture(system, name)
        self.package_url = f"{BASE}/{system}/packages/{quote(name, safe='')}"
        self.versions = {v["versionKey"]["version"]
                         for v in json.loads(self.listing)["versions"]}
        self.system, self.name = system, name
        self.asked = []

    def url_request_with_status(self, url, **_kwargs):
        if url == self.package_url:
            return self.listing, 200
        prefix = self.package_url + "/versions/"
        if url.startswith(prefix):
            version = url[len(prefix):]
            self.asked.append(version)
            if version in self.versions:
                return json.dumps({"versionKey": {"system": self.system.upper(),
                                                  "name": self.name, "version": version},
                                   "publishedAt": "2026-01-01T00:00:00Z"}), 200
        return None, 404

    def url_request(self, url, **_kwargs):
        return self.url_request_with_status(url)[0]


def _row(system, name, declared):
    return {"package_name": name, "package_version": declared, "package_type": system,
            "requirements_type": "direct", "file_path": "package.json",
            "_repo_id": "github.com~acme~app"}


def _osi(system, name, declared):
    query = _Query(system, name)
    kdeps = KospexDependencies(kospex_query=query)
    row = krunner.enrich_dependency_records([_row(system, name, declared)], kdeps)[0]
    return row, query


def _sca(system, name, declared):
    query = _Query(system, name)
    kdeps = KospexDependencies(kospex_query=query)
    extractor = next(e for e in REGISTRY if e.package_type == system)
    row = kdeps._enrich_dependency_records([_row(system, name, declared)], extractor)[0]
    return row, query


PATHS = pytest.mark.parametrize("scan", [_osi, _sca], ids=["osi", "sca"])


@PATHS
@pytest.mark.parametrize("name, declared, expected", [
    # Partial floor; `1.0.0` was never published (first 1.x is 1.12.0).
    ("@playwright/test", "^1", "1.12.0"),
    # Partial floor that is published once zero-filled.
    ("@types/leaflet", "^1.9", "1.9.0"),
    # Ceiling only: clean_version_spec reads `4` -- the ceiling -- as the floor.
    ("@types/html-webpack-plugin", "<4", "2.11.0"),
    # Alternatives: the lowest of either.
    ("@types/react", "18 || 19", "18.0.0"),
    # A bounded range whose zero-filled floor (24.0.0) was never published.
    ("@jest/types", ">=24 <=27", "24.3.0"),
])
def test_range_is_looked_up_at_its_lowest_published_version(scan, name, declared, expected):
    row, query = scan("npm", name, declared)

    assert row["resolution"] == "resolved"
    assert row["resolved_version"] == expected
    assert query.asked[-1] == expected
    assert row["package_version"] == declared          # the declaration is untouched


@PATHS
def test_range_nothing_satisfies_is_not_yanked(scan):
    """A peer-dependency sentinel: no published react satisfies it."""
    row, _ = scan("npm", "react", "1000000000000000000")

    assert row["resolution"] == "no_satisfying_version"
    assert row["versions_behind"] is None


@PATHS
def test_a_floor_that_is_published_is_unchanged(scan):
    row, query = scan("npm", "react", "^18.2.0")

    assert row["resolution"] == "resolved"
    assert row["resolved_version"] == "18.2.0"
    assert query.asked == ["18.2.0"]


@PATHS
def test_an_unpublished_pin_is_still_a_lookup_miss(scan):
    """A pin is not a range: nothing to choose between, so no substitution."""
    row, query = scan("npm", "react", "18.2.9")

    assert query.asked == ["18.2.9"]
    assert row["resolved_version"] == "18.2.9"
    assert row["resolution"] == "version_yanked"


@PATHS
def test_other_ecosystems_are_untouched(scan):
    """PyPI `>=9` is sent as its floor `9`: the npm range logic does not apply.

    (The real deps.dev resolves `9` to `9.0.0`, which resolved_version then
    records -- tests/test_versions_behind_canonical.py. This fake answers only
    exact listed versions, so here the lookup misses and `9` is what was sent.)"""
    row, query = scan("pypi", "sphinx", ">=9")

    assert query.asked == ["9"]
    assert row["resolved_version"] == "9"


def test_new_resolution_is_labelled_in_kweb():
    """kweb shows a label per failure resolution, kept in two places: the
    package-check classifier and the /dependencies/ template's `_labels` map."""
    from kweb2 import UNRESOLVED_STATUS_LABELS
    template = (pathlib.Path(__file__).parent.parent / "src" / "templates"
                / "dependencies.html").read_text()

    assert UNRESOLVED_STATUS_LABELS["no_satisfying_version"] == "No matching version"
    assert "'no_satisfying_version': 'no matching version'" in template
