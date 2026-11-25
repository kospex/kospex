"""Enrich Dependabot alerts with upgrade-distance and patched-version facts.

`Enricher.enrich(alert)` returns a copy of the alert with every SCA_FIELDS key
appended (None when unknown), so each output row has the same shape. A row
that cannot be enriched is kept, with the reason in `sca_status`.

The alert's `package_ecosystem`, `package_name`, `current_version` and
`patched_version` keys are read (ALERT_KEYS_READ). Lookups go through
KospexDependencies (deps.dev) and are cached per package and per (package,
version) when deps.dev answered.
"""
import collections
import datetime
import json
import os

from kospex.dependencies.version_diff import change_level, version_diff
from kospex.integrations.osv import queries as osv_queries
from kospex.integrations.osv.client import OsvClient, OsvError
from kospex.prerelease import is_prerelease

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

# The alert keys read, named as the alerts file writer names them (ALERT_KEYS in
# kospex.github_dependabot; a test checks these against it).
_K_ECOSYSTEM = "package_ecosystem"
_K_PACKAGE = "package_name"
_K_CURRENT = "current_version"
_K_PATCHED = "patched_version"
ALERT_KEYS_READ = (_K_ECOSYSTEM, _K_PACKAGE, _K_CURRENT, _K_PATCHED)

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
    "sca_latest_age_days",
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
    "sca_patched_releases",
    "sca_patched_age_days",
    "sca_patched_version_vulnerabilities",
    "sca_patched_version_max_cvss",
    "sca_patched_ghsa_ids",
)

# Added after SCA_FIELDS, only when -next-clean is given. Null when not attempted.
NEXT_CLEAN_FIELDS = (
    "sca_next_clean_version",
    "sca_next_clean_age_days",
    "sca_next_clean_affected_between",
    "sca_next_clean_status",
)

NC_FOUND = "found"
NC_START_CLEAN = "start_clean"
NC_NONE = "none"
NC_NO_CURRENT = "no_current_version"
NC_PRERELEASE = "current_is_prerelease"
NC_NOT_LISTED = "not_in_release_list"
NC_ERROR = "lookup_error"

# Failed OSV batches in a row before a run stops asking OSV (a blocked host would
# otherwise cost the full retry time on every package).
MAX_OSV_FAILURES = 3

# HTTP statuses meaning deps.dev answered: found, or has no such record.
_ANSWERED = (200, 404)


def _deps_dev_version(package_type, version):
    """`version` as deps.dev spells it. Go versions there start with `v`."""
    if package_type == "go" and version and version[:1].isdigit():
        return "v" + version
    return version


