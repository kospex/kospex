"""`kgit github --dependabot`: open Dependabot alerts to JSONL, plus a per-repo status file.

The alert file is loaded by other scripts, so its shape is the contract: one
flat line per open alert, every key on every line, and the same `repo_id` /
`github_repo_id` as the custom properties export so the two join.
"""
import json

import pytest
import requests

import kospex.github_dependabot as GD
import kospex.github_properties as GP
from kospex_github import KospexGithub

FETCHED_AT = "2026-11-09T03:12:00Z"
BASE = "https://api.github.com/repos"


def response(status, body=None, link=None):
    r = requests.Response()
    r.status_code = status
    if body is not None:
        r._content = json.dumps(body).encode()
    if link:
        r.headers["Link"] = f'<{link}>; rel="next"'
    return r


def alert(number=1, **over):
    """An alert in the shape the repo alerts endpoint returns (measured 2026-11-09)."""
    a = {
        "number": number,
        "state": "open",
        "html_url": f"https://github.com/acme/payments/security/dependabot/{number}",
        "created_at": "2026-11-01T00:00:00Z",
        "updated_at": "2026-11-02T00:00:00Z",
        "dependency": {
            "package": {"ecosystem": "pip", "name": "requests"},
            "manifest_path": "requirements.txt",
            "scope": "runtime",
            "relationship": "direct",
            "version_requirement": "= 2.19.0",
        },
        "security_vulnerability": {
            "vulnerable_version_range": "< 2.20.0",
            "first_patched_version": {"identifier": "2.20.0"},
        },
        "security_advisory": {
            "ghsa_id": "GHSA-x84v-xcm2-53pg",
            "cve_id": "CVE-2018-18074",
            "summary": "Insufficiently Protected Credentials in Requests",
            "severity": "high",
            "published_at": "2018-10-29T19:06:46Z",
            "cwes": [{"cwe_id": "CWE-522", "name": "Insufficiently Protected Credentials"}],
            "cvss_severities": {
                "cvss_v3": {"vector_string": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                            "score": 9.8},
                "cvss_v4": {"vector_string": None, "score": 0.0},
            },
            "epss": {"percentage": 0.01134, "percentile": 0.66524},
        },
    }
    a.update(over)
    return a


def scripted(gh, monkeypatch, replies):
    """Fake _make_request: replies is a list of (data, response), consumed in order."""
    calls = []

    def fake(url, params=None, method="GET"):
        calls.append((url, params))
        return replies.pop(0)

    monkeypatch.setattr(gh, "_make_request", fake)
    return calls


class TestClassifyFailure:
    def test_404_is_not_found(self):
        assert GD.classify_failure(response(404, {"message": "Not Found"})) == (
            "not_found", "HTTP 404 Not Found")

    def test_disabled_403(self):
        r = response(403, {"message": "Dependabot alerts are disabled for this repository."})
        assert GD.classify_failure(r) == ("disabled", None)

    def test_not_authorized_403_is_no_access(self):
        r = response(403, {"message": "You are not authorized to perform this operation."})
        assert GD.classify_failure(r) == (
            "no_access", "HTTP 403 You are not authorized to perform this operation.")

    def test_fine_grained_403_is_no_access(self):
        r = response(403, {"message": "Resource not accessible by personal access token"})
        assert GD.classify_failure(r)[0] == "no_access"

    def test_unknown_403_is_error(self):
        assert GD.classify_failure(response(403, {"message": "Something else"})) == (
            "error", "HTTP 403 Something else")

    def test_no_response_is_error(self):
        assert GD.classify_failure(None) == ("error", "no response")


class TestFetchRepoAlerts:
    def test_first_request_asks_for_open_alerts(self, monkeypatch):
        gh = KospexGithub()
        calls = scripted(gh, monkeypatch, [([], response(200, []))])
        GD.fetch_repo_alerts(gh, "acme", "payments")
        url, params = calls[0]
        assert url == f"{BASE}/acme/payments/dependabot/alerts"
        assert params == {"state": "open", "per_page": 100}

    def test_follows_next_link_until_none(self, monkeypatch):
        gh = KospexGithub()
        nxt = "https://api.github.com/repositories/1/dependabot/alerts?state=open&per_page=100&after=X"
        page1 = [alert(n) for n in range(100)]
        page2 = [alert(n) for n in range(100, 103)]
        calls = scripted(gh, monkeypatch, [(page1, response(200, [], link=nxt)),
                                           (page2, response(200, []))])
        status, alerts, error = GD.fetch_repo_alerts(gh, "acme", "payments")
        assert (status, len(alerts), error) == ("enabled", 103, None)
        assert calls[1] == (nxt, None)

    def test_enabled_with_no_alerts(self, monkeypatch):
        gh = KospexGithub()
        scripted(gh, monkeypatch, [([], response(200, []))])
        assert GD.fetch_repo_alerts(gh, "acme", "payments") == ("enabled", [], None)

    def test_first_page_failure_is_classified(self, monkeypatch):
        gh = KospexGithub()
        scripted(gh, monkeypatch, [(None, response(
            403, {"message": "Dependabot alerts are disabled for this repository."}))])
        assert GD.fetch_repo_alerts(gh, "acme", "payments") == ("disabled", None, None)

    def test_later_page_failure_is_error_without_alerts(self, monkeypatch):
        gh = KospexGithub()
        nxt = "https://api.github.com/repositories/1/dependabot/alerts?after=X"
        scripted(gh, monkeypatch, [([alert(1)], response(200, [], link=nxt)),
                                   (None, response(502, {"message": "Bad Gateway"}))])
        assert GD.fetch_repo_alerts(gh, "acme", "payments") == (
            "error", None, "HTTP 502 Bad Gateway (page 2)")

    def test_rate_limit_raises(self, monkeypatch):
        gh = KospexGithub()

        def throttled(url, params=None, method="GET"):
            gh.throttled = True
            return None, response(429)

        monkeypatch.setattr(gh, "_make_request", throttled)
        with pytest.raises(GP.RateLimited):
            GD.fetch_repo_alerts(gh, "acme", "payments")


