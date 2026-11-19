"""How far apart are two versions, by major / minor / patch? Pure: no I/O.

A rough, offline answer from the version numbers alone. It cannot say how many
releases sit between two versions -- for that, see
KospexDependencies.releases_between(), which reads deps.dev's version list.

Only the release numbers are compared. Pre-release and build tags
(`-rc.1`, `+build`, `.Final`, `-jre`) are ignored, so `1.2.3-rc.1` -> `1.2.3`
is "none". Missing components are 0, so `1.2` == `1.2.0`.

No semver meaning is applied: in semver a 0.x minor bump is breaking, and many
ecosystems (CalVer, Maven) do not follow semver at all. The level is the first
component that moved, nothing more.
"""
import re

from packaging.version import InvalidVersion, Version

# Each repetition starts with a separator, so a digit run can match only one
# way and a failed match is linear (CWE-1333; versions come from deps.dev,
# where anyone can publish one).
_LEADING_NUMBERS = re.compile(r"[vV]?(\d+(?:\.\d+)*)")

LEVELS = ("major", "minor", "patch")


def parse_release(version, system=None):
    """The release numbers of `version` as a tuple of ints, at least 3 long.

    PyPI versions go through PEP 440 (`packaging`), which handles epochs
    (`1!2.0`), `.postN` and `rcN` without separators. Everything else, and a
    PyPI version `packaging` refuses, uses the leading dotted numbers after an
    optional `v`. None when there is no leading number (`latest`, a range like
    `^1.2.3` -- resolve a range to a version first).
    """
    if not isinstance(version, str):
        return None
    version = version.strip()
    numbers = None
    if (system or "").lower() == "pypi":
        try:
            numbers = Version(version).release
        except InvalidVersion:
            numbers = None
    if numbers is None:
        match = _LEADING_NUMBERS.match(version)
        if not match:
            return None
        numbers = tuple(int(n) for n in match.group(1).split("."))
    return tuple(numbers) + (0,) * (3 - len(numbers))


def version_diff(from_version, to_version, system=None):
    """Raw `to - from` per component: {"major", "minor", "patch"}, or None.

    Raw means each component is subtracted on its own: 1.9.9 -> 3.0.0 is
    {"major": 2, "minor": -9, "patch": -9}. A negative minor or patch under a
    positive major is normal; use change_level() for the direction and level.
    """
    a = parse_release(from_version, system)
    b = parse_release(to_version, system)
    if a is None or b is None:
        return None
    return {level: b[i] - a[i] for i, level in enumerate(LEVELS)}


def level_between(a, b):
    """change_level() over tuples parse_release() returned (first three components)."""
    a3, b3 = a[:3], b[:3]
    if b3 < a3:
        return "downgrade"
    for i, level in enumerate(LEVELS):
        if a3[i] != b3[i]:
            return level
    return "none"


def change_level(from_version, to_version, system=None):
    """"major", "minor", "patch", "none", "downgrade", or None if unparseable.

    The first of major / minor / patch that differs. A fourth component (NuGet
    `1.2.3.4`) is not a level of its own; a change only there reads "none".
    """
    a = parse_release(from_version, system)
    b = parse_release(to_version, system)
    if a is None or b is None:
        return None
    return level_between(a, b)
