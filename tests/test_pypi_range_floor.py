"""A PyPI range is looked up at the lowest version it allows, not its written floor (#388).

`clean_version_spec()` reads a floor off the declared text, and for PyPI that
is wrong four ways, each of which pip itself resolves without trouble:

* `click==8.*` gives `8.*`, which is not a version: never looked up, so the
  row has no advisory data at all.
* `tqdm>4` gives `4` and `cffi<2` gives `2`: versions the range excludes,
  so the row's numbers describe something that cannot be installed.
* `setuptools>=77.0` gives `77.0`, never released (the first is 77.0.1), and
  the 404 was recorded as `version_yanked`.

So when the written floor is not a published version the specifier allows,
the lookup uses the lowest stable version in deps.dev's list that
`packaging.specifiers.SpecifierSet` -- pip's own logic -- accepts. deps.dev's
list, not PyPI's: deps.dev has no numpy 1.0, so `numpy>=1` resolves to 1.3.0.

Fixtures are real deps.dev records (tests/data/depsdev/pypi__*.json). Expected
versions were computed outside kospex with `packaging` over the same records,
and agree with `pip install --dry-run` on PyPI except where PyPI has a version
deps.dev lacks (numpy 1.0).
"""
import json
import pathlib
from urllib.parse import quote

import pytest
from packaging.version import InvalidVersion, Version

import krunner
from kospex.extractors.registry import REGISTRY
from kospex_dependencies import KospexDependencies

DATA = pathlib.Path(__file__).parent / "data" / "depsdev"
BASE = "https://api.deps.dev/v3alpha/systems/pypi/packages/"


class _Query:
    """deps.dev from a fixture. The exact-version endpoint answers any
    PEP 440-equal spelling with the listed one (`8.1` -> `8.1.0`), as the
    real endpoint does, and 404s anything else."""

    def __init__(self, name):
        self.listing = (DATA / f"pypi__{name}.json").read_text()
        self.package_url = BASE + quote(name, safe="")
        self.name = name
        self.listed = [v["versionKey"]["version"]
                       for v in json.loads(self.listing)["versions"]]
        self.asked = []

    def _canonical(self, asked):
        if asked in self.listed:
            return asked
        try:
            want = Version(asked)
        except InvalidVersion:
            return None
        for v in self.listed:
            try:
                if Version(v) == want:
                    return v
            except InvalidVersion:
                continue
        return None

    def url_request_with_status(self, url, **_kwargs):
        if url == self.package_url:
            return self.listing, 200
        prefix = self.package_url + "/versions/"
        if url.startswith(prefix):
            asked = url[len(prefix):]
            self.asked.append(asked)
            canonical = self._canonical(asked)
            if canonical:
                return json.dumps({"versionKey": {"system": "PYPI", "name": self.name,
                                                  "version": canonical},
                                   "publishedAt": "2026-01-01T00:00:00Z"}), 200
        return None, 404

    def url_request(self, url, **_kwargs):
        return self.url_request_with_status(url)[0]


def _row(name, declared):
    return {"package_name": name, "package_version": declared, "package_type": "pypi",
            "requirements_type": "direct", "file_path": "requirements.txt",
            "_repo_id": "github.com~acme~app"}


def _osi(name, declared):
    query = _Query(name)
    kdeps = KospexDependencies(kospex_query=query)
    return krunner.enrich_dependency_records([_row(name, declared)], kdeps)[0], query


def _sca(name, declared):
    query = _Query(name)
    kdeps = KospexDependencies(kospex_query=query)
    extractor = next(e for e in REGISTRY if e.package_type == "pypi")
    return kdeps._enrich_dependency_records([_row(name, declared)], extractor)[0], query


PATHS = pytest.mark.parametrize("scan", [_osi, _sca], ids=["osi", "sca"])