class TestCurrentVersion:
    def test_exact_pin(self):
        assert GD.current_version("= 2.2.0") == "2.2.0"

    def test_bare_lockfile_version(self):
        # npm lockfile alerts carry the resolved version with no operator (measured).
        assert GD.current_version("0.4.0") == "0.4.0"

    def test_range_is_none(self):
        assert GD.current_version(">= 2.10, < 3.0") is None
        assert GD.current_version("< 1.25") is None
        assert GD.current_version("^4.17.1") is None
        assert GD.current_version("~= 1.4") is None

    def test_empty_and_none_are_none(self):
        # A ranged pip requirement comes back as "" (measured).
        assert GD.current_version("") is None
        assert GD.current_version(None) is None


class TestAlertLine:
    def line(self, a=None):
        return GD.alert_line(a or alert(7), "Acme", "Payments", 101, FETCHED_AT)

    def test_keys_and_order(self):
        assert tuple(self.line()) == GD.ALERT_KEYS

    def test_fields_map_from_alert(self):
        line = self.line()
        assert line["repo_id"] == "github.com~acme~payments"
        assert line["github_repo_id"] == 101
        assert (line["org"], line["repo"]) == ("Acme", "Payments")
        assert line["manifest_path"] == "requirements.txt"
        assert (line["package_ecosystem"], line["package_name"]) == ("pip", "requests")
        assert (line["relationship"], line["scope"]) == ("direct", "runtime")
        assert line["version_requirement"] == "= 2.19.0"
        assert line["current_version"] == "2.19.0"
        assert line["vulnerable_range"] == "< 2.20.0"
        assert line["patched_version"] == "2.20.0"
        assert (line["ghsa_id"], line["cve_id"]) == ("GHSA-x84v-xcm2-53pg", "CVE-2018-18074")
        assert line["severity"] == "high"
        assert line["published_at"] == "2018-10-29T19:06:46Z"
        assert line["cvss_v3_score"] == 9.8
        assert line["cvss_v3_vector"].startswith("CVSS:3.1/")
        assert (line["epss_percentage"], line["epss_percentile"]) == (0.01134, 0.66524)
        assert line["alert_number"] == 7
        assert line["alert_url"].endswith("/dependabot/7")
        assert line["state"] == "open"
        assert (line["created_at"], line["updated_at"]) == ("2026-11-01T00:00:00Z",
                                                             "2026-11-02T00:00:00Z")
        assert line["fetched_at"] == FETCHED_AT

    def test_cwes_are_ids(self):
        assert self.line()["cwes"] == ["CWE-522"]
        a = alert()
        a["security_advisory"]["cwes"] = []
        assert self.line(a)["cwes"] == []

    def test_absent_cvss_is_null(self):
        # GitHub reports an unscored version as score 0.0 with no vector (measured).
        line = self.line()
        assert line["cvss_v4_score"] is None
        assert line["cvss_v4_vector"] is None

    def test_zero_score_with_vector_is_kept(self):
        a = alert()
        a["security_advisory"]["cvss_severities"]["cvss_v3"] = {
            "vector_string": "CVSS:3.1/AV:N/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:N", "score": 0.0}
        assert self.line(a)["cvss_v3_score"] == 0.0

    def test_missing_cvss_severities_and_epss(self):
        a = alert()
        del a["security_advisory"]["cvss_severities"]
        del a["security_advisory"]["epss"]
        line = self.line(a)
        for key in ("cvss_v3_score", "cvss_v3_vector", "cvss_v4_score", "cvss_v4_vector",
                    "epss_percentage", "epss_percentile"):
            assert line[key] is None, key

    def test_no_fix_patched_version_is_none(self):
        a = alert()
        a["security_vulnerability"]["first_patched_version"] = None
        assert self.line(a)["patched_version"] is None
