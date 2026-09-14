"""A repo kospex has never seen must not report as having no developers (#134).

`kospex developers -repo .` on an unsynced repository produced output identical
to a synced-but-dormant one:

    No active developers found in the kospex DB
    Found 0 active developers in the last 90 days.

The repo in the reported case had 137 commits, the most recent that week. The
wrong reading is the natural one, and for a tool whose claim is identifying
unmaintained code a false "unmaintained" is the most damaging answer available.

Three states collapse into that one output:
  1. not in the database at all      -> needs a sync
  2. in `repos` but no commit rows   -> the sync did not complete
  3. in the database, no commits in the window -> genuinely dormant
"""
import pytest
import sqlite_utils

import kospex_schema as KospexSchema
from kospex_core import Kospex

KNOWN = "github.com~acme~synced"
UNKNOWN = "github.com~acme~never-synced"
EMPTY = "github.com~acme~no-commits"


@pytest.fixture
def kospex():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_COMMITS)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db[KospexSchema.TBL_REPOS].insert_all([
        {"_repo_id": KNOWN, "_git_server": "github.com",
         "_git_owner": "acme", "_git_repo": "synced"},
        {"_repo_id": EMPTY, "_git_server": "github.com",
         "_git_owner": "acme", "_git_repo": "no-commits"},
    ], pk="_repo_id")
    db[KospexSchema.TBL_COMMITS].insert_all([
        {"_repo_id": KNOWN, "hash": "h1", "author_email": "a@e.com",
         "author_when": "2020-01-01T00:00:00Z",
         "committer_when": "2020-01-01T00:00:00Z"},
    ], pk=["_repo_id", "hash"])
    k = Kospex(kospex_db=db)
    return k


def test_a_repo_never_synced_is_reported_as_unknown(kospex):
    status = kospex.repo_db_status(UNKNOWN)
    assert status["known"] is False
    assert "sync" in status["message"].lower()


def test_a_synced_repo_is_reported_as_known(kospex):
    assert kospex.repo_db_status(KNOWN)["known"] is True


def test_a_repo_with_no_commits_is_distinguished_from_both(kospex):
    """In `repos` but no commit rows -- the sync did not complete.

    Reports as known (so the caller does not tell the user to sync a repo it
    already has) but flags the missing commits separately.
    """
    status = kospex.repo_db_status(EMPTY)
    assert status["known"] is True
    assert status["has_commits"] is False


def test_the_suggested_command_exists(kospex):
    """`kospex sync` does not exist -- it is commented out and parked (#123).

    The previous hotspot message told users to run it.
    """
    message = kospex.repo_db_status(UNKNOWN, repo_directory="/tmp/x")["message"]
    assert "sync-directory" in message
    assert "kospex sync /" not in message
