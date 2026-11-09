"""`kgit github --dependabot`: open Dependabot alerts to JSONL, plus a per-repo status file.

One request per repo: GET /repos/{owner}/{repo}/dependabot/alerts?state=open.
Its response is both the alerts and the repo's Dependabot status, because the
org-wide alerts endpoint can't say which repos have Dependabot turned off or
are hidden from the token. Seeing alerts needs Write, Maintain or Admin on the
repo, or the Security Manager role: more than read access.

Paging is by cursor only. These endpoints return 400 for any `page=` parameter,
even page=1 (measured 2026-11-09), so the next page comes from the Link header.

Two files, both with every key on every line (sqlite-utils infers columns from
the first 100 rows):

- alerts: one flat line per open alert (ALERT_KEYS), keyed by
  (repo_id, alert_number). repo_id / github_repo_id join to the properties export.
- status: one line per repo (STATUS_KEYS). `dependabot_status` is enabled,
  disabled, no_access, not_found or error; `open_alerts` is a count only when
  enabled, otherwise null because it isn't known.
"""
from kospex_git import KospexGit
from kospex.github_properties import GITHUB_HOST, RateLimited, _error_text

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_NO_ACCESS = "no_access"
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR = "error"

# The two 403s differ only in their message, so these are matched exactly.
# Any other 403 is an error, never guessed.
DISABLED_MESSAGE = "Dependabot alerts are disabled for this repository."
NO_ACCESS_MESSAGES = (
    "You are not authorized to perform this operation.",
    # Fine-grained token without "Dependabot alerts: Read" (per GitHub's docs, unmeasured).
    "Resource not accessible by personal access token",
)

ALERT_KEYS = (
    "repo_id", "github_repo_id", "org", "repo",
    "manifest_path", "package_ecosystem", "package_name", "relationship", "scope",
    "version_requirement", "current_version", "vulnerable_range", "patched_version",
    "ghsa_id", "cve_id", "summary", "severity", "cwes", "published_at",
    "cvss_v3_score", "cvss_v3_vector", "cvss_v4_score", "cvss_v4_vector",
    "epss_percentage", "epss_percentile",
    "alert_number", "alert_url", "state", "created_at", "updated_at",
    "fetched_at",
)

_RANGE_CHARS = set("<>=~^!, ")


def _message(response):
    try:
        return response.json().get("message")
    except (ValueError, AttributeError):
        return None


def classify_failure(response):
    """(status, error text) for a failed alerts request. Error text is None for disabled."""
    if response is None:
        return STATUS_ERROR, "no response"
    message = _message(response)
    if response.status_code == 404:
        return STATUS_NOT_FOUND, _error_text(response)
    if response.status_code == 403:
        if message == DISABLED_MESSAGE:
            return STATUS_DISABLED, None
        if message in NO_ACCESS_MESSAGES:
            return STATUS_NO_ACCESS, _error_text(response)
    return STATUS_ERROR, _error_text(response)


def fetch_repo_alerts(gh, owner, repo):
    """(status, alerts or None, error) for one repo's open alerts, every page.

    A failure on a later page makes the whole repo an error with no alerts: a
    partial list must never pass as complete. Raises RateLimited.
    """
    url = f"{gh.base_url}/repos/{owner}/{repo}/dependabot/alerts"
    params = {"state": "open", "per_page": 100}
    alerts, page = [], 1
    while url:
        data, response = gh._make_request(url, params=params)
        if gh.throttled:
            raise RateLimited(f"{owner}/{repo}")
        if data is None:
            if page == 1:
                status, error = classify_failure(response)
                return status, None, error
            _, error = classify_failure(response)
            return STATUS_ERROR, None, f"{error} (page {page})"
        alerts.extend(data)
        # The cursor URL carries the whole query, so no params after the first page.
        url = response.links.get("next", {}).get("url")
        params = None
        page += 1
    return STATUS_ENABLED, alerts, None


def current_version(version_requirement):
    """The installed version, when the requirement names exactly one.

    "= 2.2.0" (pip exact pin) and "0.4.0" (npm lockfile, the resolved version)
    both give the version. A range, or "" (an unpinned pip requirement), gives None.
    """
    if not version_requirement:
        return None
    version = version_requirement.removeprefix("= ").strip()
    if not version or _RANGE_CHARS & set(version):
        return None
    return version


def _cvss(severities, key):
    """(score, vector). An unscored version is reported as score 0.0 with no
    vector (measured), so a missing vector means both are unknown."""
    entry = (severities or {}).get(key) or {}
    vector = entry.get("vector_string")
    if not vector:
        return None, None
    return entry.get("score"), vector


def alert_line(alert, owner, repo, github_repo_id, fetched_at):
    dep = alert.get("dependency") or {}
    pkg = dep.get("package") or {}
    vuln = alert.get("security_vulnerability") or {}
    adv = alert.get("security_advisory") or {}
    epss = adv.get("epss") or {}
    v3_score, v3_vector = _cvss(adv.get("cvss_severities"), "cvss_v3")
    v4_score, v4_vector = _cvss(adv.get("cvss_severities"), "cvss_v4")
    line = {
        "repo_id": KospexGit.generate_repo_id(GITHUB_HOST, owner, repo),
        "github_repo_id": github_repo_id,
        "org": owner,
        "repo": repo,
        "manifest_path": dep.get("manifest_path"),
        "package_ecosystem": pkg.get("ecosystem"),
        "package_name": pkg.get("name"),
        "relationship": dep.get("relationship"),
        "scope": dep.get("scope"),
        "version_requirement": dep.get("version_requirement"),
        "current_version": current_version(dep.get("version_requirement")),
        "vulnerable_range": vuln.get("vulnerable_version_range"),
        "patched_version": (vuln.get("first_patched_version") or {}).get("identifier"),
        "ghsa_id": adv.get("ghsa_id"),
        "cve_id": adv.get("cve_id"),
        "summary": adv.get("summary"),
        "severity": adv.get("severity"),
        "cwes": [c["cwe_id"] for c in adv.get("cwes") or [] if c.get("cwe_id")],
        "published_at": adv.get("published_at"),
        "cvss_v3_score": v3_score,
        "cvss_v3_vector": v3_vector,
        "cvss_v4_score": v4_score,
        "cvss_v4_vector": v4_vector,
        "epss_percentage": epss.get("percentage"),
        "epss_percentile": epss.get("percentile"),
        "alert_number": alert.get("number"),
        "alert_url": alert.get("html_url"),
        "state": alert.get("state"),
        "created_at": alert.get("created_at"),
        "updated_at": alert.get("updated_at"),
        "fetched_at": fetched_at,
    }
    return line
