"""Classify how a dependency version is constrained.

Pure: no DB, no CLI, no I/O. Given a declared version string and its ecosystem,
returns the normalised `kind` and the raw declared `operator`.

Two columns rather than one because they answer different questions. `kind` is
queryable across ecosystems — "show me everything floating" is one WHERE clause
whether the manifest wrote `^` or `>=`. `operator` keeps the declared operator
text for a version constraint, so a classification we get wrong is still
recoverable from the row. For a resolution mechanism match (`workspace:`,
`link:`, ...), `operator` is the canonical lowercased prefix rather than the
declared text verbatim — `WORKSPACE:^` yields `"workspace:"`, not `"WORKSPACE:"`.

The vocabulary is deliberately finer than pinned/floating. `tilde` is separate
from `caret` because `~29.0.0` permits patch drift only where `^4.18.0` permits
minor and patch — collapsing them misreports how far a dependency can move,
which is the question this exists to answer.

`pinned` means exactly one version, nothing can drift. That is a statement
about what a declaration PERMITS, not what it looks like: `1.x`, `==1.*` and
`1.2.3 - 2.3.4` all start with a digit but each permits a range, so they are
`bounded`. `!=1.0` is the opposite of a pin — every version except one — and
is `excluded`.

`commit` is separate from `pinned` because a Go pseudo-version points at a
commit with no published release, so deps.dev has nothing to match against.
Strictly, Minimal Version Selection makes it a floor like any other Go
requirement, but `commit` records more than `gte` would and is kept for that.

See changes/202609-version-constraint-column.md.
"""
import re

# The closed vocabulary. A value not in here is a bug.
KINDS = (
    "pinned", "commit", "caret", "tilde", "gte", "bounded", "latest",
    "workspace", "link", "catalog", "alias", "patch", "none", "excluded",
)

# Ecosystems where a BARE version is a minimum, not an equality.
#
# Go: `require foo v1.2.3` is a Minimal Version Selection floor. The build
# selects the maximum of all minimums across the module graph, so an
# unrelated dependency requiring v1.5.0 raises the selected version.
# NuGet: `PackageReference Version="3.1.1"` is documented as `>= 3.1.1`; an
# exact pin needs the bracket form `[3.1.1]`.
#
# npm is the counter-example and the reason this cannot be decided by shape:
# a bare npm version IS strict equality. Same string, different meaning.
_BARE_IS_MINIMUM = ("go", "nuget")

# npm wildcard: 1.x, 2.*, 1.2.x, 1.x.x — a floor and a ceiling, so `bounded`.
# Also matches the pypi `==1.*` form once the operator is stripped.
_WILDCARD = re.compile(r"^[vV]?\d+(\.\d+)*\.[xX*](\.[xX*])*$")

# npm partial version: `16`, `16.8`. npm reads these as ranges — `16` is
# `>=16.0.0 <17.0.0` — but they carry no wildcard character, so they look
# exactly like a pin. 12 rows in the reference estate (react "16", "16.8",
# chalk "4", @types/react "19.2"), three times the `==N.*` family.
_PARTIAL_VERSION = re.compile(r"^[vV]?\d+(\.\d+)?$")

# npm hyphen range: `1.2.3 - 2.3.4` is `>=1.2.3 <=2.3.4`. npm requires the
# surrounding spaces, which is what distinguishes it from a prerelease
# version like `1.2.3-beta`.
_HYPHEN_RANGE = re.compile(r"^\S+\s+-\s+\S+$")

# NuGet interval notation. Each end is independent: `[`/`]` inclusive,
# `(`/`)` exclusive, so `[1.0,2.0)` is `>=1.0 <2.0` — the idiomatic "any 1.x".
_NUGET_INTERVAL = re.compile(r"^([\[(])\s*([^,\]\)]*)\s*(?:,\s*([^,\]\)]*)\s*)?([\])])$")

# Resolution mechanisms, not version constraints. Prefix -> kind.
_MECHANISM_PREFIXES = (
    ("workspace:", "workspace"),
    ("link:", "link"),
    ("catalog:", "catalog"),
    ("patch:", "patch"),
    ("npm:", "alias"),
    ("file:", "link"),
    ("portal:", "link"),
)

