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


class TestResolvedVersionIsDepsDevsSpelling:
    """resolved_version records the version the numbers are for.

    When deps.dev resolves a lookup, that is its canonical spelling: PyPI `6`
    is `6.0.0` (PEP 440 zero-padding), `2026.3.post1` is `2026.3.0.post1`, and
    NuGet `13.0.1.0` is `13.0.1` (NuGet drops a zero fourth part). When it does
    not resolve, nothing answered, so it is the version that was sent. Both
    scan paths share lookup_dependency(), so both are driven.
    """

    @staticmethod
    def _osi(kd, row):
        import krunner
        return krunner.enrich_dependency_records([row], kd)[0]

    @staticmethod
    def _sca(kd, row):
        from kospex.extractors.registry import REGISTRY
        extractor = next(e for e in REGISTRY if e.package_type == row["package_type"])
        return kd._enrich_dependency_records([row], extractor)[0]

    @staticmethod
    def _row(system, name, declared):
        return {"package_name": name, "package_version": declared, "package_type": system,
                "requirements_type": "direct", "file_path": "requirements.txt",
                "_repo_id": "github.com~acme~app"}

    @pytest.mark.parametrize("scan", ["_osi", "_sca"])
    @pytest.mark.parametrize("system, name, declared, sent, canonical", [
        ("pypi", "sphinx", ">=9", "9", "9.0.0"),
        ("pypi", "pytz", "==2026.3.post1", "2026.3.post1", "2026.3.0.post1"),
    ])
    def test_resolved_lookup_records_depsdev_spelling(self, scan, system, name,
                                                      declared, sent, canonical):
        kd = _kdeps(system, name, sent, canonical)

        row = getattr(self, scan)(kd, self._row(system, name, declared))

        assert row["resolution"] == "resolved"
        assert row["resolved_version"] == canonical
        assert row["package_version"] == declared

    @pytest.mark.parametrize("scan", ["_osi", "_sca"])
    def test_nuget_zero_fourth_part(self, scan):
        listing = json.dumps({"versions": [
            {"versionKey": {"system": "NUGET", "name": "Newtonsoft.Json", "version": v},
             "publishedAt": f"2026-0{i}-01T00:00:00Z", "isDefault": v == "13.0.4"}
            for i, v in enumerate(["13.0.1", "13.0.2", "13.0.3", "13.0.4"], start=1)]})
        responses = {
            _package_url("nuget", "Newtonsoft.Json"): (listing, 200),
            f"{_package_url('nuget', 'Newtonsoft.Json')}/versions/13.0.1.0":
                (_version_record("nuget", "Newtonsoft.Json", "13.0.1"), 200),
        }
        kd = KospexDependencies(kospex_query=_Query(responses))
        row = self._row("nuget", "Newtonsoft.Json", "13.0.1.0")
        row["file_path"] = "app.csproj"

        row = getattr(self, scan)(kd, row)

        assert row["resolved_version"] == "13.0.1"
        assert row["versions_behind"] == 3

    @pytest.mark.parametrize("scan", ["_osi", "_sca"])
    def test_unresolved_lookup_records_what_was_sent(self, scan):
        """A 404: deps.dev gave no spelling, so the sent version stands."""
        kd = _kdeps("pypi", "sphinx", "9", "9.0.0")

        row = getattr(self, scan)(kd, self._row("pypi", "sphinx", ">=8.9"))

        assert row["resolution"] != "resolved"
        assert row["resolved_version"] == "8.9"
