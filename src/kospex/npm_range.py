"""npm semver ranges, evaluated against stable versions only.

Pure: no I/O. A port of node-semver 7's range desugaring (`^`, `~`, x-ranges,
hyphen ranges, `||`), checked against node-semver itself by
tests/test_npm_range.py over a corpus of real npm declarations.

Deliberately narrow. kospex asks one question -- which *published stable*
versions does a declared range permit? -- so:

* only stable `MAJOR.MINOR.PATCH` versions are ever tested against a range; a
  pre-release candidate never satisfies (node-semver's own rule, minus its
  same-tuple exception, which can't matter for a stable-only answer);
* pre-release *bounds* in a range (`^1.2.3-beta.1`, `<2.0.0-0`) are honoured,
  because they decide whether e.g. `1.2.3` is in;
* anything node-semver rejects -- dist-tags (`latest`, `next`), aliases, URLs,
  numbers above 2**53 - 1, leading zeros -- returns None from parse_range(),
  so callers fall back rather than guess.

Why not a dependency: the only Python port of node-semver had a three-year gap
between releases (0.9.0 Feb 2023, 0.9.1 Jun 2026). This module is ~150 lines
against a golden file of node-semver's verdicts. See #379.
"""
import re

MAX_SAFE_INTEGER = 2**53 - 1

_NUM = r"0|[1-9]\d*"
_XID = rf"{_NUM}|x|X|\*"
_PRE_ID = r"(?:0|[1-9]\d*|\d*[a-zA-Z-][a-zA-Z0-9-]*)"
_PRE = rf"(?:-({_PRE_ID}(?:\.{_PRE_ID})*))"
_PRE_NC = rf"(?:-{_PRE_ID}(?:\.{_PRE_ID})*)"
_BUILD = r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)"
_XPLAIN = rf"[v=\s]*({_XID})(?:\.({_XID})(?:\.({_XID})(?:{_PRE})?{_BUILD}?)?)?"
# Same as _XPLAIN but non-capturing, for the hyphen range's two halves.
_XPLAIN_NC = rf"[v=\s]*(?:{_XID})(?:\.(?:{_XID})(?:\.(?:{_XID}){_PRE_NC}?{_BUILD}?)?)?"

_HYPHEN = re.compile(rf"^\s*({_XPLAIN_NC})\s+-\s+({_XPLAIN_NC})\s*$")
_XPLAIN_FULL = re.compile(rf"^{_XPLAIN}$")
_CARET = re.compile(rf"^\^{_XPLAIN}$")
_TILDE = re.compile(rf"^~>?{_XPLAIN}$")
_XRANGE = re.compile(rf"^((?:<|>)?=?)\s*{_XPLAIN}$")
_COMPARATOR = re.compile(
    rf"^((?:<|>)?=?)\s*v?({_NUM})\.({_NUM})\.({_NUM})(?:{_PRE})?{_BUILD}?$")
_STABLE = re.compile(rf"^v?({_NUM})\.({_NUM})\.({_NUM})(?:\+[0-9A-Za-z.-]+)?$")


class _Invalid(Exception):
    pass


def _is_x(part):
    return part is None or part in ("x", "X", "*")


def _caret(m):
    M, mi, p, pr = m.groups()
    if _is_x(M):
        return ""
    if _is_x(mi):
        return f">={M}.0.0 <{int(M) + 1}.0.0-0"
    if _is_x(p):
        if M == "0":
            return f">={M}.{mi}.0 <{M}.{int(mi) + 1}.0-0"
        return f">={M}.{mi}.0 <{int(M) + 1}.0.0-0"
    low = f">={M}.{mi}.{p}" + (f"-{pr}" if pr else "")
    if M == "0":
        if mi == "0":
            return f"{low} <{M}.{mi}.{int(p) + 1}-0"
        return f"{low} <{M}.{int(mi) + 1}.0-0"
    return f"{low} <{int(M) + 1}.0.0-0"


def _tilde(m):
    M, mi, p, pr = m.groups()
    if _is_x(M):
        return ""
    if _is_x(mi):
        return f">={M}.0.0 <{int(M) + 1}.0.0-0"
    if _is_x(p):
        return f">={M}.{mi}.0 <{M}.{int(mi) + 1}.0-0"
    low = f">={M}.{mi}.{p}" + (f"-{pr}" if pr else "")
    return f"{low} <{M}.{int(mi) + 1}.0-0"


