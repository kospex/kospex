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
which is the question this exists to answer. `commit` is separate from `pinned`
because a Go pseudo-version is maximally pinned yet deps.dev has nothing to
match it against.

See changes/202609-version-constraint-column.md.
"""
import re

# The closed vocabulary. A value not in here is a bug.
KINDS = (
    "pinned", "commit", "caret", "tilde", "gte", "bounded", "latest",
    "workspace", "link", "catalog", "alias", "patch", "none",
)

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
    whole manifest rather than one row. Unrecognised input classifies as
    "pinned" if it looks like a version and "none" otherwise.
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
            return ("pinned", "==")
        if has_lower:
            return ("gte", operators[0])
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

    # A bare version, or something unrecognised that is shaped like one.
    if re.match(r"^v?\d", text):
        return ("pinned", "")

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