class Enricher:
    """Adds SCA_FIELDS to Dependabot alerts using `kdeps` (KospexDependencies)."""

    def __init__(self, kdeps, now=None, next_clean=False, osv_client=None):
        self.kdeps = kdeps
        self.now = now
        self.next_clean = next_clean
        self.osv_stopped = False                       # set after MAX_OSV_FAILURES
        self.next_clean_counts = collections.Counter()  # sca_next_clean_status -> rows
        self._osv = osv_client
        self._osv_failures = 0
        self._releases = {}   # (type, name) -> (releases | None, status)
        self._details = {}    # (type, name, version) -> detail | None
        self._advisory_rows = {}   # (type, name) -> rows | None (OSV did not answer)

    def enrich(self, alert):
        """A new dict: the alert's keys, then every SCA_FIELDS key."""
        out = dict(alert)
        fields = dict.fromkeys(SCA_FIELDS)
        if self.next_clean:
            fields.update(dict.fromkeys(NEXT_CLEAN_FIELDS))
        fields["sca_status"] = self._fill(alert, fields)
        out.update(fields)
        return out

    def _fill(self, alert, fields):
        """Fill `fields` in place and return the status."""
        package_type = ECOSYSTEM_MAP.get(str(alert.get(_K_ECOSYSTEM) or "").lower())
        name = alert.get(_K_PACKAGE)
        if package_type is None:
            return UNSUPPORTED_ECOSYSTEM
        permitted, _reason = self.kdeps.lookup_permitted(package_type, name)
        if not permitted:
            return PRIVATE_SKIPPED

        releases, failure = self._release_list(package_type, name)
        if releases is None:
            return failure

        current = alert.get(_K_CURRENT) or None
        patched = alert.get(_K_PATCHED) or None
        # The spelling deps.dev knows: GitHub writes Go versions without the `v`.
        current_lookup = _deps_dev_version(package_type, current)
        patched_lookup = _deps_dev_version(package_type, patched)

        if current:
            behind = self.kdeps.behind_fields(package_type, name, current_lookup, releases)
            fields["sca_latest_version"] = behind.get("latest_version")
            fields["sca_latest_age_days"] = self._release_age_days(
                releases, behind.get("latest_version"))
            for key in ("level", "major", "minor", "patch", "releases"):
                fields[f"sca_behind_{key}"] = behind.get(f"behind_{key}")
            # Age only: a failed lookup leaves it None and does not change the status.
            detail, _status = self._detail(package_type, name, current_lookup, releases)
            if detail:
                fields["sca_current_age_days"] = detail.get("age_days")

        if self.next_clean:
            self._fill_next_clean(package_type, name, current, current_lookup, releases,
                                  fields)

        patched_status = OK
        if patched:
            detail, status = self._detail(package_type, name, patched_lookup, releases)
            if detail is not None:
                # Known even when the advisory lookup below failed.
                fields["sca_patched_age_days"] = detail.get("age_days")
            if detail is None:
                patched_status = NOT_FOUND if status in _ANSWERED else LOOKUP_ERROR
            elif any(a.get("lookup_failed") for a in detail.get("advisories") or []):
                # A count or maximum over a partial set would understate the risk.
                patched_status = LOOKUP_ERROR
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
            fields["sca_patched_releases"] = self._releases_to_patched(
                package_type, name, current_lookup, patched_lookup, releases)

        if not current:
            return NO_CURRENT_VERSION
        if not patched:
            return NO_PATCHED_VERSION
        return patched_status

    def _fill_next_clean(self, package_type, name, current, current_lookup, releases,
                         fields):
        """Fill the sca_next_clean_* fields: the first stable, non-deprecated release
        above `current` with no known advisories in OSV. Never changes sca_status."""
        status = self._next_clean(package_type, name, current, current_lookup, releases,
                                  fields)
        fields["sca_next_clean_status"] = status
        self.next_clean_counts[status] += 1

    def _next_clean(self, package_type, name, current, current_lookup, releases, fields):
        if not current:
            return NC_NO_CURRENT
        if is_prerelease(package_type, current_lookup):
            return NC_PRERELEASE
        if not any(r["version"] == current_lookup for r in releases):
            return NC_NOT_LISTED
        rows = self._advisory_rows_for(package_type, name, releases)
        if rows is None:
            return NC_ERROR

        def order_key(version):
            return self.kdeps._version_order_key(package_type, version)

        clean = osv_queries.next_clean(rows, current_lookup, order_key)
        fields["sca_next_clean_affected_between"] = clean["skipped_affected"]
        if clean["status"] == "start_clean":
            return NC_START_CLEAN
        if clean["status"] == "none":
            return NC_NONE
        found = clean["release"]["version"]
        fields["sca_next_clean_version"] = found
        fields["sca_next_clean_age_days"] = self._release_age_days(releases, found)
        return NC_FOUND

    def _advisory_rows_for(self, package_type, name, releases):
        """OSV advisory rows for every stable release of the package, or None.

        One OSV batch and one cached fetch per distinct advisory, per package: the
        result is kept for every alert on it. None when OSV did not answer, or when
        the run has stopped asking after MAX_OSV_FAILURES batch failures in a row.
        """
        key = (package_type, name)
        if key in self._advisory_rows:
            return self._advisory_rows[key]
        if self.osv_stopped:
            return None
        ecosystem = osv_queries.ecosystem_for(package_type)
        considered = [r for r in releases if not r["is_prerelease"]]
        spelled = {r["version"]: osv_queries.osv_version(ecosystem, r["version"])
                   for r in considered}
        client = self._osv_client()
        try:
            answers = client.querybatch(ecosystem, name, list(spelled.values()))
        except OsvError:
            self._osv_failures += 1
            if self._osv_failures >= MAX_OSV_FAILURES:
                self.osv_stopped = True
            self._advisory_rows[key] = None
            return None
        self._osv_failures = 0
        ids_by_version = {version: answers.get(osv) for version, osv in spelled.items()}
        records = {}
        for osv_id in sorted({i for ids in ids_by_version.values() for i in ids or []}):
            try:
                records[osv_id] = client.vuln(osv_id)
            except OsvError:
                records[osv_id] = None
        rows = osv_queries.release_rows(considered, ids_by_version, records, ecosystem,
                                        name, current_version=None)
        self._advisory_rows[key] = rows
        return rows

    def _osv_client(self):
        if self._osv is None:
            query = getattr(self.kdeps, "kospex_query", None)
            self._osv = OsvClient(cached_get=getattr(query, "url_request_with_status", None))
        return self._osv

    def _releases_to_patched(self, package_type, name, current, patched, releases):
        """Releases deps.dev lists above `current`, up to and including `patched`.

        Counted like `sca_behind_releases` (by version number, pre-releases
        skipped) but to the first fixed version instead of the latest. None
        when either version is a pre-release (release numbers alone would read
        `1.2.3-rc.1` as 1.2.3), when `patched` is below `current`, or when a
        version has no leading number.
        """
        if is_prerelease(package_type, current) or is_prerelease(package_type, patched):
            return None
        between = self.kdeps.releases_between(
            package_type, name, current, to_version=patched, releases=releases)
        if between is None or between["level"] == "downgrade":
            return None
        return between["releases"]

    def _release_age_days(self, releases, version):
        """Whole days since `version` was published, from the release list, or None.

        No request: the list already carries publish dates. None when the
        version is not in the list or deps.dev lists it without a date.
        """
        release = next((r for r in releases or () if r.get("version") == version), None)
        if release is None:
            return None
        now = self.now or datetime.datetime.now(datetime.timezone.utc)
        return self.kdeps._age_days(release.get("published_at"), now)

    def _release_list(self, package_type, name):
        """(releases, failure_status): releases None when the lookup failed.

        not_found means deps.dev answered 404 (or 200 with no versions); any
        other failure is lookup_error and is not cached, so a later alert for
        the same package retries.
        """
        key = (package_type, name)
        if key in self._releases:
            return self._releases[key]
        releases = self.kdeps.release_list(package_type, name)
        if releases is not None:
            result = (releases, None)
        else:
            # Read straight after the request: the query resets both on each one.
            query = getattr(self.kdeps, "kospex_query", None)
            error = getattr(query, "last_url_error", None)
            status = getattr(query, "last_url_status", None)
            if error is None and status in _ANSWERED:
                result = (None, NOT_FOUND)
            else:
                return None, LOOKUP_ERROR
        self._releases[key] = result
        return result

    def _detail(self, package_type, name, version, releases):
        """(detail | None, status). Only answered lookups are cached."""
        key = (package_type, name, version)
        if key in self._details:
            return self._details[key]
        detail, status = self.kdeps.version_detail(
            package_type, name, version, releases, now=self.now)
        result = (detail, status)
        failed = detail is not None and any(
            a.get("lookup_failed") for a in detail.get("advisories") or [])
        if not failed and (detail is not None or status in _ANSWERED):
            self._details[key] = result
        return result


