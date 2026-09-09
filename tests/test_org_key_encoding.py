"""org_key must be encoded identically by the SQL and by parse_org_key (#94).

`org_key` is both a join key and a URL path segment (/org/{org_key},
/graph/{org_key}). An org containing a '/' -- a GitLab group/subgroup, or an
Azure DevOps organisation/project -- must therefore appear as '~~', never as a
literal slash, or the route splits into two segments.

The SQL builds org_key from the columns; parse_repo_id builds it from the id.
If the two disagree, a join across them silently returns nothing.
"""
import os

import pytest
import sqlite_utils

import kospex_schema as KospexSchema
import kospex_utils as KospexUtils

NESTED_REPO_ID = "gitlab.com~group~~subgroup~repo"
FLAT_REPO_ID = "github.com~acme~svc"


@pytest.fixture(autouse=True)
def _preserve_kospex_env():
    keys = ("KOSPEX_CODE", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_HOME", "KOSPEX_LOGS")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _db():
    """A commits table holding one nested-org repo and one flat one."""
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMITS)
    db[KospexSchema.TBL_COMMITS].insert_all([
        {"_repo_id": NESTED_REPO_ID, "hash": "h1",
         "_git_server": "gitlab.com", "_git_owner": "group/subgroup",
         "_git_repo": "repo", "author_email": "a@e.com",
         "committer_when": "2026-01-01T00:00:00Z",
         "author_when": "2026-01-01T00:00:00Z"},
        {"_repo_id": FLAT_REPO_ID, "hash": "h2",
         "_git_server": "github.com", "_git_owner": "acme",
         "_git_repo": "svc", "author_email": "a@e.com",
         "committer_when": "2026-01-01T00:00:00Z",
         "author_when": "2026-01-01T00:00:00Z"},
    ], pk=["_repo_id", "hash"])
    return db


def _orgs(db):
    """Run the PRODUCTION orgs() query against the in-memory database.

    Deliberately not a local copy of the SQL -- a test that reimplements the
    expression it is checking passes no matter what production does.
    """
    from kospex_query import KospexQuery
    return {r["org_key"]: r for r in KospexQuery(kospex_db=db).orgs()}


def test_sql_org_key_matches_parse_repo_id():
    """The join contract: both sides must produce the same string."""
    org_keys = set(_orgs(_db()))
    expected = {
        KospexUtils.parse_repo_id(rid)["org_key"]
        for rid in (NESTED_REPO_ID, FLAT_REPO_ID)
    }
    assert org_keys == expected


def test_sql_org_key_has_no_slash():
    """A '/' would split /org/{org_key} into two path segments."""
    for org_key in _orgs(_db()):
        assert "/" not in org_key


def test_org_key_round_trips_back_to_the_owner():
    """parse_org_key decodes what the SQL encoded."""
    orgs = _orgs(_db())
    decoded = {KospexUtils.parse_org_key(k)["org"] for k in orgs}
    assert decoded == {"group/subgroup", "acme"}
