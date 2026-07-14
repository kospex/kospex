"""Tests for dependency resolution-status classification."""
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
