"""CVSS v3.0 / v3.1 base scores, from the FIRST specification.

Pure: no I/O, no kospex imports. OSV gives a CVSS *vector string*, not a number
(`severity[].score` is `CVSS:3.1/AV:N/...`), so a number has to be computed.

Written from the specification (https://www.first.org/cvss/v3.1/specification-document,
section 7 and Appendix A), not from any library's source. Tested against an
independent implementation over every v3 base vector (tests/test_cvss.py).

Scope: base score and rating only. Temporal and environmental metrics are
accepted so a vector carrying them still parses, and are ignored: the result is
the base score, which is what it says it is. v2 and v4 are not supported and give
None; v4 needs lookup tables and is deliberately not hand-rolled here.

Strict by design: a vector that is malformed, repeats a metric, lacks a base
metric or has an unknown metric or value gives None, never a guess. A caller that
shows a severity from a vector must be able to tell "not scored" from "scored 0".
"""
import math
from typing import NamedTuple

VERSIONS = ("3.0", "3.1")

# Base metrics, in the order the specification lists them.
BASE_VALUES = {
    "AV": "NALP",
    "AC": "LH",
    "PR": "NLH",
    "UI": "NR",
    "S": "UC",
    "C": "HLN",
    "I": "HLN",
    "A": "HLN",
}

# Temporal and environmental metrics. Validated, then ignored for the base score.
OTHER_VALUES = {
    "E": "XUPFH", "RL": "XOTWU", "RC": "XURC",
    "CR": "XLMH", "IR": "XLMH", "AR": "XLMH",
    "MAV": "XNALP", "MAC": "XLH", "MPR": "XNLH", "MUI": "XNR", "MS": "XUC",
    "MC": "XNLH", "MI": "XNLH", "MA": "XNLH",
}

_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_AC = {"L": 0.77, "H": 0.44}
_PR_UNCHANGED = {"N": 0.85, "L": 0.62, "H": 0.27}
_PR_CHANGED = {"N": 0.85, "L": 0.68, "H": 0.5}
_UI = {"N": 0.85, "R": 0.62}
_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}


class CvssScore(NamedTuple):
    version: str       # "3.0" or "3.1"
    base_score: float  # 0.0 to 10.0, one decimal
    rating: str        # NONE, LOW, MEDIUM, HIGH or CRITICAL


def parse(vector):
    """(version, base metrics) for a v3.x vector, or None if it is not one.

    Metrics are returned as a dict of the eight base metrics only.
    """
    if not isinstance(vector, str):
        return None
    parts = vector.split("/")
    if len(parts) < 1 + len(BASE_VALUES) or not parts[0].startswith("CVSS:"):
        return None
    version = parts[0][len("CVSS:"):]
    if version not in VERSIONS:
        return None

    seen = {}
    for part in parts[1:]:
        name, separator, value = part.partition(":")
        if not separator or name in seen:
            return None
        allowed = BASE_VALUES.get(name) or OTHER_VALUES.get(name)
        if allowed is None or len(value) != 1 or value not in allowed:
            return None
        seen[name] = value
    if any(name not in seen for name in BASE_VALUES):
        return None
    return version, {name: seen[name] for name in BASE_VALUES}


def _roundup_30(value):
    """3.0: the smallest number, to one decimal, that is >= value."""
    return math.ceil(value * 10) / 10


def _roundup_31(value):
    """3.1 Appendix A: the same, but immune to floating point noise.

    Working in integers at five decimals means 4.000000000000001 does not become
    4.1.
    """
    scaled = round(value * 100000)
    if scaled % 10000 == 0:
        return scaled / 100000.0
    return (math.floor(scaled / 10000) + 1) / 10.0


def rating(value):
    """The qualitative rating for a score: NONE, LOW, MEDIUM, HIGH or CRITICAL."""
    if value <= 0:
        return "NONE"
    if value < 4.0:
        return "LOW"
    if value < 7.0:
        return "MEDIUM"
    if value < 9.0:
        return "HIGH"
    return "CRITICAL"


def score(vector):
    """CvssScore for a CVSS v3.0 / v3.1 vector, or None if it cannot be scored."""
    parsed = parse(vector)
    if parsed is None:
        return None
    version, m = parsed
    roundup = _roundup_30 if version == "3.0" else _roundup_31
    changed = m["S"] == "C"

    iss = 1 - (1 - _CIA[m["C"]]) * (1 - _CIA[m["I"]]) * (1 - _CIA[m["A"]])
    if changed:
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    else:
        impact = 6.42 * iss
    privileges = (_PR_CHANGED if changed else _PR_UNCHANGED)[m["PR"]]
    exploitability = 8.22 * _AV[m["AV"]] * _AC[m["AC"]] * privileges * _UI[m["UI"]]

    if impact <= 0:
        base = 0.0
    elif changed:
        base = roundup(min(1.08 * (impact + exploitability), 10))
    else:
        base = roundup(min(impact + exploitability, 10))
    return CvssScore(version, base, rating(base))