# Go pseudo-version: <base>-<14-digit UTC timestamp>-<12-hex commit>.
# Two forms — v0.0.0-20230828082145-3c4c8a2d2371, and the pre-release form
# v1.5.1-0.20230307220236-3a3c6141e376 where the pre-release is separated from
# the timestamp by a DOT, not a dash. Getting that separator wrong silently
# classifies the second form as `pinned`, which is how it reads at a glance.
_GO_PSEUDO = re.compile(r"^v?\d+\.\d+\.\d+-(?:[\w.]+\.)?\d{14}-[0-9a-f]{12}$")

_UPPER_BOUND_OPS = ("<=", "<")


def classify_constraint(declared_version, package_type=None):
    """Return (kind, operator) for a declared version string.

    Never raises: it runs during extraction, where an exception would lose a
    whole manifest rather than one row. Unrecognised input that looks like a
    version classifies as "pinned" — or "gte" for the ecosystems where a bare
    version is a minimum, see below — and "none" otherwise.

    `package_type` decides exactly one rule: what a BARE version means.
    Everything else is shape-based, because the shapes that occur are
    disjoint across ecosystems — `^` and `~` are npm, `catalog:` /
    `workspace:` are pnpm, `~=` and `===` are PEP 440, and the Go
    pseudo-version pattern matches nothing else.

    A bare version cannot be decided by shape, because the same string means
    different things:

    * npm `1.4.3` is strict equality -> `pinned`
    * Go `v1.2.3` is a Minimal Version Selection floor -> `gte`. The build
      takes the maximum of all minimums across the module graph, so an
      unrelated dependency requiring v1.5.0 raises the selected version.
    * NuGet `3.1.1` is documented as `>= 3.1.1` -> `gte`. An exact NuGet pin
      needs the bracket form `[3.1.1]`.

    An unknown or absent `package_type` defaults to `pinned`: absent a reason
    to think otherwise, a bare version is an equality.
    """
    if declared_version is None:
        return ("none", "")

    text = str(declared_version).strip()
    if not text:
        return ("none", "")

    lowered = text.lower()

    # Resolution mechanisms first — `workspace:^` starts with a prefix AND
    # contains a caret, so prefix matching has to win.
    for prefix, kind in _MECHANISM_PREFIXES:
        if lowered.startswith(prefix):
            return (kind, prefix)

    if lowered in ("latest", "next", "*", "x"):
        return ("latest", "")

    # Go pseudo-versions are commit pins, not ranges — check before the
    # operator scan, since the embedded '-' must not be read as anything else.
    if _GO_PSEUDO.match(text):
        return ("commit", "")

    # Disjunction of ranges: `^1.0.0 || ^2.0.0`. Bounded — alternatives are
    # each windowed — and recorded as `||` so the shape stays visible.
    if "||" in text:
        return ("bounded", "||")

    ecosystem = str(package_type or "").strip().lower()

    # NuGet interval notation, before the operator scan: the brackets carry
    # the bounds, and the string contains no comparison operators to find.
    #
    # GATED ON ECOSYSTEM, deliberately. PEP 508 permits parentheses around a
    # specifier, so `requests (>=2.0)` is a valid pypi declaration that this
    # pattern matches — and ungated it returned ("pinned", ""), an open floor
    # reported as a pin with the operator erased. That is the exact defect
    # this module exists to prevent.
    if ecosystem == "nuget":
        interval = _NUGET_INTERVAL.match(text)
        if interval:
            return _classify_interval(interval, text)

    # npm hyphen range, before the operator scan so the ' - ' separator is
    # not mistaken for anything else. Gated for the same reason: `\S+` is
    # unconstrained, so an ungated rule would swallow anything with a spaced
    # hyphen.
    if ecosystem in ("npm", "") and _HYPHEN_RANGE.match(text):
        return ("bounded", "-")

    operators = _operators_in(text)

    if operators:
        has_lower = any(op in (">=", ">") for op in operators)
        has_upper = any(op in _UPPER_BOUND_OPS for op in operators)

        if has_lower and has_upper:
            return ("bounded", ">=,<")
        if has_upper:
            # `<4` alone is a ceiling with an open floor: still windowed above.
            return ("bounded", operators[0])
        if "~=" in operators:
            # PEP 440 compatible-release: patch drift, the pypi tilde.
            return ("tilde", "~=")
        if "==" in operators:
            # `==1.*` is a wildcard, not an equality: it permits 1.0 through
            # 1.<anything>. Checked here so it cannot reach the pin return.
            if _WILDCARD.match(text.split("==", 1)[1].strip()):
                return ("bounded", "==*")
            return ("pinned", "==")
        if has_lower:
            return ("gte", operators[0])
        if "!=" in operators:
            # A standalone exclusion is the OPPOSITE of a pin: it permits
            # every version except one. No floor, no ceiling, so it fits
            # nothing else. Reached only when no other operator is present —
            # `>=1.7.3,!=1.8.0` returns `gte` above, because the open floor
            # is what governs drift there, not the carve-out.
            return ("excluded", "!=")
        # `===` (PEP 440 arbitrary equality) lands here deliberately: it is
        # not `>=`/`>` (has_lower), not `~=`, and list membership means
        # "==" in operators is False for it (the strings are unequal), so it
        # falls through to this generic pin return with its own operator
        # text intact — the same "pinned" kind as `==`, just recorded verbatim.
        return ("pinned", operators[0])

    if text[0] == "^":
        return ("caret", "^")
    if text[0] == "~":
        return ("tilde", "~")

    # Bare wildcard: `1.x`, `2.*`, `1.x.x`. A floor and a ceiling, not a pin.
    if _WILDCARD.match(text):
        return ("bounded", "*")

    # npm partial version: `16` is `>=16.0.0 <17.0.0`, `16.8` is
    # `>=16.8.0 <16.9.0`. A range with no wildcard character to give it away,
    # so it reads exactly like a pin. npm-only — a bare `2.0` elsewhere is a
    # full version, not a partial one.
    if ecosystem == "npm" and _PARTIAL_VERSION.match(text):
        return ("bounded", "")

    # A bare version, or something unrecognised that is shaped like one.
    if re.match(r"^v?\d", text):
        # The one genuinely ecosystem-dependent rule. See _BARE_IS_MINIMUM:
        # npm bare is strict equality, Go and NuGet bare are floors.
        if ecosystem in _BARE_IS_MINIMUM:
            return ("gte", "")
        return ("pinned", "")

    return ("none", "")


