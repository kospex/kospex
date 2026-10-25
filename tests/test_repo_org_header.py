"""Tests for the shared entity header partial and repo/org route guards."""
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "src" / "templates"


def render_entity_header(**ctx):
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("_entity_header.html").render(**ctx)


def test_entity_header_repo_mode_breadcrumb_and_links():
    html = render_entity_header(
        git_server="github.com",
        org="apache",
        org_key="github.com~apache",
        repo="kafka",
        entity_type="Repository",
    )
    # Server segment links to the existing /orgs/{server} route
    assert 'href="/orgs/github.com"' in html
    # Org segment links to the new /org/{org_key} route
    assert 'href="/org/github.com~apache"' in html
    # Title + sub-label present
    assert "kafka" in html
    assert "Repository" in html
    # The page never links to its own /repo/ page
    assert 'href="/repo/' not in html


def test_entity_header_org_mode_org_is_current_segment():
    html = render_entity_header(
        git_server="github.com",
        org="apache",
        org_key="github.com~apache",
        entity_type="Organisation",
    )
    assert 'href="/orgs/github.com"' in html
    assert "apache" in html
    assert "Organisation" in html
    # In org mode the org is the current (non-link) segment
    assert 'href="/org/github.com~apache"' not in html


def render_breadcrumb(**ctx):
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("_breadcrumb.html").render(**ctx)


def test_scope_context_repo_sub_page_links_back_to_repo():
    import kospex_web as KospexWeb

    ctx = KospexWeb.scope_context({"repo_id": "github.com~apache~kafka"}, "Tenure")
    assert (ctx["scope_type"], ctx["scope_name"]) == ("Repository", "apache/kafka")
    html = render_breadcrumb(**ctx)
    assert 'href="/orgs/github.com"' in html
    assert 'href="/org/github.com~apache"' in html
    # On a sub-page the repo is a link back to its own view, not the current segment
    assert 'href="/repo/github.com~apache~kafka"' in html
    assert 'aria-current="page">Tenure<' in html


def test_scope_context_org_sub_page_links_back_to_org():
    import kospex_web as KospexWeb

    ctx = KospexWeb.scope_context({"org_key": "github.com~apache"}, "Dependencies")
    assert (ctx["scope_type"], ctx["scope_name"]) == ("Organisation", "apache")
    html = render_breadcrumb(**ctx)
    assert 'href="/org/github.com~apache"' in html
    assert 'href="/repo/' not in html
    assert 'aria-current="page">Dependencies<' in html


def test_scope_context_server_sub_page_links_back_to_server():
    import kospex_web as KospexWeb

    ctx = KospexWeb.scope_context({"server": "github.com"}, "Tenure")
    assert (ctx["scope_type"], ctx["scope_name"]) == ("Server", "github.com")
    html = render_breadcrumb(**ctx)
    assert 'href="/orgs/github.com"' in html
    assert 'href="/org/' not in html
    assert 'aria-current="page">Tenure<' in html


@pytest.mark.parametrize("params, scope_type", [
    ({}, "All repositories"),
    ({"author_email": "a@example.com"}, "Developer"),
])
def test_scope_context_no_breadcrumb_without_server(params, scope_type):
    import kospex_web as KospexWeb

    ctx = KospexWeb.scope_context(params, "Tenure")
    # No git_server => the templates skip the breadcrumb, but still label the scope
    assert "git_server" not in ctx
    assert ctx["scope_type"] == scope_type


@pytest.fixture(scope="module")
def client():
    pytest.importorskip("httpx", reason="httpx required for FastAPI TestClient")
    from fastapi.testclient import TestClient
    from kweb2 import app

    return TestClient(app, base_url="http://localhost")


def test_repo_route_404_on_malformed_id(client):
    resp = client.get("/repo/notavalidrepoid")
    assert resp.status_code == 404


def test_org_route_registered_and_404_on_malformed_key(client):
    # Route must actually exist (distinguishes guard-fired from route-absent)
    from kweb2 import app

    assert "/org/{org_key}" in {getattr(r, "path", None) for r in app.routes}
    resp = client.get("/org/notavalidorgkey")
    assert resp.status_code == 404
