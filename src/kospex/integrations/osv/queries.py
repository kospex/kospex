"""Pure functions over OSV vulnerability records: no I/O, testable from fixtures.

OSV schema: https://ossf.github.io/osv-schema/. The fields used here:

- `id`, `aliases[]`, `summary`, `published`, `withdrawn` (set when retracted)
- `severity[]`: `{type: CVSS_V2|CVSS_V3|CVSS_V4, score: <vector string>}`. The
  `score` is a VECTOR, not a number.
- `database_specific.severity`: a label, present on GitHub advisories (GHSA).
- `affected[]`: each names ONE package (`package.ecosystem`, `package.name`) and
  holds `ranges[].events[]` (`introduced`, `fixed`, `last_affected`) and the
  `versions[]` OSV expanded those ranges into.

A record names every package it affects, in any ecosystem (a lodash advisory lists
lodash, lodash-es and the RubyGems lodash-rails), so `affected[]` is always filtered
to the package asked about.
"""
import re
from typing import NamedTuple

from kospex.scoring import cvss

# kospex package_type (deps.dev system name) -> OSV ecosystem name.
ECOSYSTEMS = {
    "cargo": "crates.io",
    "go": "Go",
    "maven": "Maven",
    "npm": "npm",
    "nuget": "NuGet",
    "pypi": "PyPI",
    "rubygems": "RubyGems",
}

ADVISORY_URL = "https://osv.dev/vulnerability/"

ROW_FIELDS = ("id", "aliases", "cves", "severity", "severity_score", "severity_source",
              "fixed_in", "last_affected", "fix_matched", "published", "withdrawn",
              "summary", "url")

_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}
# GitHub's label is MODERATE where CVSS's is MEDIUM.
_LABELS = {"CRITICAL": "CRITICAL", "HIGH": "HIGH", "MODERATE": "MEDIUM",
           "MEDIUM": "MEDIUM", "LOW": "LOW", "NONE": "NONE"}
UNSCORED = "UNSCORED"


class Severity(NamedTuple):
    label: str            # CRITICAL, HIGH, MEDIUM, LOW, NONE, or UNSCORED
    score: float | None   # a base score, only when kospex computed one
    source: str           # where the label came from: "CVSS 3.1", "GitHub", "CVSS 4"


def ecosystem_for(package_type):
    """The OSV ecosystem for a kospex package type, or None when OSV has no match."""
    return ECOSYSTEMS.get((package_type or "").lower())


def _same_package(ecosystem, listed_name, name):
    """PyPI names compare as PEP 503 normalises them; the rest ignore case only."""
    def normal(value):
        value = (value or "").lower()
        return re.sub(r"[-_.]+", "-", value) if ecosystem == "PyPI" else value
    return normal(listed_name) == normal(name)


def affected_for(record, ecosystem, name):
    """The record's `affected[]` entries for this package, in order."""
    return [entry for entry in record.get("affected") or []
            if (entry.get("package") or {}).get("ecosystem") == ecosystem
            and _same_package(ecosystem, (entry.get("package") or {}).get("name"), name)]


