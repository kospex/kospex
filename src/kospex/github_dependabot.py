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
import json
import os
import re
from collections import Counter

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

_VERSION = re.compile(r"v?\d+(\.\d+)*([-+.]?[0-9A-Za-z][0-9A-Za-z.+-]*)?")
_WILDCARD_SEGMENT = re.compile(r"(^|\.)[xX*](\.|$)")


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
        # 403 and 404 become the repo's status, so they don't print as errors.
        data, response = gh._make_request(url, params=params, quiet_statuses=(403, 404))
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
    # Accept only what looks like one concrete version: digits first, then
    # pre-release / build suffixes. Wildcards (*, 1.x), dist-tags (latest),
    # bracketed ranges and unresolved placeholders are not an installed version.
    if not _VERSION.fullmatch(version) or _WILDCARD_SEGMENT.search(version):
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


STATUS_KEYS = ("repo_id", "github_repo_id", "org", "repo",
               "dependabot_status", "open_alerts", "error", "fetched_at")

# An -org run stops if this many repos in a row from the start are no_access:
# the token can't see alerts, and the full walk would only confirm it.
EARLY_STOP_REPOS = 5

ACCESS_NEEDED = ("Seeing Dependabot alerts needs Write, Maintain, Admin or Security Manager "
                 "on the repo, which is more than read access.")


class NoAlertAccess(Exception):
    """The first repos of an -org run were all no_access: stop before the full walk."""


def status_line(owner, repo, github_repo_id, status, open_alerts, error, fetched_at):
    return {
        "repo_id": KospexGit.generate_repo_id(GITHUB_HOST, owner, repo),
        "github_repo_id": github_repo_id,
        "org": owner,
        "repo": repo,
        "dependabot_status": status,
        "open_alerts": open_alerts,
        "error": error,
        "fetched_at": fetched_at,
    }


def collect(gh, targets, fetched_at, early_stop, on_repo=None):
    """(alert lines, status lines) for (owner, repo, github_repo_id) targets, in order.

    on_repo(index, total, status_line, alert_lines), if given, is called as each
    repo finishes (index from 1), so a caller can report progress and write the
    lines before the run ends. It sees every repo that finished before a raise.

    Raises RateLimited, or NoAlertAccess when early_stop is set and the first
    min(EARLY_STOP_REPOS, len(targets)) repos are all no_access.
    """
    alert_lines, status_lines = [], []
    total = len(targets)
    check_at = min(EARLY_STOP_REPOS, total)
    for index, (owner, repo, github_repo_id) in enumerate(targets, start=1):
        status, alerts, error = fetch_repo_alerts(gh, owner, repo)
        open_alerts = len(alerts) if status == STATUS_ENABLED else None
        line = status_line(owner, repo, github_repo_id, status, open_alerts, error, fetched_at)
        repo_alerts = [alert_line(a, owner, repo, github_repo_id, fetched_at)
                       for a in alerts or []]
        status_lines.append(line)
        alert_lines.extend(repo_alerts)
        if on_repo:
            on_repo(index, total, line, repo_alerts)
        if early_stop and len(status_lines) == check_at and all(
                s["dependabot_status"] == STATUS_NO_ACCESS for s in status_lines):
            raise NoAlertAccess(f"the first {check_at} repos were all no_access")
    return alert_lines, status_lines


def progress_line(index, total, line):
    """One repo's result for the console and kgit.log: "[412/1300] acme/payments: enabled, 7 open alerts"."""
    text = f"[{index}/{total}] {line['org']}/{line['repo']}: {line['dependabot_status']}"
    if line["dependabot_status"] == STATUS_ENABLED:
        n = line["open_alerts"]
        text += f", {n} open alert{'' if n == 1 else 's'}"
    elif line["error"]:
        text += f" ({line['error']})"
    return text


PARTIAL_SUFFIX = ".partial"


class RunFiles:
    """The alerts and status files, written one repo at a time.

    Lines go to `<name>.partial` and are flushed after each repo, so an
    interrupted run (rate limit, Ctrl-C, a dropped session) keeps every repo it
    finished. Only finish() renames them to the final names, so a file without
    `.partial` is always a complete run, and a previous complete file is left
    alone until a new run completes.
    """

    def __init__(self, alerts_path, status_path):
        self.alerts_path = os.fspath(alerts_path)
        self.status_path = os.fspath(status_path)
        self.alerts_partial = self.alerts_path + PARTIAL_SUFFIX
        self.status_partial = self.status_path + PARTIAL_SUFFIX
        self.repos = 0
        self.alerts = 0
        self._files = None

    def __enter__(self):
        alerts_file = open(self.alerts_partial, "w", encoding="utf-8")
        try:
            status_file = open(self.status_partial, "w", encoding="utf-8")
        except BaseException:
            alerts_file.close()
            raise
        self._files = (alerts_file, status_file)
        return self

    def add(self, status, alert_lines):
        alerts_file, status_file = self._files
        for a in alert_lines:
            alerts_file.write(json.dumps(a, ensure_ascii=False) + "\n")
        status_file.write(json.dumps(status, ensure_ascii=False) + "\n")
        alerts_file.flush()
        status_file.flush()
        self.repos += 1
        self.alerts += len(alert_lines)

    def _close(self):
        if self._files:
            for f in self._files:
                f.close()
            self._files = None

    def finish(self):
        self._close()
        os.replace(self.alerts_partial, self.alerts_path)
        os.replace(self.status_partial, self.status_path)

    def __exit__(self, *exc):
        self._close()
        return False


def status_filename(filename):
    """alerts.jsonl -> alerts-status.jsonl; a name with no extension gets -status.jsonl."""
    path = os.fspath(filename)
    root, ext = os.path.splitext(path)
    return f"{root}-status{ext or '.jsonl'}"


def summarise(alert_lines, status_lines):
    return {
        "repos": len(status_lines),
        "alerts": len(alert_lines),
        "by_status": dict(Counter(s["dependabot_status"] for s in status_lines)),
        "by_severity": dict(Counter(a["severity"] for a in alert_lines)),
    }


def notes(status_lines, org_scope):
    """What the counts can't show, for the console after a run."""
    out = []
    if org_scope:
        out.append("Only repos this token can see are included. If the token isn't an "
                   "org member, that's public repos only: private and internal repos "
                   "are left out, with no error.")
    no_access = sum(1 for s in status_lines if s["dependabot_status"] == STATUS_NO_ACCESS)
    if status_lines and no_access * 2 > len(status_lines):
        out.append(f"{no_access} of {len(status_lines)} repos are no_access. {ACCESS_NEEDED}")
    out.append("No alerts doesn't mean no vulnerable dependencies: Dependabot can't match "
               "a ranged or unpinned requirement (e.g. requirements.txt without ==) "
               "unless the advisory affects every version.")
    return out
