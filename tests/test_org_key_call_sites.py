"""Every org_key consumer must go through parse_org_key (#94 Step 7).

parse_org_key was fixed for nested orgs in #195, but seven call sites still
hand-split on '~' and required exactly two parts. An org_key whose org contains
a '/' -- a GitLab group/subgroup, or an ADO organisation/project, encoded as
'~~' -- has more than two segments, so those sites raised ValueError or took
the wrong slice.

The bound value must be the DECODED org ('group/subgroup'), because that is
what the _git_owner column holds (kospex_git sets it from the parsed URL).
"""
import pytest
import sqlite_utils

import kospex_schema as KospexSchema
import kospex_utils as KospexUtils
from kospex_query import KospexData, KospexQuery

NESTED_ORG_KEY = "gitlab.com~group~~subgroup"
NESTED_REPO_ID = "gitlab.com~group~~subgroup~repo"
FLAT_ORG_KEY = "github.com~acme"


def _db():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMITS)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    rows = [
        {"_repo_id": NESTED_REPO_ID, "hash": "h1", "_git_server": "gitlab.com",
         "_git_owner": "group/subgroup", "_git_repo": "repo",
         "author_email": "a@e.com", "committer_when": "2026-01-01T00:00:00Z",
         "author_when": "2026-01-01T00:00:00Z"},
        {"_repo_id": "github.com~acme~svc", "hash": "h2", "_git_server": "github.com",
         "_git_owner": "acme", "_git_repo": "svc",
         "author_email": "a@e.com", "committer_when": "2026-01-01T00:00:00Z",
         "author_when": "2026-01-01T00:00:00Z"},
    ]
    db[KospexSchema.TBL_COMMITS].insert_all(rows, pk=["_repo_id", "hash"])
    db[KospexSchema.TBL_REPOS].insert_all(
        [{"_repo_id": r["_repo_id"], "_git_server": r["_git_server"],
          "_git_owner": r["_git_owner"], "_git_repo": r["_git_repo"]} for r in rows],
        pk="_repo_id")
    return db


@pytest.mark.parametrize("org_key", [NESTED_ORG_KEY, FLAT_ORG_KEY])
def test_repos_accepts_a_nested_org_key(org_key):
    """Raised ValueError('org_key must be of the form <server>~<owner>')."""
    assert KospexQuery(kospex_db=_db()).repos(org_key=org_key) is not None


def test_repos_finds_the_nested_org_repo():
    rows = KospexQuery(kospex_db=_db()).repos(org_key=NESTED_ORG_KEY)
    assert [r["_repo_id"] for r in rows] == [NESTED_REPO_ID]


def test_where_org_key_accepts_a_nested_org_key():
    kd = KospexData(kospex_db=_db())
    kd.from_table(KospexSchema.TBL_COMMITS)
    kd.select("_repo_id")
    kd.where_org_key(NESTED_ORG_KEY)
    assert [r["_repo_id"] for r in kd.execute()] == [NESTED_REPO_ID]


def test_the_bound_owner_is_the_decoded_form():
    """_git_owner holds 'group/subgroup', not the '~~' encoding."""
    assert KospexUtils.parse_org_key(NESTED_ORG_KEY)["org"] == "group/subgroup"


@pytest.mark.parametrize("bad", ["no-tilde", "", None])
def test_a_malformed_org_key_is_still_rejected(bad):
    with pytest.raises(ValueError):
        KospexData(kospex_db=_db()).where_org_key(bad)