def fixed_versions(record, ecosystem, name, version=None):
    """{"fixed": [...], "last_affected": [...], "matched": bool} for this package.

    A record can hold several `affected[]` entries for one package, one per release
    line (log4j-core 2.0-beta9 to 2.3.1, 2.4 to 2.12.2, 2.13.0 to 2.15.0). Comparing
    versions needs a per-ecosystem ordering, which kospex does not have here, so the
    entry that lists `version` in its `versions[]` (OSV expands each range into that
    list) picks the line. `matched` says that happened. Without a match every fixed
    version of the package is returned, and the caller must not present them as the
    one answer.

    For Go, OSV leaves `versions[]` empty, so no line is ever matched: a Go advisory
    with several release lines shows every fix with the `*` note, and one with a single
    line shows its fix. That fails safe; it is not a wrong answer. OSV's Go fixes are
    spelled without the `v` that deps.dev uses (`0.3.3`, not `v0.3.3`).

    `last_affected` is returned only for ranges with no `fixed` event: there is no
    fix, and the last affected version is what OSV knows. GIT ranges are commit
    hashes, not versions, and are skipped.
    """
    entries = affected_for(record, ecosystem, name)
    named = [entry for entry in entries if version and version in (entry.get("versions") or [])]
    matched = bool(named)
    fixed, last_affected = [], []
    for entry in named or entries:
        for range_ in entry.get("ranges") or []:
            if range_.get("type") == "GIT":
                continue
            events = range_.get("events") or []
            fixes = [e["fixed"] for e in events if e.get("fixed")]
            for value in fixes:
                if value not in fixed:
                    fixed.append(value)
            if not fixes:
                for value in (e["last_affected"] for e in events if e.get("last_affected")):
                    if value not in last_affected:
                        last_affected.append(value)
    return {"fixed": fixed, "last_affected": last_affected, "matched": matched}


def _rating_rank(label):
    return _RANK.get(label, 1.5 if label == UNSCORED else -1)


def _worst(label, score):
    """Sort key, larger is worse: the label first, then the number (none is lowest)."""
    return (_rating_rank(label), -1.0 if score is None else score)


def severity(record):
    """The worst severity the record carries, as a Severity, or None.

    Candidates: each CVSS v3 vector (scored here by kospex.scoring.cvss, since OSV
    gives a vector, not a number) and the database's own label. The worst wins; on
    a tie the higher number wins, and any number beats none, so a score is shown when there is one.

    A CVSS v4 vector is NOT scored (that needs lookup tables). It counts only through
    the database label. A record whose only severity is a v4 vector and which has no
    label is UNSCORED, which is not the same as no severity at all: it says a severity
    exists and kospex cannot read it. CVSS v2 vectors are ignored.
    """
    candidates = []
    saw_unreadable = None
    for entry in record.get("severity") or []:
        kind, vector = entry.get("type"), entry.get("score")
        if kind == "CVSS_V3":
            scored = cvss.score(vector)
            if scored:
                candidates.append(Severity(scored.rating, scored.base_score,
                                           f"CVSS {scored.version}"))
            else:
                saw_unreadable = saw_unreadable or "CVSS 3"
        elif kind == "CVSS_V4":
            saw_unreadable = saw_unreadable or "CVSS 4"
    label = _LABELS.get(str((record.get("database_specific") or {}).get("severity", "")).upper())
    if label:
        source = "GitHub" if str(record.get("id", "")).startswith("GHSA-") else "database"
        candidates.append(Severity(label, None, source))
    if candidates:
        return max(candidates, key=lambda s: _worst(s.label, s.score))
    if saw_unreadable:
        return Severity(UNSCORED, None, saw_unreadable)
    return None


def _date(value):
    return (value or "")[:10]


def advisory_row(record, ecosystem, name, version=None):
    """One flat row (ROW_FIELDS) for a vulnerability record."""
    found = fixed_versions(record, ecosystem, name, version)
    worst = severity(record)
    aliases = [a for a in record.get("aliases") or [] if a != record.get("id")]
    return {
        "id": record.get("id", ""),
        "aliases": aliases,
        "cves": [a for a in aliases if a.startswith("CVE-")],
        "severity": worst.label if worst else "",
        "severity_score": worst.score if worst else None,
        "severity_source": worst.source if worst else "",
        "fixed_in": found["fixed"],
        "last_affected": found["last_affected"],
        "fix_matched": found["matched"],
        "published": _date(record.get("published")),
        "withdrawn": _date(record.get("withdrawn")),
        "summary": record.get("summary") or "",
        "url": ADVISORY_URL + str(record.get("id", "")),
    }


def _ids(row):
    return {row["id"], *row["aliases"]}


