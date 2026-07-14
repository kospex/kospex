"""Tests for dependency resolution-status classification."""
import json
import sqlite_utils
import kospex_schema as KospexSchema
from kospex_query import KospexQuery


def _kq():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_URL_CACHE)
    return KospexQuery(kospex_db=db)


def test_url_request_with_status_200_returns_body_and_caches(monkeypatch):
    kq = _kq()

    class _Resp:
        status_code = 200
        text = '{"ok": true}'
        def raise_for_status(self): pass

    monkeypatch.setattr("kospex_query.requests.get", lambda *a, **k: _Resp())
    content, status = kq.url_request_with_status("http://x/pkg")
    assert status == 200 and content == '{"ok": true}'
    # cached: a second call with requests.get patched to blow up still returns it
    monkeypatch.setattr("kospex_query.requests.get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should be cached")))
    content2, status2 = kq.url_request_with_status("http://x/pkg")
    assert status2 == 200 and content2 == '{"ok": true}'


def test_url_request_with_status_404(monkeypatch):
    import requests
    kq = _kq()

    class _Resp:
        status_code = 404
        text = "not found"
        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    monkeypatch.setattr("kospex_query.requests.get", lambda *a, **k: _Resp())
    content, status = kq.url_request_with_status("http://x/missing")
    assert content is None and status == 404


def test_url_request_with_status_network_error(monkeypatch):
    import requests
    kq = _kq()
    def _boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr("kospex_query.requests.get", _boom)
    content, status = kq.url_request_with_status("http://x/err")
    assert content is None and status is None


def test_url_request_still_returns_bare_content(monkeypatch):
    """url_request delegates to url_request_with_status but keeps its old
    contract: bare content on success, None on failure (no tuple)."""
    import requests
    kq = _kq()

    class _Resp:
        status_code = 200
        text = '{"ok": true}'
        def raise_for_status(self): pass

    monkeypatch.setattr("kospex_query.requests.get", lambda *a, **k: _Resp())
    assert kq.url_request("http://x/pkg") == '{"ok": true}'

    def _boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr("kospex_query.requests.get", _boom)
    assert kq.url_request("http://x/err") is None


import pytest
from kospex_dependencies import KospexDependencies


@pytest.mark.parametrize("v,expected", [
    ("1.2.3", True), ("0.0.16", True), ("2.0.0rc1", True), ("1.2.3-beta.1", True),
    ("", False), (None, False), ("^4.4.0", False), (">=1.0,<2.0", False),
    ("~1.2", False), ("1.x", False), ("latest", False), ("*", False),
    ("workspace:*", False), ("https://github.com/o/r", False), ("git+https://x", False),
])
def test_is_concrete_version(v, expected):
    assert KospexDependencies().is_concrete_version(v) is expected


class _StubQuery:
    """Stub KospexQuery: url_request_with_status returns queued (content, status)
    for the exact-version lookup (deps_dev_status). url_request (bare content on
    success, None otherwise) drives the package-level lookup used by both
    deps_dev_package (KospexDependencies._package_exists) and get_versions_behind
    (via get_url_json) -- both hit the same package-level URL (no "/versions/")."""
    def __init__(self, version_result, package_result=None):
        self._version = version_result      # (content, status) for the exact version
        self._package = package_result      # (content, status) for the package-level call

    def url_request_with_status(self, url, **kw):
        return self._package if "/versions/" not in url else self._version

    def url_request(self, url, **kw):
        if self._package is None:
            return None
        content, status = self._package
        return content if status == 200 else None


def _deps(version_result, package_result=None):
    kd = KospexDependencies(kospex_query=_StubQuery(version_result, package_result))
    return kd


def test_depsdev_record_no_version():
    rec = _deps((None, None)).depsdev_record("pypi", "foo", "")
    assert rec["resolution"] == "no_version" and rec["versions_behind"] is None


def test_depsdev_record_unresolved_spec():
    rec = _deps((None, None)).depsdev_record("npm", "chartjs", "^4.4.0")
    assert rec["resolution"] == "unresolved_spec" and rec["versions_behind"] is None


def test_depsdev_record_package_not_found():
    # exact version 404, package-level also 404
    rec = _deps(version_result=(None, 404), package_result=(None, 404)).depsdev_record(
        "pypi", "nope-typo", "1.0.0")
    assert rec["resolution"] == "package_not_found" and rec["versions_behind"] is None


def test_depsdev_record_version_yanked():
    # exact version 404, but package exists
    rec = _deps(version_result=(None, 404), package_result=('{"versions": []}', 200)).depsdev_record(
        "pypi", "realpkg", "9.9.9")
    assert rec["resolution"] == "version_yanked" and rec["versions_behind"] is None


def test_depsdev_record_lookup_error():
    rec = _deps(version_result=(None, None)).depsdev_record("pypi", "x", "1.0.0")
    assert rec["resolution"] == "lookup_error" and rec["versions_behind"] is None


def test_depsdev_record_resolved_versions_behind_is_int():
    """get_versions_behind always returns an int in its 'versions_behind' key
    (initialised to 0, only ever incremented) when the package-level fetch
    succeeds, so the resolved path must carry a real int, never a sentinel
    string like the old 'Unknown'/''."""
    version_body = json.dumps({
        "publishedAt": "2024-01-01T00:00:00Z",
        "isDefault": True,
        "links": [],
        "advisoryKeys": [],
    })
    package_body = json.dumps({
        "versions": [
            {"isDefault": True, "publishedAt": "2024-01-01T00:00:00Z",
             "versionKey": {"version": "1.0.0"}},
        ]
    })
    rec = _deps(version_result=(version_body, 200),
                package_result=(package_body, 200)).depsdev_record("pypi", "foo", "1.0.0")
    assert rec["resolution"] == "resolved"
    assert type(rec["versions_behind"]) is int
    assert rec["versions_behind"] == 0
