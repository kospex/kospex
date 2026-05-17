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


@pytest.fixture(scope="module")
def client():
    pytest.importorskip("httpx", reason="httpx required for FastAPI TestClient")
    from fastapi.testclient import TestClient
    from kweb2 import app

    return TestClient(app)


def test_repo_route_404_on_malformed_id(client):
    resp = client.get("/repo/notavalidrepoid")
    assert resp.status_code == 404