def _merge(rows):
    """One row for rows that are the same vulnerability filed under several IDs.

    The first row (OSV's order) keeps its `id`; the others' IDs become aliases.
    Fixed versions come from the live records only. Severity is the worst of the group (the sources can disagree: requests'
    PYSEC-2026-2275 is 5.5 where its GHSA twin is 4.4), fixed versions are the union,
    the date is the earliest, and it is withdrawn only if every record is.
    """
    first = dict(rows[0])
    everything = set().union(*(_ids(row) for row in rows)) - {first["id"]}
    first["aliases"] = [a for a in dict.fromkeys(
        alias for row in rows for alias in [row["id"], *row["aliases"]]) if a in everything]
    first["cves"] = [a for a in first["aliases"] if a.startswith("CVE-")]
    # A retracted record's fix is not evidence: use only the live ones, unless every
    # record is withdrawn, when the row is shown as withdrawn and its fix is all there is.
    evidence = [row for row in rows if not row["withdrawn"]] or rows
    for field in ("fixed_in", "last_affected"):
        first[field] = list(dict.fromkeys(v for row in evidence for v in row[field]))
    first["fix_matched"] = all(row["fix_matched"] for row in evidence if row["fixed_in"])
    worst = max(rows, key=lambda r: _worst(r["severity"], r["severity_score"]))
    first["severity"], first["severity_score"], first["severity_source"] = (
        worst["severity"], worst["severity_score"], worst["severity_source"])
    first["published"] = min((r["published"] for r in rows if r["published"]), default="")
    first["withdrawn"] = min((r["withdrawn"] for r in rows), default="") \
        if all(r["withdrawn"] for r in rows) else ""
    first["summary"] = next((r["summary"] for r in rows if r["summary"]), "")
    return first


def advisory_rows(records, ecosystem, name, version=None):
    """One advisory_row() per vulnerability, in OSV's order.

    A vulnerability is often filed under several IDs, each its own record: the
    requests advisory is a GHSA and a PYSEC record that alias each other. Records
    sharing any ID or alias are merged, as deps.dev merges its advisoryKeys, so
    the count is vulnerabilities and not records.
    """
    groups = []
    for row in (advisory_row(record, ecosystem, name, version) for record in records):
        touching = [i for i, group in enumerate(groups)
                    if any(_ids(row) & _ids(member) for member in group)]
        if not touching:
            groups.append([row])
            continue
        # The merged group takes the place of the earliest group it touches, so a
        # later alias does not move an advisory below unrelated ones.
        members = [member for i in touching for member in groups[i]] + [row]
        for i in reversed(touching[1:]):
            del groups[i]
        groups[touching[0]] = members
    return [_merge(group) for group in groups]


def compare(osv_rows, depsdev_advisories):
    """(in_both, only_osv, only_depsdev) by shared IDs.

    Each source files one vulnerability under its own IDs (GHSA-..., PYSEC-...,
    CVE-...), so rows match when ANY ID, alias or CVE is common. OSV rows are
    `advisory_row()` dicts; deps.dev ones are `KospexDependencies.advisories()`
    entries (`id`, `also`, `cves`). Withdrawn OSV records are left out: a retracted
    advisory is not a disagreement.

    Returns lists of OSV rows (in_both, only_osv) and deps.dev entries (only_depsdev).
    """
    def osv_ids(row):
        return {row["id"], *row["aliases"]}

    def deps_ids(advisory):
        return {advisory["id"], *advisory.get("also", []), *advisory.get("cves", [])}

    live = [row for row in osv_rows if not row["withdrawn"]]
    deps = list(depsdev_advisories or [])
    in_both = [row for row in live if any(osv_ids(row) & deps_ids(a) for a in deps)]
    only_osv = [row for row in live if row not in in_both]
    only_deps = [a for a in deps if not any(deps_ids(a) & osv_ids(row) for row in live)]
    return in_both, only_osv, only_deps