def _classify_interval(match, text):
    """Classify a NuGet interval like `[1.0]`, `[1.0,)` or `[1.0,2.0)`.

    `[`/`]` are inclusive and `(`/`)` exclusive, and the two ends are
    independent — so the bracket characters alone do not decide the kind.
    What matters is which bounds are actually present.
    """
    open_br, low, high, close_br = match.groups()
    low = (low or "").strip()
    high = (high or "").strip()

    # `[1.0]` — a single value with no comma is an exact pin, but ONLY with
    # matched inclusive brackets. NuGet documents `(1.0)` as invalid, and
    # `[1.0)` / `(1.0]` are mismatched; reporting any of them as a confident
    # pin would be the wrong direction of error for malformed input.
    if "," not in text:
        if low and open_br == "[" and close_br == "]":
            return ("pinned", "")
        return ("none", "")

    if low and high:
        return ("bounded", f"{open_br}{close_br}")
    if high:
        # `(,2.0]` — a ceiling with an open floor, windowed above.
        return ("bounded", f"{open_br}{close_br}")
    if low:
        # `[1.0,)` — a floor with no ceiling, the same shape as bare NuGet.
        return ("gte", "")
    return ("none", "")


def _operators_in(text):
    """Comparison operators present in a declaration.

    Operators are consumed longest-first — three-character before
    two-character before one-character — so a shorter operator that is a
    substring of a longer one is never counted separately. Without this,
    `>=1.0` would read as having both an inclusive lower bound (`>=`) and a
    bare greater-than (`>`), and PEP 440 `===1.0` would read as `==` (losing
    the third `=`, since `==` matches first and consumes two of the three
    characters).
    """
    found = []
    remaining = text
    for op in ("===", ">=", "<=", "==", "!=", "~="):
        if op in remaining:
            found.append(op)
            remaining = remaining.replace(op, " ")
    for op in (">", "<"):
        if op in remaining:
            found.append(op)
    return found