def _xrange(m, token):
    gtlt, M, mi, p, _pr = m.groups()
    x_major = _is_x(M)
    x_minor = x_major or _is_x(mi)
    x_patch = x_minor or _is_x(p)
    if gtlt == "=" and x_patch:
        gtlt = ""
    if x_major:
        return "<0.0.0-0" if gtlt in (">", "<") else ""
    if gtlt and x_patch:
        M = int(M)
        mi = 0 if x_minor else int(mi)
        p = 0
        pr = ""
        if gtlt == ">":
            gtlt = ">="
            if x_minor:
                M, mi = M + 1, 0
            else:
                mi += 1
        elif gtlt == "<=":
            gtlt = "<"
            if x_minor:
                M += 1
            else:
                mi += 1
        if gtlt == "<":
            pr = "-0"
        return f"{gtlt}{M}.{mi}.{p}{pr}"
    if x_minor:
        return f">={M}.0.0 <{int(M) + 1}.0.0-0"
    if x_patch:
        return f">={M}.{mi}.0 <{M}.{int(mi) + 1}.0-0"
    return token


def _hyphen(m):
    frm, to = m.group(1), m.group(2)
    fM, fm, fp, fpr = _XPLAIN_FULL.match(frm).groups()
    tM, tm, tp, tpr = _XPLAIN_FULL.match(to).groups()
    if _is_x(fM):
        low = ""
    elif _is_x(fm):
        low = f">={fM}.0.0"
    elif _is_x(fp):
        low = f">={fM}.{fm}.0"
    else:
        low = f">={fM}.{fm}.{fp}" + (f"-{fpr}" if fpr else "")
    if _is_x(tM):
        high = ""
    elif _is_x(tm):
        high = f"<{int(tM) + 1}.0.0-0"
    elif _is_x(tp):
        high = f"<{tM}.{int(tm) + 1}.0-0"
    else:
        high = f"<={tM}.{tm}.{tp}" + (f"-{tpr}" if tpr else "")
    return f"{low} {high}".strip()


def _desugar(token):
    """One whitespace-separated token -> zero, one or two comparator strings."""
    if not token:
        return ""
    for pattern, fn in ((_CARET, _caret), (_TILDE, _tilde)):
        m = pattern.match(token)
        if m:
            return fn(m)
    m = _XRANGE.match(token)
    if m:
        return _xrange(m, token)
    raise _Invalid(token)


def _comparator(text):
    """'>=1.2.3-beta' -> ('>=', (1, 2, 3, 0)); the last element is 1 for a release.

    A release sorts above its own pre-releases, so (1, 2, 3, 1) > (1, 2, 3, 0).
    Stable candidates are always ...1, which is all the ordering this needs.
    """
    m = _COMPARATOR.match(text)
    if not m:
        raise _Invalid(text)
    op, M, mi, p, pr = m.groups()
    nums = (int(M), int(mi), int(p))
    if max(nums) > MAX_SAFE_INTEGER:
        raise _Invalid(text)
    return op, nums + (0 if pr else 1,)


def _parse_alternative(text):
    text = " ".join(text.split())
    m = _HYPHEN.match(text)
    if m:
        text = _hyphen(m)
    # node-semver's trims: no space between an operator and its version.
    text = re.sub(r"(^|\s)((?:<|>)?=?)\s+(?=[v=\dxX*])", r"\1\2", text)
    text = re.sub(r"(^|\s)~>?\s+", r"\1~", text)
    text = re.sub(r"(^|\s)\^\s+", r"\1^", text)
    comparators = []
    for token in text.split(" "):
        for part in _desugar(token).split():
            if part == ">=0.0.0":
                continue                     # node-semver's replaceGTE0
            comparators.append(_comparator(part))
    return comparators


def parse_range(text):
    """Parse an npm range into alternatives of comparators, or None if invalid.

    The result is opaque; pass it to satisfies(). An empty alternative means
    "any version", as in node-semver (`*`, `x`, `""`, `1.2.3 || `).
    """
    if text is None:
        return None
    try:
        return [_parse_alternative(alt) for alt in re.split(r"\s*\|\|\s*", text.strip())]
    except _Invalid:
        return None


def _stable_key(version):
    m = _STABLE.match(version.strip()) if isinstance(version, str) else None
    if not m:
        return None
    nums = tuple(int(x) for x in m.groups())
    return nums + (1,)


def _passes(key, op, bound):
    if op in ("", "="):
        return key == bound
    if op == ">=":
        return key >= bound
    if op == ">":
        return key > bound
    if op == "<=":
        return key <= bound
    return key < bound                       # "<"


def satisfies(version, parsed):
    """True if the stable `version` is permitted by a parse_range() result.

    A pre-release or unparseable version never satisfies.
    """
    key = _stable_key(version)
    if key is None or parsed is None:
        return False
    return any(all(_passes(key, op, bound) for op, bound in alt) for alt in parsed)


def min_satisfying(versions, range_text):
    """The lowest stable version in `versions` the range permits, else None.

    None also when the range itself is invalid -- the caller cannot tell "no
    release matches" from "not a range" by this value alone, and should not
    need to: both mean there is no version to look up.
    """
    parsed = parse_range(range_text)
    if parsed is None:
        return None
    best = None
    for v in versions:
        key = _stable_key(v)
        if key is not None and (best is None or key < best[0]) and satisfies(v, parsed):
            best = (key, v)
    return best[1] if best else None
