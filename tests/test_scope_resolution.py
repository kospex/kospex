"""A query asked for a scope it cannot honour must not silently widen (#158, #43).

get_dependency_files() and get_dependencies() hand-rolled the scope cascade and
the fallback branch printed an error then fell through with NO where clause, so
the query ran completely unscoped. Reached from /osi/{id} and /dependencies/{id}
whenever the path segment is a base64 author_email -- a shape get_id_params
produces and neither query understands.

Measured on a 111-repo database before the fix: an author scope returned 852
rows, exactly the same as no scope at all.

Note the fix #158 suggested -- routing through set_params_by_id() -- would NOT
have fixed it. That helper filters to repo_id/org_key/server and silently
ignores anything else, so an author_email still yields no where clause. The
distinction that matters is "no scope requested" (return everything, which
krunner relies on) versus "a scope I cannot honour" (refuse).
"""
import pytest

import kospex_utils as KospexUtils
from kospex_query import KospexQuery
from kospex_web import get_id_params

AUTHOR_EMAIL = "someone@example.com"


@pytest.fixture
def kq():
    return KospexQuery()


def _author_scope():
    """What get_id_params returns for a base64 author email in the URL."""
    params = get_id_params(KospexUtils.encode_base64(AUTHOR_EMAIL))
    assert "author_email" in params, "precondition: this shape reaches the queries"
    return params


@pytest.mark.parametrize("method", ["get_dependency_files", "get_dependencies"])
def test_unhonourable_scope_is_refused_not_widened(kq, method):
    """The bug: this returned the entire table."""
    with pytest.raises(ValueError, match="scope"):
        getattr(kq, method)(request_id=_author_scope())


@pytest.mark.parametrize("method", ["get_dependency_files", "get_dependencies"])
def test_no_scope_still_means_all_scope(kq, method):
    """krunner calls these with no request_id and relies on getting everything."""
    assert getattr(kq, method)() is not None


@pytest.mark.parametrize("method", ["get_dependency_files", "get_dependencies"])
def test_a_recognised_scope_still_works(kq, method):
    rows = getattr(kq, method)(request_id={"repo_id": "github.com~kospex~kospex"})
    assert rows is not None


def test_refusing_is_not_the_same_as_returning_nothing(kq):
    """Returning zero rows would read as 'this author has no dependencies'.

    That is a plausible answer and therefore the wrong failure -- the caller
    asked a question the query cannot answer, and must be told so.
    """
    with pytest.raises(ValueError):
        kq.get_dependency_files(request_id=_author_scope())


# --- the web layer must render a refusal, not a 500 (#158) and not an empty
#     table (#43) ---------------------------------------------------------

import asyncio

import kweb2
from fastapi import HTTPException


def _call(handler, **kwargs):
    """Call a route handler directly.

    starlette's TestClient needs httpx2, which is not installed, so the web
    suites skip unless a live kweb is running. A handler is better exercised
    directly than not at all.
    """
    return asyncio.run(handler(request=None, **kwargs))


def _status_of(handler, **kwargs):
    try:
        _call(handler, **kwargs)
        return 200
    except HTTPException as exc:
        return exc.status_code


@pytest.mark.parametrize("handler,kw", [
    (kweb2.osi, "id"),
    (kweb2.dependencies, "id"),
])
def test_author_scope_is_a_client_error_not_a_server_error(handler, kw):
    """An author email is a valid URL segment kospex cannot scope these by.

    Before: the query widened and the page rendered every row.
    A 500 would also be wrong -- the request is malformed, not the server.
    """
    b64 = KospexUtils.encode_base64(AUTHOR_EMAIL)
    assert _status_of(handler, **{kw: b64}) == 400


def test_collab_rejects_an_id_that_is_not_a_repo_id():
    """#43 -- /collab/ passed the id straight to the query and rendered an
    empty table, which reads as 'this repo has no collaborators'."""
    assert _status_of(kweb2.collab, repo_id="not-a-repo-id") == 404


def test_collab_rejects_a_well_formed_id_that_is_not_in_the_database():
    """The commoner case, and the same wrong reading."""
    assert _status_of(kweb2.collab, repo_id="github.com~nobody~nothing") == 404


def test_collab_still_serves_a_real_repo():
    kq = KospexQuery()
    row = next(iter(kq.kospex_db.query("SELECT _repo_id FROM repos LIMIT 1")), None)
    if not row:
        pytest.skip("no repos in the database")
    assert _status_of(kweb2.collab, repo_id=row["_repo_id"]) == 200
