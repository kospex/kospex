"""Enrich Dependabot alerts with upgrade-distance and patched-version facts.

`Enricher.enrich(alert)` returns a copy of the alert with every SCA_FIELDS key
appended (None when unknown), so each output row has the same shape. A row
that cannot be enriched is kept, with the reason in `sca_status`.

The alert's `ecosystem`, `package`, `current_version` and `patched_version`
keys are read. Lookups go through KospexDependencies (deps.dev) and are cached
per package and per (package, version), negative results included.
"""
from kospex.dependencies.version_diff import change_level, version_diff

# GitHub ecosystem name (lower case) -> kospex / deps.dev package type.
ECOSYSTEM_MAP = {
    "pip": "pypi",
    "npm": "npm",
    "maven": "maven",
    "nuget": "nuget",
    "go": "go",
    "rubygems": "rubygems",
    "rust": "cargo",
}

OK = "ok"
NO_CURRENT_VERSION = "no_current_version"
NO_PATCHED_VERSION = "no_patched_version"
PRIVATE_SKIPPED = "private_skipped"
UNSUPPORTED_ECOSYSTEM = "unsupported_ecosystem"
NOT_FOUND = "not_found"
LOOKUP_ERROR = "lookup_error"

SCA_FIELDS = (
    "sca_status",
    "sca_latest_version",
    "sca_behind_level",
    "sca_behind_major",
    "sca_behind_minor",
    "sca_behind_patch",
    "sca_behind_releases",
    "sca_current_age_days",
    "sca_patched_diff_level",
    "sca_patched_diff_major",
    "sca_patched_diff_minor",
    "sca_patched_diff_patch",
    "sca_patched_version_vulnerabilities",
    "sca_patched_version_max_cvss",
    "sca_patched_ghsa_ids",
)

_MISSING = object()


class Enricher:
    """Adds SCA_FIELDS to Dependabot alerts using `kdeps` (KospexDependencies)."""

    def __init__(self, kdeps, now=None):
        self.kdeps = kdeps
        self.now = now
        self._releases = {}   # (type, name) -> (releases | None, status)
        self._details = {}    # (type, name, version) -> detail | None

    def enrich(self, alert):
        """A new dict: the alert's keys, then every SCA_FIELDS key."""
        out = dict(alert)
        fields = dict.fromkeys(SCA_FIELDS)
        fields["sca_status"] = self._fill(alert, fields)
        out.update(fields)
        return out

    def _fill(self, alert, fields):
        """Fill `fields` in place and return the status."""
        package_type = ECOSYSTEM_MAP.get(str(alert.get("ecosystem") or "").lower())
        name = alert.get("package")
        if package_type is None:
            return UNSUPPORTED_ECOSYSTEM
        permitted, _reason = self.kdeps.lookup_permitted(package_type, name)
        if not permitted:
            return PRIVATE_SKIPPED

        releases, failure = self._release_list(package_type, name)
        if releases is None:
            return failure

        current = alert.get("current_version") or None
        patched = alert.get("patched_version") or None

        if current:
            behind = self.kdeps.behind_fields(package_type, name, current, releases)
            fields["sca_latest_version"] = behind.get("latest_version")
            for key in ("level", "major", "minor", "patch", "releases"):
                fields[f"sca_behind_{key}"] = behind.get(f"behind_{key}")
            detail = self._detail(package_type, name, current, releases)
            if detail:
                fields["sca_current_age_days"] = detail.get("age_days")

        patched_known = True
        if patched:
            detail = self._detail(package_type, name, patched, releases)
            if detail is None:
                patched_known = False
            else:
                advisories = detail.get("advisories") or []
                scores = [a["cvss3_score"] for a in advisories
                          if a.get("cvss3_score") is not None]
                fields["sca_patched_version_vulnerabilities"] = len(advisories)
                fields["sca_patched_version_max_cvss"] = max(scores) if scores else None
                fields["sca_patched_ghsa_ids"] = [a["id"] for a in advisories]

        if current and patched:
            diff = version_diff(current, patched, package_type)
            if diff is not None:
                fields["sca_patched_diff_level"] = change_level(current, patched, package_type)
                fields["sca_patched_diff_major"] = diff["major"]
                fields["sca_patched_diff_minor"] = diff["minor"]
                fields["sca_patched_diff_patch"] = diff["patch"]

        if not current:
            return NO_CURRENT_VERSION
        if not patched:
            return NO_PATCHED_VERSION
        return OK if patched_known else NOT_FOUND

    def _release_list(self, package_type, name):
        """(releases, failure_status): releases None when the lookup failed."""
        key = (package_type, name)
        if key not in self._releases:
            releases = self.kdeps.release_list(package_type, name)
            failure = None
            if releases is None:
                # Read straight after the request: the query resets
                # last_url_error on each one, so only this lookup's error shows.
                error = getattr(getattr(self.kdeps, "kospex_query", None),
                                "last_url_error", None)
                failure = LOOKUP_ERROR if error is not None else NOT_FOUND
            self._releases[key] = (releases, failure)
        return self._releases[key]

    def _detail(self, package_type, name, version, releases):
        key = (package_type, name, version)
        detail = self._details.get(key, _MISSING)
        if detail is _MISSING:
            detail, _status = self.kdeps.version_detail(
                package_type, name, version, releases, now=self.now)
            self._details[key] = detail
        return detail
