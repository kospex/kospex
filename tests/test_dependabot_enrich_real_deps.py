"""`kospex sca -dependabot` against the REAL KospexDependencies.

The other dependabot tests drive a stub of KospexDependencies, so they cannot
catch a wrong assumption about what deps.dev says or how KospexDependencies
reads it. Here only the HTTP edge is faked: `url_request_with_status` serves
deps.dev-shaped JSON. Alerts come from the production `alert_line()` writer.
"""
import datetime
import json
import pathlib
from urllib.parse import quote

import pytest

from kospex.dependabot_enrich import Enricher
from kospex.github_dependabot import alert_line
from kospex.habitat_config import HabitatConfig
from kospex_dependencies import KospexDependencies

DATA = pathlib.Path(__file__).parent / "data" / "depsdev"
BASE = "https://api.deps.dev/v3alpha"


class FakeQuery:
    """Serves canned deps.dev responses; anything else is a 404."""

    def __init__(self, responses=None, failing=()):
        self.responses = responses or {}
        self.failing = set(failing)     # urls answered 503
        self.calls = []
        self.last_url_status = None
        self.last_url_error = None
        self.url_failures = {}

    def url_request_with_status(self, url, cache=None, **_kw):
        self.calls.append(url)
        self.last_url_error = None
        if url in self.failing:
            self.last_url_status = 503
            return None, 503
        if url in self.responses:
            body, status = self.responses[url]
        else:
            body, status = None, 404
        self.last_url_status = status
        return (json.dumps(body) if body is not None else None), status

    def url_request(self, url, cache=None, **kw):
        return self.url_request_with_status(url, cache)[0]


def _ver(system, name, version, published, advisories=(), default=False):
    return {"versionKey": {"system": system.upper(), "name": name, "version": version},
            "publishedAt": published, "isDefault": default,
            "advisoryKeys": [{"id": a} for a in advisories]}


def _add_package(responses, system, name, versions):
    url = f"{BASE}/systems/{system}/packages/{quote(name, safe='')}"
    responses[url] = ({"versions": versions}, 200)
    for v in versions:
        responses[f"{url}/versions/{v['versionKey']['version']}"] = (v, 200)


def _add_advisory(responses, key, score=None, vector=None, aliases=()):
    body = {"advisoryKey": {"id": key}, "aliases": list(aliases), "title": key}
    if score is not None:
        body["cvss3Score"] = score
    if vector:
        body["cvss3Vector"] = vector
    responses[f"{BASE}/advisories/{key}"] = (body, 200)


def _alert(ecosystem, name, current, patched):
    api = {"dependency": {"package": {"ecosystem": ecosystem, "name": name},
                          "manifest_path": "manifest", "version_requirement": current},
           "security_vulnerability": {"first_patched_version":
                                      {"identifier": patched} if patched else None},
           "security_advisory": {"ghsa_id": "GHSA-xxxx-xxxx-xxxx"}}
    return alert_line(api, "acme", "app", 1, "2026-11-24T00:00:00Z")


def _enrich(query, alert, now=None):
    return Enricher(KospexDependencies(kospex_query=query), now=now).enrich(alert)


@pytest.fixture(autouse=True)
def _clean_private_env(monkeypatch):
    for name in ("KOSPEX_DEPS_PRIVATE", "GOPRIVATE"):
        monkeypatch.delenv(name, raising=False)
    HabitatConfig.reset_instance()
    yield
    HabitatConfig.reset_instance()


