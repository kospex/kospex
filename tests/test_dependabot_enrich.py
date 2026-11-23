"""Enricher for Dependabot alerts (`kospex sca -dependabot`).

The Enricher is driven with a stub of KospexDependencies: no HTTP.
"""
import copy

from kospex.dependabot_enrich import (
    ALERT_KEYS_READ, ECOSYSTEM_MAP, SCA_FIELDS, Enricher, OK, NO_CURRENT_VERSION,
    NO_PATCHED_VERSION, PRIVATE_SKIPPED, UNSUPPORTED_ECOSYSTEM, NOT_FOUND,
    LOOKUP_ERROR,
)
from kospex.github_dependabot import ALERT_KEYS, alert_line


class _Query:
    last_url_error = None
    last_url_status = 404


class StubDeps:
    def __init__(self, latest="0.7.2", advisories=None, releases=True,
                 permitted=(True, ""), unknown_versions=(), release_status=404, detail_status=404):
        self.latest = latest
        self.advisories = advisories or {}
        self.releases = releases
        self.permitted = permitted
        self.unknown_versions = unknown_versions
        self.detail_status = detail_status
        self.kospex_query_status = release_status
        self.kospex_query = _Query()
        self.calls = {"release_list": [], "behind_fields": [],
                      "version_detail": [], "lookup_permitted": []}

    def lookup_permitted(self, package_type, name):
        self.calls["lookup_permitted"].append((package_type, name))
        return self.permitted

    def release_list(self, package_type, name, cache=None):
        self.calls["release_list"].append((package_type, name))
        self.kospex_query.last_url_status = (200 if self.releases
                                             else self.kospex_query_status)
        return [{"version": self.latest}] if self.releases else None

    def behind_fields(self, package_type, name, version, releases=None):
        self.calls["behind_fields"].append((package_type, name, version))
        return {"latest_version": self.latest, "behind_level": "minor",
                "behind_major": 0, "behind_minor": 3, "behind_patch": 2,
                "behind_other": 0, "behind_releases": 5,
                "behind_on_major": 0, "latest_on_major": 0}

    def version_detail(self, package_type, name, version, releases, **_kw):
        self.calls["version_detail"].append((package_type, name, version))
        if version in self.unknown_versions:
            return None, self.detail_status
        return {"version": version, "age_days": 400,
                "advisories": self.advisories.get(version, [])}, 200


def _alert(**over):
    """An alert with exactly the keys `kgit github --dependabot` writes."""
    alert = dict.fromkeys(ALERT_KEYS)
    alert.update({"package_ecosystem": "npm", "package_name": "cookie",
                  "current_version": "0.4.0", "patched_version": "0.7.0"})
    for key in over:
        assert key in alert, f"{key} is not an alert key"
    alert.update(over)
    return alert


def test_real_alert_line_is_enriched():
    """An alert built by the production writer, not by hand."""
    api_alert = {
        "dependency": {"package": {"ecosystem": "npm", "name": "cookie"},
                       "manifest_path": "package.json",
                       "version_requirement": "0.4.0"},
        "security_vulnerability": {"first_patched_version": {"identifier": "0.7.0"}},
        "security_advisory": {"ghsa_id": "GHSA-pxg6-pf52-xh8x"},
    }
    line = alert_line(api_alert, "acme", "payments", 1, "2026-11-24T00:00:00Z")
    assert line["package_ecosystem"] == "npm" and line["current_version"] == "0.4.0"
    out = _enrich(line)
    assert out["sca_status"] == OK
    assert out["sca_patched_diff_level"] == "minor"


def test_alert_keys_read_are_written_by_kgit_github():
    assert ALERT_KEYS_READ
    assert set(ALERT_KEYS_READ) <= set(ALERT_KEYS)


TWO = [{"id": "GHSA-aaaa-bbbb-cccc", "cvss3_score": 5.3},
       {"id": "GHSA-dddd-eeee-ffff", "cvss3_score": None}]


