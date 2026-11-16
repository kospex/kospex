"""Package names that are never looked up: KOSPEX_DEPS_PRIVATE and GOPRIVATE.

An organisation's own packages are named after it: a Maven groupId under its
reverse domain (`com.example`), a Go module under its Git host
(`github.com/example`, `git.example.internal`). Those names mean nothing to a public
index, and sending them to one tells it what the organisation builds.

KOSPEX_DEPS_PRIVATE lists them as comma-separated `ecosystem:prefix` entries.
A prefix matches a name equal to it, or one that continues past it at a
boundary -- `.` or `:` for Maven, `/` for Go -- so `com.example` does not
match `com.examples`. Matching ignores case: a wrong match costs one
lookup, a missed one sends the name.

Gradle needs nothing of its own: every Gradle file kospex parses records
package_type `maven`, with `group:artifact` names, and `gradle:` is accepted
as another spelling of `maven:`.

GOPRIVATE, Go's own answer to the same question, is honoured for Go modules
with Go's syntax: comma-separated glob patterns, each matched against as many
leading path elements as it has. Only the process environment is read: a
value saved with `go env -w` is not, so export it.
"""
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
from typing import Optional, Tuple

SETTING = "KOSPEX_DEPS_PRIVATE"

# Accepted ecosystem spellings -> the package_type they match.
_ECOSYSTEMS = {"maven": "maven", "gradle": "maven", "go": "go"}

# Characters that may follow a prefix for the name to still be under it.
_BOUNDARIES = {"maven": ".:", "go": "/"}


@lru_cache(maxsize=16)
def parse_private_prefixes(raw: Optional[str]) -> Tuple[Tuple[str, str], ...]:
    """((package_type, prefix), ...) from a KOSPEX_DEPS_PRIVATE value.

    Unset or empty means none. An entry that is malformed, or names an
    ecosystem not supported yet, raises ValueError naming it: a setting that
    looks set but protects nothing is worse than an error.
    """
    prefixes = []
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        ecosystem, colon, prefix = entry.partition(":")
        ecosystem, prefix = ecosystem.strip().lower(), prefix.strip()
        if not colon or not ecosystem or not prefix:
            raise ValueError(f"{SETTING} entry {entry!r} is not ecosystem:prefix "
                             "(e.g. maven:com.example)")
        if any(c in prefix for c in "*?["):
            # Taken literally it would never match: set, and protecting nothing.
            raise ValueError(f"{SETTING} entry {entry!r} is a glob; entries are "
                             "prefixes. For Go globs, use GOPRIVATE")
        if ecosystem not in _ECOSYSTEMS:
            raise ValueError(f"{SETTING} entry {entry!r}: {ecosystem} is not supported "
                             f"yet; supported: {', '.join(sorted(_ECOSYSTEMS))}")
        prefixes.append((_ECOSYSTEMS[ecosystem], prefix))
    return tuple(prefixes)


def parse_goprivate(raw: Optional[str]) -> Tuple[str, ...]:
    """GOPRIVATE's patterns, empty entries dropped."""
    return tuple(p.strip() for p in (raw or "").split(",") if p.strip())


def goprivate_match(patterns, module: str) -> Optional[str]:
    """The first GOPRIVATE pattern that matches `module`, or None.

    Go's module.MatchPrefixPatterns: a pattern with n path elements is matched,
    as a glob, against the module's first n elements. So `github.com/example`
    covers `github.com/example/lib`, and `*.example.internal` covers every module on
    any host under example.internal. Case is ignored, as for KOSPEX_DEPS_PRIVATE.
    """
    elements = module.lower().split("/")
    for pattern in patterns:
        pattern = pattern.strip().rstrip("/")
        if not pattern:
            continue
        n = pattern.count("/") + 1
        if len(elements) >= n and fnmatchcase("/".join(elements[:n]), pattern.lower()):
            return pattern
    return None


@lru_cache(maxsize=16)
def private_packages(private_raw: Optional[str], goprivate_raw: Optional[str]) -> "PrivatePackages":
    """PrivatePackages for the raw setting values, built once per pair: it is
    asked for before every outbound call, several times per package."""
    return PrivatePackages(parse_private_prefixes(private_raw), parse_goprivate(goprivate_raw))


@dataclass(frozen=True)
class PrivatePackages:
    """The configured private prefixes and GOPRIVATE patterns."""

    prefixes: Tuple[Tuple[str, str], ...] = ()
    goprivate: Tuple[str, ...] = ()

    def __bool__(self):
        return bool(self.prefixes or self.goprivate)

    def match(self, package_type, package_name) -> Optional[str]:
        """Why `package_name` is private ("KOSPEX_DEPS_PRIVATE maven:com.example",
        "GOPRIVATE *.example.internal"), or None if it may be looked up."""
        package_type = str(package_type or "").lower()
        name = str(package_name or "").lower()
        if not name:
            return None
        boundaries = _BOUNDARIES.get(package_type, "")
        for ecosystem, prefix in self.prefixes:
            if ecosystem != package_type:
                continue
            p = prefix.lower().rstrip(boundaries) or prefix.lower()
            if name == p or (name.startswith(p) and name[len(p)] in boundaries):
                return f"{SETTING} {ecosystem}:{prefix}"
        if package_type == "go" and (pattern := goprivate_match(self.goprivate, name)):
            return f"GOPRIVATE {pattern}"
        return None

    def summary(self) -> str:
        """`2 prefixes (maven 1, go 1) from KOSPEX_DEPS_PRIVATE; GOPRIVATE set.`"""
        parts = []
        if self.prefixes:
            counts = {}
            for ecosystem, _ in self.prefixes:
                counts[ecosystem] = counts.get(ecosystem, 0) + 1
            n = len(self.prefixes)
            by = ", ".join(f"{e} {c}" for e, c in counts.items())
            parts.append(f"{n} prefix{'' if n == 1 else 'es'} ({by}) from {SETTING}")
        if self.goprivate:
            parts.append("GOPRIVATE set")
        return "; ".join(parts) + "."