def test_go_version_without_v_is_found():
    """GitHub writes Go versions without `v`; deps.dev only knows `v0.17.0`."""
    name = "golang.org/x/crypto"
    r = {}
    _add_package(r, "go", name, [
        _ver("go", name, "v0.14.0", "2023-10-01T00:00:00Z"),
        _ver("go", name, "v0.17.0", "2023-12-18T00:00:00Z"),
        _ver("go", name, "v0.30.0", "2024-12-01T00:00:00Z", default=True)])
    out = _enrich(FakeQuery(r), _alert("go", name, "0.14.0", "0.17.0"))
    assert out["sca_status"] == "ok"
    assert out["sca_current_age_days"] is not None
    assert out["sca_latest_version"] == "v0.30.0"
    assert out["sca_behind_level"] == "minor"
    assert out["sca_behind_releases"] == 2
    assert out["sca_patched_version_vulnerabilities"] == 0
    assert out["sca_patched_diff_level"] == "minor"
    assert out["sca_patched_releases"] == 1     # v0.17.0 only; latest is two releases away


def test_go_version_with_v_is_not_doubled():
    name = "golang.org/x/crypto"
    r = {}
    _add_package(r, "go", name, [
        _ver("go", name, "v0.14.0", "2023-10-01T00:00:00Z"),
        _ver("go", name, "v0.17.0", "2023-12-18T00:00:00Z", default=True)])
    q = FakeQuery(r)
    out = _enrich(q, _alert("go", name, "v0.14.0", "v0.17.0"))
    assert out["sca_status"] == "ok"
    assert not any("/versions/vv" in c for c in q.calls)


def test_go_fixture_package_resolves_unprefixed_version():
    """Real deps.dev Go record: the lookup must ask for the `v` spelling."""
    name = "github.com/gin-gonic/gin"
    listing = json.loads((DATA / "go__github.com_gin-gonic_gin.json").read_text())
    tagged = [v for v in listing["versions"]
              if v["versionKey"]["version"].startswith("v1.9.")]
    assert tagged, "fixture has no v1.9.x release"
    version = tagged[0]["versionKey"]["version"]
    r = {}
    _add_package(r, "go", name, listing["versions"])
    out = _enrich(FakeQuery(r), _alert("go", name, version[1:], version[1:]))
    assert out["sca_status"] == "ok"


def test_pypi_pin_is_canonicalised():
    r = {}
    _add_package(r, "pypi", "requests", [
        _ver("pypi", "requests", "2.25.0", "2020-11-11T00:00:00Z"),
        _ver("pypi", "requests", "2.32.0", "2024-05-20T00:00:00Z", default=True)])
    # deps.dev canonicalises "2.25" to the 2.25.0 record
    r[f"{BASE}/systems/pypi/packages/requests/versions/2.25"] = (
        _ver("pypi", "requests", "2.25.0", "2020-11-11T00:00:00Z"), 200)
    r[f"{BASE}/systems/pypi/packages/requests/versions/2.32"] = (
        _ver("pypi", "requests", "2.32.0", "2024-05-20T00:00:00Z"), 200)
    out = _enrich(FakeQuery(r), _alert("pip", "requests", "= 2.25", "2.32"))
    assert out["current_version"] == "2.25"
    assert out["sca_status"] == "ok"
    assert out["sca_latest_version"] == "2.32.0"
    assert out["sca_behind_level"] == "minor"
    assert out["sca_current_age_days"] is not None


def test_ages_of_current_patched_and_latest_versions():
    """Whole days from each version's publishedAt to `now`, for the three versions."""
    r = {}
    _add_package(r, "pypi", "requests", [
        _ver("pypi", "requests", "2.25.0", "2020-11-11T00:00:00Z"),
        _ver("pypi", "requests", "2.31.0", "2023-05-22T00:00:00Z"),
        _ver("pypi", "requests", "2.32.0", "2024-05-20T00:00:00Z", default=True)])
    now = datetime.datetime(2024, 6, 19, tzinfo=datetime.timezone.utc)
    out = _enrich(FakeQuery(r), _alert("pip", "requests", "= 2.25.0", "2.31.0"), now=now)
    assert out["sca_status"] == "ok"
    assert out["sca_latest_version"] == "2.32.0"
    assert out["sca_current_age_days"] == 1316
    assert out["sca_patched_age_days"] == 394
    assert out["sca_latest_age_days"] == 30