class DependabotInputError(Exception):
    """A line of the input file is unusable. `line_no` is 1-based."""

    def __init__(self, line_no, msg):
        super().__init__(f"line {line_no}: {msg}")
        self.line_no = line_no


def _check_alert_types(line_no, alert):
    """Raise DependabotInputError for a valid-JSON line the enricher cannot read.

    The message names the key and the type, never the value.
    """
    name = alert.get(_K_PACKAGE)
    if not isinstance(name, str) or not name:
        got = ("a missing key" if _K_PACKAGE not in alert
               else "null" if name is None else type(name).__name__)
        raise DependabotInputError(
            line_no, f"{_K_PACKAGE} must be a non-empty string, got {got}")
    for key in (_K_CURRENT, _K_PATCHED):
        value = alert.get(key)
        if value is not None and not isinstance(value, str):
            raise DependabotInputError(
                line_no, f"{key} must be a string or null, got {type(value).__name__}")


def enrich_file(in_path, out_path, enricher, on_progress=None):
    """Enrich a JSONL file of Dependabot alerts into `out_path`.

    Returns a Counter of `sca_status` over the rows written. On any error the
    partial output is closed and deleted before the error is re-raised, so a
    half-written file never looks complete. Raises ValueError, before the
    input is opened or the output touched, when both paths are the same file.
    """
    if os.path.realpath(in_path) == os.path.realpath(out_path) or (
            os.path.exists(out_path) and os.path.exists(in_path)
            and os.path.samefile(in_path, out_path)):
        raise ValueError("output path must differ from the input path")

    counts = collections.Counter()
    out = None
    opened = False
    try:
        # Binary + per-line decode: text-mode decoding is chunked, so it
        # cannot say which line held the bad bytes.
        with open(in_path, "rb") as src:
            out = open(out_path, "w", encoding="utf-8", newline="\n")
            opened = True
            for line_no, raw in enumerate(src, start=1):
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise DependabotInputError(
                        line_no, f"not valid UTF-8: {exc}") from exc
                line = line.rstrip("\r\n")
                if not line.strip():
                    raise DependabotInputError(line_no, "blank line")
                try:
                    alert = json.loads(line)
                except ValueError as exc:
                    raise DependabotInputError(line_no, f"invalid JSON: {exc}") from exc
                if not isinstance(alert, dict):
                    raise DependabotInputError(line_no, "not a JSON object")
                _check_alert_types(line_no, alert)
                row = enricher.enrich(alert)
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                status = row["sca_status"]
                counts[status] += 1
                if on_progress:
                    on_progress(line_no, status)
            out.close()
            out = None
    except BaseException:
        if out is not None:
            out.close()
        if opened:   # never delete a file we did not truncate
            try:
                os.remove(out_path)
            except FileNotFoundError:
                pass
        raise
    return counts