def _enrich(alert, **kw):
    return Enricher(StubDeps(**kw)).enrich(alert)


def test_all_keys_present_and_input_keys_first():
    alert = _alert()
    alert["extra"] = "x"
    before = copy.deepcopy(alert)
    out = _enrich(alert)
    assert list(out) == list(alert) + list(SCA_FIELDS)
    assert alert == before
    assert len(SCA_FIELDS) == 15


def test_ok_row():
    out = _enrich(_alert(), advisories={"0.7.0": TWO})
    assert out["sca_status"] == OK == "ok"
    assert out["sca_patched_diff_level"] == "minor"
    assert (out["sca_patched_diff_major"], out["sca_patched_diff_minor"],
            out["sca_patched_diff_patch"]) == (0, 3, 0)
    assert out["sca_patched_version_vulnerabilities"] == 2
    assert out["sca_patched_version_max_cvss"] == 5.3
    assert out["sca_patched_ghsa_ids"] == ["GHSA-aaaa-bbbb-cccc", "GHSA-dddd-eeee-ffff"]
    assert out["sca_latest_version"] == "0.7.2"
    assert out["sca_behind_level"] == "minor"
    assert out["sca_behind_releases"] == 5
    assert out["sca_current_age_days"] == 400


def test_raw_diff_preserved():
    out = _enrich(_alert(current_version="1.9.9", patched_version="3.0.0"))
    assert out["sca_patched_diff_level"] == "major"
    assert (out["sca_patched_diff_major"], out["sca_patched_diff_minor"],
            out["sca_patched_diff_patch"]) == (2, -9, -9)


def test_current_version_null():
    out = _enrich(_alert(current_version=None), advisories={"0.7.0": TWO})
    assert out["sca_status"] == NO_CURRENT_VERSION == "no_current_version"
    for key in ("sca_patched_diff_level", "sca_patched_diff_major",
                "sca_behind_level", "sca_behind_major", "sca_behind_releases",
                "sca_current_age_days"):
        assert out[key] is None
    assert out["sca_patched_version_vulnerabilities"] == 2
    assert out["sca_patched_version_max_cvss"] == 5.3


def test_patched_version_null():
    out = _enrich(_alert(patched_version=None))
    assert out["sca_status"] == NO_PATCHED_VERSION == "no_patched_version"
    for key in ("sca_patched_diff_level", "sca_patched_version_vulnerabilities",
                "sca_patched_version_max_cvss", "sca_patched_ghsa_ids"):
        assert out[key] is None
    assert out["sca_behind_level"] == "minor"
    assert out["sca_latest_version"] == "0.7.2"


def test_unsupported_ecosystem():
    stub = StubDeps()
    out = Enricher(stub).enrich(_alert(package_ecosystem="composer"))
    assert out["sca_status"] == UNSUPPORTED_ECOSYSTEM == "unsupported_ecosystem"
    assert all(not calls for calls in stub.calls.values())
    assert all(out[k] is None for k in SCA_FIELDS if k != "sca_status")


def test_private_skipped():
    stub = StubDeps(permitted=(False, "maven:com.acme"))
    out = Enricher(stub).enrich(_alert(package_ecosystem="maven", package_name="com.acme:x"))
    assert out["sca_status"] == PRIVATE_SKIPPED == "private_skipped"
    assert stub.calls["lookup_permitted"] == [("maven", "com.acme:x")]
    assert stub.calls["release_list"] == []
    assert stub.calls["version_detail"] == []


def test_unknown_package():
    out = _enrich(_alert(), releases=False)
    assert out["sca_status"] == NOT_FOUND == "not_found"
    assert out["sca_latest_version"] is None


def test_lookup_error_not_stale_across_packages():
    stub = StubDeps(releases=False)
    stub.kospex_query.last_url_error = ConnectionError("boom")
    enricher = Enricher(stub)
    assert enricher.enrich(_alert())["sca_status"] == LOOKUP_ERROR == "lookup_error"
    # A later plain 404 for another package must not inherit the old error.
    stub.kospex_query.last_url_error = None
    assert enricher.enrich(_alert(package_name="other"))["sca_status"] == NOT_FOUND