def _requests_with_a_prerelease():
    r = {}
    _add_package(r, "pypi", "requests", [
        _ver("pypi", "requests", "2.25.0", "2020-11-11T00:00:00Z"),
        _ver("pypi", "requests", "2.26.0", "2021-07-13T00:00:00Z"),
        _ver("pypi", "requests", "2.27.0", "2022-01-03T00:00:00Z"),
        _ver("pypi", "requests", "2.28.0", "2022-06-09T00:00:00Z"),
        _ver("pypi", "requests", "2.30.0rc1", "2023-04-20T00:00:00Z"),
        _ver("pypi", "requests", "2.31.0", "2023-05-22T00:00:00Z"),
        _ver("pypi", "requests", "2.32.0", "2024-05-20T00:00:00Z", default=True)])
    return r


def test_patched_releases_counts_to_patched_while_behind_counts_to_latest():
    """2.25.0 -> patched 2.28.0 is 3 releases; to latest 2.32.0 it is 5 (the 2.30.0rc1
    pre-release is skipped by both)."""
    out = _enrich(FakeQuery(_requests_with_a_prerelease()),
                  _alert("pip", "requests", "= 2.25.0", "2.28.0"))
    assert out["sca_status"] == "ok"
    assert out["sca_patched_releases"] == 3
    assert out["sca_behind_releases"] == 5
    assert out["sca_latest_version"] == "2.32.0"


def test_patched_releases_null_when_the_patched_version_is_below_the_current():
    out = _enrich(FakeQuery(_requests_with_a_prerelease()),
                  _alert("pip", "requests", "= 2.31.0", "2.28.0"))
    assert out["sca_patched_diff_level"] == "downgrade"
    assert out["sca_patched_releases"] is None
    assert out["sca_behind_releases"] == 1      # the latest count is unaffected


def test_latest_age_for_go_uses_the_v_prefixed_release():
    name = "golang.org/x/crypto"
    r = {}
    _add_package(r, "go", name, [
        _ver("go", name, "v0.14.0", "2023-10-01T00:00:00Z"),
        _ver("go", name, "v0.17.0", "2023-12-18T00:00:00Z"),
        _ver("go", name, "v0.30.0", "2024-12-01T00:00:00Z", default=True)])
    now = datetime.datetime(2024, 12, 11, tzinfo=datetime.timezone.utc)
    out = _enrich(FakeQuery(r), _alert("go", name, "0.14.0", "0.17.0"), now=now)
    assert out["sca_latest_version"] == "v0.30.0"
    assert out["sca_latest_age_days"] == 10
    assert out["sca_patched_age_days"] == 359   # 2023-12-18 -> 2024-12-11


def test_prerelease_current_version_is_ok_with_null_behind():
    r = {}
    _add_package(r, "npm", "cookie", [
        _ver("npm", "cookie", "1.0.0-rc.1", "2024-01-01T00:00:00Z"),
        _ver("npm", "cookie", "0.7.0", "2024-10-01T00:00:00Z"),
        _ver("npm", "cookie", "0.7.2", "2024-11-01T00:00:00Z", default=True)])
    out = _enrich(FakeQuery(r), _alert("npm", "cookie", "1.0.0-rc.1", "0.7.0"))
    assert out["sca_status"] == "ok"
    assert out["sca_latest_version"] is None
    assert out["sca_behind_level"] is None
    assert out["sca_behind_releases"] is None


def test_unscored_advisory_is_null_not_zero():
    r = {}
    _add_package(r, "npm", "cookie", [
        _ver("npm", "cookie", "0.4.0", "2019-04-01T00:00:00Z"),
        _ver("npm", "cookie", "0.7.0", "2024-10-01T00:00:00Z",
             advisories=["GHSA-zzzz-zzzz-zzzz"], default=True)])
    _add_advisory(r, "GHSA-zzzz-zzzz-zzzz", score=0)    # no vector: unscored
    out = _enrich(FakeQuery(r), _alert("npm", "cookie", "0.4.0", "0.7.0"))
    assert out["sca_status"] == "ok"
    assert out["sca_patched_version_vulnerabilities"] == 1
    assert out["sca_patched_version_max_cvss"] is None
    assert out["sca_patched_ghsa_ids"] == ["GHSA-zzzz-zzzz-zzzz"]


