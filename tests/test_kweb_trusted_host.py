"""kweb refuses requests whose Host header it does not serve (CWE-346).

A DNS-rebound page reaches 127.0.0.1 as same-origin, so CORS never applies;
the Host header still names the attacker's domain, and that is what is checked.

Driven as a raw ASGI call rather than TestClient: starlette's requires httpx2,
which is not installed, so TestClient suites importorskip and never run.
"""
import asyncio

import pytest

import kweb2

# A path no route serves: an accepted request reaches routing and gets a 404,
# a refused one is answered 400 by the middleware without touching the DB.
UNROUTED = "/__trusted-host-test__"


def _start(host, extra_headers=()):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": UNROUTED,
        "raw_path": UNROUTED.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", host.encode()), *extra_headers],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(kweb2.app(scope, receive, send))
    return next(m for m in sent if m["type"] == "http.response.start")


def _status(host):
    return _start(host)["status"]


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "localhost"])
def test_loopback_hosts_are_served(host):
    assert _status(host) == 404


@pytest.mark.parametrize("host", ["evil.example.com:8000", "evil.example.com", "", "127.0.0.1.evil.example.com"])
def test_other_hosts_are_refused(host):
    assert _status(host) == 400


def test_default_hosts(monkeypatch):
    monkeypatch.delenv("KOSPEX_WEB_ALLOWED_HOSTS", raising=False)
    assert kweb2.allowed_hosts() == ["127.0.0.1", "localhost"]


def test_env_adds_hosts(monkeypatch):
    monkeypatch.setenv("KOSPEX_WEB_ALLOWED_HOSTS", " kospex.example.com, 10.0.0.5 ,,localhost, *.example.org")
    assert kweb2.allowed_hosts() == [
        "127.0.0.1", "localhost", "kospex.example.com", "10.0.0.5", "*.example.org",
    ]


@pytest.mark.parametrize("bad", ["kospex.*.com", "*example.com", "**"])
def test_invalid_wildcards_are_dropped(monkeypatch, bad):
    monkeypatch.setenv("KOSPEX_WEB_ALLOWED_HOSTS", bad)
    assert kweb2.allowed_hosts() == ["127.0.0.1", "localhost"]


def test_no_cors_header_for_another_origin():
    """kweb sends no Access-Control-Allow-Origin, so browsers keep other sites
    from reading its responses. Its own pages are same-origin and need none."""
    start = _start("localhost:8000", [(b"origin", b"https://evil.example.com")])
    names = {name.lower() for name, _ in start["headers"]}
    assert b"access-control-allow-origin" not in names