def test_patched_version_unknown_to_deps_dev_is_not_found():
    out = _enrich(_alert(), unknown_versions=("0.7.0",))
    assert out["sca_status"] == NOT_FOUND
    assert out["sca_patched_version_vulnerabilities"] is None
    assert out["sca_patched_diff_level"] == "minor"


def test_ecosystem_case_insensitive():
    stub = StubDeps()
    out = Enricher(stub).enrich(_alert(package_ecosystem="PIP", package_name="requests"))
    assert stub.calls["release_list"] == [("pypi", "requests")]
    assert out["sca_status"] == OK
    assert ECOSYSTEM_MAP == {"pip": "pypi", "npm": "npm", "maven": "maven",
                             "nuget": "nuget", "go": "go",
                             "rubygems": "rubygems", "rust": "cargo"}


def test_zero_advisories():
    out = _enrich(_alert(), advisories={"0.7.0": []})
    assert out["sca_patched_version_vulnerabilities"] == 0
    assert out["sca_patched_version_max_cvss"] is None
    assert out["sca_patched_ghsa_ids"] == []


def test_caching():
    stub = StubDeps()
    enricher = Enricher(stub)
    for _ in range(3):
        enricher.enrich(_alert())
    assert len(stub.calls["release_list"]) == 1
    assert sorted(stub.calls["version_detail"]) == [
        ("npm", "cookie", "0.4.0"), ("npm", "cookie", "0.7.0")]


def test_negative_detail_cached():
    stub = StubDeps(unknown_versions=("0.7.0",))
    enricher = Enricher(stub)
    for _ in range(2):
        enricher.enrich(_alert())
    assert stub.calls["version_detail"].count(("npm", "cookie", "0.7.0")) == 1


import pytest


@pytest.mark.parametrize("status", [429, 503, 500, 403, None])
def test_release_list_transient_is_lookup_error(status):
    out = _enrich(_alert(), releases=False, release_status=status)
    assert out["sca_status"] == LOOKUP_ERROR


@pytest.mark.parametrize("status", [429, 503])
def test_patched_detail_transient_is_lookup_error(status):
    out = _enrich(_alert(), unknown_versions=("0.7.0",), detail_status=status)
    assert out["sca_status"] == LOOKUP_ERROR
    assert out["sca_patched_version_vulnerabilities"] is None


def test_patched_detail_404_is_not_found():
    out = _enrich(_alert(), unknown_versions=("0.7.0",), detail_status=404)
    assert out["sca_status"] == NOT_FOUND


def test_transient_release_failure_is_retried_but_404_is_not():
    stub = StubDeps(releases=False, release_status=503)
    enricher = Enricher(stub)
    enricher.enrich(_alert())
    enricher.enrich(_alert())
    assert len(stub.calls["release_list"]) == 2
    stub = StubDeps(releases=False, release_status=404)
    enricher = Enricher(stub)
    enricher.enrich(_alert())
    enricher.enrich(_alert())
    assert len(stub.calls["release_list"]) == 1


def test_transient_detail_failure_is_retried_but_404_is_not():
    stub = StubDeps(unknown_versions=("0.7.0",), detail_status=503)
    enricher = Enricher(stub)
    enricher.enrich(_alert())
    enricher.enrich(_alert())
    assert stub.calls["version_detail"].count(("npm", "cookie", "0.7.0")) == 2


def test_current_age_failure_keeps_status_and_is_not_cached():
    stub = StubDeps(unknown_versions=("0.4.0",), detail_status=503)
    enricher = Enricher(stub)
    out = enricher.enrich(_alert())
    assert out["sca_status"] == OK
    assert out["sca_current_age_days"] is None
    assert out["sca_behind_level"] == "minor"
    enricher.enrich(_alert())
    assert stub.calls["version_detail"].count(("npm", "cookie", "0.4.0")) == 2