def test_scored_advisory_gives_max_cvss():
    r = {}
    _add_package(r, "npm", "cookie", [
        _ver("npm", "cookie", "0.4.0", "2019-04-01T00:00:00Z"),
        _ver("npm", "cookie", "0.7.0", "2024-10-01T00:00:00Z",
             advisories=["GHSA-aaaa", "GHSA-bbbb"], default=True)])
    _add_advisory(r, "GHSA-aaaa", score=5.3, vector="CVSS:3.1/AV:N")
    _add_advisory(r, "GHSA-bbbb", score=9.8, vector="CVSS:3.1/AV:N")
    out = _enrich(FakeQuery(r), _alert("npm", "cookie", "0.4.0", "0.7.0"))
    assert out["sca_patched_version_max_cvss"] == 9.8
    assert out["sca_patched_version_vulnerabilities"] == 2


def test_failed_advisory_fetch_is_lookup_error_with_null_patched_fields():
    r = {}
    _add_package(r, "npm", "cookie", [
        _ver("npm", "cookie", "0.4.0", "2019-04-01T00:00:00Z"),
        _ver("npm", "cookie", "0.7.0", "2024-10-01T00:00:00Z",
             advisories=["GHSA-aaaa", "GHSA-bbbb"], default=True)])
    _add_advisory(r, "GHSA-aaaa", score=5.3, vector="CVSS:3.1/AV:N")
    _add_advisory(r, "GHSA-bbbb", score=9.8, vector="CVSS:3.1/AV:N")
    q = FakeQuery(r, failing={f"{BASE}/advisories/GHSA-bbbb"})
    out = _enrich(q, _alert("npm", "cookie", "0.4.0", "0.7.0"))
    assert out["sca_status"] == "lookup_error"
    assert out["sca_patched_version_vulnerabilities"] is None
    assert out["sca_patched_version_max_cvss"] is None
    assert out["sca_patched_ghsa_ids"] is None
    # the rest of the row is still filled
    assert out["sca_patched_diff_level"] == "minor"


def test_failed_advisory_fetch_is_not_cached():
    r = {}
    _add_package(r, "npm", "cookie", [
        _ver("npm", "cookie", "0.4.0", "2019-04-01T00:00:00Z"),
        _ver("npm", "cookie", "0.7.0", "2024-10-01T00:00:00Z",
             advisories=["GHSA-aaaa"], default=True)])
    _add_advisory(r, "GHSA-aaaa", score=7.5, vector="CVSS:3.1/AV:N")
    q = FakeQuery(r, failing={f"{BASE}/advisories/GHSA-aaaa"})
    enricher = Enricher(KospexDependencies(kospex_query=q))
    alert = _alert("npm", "cookie", "0.4.0", "0.7.0")
    assert enricher.enrich(alert)["sca_status"] == "lookup_error"
    q.failing.clear()
    again = enricher.enrich(alert)
    assert again["sca_status"] == "ok"
    assert again["sca_patched_version_max_cvss"] == 7.5


def test_real_private_setting_skips_with_no_requests(monkeypatch):
    monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "go:github.com/acme")
    HabitatConfig.reset_instance()
    q = FakeQuery({})
    out = _enrich(q, _alert("go", "github.com/acme/internal", "1.0.0", "1.0.1"))
    assert out["sca_status"] == "private_skipped"
    assert q.calls == []
    # a public Go module is still looked up
    _enrich(q, _alert("go", "golang.org/x/crypto", "0.14.0", "0.17.0"))
    assert q.calls
