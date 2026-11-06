"""/files/repo/{repo_id} links each file to its collaboration page.

The link was /repo/{{ row['Provider'] }}, but Provider is the file's path
relative to the repo, not a repo_id, so every file link on the page was a 404.

The handler is called directly rather than through TestClient (starlette's
needs httpx2, which is not installed, so TestClient suites skip).
"""
import re

from starlette.requests import Request

import kweb2

REPO = "github.com~acme~api"
ROWS = [
    {"_repo_id": REPO, "_git_server": "github.com", "_git_owner": "acme", "_git_repo": "api",
     "Provider": "src/main/App Config.java", "Filename": "App Config.java",
     "committer_when": "2026-10-01T00:00:00Z", "Language": "Java", "tech_type": None,
     "hash": "abc", "Lines": 10, "latest": 1},
    {"_repo_id": REPO, "_git_server": "github.com", "_git_owner": "acme", "_git_repo": "api",
     "Provider": "README.md", "Filename": "README.md",
     "committer_when": "2026-10-01T00:00:00Z", "Language": "Markdown", "tech_type": None,
     "hash": "def", "Lines": 3, "latest": 1},
]


class _Query:
    def scope_exists(self, params=None):
        # repo_files() 404s an unknown repo first (_require_known_scope).
        return True

    def repo_files(self, repo_id=None):
        return ROWS


def _request():
    return Request({
        "type": "http", "method": "GET", "path": f"/files/repo/{REPO}",
        "headers": [], "query_string": b"", "app": kweb2.app,
    })


def _file_links(monkeypatch):
    monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: _Query())
    response = kweb2.repo_files(_request(), repo_id=REPO)
    assert response.status_code == 200
    body = response.body.decode()
    return re.findall(r'href="(/(?:repo|file-collab)/[^"]*)"', body)


def test_file_links_go_to_file_collab_with_the_path_encoded(monkeypatch):
    links = _file_links(monkeypatch)
    assert f"/file-collab/{REPO}/?file_path=src/main/App%20Config.java" in links
    assert f"/file-collab/{REPO}/?file_path=README.md" in links


def test_no_file_link_points_at_the_repo_route(monkeypatch):
    links = _file_links(monkeypatch)
    assert links, "expected file links on the page"
    assert not [link for link in links if link.startswith("/repo/")]
