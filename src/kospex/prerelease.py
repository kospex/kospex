"""Is a version a pre-release, by its ecosystem's own grammar? (#276)

Pure: no I/O. deps.dev's version records carry no pre-release flag, so every
canary, nightly, release candidate and Go pseudo-version looked like a release
and was counted in versions_behind (react@16.12.0: 2,730 behind, 43 of them
stable). A word list cannot keep up -- react alone publishes `experimental`,
`canary`, `next`, `rc` and `alpha` builds -- so each ecosystem's own rule
decides:

* npm, Cargo, NuGet, and anything unrecognised: semver -- a `-` after the
  version core is a pre-release; `+build` metadata is not
  (https://semver.org/#spec-item-9). NuGet four-part versions are releases.
* Go: semver too. Pseudo-versions (`v0.0.0-20240829181941-546108da6839`)
  contain a `-`, so they are pre-releases, as Go itself orders them;
  `+incompatible` is build metadata on a release.
* PyPI: `packaging`'s PEP 440 `is_prerelease`, which includes `.devN` and
  keeps `.postN` as a release. A version `packaging` cannot parse (legacy
  uploads like `0.10.0.win32-py2.7`) is treated as not a release.
* RubyGems: any letter in the version (Gem::Version#prerelease?).
* Maven: a pre-release qualifier after a separator -- alpha, beta, rc, cr,
  milestone (`M1`), snapshot, preview, ea, dev, ... Release qualifiers and
  flavours (`.Final`, `.RELEASE`, `.GA`, `-SP1`, `-jre`, `-android`) are not.

Used by KospexDependencies.release_list(), which versions_behind and the
first-release date (#390) both read, so they agree on what a release is.
"""
import re

from packaging.version import InvalidVersion, Version

# A qualifier starts after a separator or straight after a digit: Maven's own
# version parser splits on a digit-to-letter change, so `3.2.0rc2` is rc 2.
# `pr` is Jackson's pre-release spelling (`2.10.0.pr1`). Letters that are
# not a pre-release qualifier stay a release: protobuf `2.4.0a`, Guava `r03`.
_MAVEN_PRERELEASE = re.compile(
    r"(?:^|[.\-_]|(?<=\d))"
    r"(alpha|beta|rc|cr|milestone|snapshot|preview|pre|ea|dev|nightly|canary|incubating"
    r"|a(?=\d)|b(?=\d)|m(?=\d)|pr(?=\d))"
    r"(?:[.\-_]?\d+)*(?=$|[.\-_+])",
    re.IGNORECASE,
)


def _semver_prerelease(version):
    core = version.split("+", 1)[0]
    return "-" in core


def is_prerelease(system, version):
    """True if `version` is not a release in `system` (npm, pypi, maven, ...).

    An empty or unparseable version is treated as not a release: callers
    count releases, and a value that cannot be read is not one to count.
    """
    if not version:
        return True
    eco = (system or "").lower()
    if eco == "pypi":
        try:
            return Version(version).is_prerelease
        except InvalidVersion:
            return True
    if eco == "rubygems":
        return re.search(r"[a-zA-Z]", version) is not None
    if eco == "maven":
        return _MAVEN_PRERELEASE.search(version) is not None
    # npm, cargo, nuget, go, and any ecosystem without its own rule.
    return _semver_prerelease(version)