@PATHS
@pytest.mark.parametrize("name, declared, expected", [
    # Wildcards: `8.*` is not a version, so these were never looked up.
    ("click", "==8.*", "8.0.0"),
    ("httpcore", "==1.*", "1.0.0"),
    ("click", "==8.1.*", "8.1.0"),
    # The written floor is a version the range excludes.
    ("tqdm", ">4", "4.1.0"),
    ("cffi", "<2", "0.1.0"),
    ("cffi", "!=1.0", "0.1.0"),
    # The written floor was never released.
    ("setuptools", ">=77.0", "77.0.1"),
    # deps.dev's list, not PyPI's: PyPI has numpy 1.0, deps.dev starts at 1.3.0.
    ("numpy", ">=1", "1.3.0"),
])
def test_range_is_looked_up_at_its_lowest_allowed_version(scan, name, declared, expected):
    row, query = scan(name, declared)

    assert row["resolution"] == "resolved"
    assert query.asked == [expected]
    assert row["resolved_version"] == expected
    assert row["package_version"] == declared          # the declaration is untouched


@PATHS
@pytest.mark.parametrize("name, declared, sent, recorded", [
    # The written floor is published and allowed: sent as written, as before,
    # and recorded in deps.dev's spelling (#387).
    ("click", ">=8.1", "8.1", "8.1.0"),
    ("tqdm", ">=4.66,<4.67", "4.66", "4.66.0"),
])
def test_an_allowed_published_floor_is_unchanged(scan, name, declared, sent, recorded):
    row, query = scan(name, declared)

    assert query.asked == [sent]
    assert row["resolved_version"] == recorded


@PATHS
@pytest.mark.parametrize("declared", [">9999", "==99.*"])
def test_nothing_allowed_is_no_satisfying_version_without_a_lookup(scan, declared):
    """`>9999` must not be looked up at 9999 and come back with numbers for it."""
    row, query = scan("click", declared)

    assert row["resolution"] == "no_satisfying_version"
    assert row["versions_behind"] is None
    assert query.asked == []


@PATHS
def test_a_specifier_naming_a_prerelease_allows_prereleases(scan):
    """pip's rule: a specifier that names a pre-release allows pre-releases.
    keras-nightly has published 1,828 versions and no stable one; pip installs
    `keras-nightly~=2.14.0.dev`. Filtering every pre-release out made it
    `no_satisfying_version`."""
    row, query = scan("keras-nightly", "~=2.14.0.dev")

    assert row["resolution"] == "resolved"
    assert query.asked == ["2.14.0.dev2023050307"]


@PATHS
def test_a_pin_is_untouched(scan):
    row, query = scan("click", "==8.1.3")

    assert query.asked == ["8.1.3"]
    assert row["resolution"] == "resolved"


def test_an_oversized_range_is_not_evaluated():
    """Declared versions come from untrusted manifests. Evaluation checks every
    clause against every release: 1,000 distinct `!=` clauses against 700
    releases took 1.2 s, 4,000 took 18 s, per row. Real ranges on a dev
    estate are at most 46 (PyPI) and 104 (npm) characters. Over the cap the
    range is not evaluated and the written floor is used, as before #388."""
    kd = KospexDependencies(kospex_query=_Query("cffi"))
    declared = "<2," + ",".join(f"!=0.0.{i}" for i in range(60))
    assert len(declared) > 256

    assert kd._range_floor("pypi", "cffi", declared)[1] is None


def test_unparseable_releases_are_skipped():
    """deps.dev lists legacy uploads like `1.0.1.dev3460.win32-py2.4`; one in
    the list must not stop the others being considered."""
    listed = json.loads((DATA / "pypi__numpy.json").read_text())["versions"]
    assert any(_unparseable(v["versionKey"]["version"]) for v in listed)

    row, _ = _osi("numpy", ">=1")
    assert row["resolved_version"] == "1.3.0"


def _unparseable(version):
    try:
        Version(version)
        return False
    except InvalidVersion:
        return True
