"""kweb pages return 404 for an id that isn't in the database (#206).

They rendered an empty page with HTTP 200 instead, which looks the same as a
real repository, org or server with no data. An unknown id is most likely a
typo or a stale link, so each page now says so.

Handlers are called directly rather than through TestClient (starlette's needs
httpx2, which is not installed, so TestClient suites skip).
"""
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import kospex_schema as KospexSchema
import kweb2
from kospex_query import KospexQuery

REPO = "github.com~acme~api"
ORG = "github.com~acme"
SERVER = "github.com"
HASH = "abc123"

KNOWN = {"repository": REPO, "organisation": ORG, "server": SERVER}
UNKNOWN = {"repository": "github.com~acme~nope", "organisation": "github.com~nope",
           "server": "nope.example.com"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()
    db = KospexSchema.connect_or_create_kospex_db()
    db["repos"].insert({"_repo_id": REPO, "_git_server": SERVER,
                        "_git_owner": "acme", "_git_repo": "api"})
    db["commits"].insert({
        "hash": HASH, "_repo_id": REPO, "_git_server": SERVER,
        "_git_owner": "acme", "_git_repo": "api",
        "author_email": "dev@example.com", "committer_email": "dev@example.com",
        "author_when": "2026-10-01T00:00:00+00:00",
        "committer_when": "2026-10-01T00:00:00+00:00",
        "parents": 1, "_files": 1,
    })
    monkeypatch.setattr(kweb2, "KospexQuery", lambda *a, **k: KospexQuery(kospex_db=db))
    kweb2.request_cache.clear()
    return db


def _request(path, query_string=b""):
    return Request({
        "type": "http", "method": "GET", "path": path, "server": ("localhost", 8000),
        "scheme": "http", "headers": [], "query_string": query_string,
        "app": kweb2.app,
    })


# (label, kinds the route accepts, call(id) -> response)
ROUTES = [
    ("/summary/", ("repository", "organisation", "server"),
     lambda id: kweb2.summary(_request(f"/summary/{id}"), id=id)),
    ("/summary2/", ("repository", "organisation", "server"),
     lambda id: kweb2.summary2(_request(f"/summary2/{id}"), id=id)),
    ("/metadata/repos/", ("repository", "organisation", "server"),
     lambda id: kweb2.metadata_repos(_request(f"/metadata/repos/{id}"), id=id)),
    ("/orphans/", ("repository", "organisation", "server"),
     lambda id: kweb2.orphans(_request(f"/orphans/{id}"), id=id)),
    ("/bubble/", ("repository", "organisation", "server"),
     lambda id: kweb2.bubble_treemap(_request(f"/bubble/{id}"), id=id)),
    ("/treemap/", ("repository", "organisation", "server"),
     lambda id: kweb2.bubble_treemap(_request(f"/treemap/{id}"), id=id)),
    ("/osi/", ("repository", "organisation", "server"),
     lambda id: kweb2.osi(_request(f"/osi/{id}"), id=id)),
    ("/repos/", ("repository", "organisation", "server"),
     lambda id: kweb2.repos(_request(f"/repos/{id}"), id=id)),
    ("/landscape/", ("repository", "organisation", "server"),
     lambda id: kweb2.landscape(_request(f"/landscape/{id}"), id=id)),
    ("/developers/", ("repository", "organisation", "server"),
     lambda id: kweb2.developers(_request(f"/developers/{id}"), id=id)),
    ("/tenure/", ("repository", "organisation", "server"),
     lambda id: kweb2.tenure(_request(f"/tenure/{id}"), id=id)),
    ("/developers/active/", ("repository",),
     lambda id: kweb2.active_developers(_request(f"/developers/active/{id}"), repo_id=id)),
    ("/collab/graph/", ("repository",),
     lambda id: kweb2.collab_graph(_request(f"/collab/graph/{id}"), repo_id=id)),
    ("/file-collab/", ("repository",),
     lambda id: kweb2.file_collaboration(
         _request(f"/file-collab/{id}/", b"file_path=README.md"), repo_id=id)),
    ("/repo/", ("repository",),
     lambda id: kweb2.repo(_request(f"/repo/{id}"), repo_id=id)),
    ("/key-person/", ("repository",),
     lambda id: kweb2.key_person(_request(f"/key-person/{id}"), repo_id=id)),
    ("/commits/", ("repository",),
     lambda id: kweb2.commits(_request(f"/commits/{id}"), repo_id=id)),
    ("/history/", ("repository",),
     lambda id: kweb2.commit_history(_request(f"/history/{id}"), repo_id=id)),
    ("/commit/", ("repository",),
     lambda id: kweb2.commit(_request(f"/commit/{id}/{HASH}"), repo_id=id, commit_hash=HASH)),
    ("/hotspots/", ("repository",),
     lambda id: kweb2.hotspots(_request(f"/hotspots/{id}"), repo_id=id)),
    ("/files/repo/", ("repository",),
     lambda id: kweb2.repo_files(_request(f"/files/repo/{id}"), repo_id=id)),
    ("/orgs/", ("server",),
     lambda id: kweb2.orgs(_request(f"/orgs/{id}"), server=id)),
    ("/org/", ("organisation",),
     lambda id: kweb2.org_view(_request(f"/org/{id}"), org_key=id)),
    ("/graph/", ("organisation",),
     lambda id: kweb2.graph(_request(f"/graph/{id}"), org_key=id)),
]

CASES = [(label, kind, call) for label, kinds, call in ROUTES for kind in kinds]
IDS = [f"{label}-{kind}" for label, kind, _ in CASES]


@pytest.mark.parametrize("label,kind,call", CASES, ids=IDS)
def test_unknown_id_is_404_naming_the_id(db, label, kind, call):
    id = UNKNOWN[kind]
    with pytest.raises(HTTPException) as exc:
        call(id)
    assert exc.value.status_code == 404
    assert f"No {kind} with ID '{id}'" in exc.value.detail


@pytest.mark.parametrize("label,kind,call", CASES, ids=IDS)
def test_known_id_renders(db, label, kind, call):
    assert call(KNOWN[kind]).status_code == 200


def test_unknown_commit_in_a_known_repo_is_404(db):
    with pytest.raises(HTTPException) as exc:
        kweb2.commit(_request(f"/commit/{REPO}/deadbeef"), repo_id=REPO, commit_hash="deadbeef")
    assert exc.value.status_code == 404
    assert "deadbeef" in exc.value.detail


@pytest.mark.parametrize("call", [
    lambda: kweb2.summary(_request("/summary/"), id=None),
    lambda: kweb2.repos(_request("/repos/"), id=None),
    lambda: kweb2.tenure(_request("/tenure/"), id=None),
    lambda: kweb2.orgs(_request("/orgs/"), server=None),
    lambda: kweb2.graph(_request("/graph/"), org_key=None),
])
def test_pages_without_an_id_are_unchanged(db, call):
    assert call().status_code == 200
