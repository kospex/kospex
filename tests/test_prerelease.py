"""Which versions are pre-releases, by each ecosystem's own grammar (#276).

deps.dev's version records carry no pre-release flag, so canaries, nightlies,
release candidates and Go pseudo-versions all looked like releases and were
counted in versions_behind: react@16.12.0 showed 2,730 behind, 43 of them
stable. A word list ("canary", "nightly", ...) cannot work -- react alone uses
five words -- so each ecosystem's own rule decides.

The version strings below are real, from deps.dev records surveyed for the
design doc (changes/202611-versions-behind-design.md).
"""
import pytest

from kospex.prerelease import is_prerelease

STABLE = [
    # npm: no "-" after x.y.z. Build metadata (+...) is not a pre-release.
    ("npm", "19.2.0"), ("npm", "4.17.21"), ("npm", "1.0.0+build.5"),
    ("cargo", "1.47.2"), ("cargo", "0.9.2"),
    # NuGet: four-part versions are releases.
    ("nuget", "13.0.1"), ("nuget", "1.0.98.1"),
    # Go: tags, and `+incompatible`, which marks a v2+ module without /vN.
    ("go", "v1.10.0"), ("go", "v2.0.0+incompatible"),
    # PyPI: .post releases are releases.
    ("pypi", "2.31.0"), ("pypi", "2026.3.0.post1"), ("pypi", "6"),
    ("rubygems", "7.0.0"), ("rubygems", "1.18.10"),
    # Maven: release qualifiers and flavours are releases.
    ("maven", "4.2.18.Final"), ("maven", "5.3.0.RELEASE"), ("maven", "33.5.0-jre"),
    ("maven", "33.5.0-android"), ("maven", "4.13"), ("maven", "2.20.0"),
    ("maven", "1.0.GA"), ("maven", "4.0.0-SP1"),
    # Real releases despite the letters: protobuf 2.4.0a, Guava's old r03 scheme.
    ("maven", "2.4.0a"), ("maven", "r03"),
]

PRERELEASE = [
    ("npm", "0.0.0-experimental-99c106c4-20261014"), ("npm", "16.0.0-canary.1"),
    ("npm", "5.8.0-dev.20250101"), ("npm", "1.0.0-insiders.20250101"),
    ("cargo", "0.9.0-alpha.1"),
    ("nuget", "10.0.0-preview1"), ("nuget", "4.3.0-dev-02219"),
    # Go: pseudo-versions in both forms, and tagged pre-releases.
    ("go", "v0.0.0-20240829181941-546108da6839"),
    ("go", "v1.5.1-0.20230307220236-3a3c6141e376"),
    ("go", "v2.0.0-rc.1"),
    ("pypi", "1.0rc1"), ("pypi", "1.5.6.dev50"), ("pypi", "2.0.0b2"), ("pypi", "8.0.0a1"),
    ("rubygems", "7.1.0.rc1"), ("rubygems", "8.0.0.beta1"), ("rubygems", "1.0.0.pre"),
    ("maven", "5.0.0.Alpha2"), ("maven", "7.0.0-M1"), ("maven", "2.0-m1"),
    ("maven", "2.0.0-RC1"), ("maven", "1.0-SNAPSHOT"), ("maven", "6.0.0.CR1"),
    ("maven", "1.0-beta-1"), ("maven", "3.0.0-alpha-2"), ("maven", "1.0.0-preview"),
    # Found by auditing every lettered Maven version in a dev estate's cache:
    # Jackson's `.prN` pre-releases, and a qualifier straight after a digit,
    # which Maven itself splits on (protobuf `3.2.0rc2`).
    ("maven", "2.10.0.pr1"), ("maven", "3.2.0rc2"),
]


@pytest.mark.parametrize("system, version", STABLE)
def test_releases(system, version):
    assert is_prerelease(system, version) is False


@pytest.mark.parametrize("system, version", PRERELEASE)
def test_prereleases(system, version):
    assert is_prerelease(system, version) is True


def test_system_name_is_case_insensitive():
    """deps.dev spells systems NPM, PyPi, MAVEN in different places."""
    assert is_prerelease("NPM", "1.0.0-rc.1") is True
    assert is_prerelease("PyPi", "1.0rc1") is True
    assert is_prerelease("MAVEN", "1.0.Final") is False


def test_unparseable_pypi_is_not_a_release():
    """Legacy uploads such as `0.10.0.win32-py2.7` (226 of pandas' 347
    versions) are not PEP 440 versions. Counting them as releases would
    inflate versions_behind; they are excluded."""
    assert is_prerelease("pypi", "0.10.0.win32-py2.7") is True
    assert is_prerelease("pypi", "1.0.1.dev3460.win32-py2.4") is True


def test_an_empty_version_is_not_a_release():
    assert is_prerelease("npm", "") is True
    assert is_prerelease("pypi", None) is True


def test_an_unknown_ecosystem_uses_the_semver_rule():
    assert is_prerelease("hex", "1.0.0") is False
    assert is_prerelease("hex", "1.0.0-rc.1") is True
