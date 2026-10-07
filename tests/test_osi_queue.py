"""The `krunner osi -next` work queue and its extraction-outcome record.

Design: changes/202610-krunner-osi-next.md

Two things are being established here.

**The outcome record.** Nothing today records that a repository was examined, so
a repository producing zero dependency rows is indistinguishable from one never
scanned. On a 167-repo estate that was 85 repositories (51%), only 9 of which
genuinely have no dependency files. It is why neither obvious queue works: one
built from `dependency_data` never selects those 85, and one built from `repos`
keyed on dependency rows leaves them permanently at the front. See #148.

**The queue.** Repositories ordered never-examined first, then least recently
examined. A daily full pass is the requirement, so this is round-robin with a
deadline rather than a priority scheme.
"""
import sqlite_utils

import kospex_schema as KospexSchema
from kospex.osi_queue import (
    OBSERVATION_KEY,
    get_outcome,
    next_repos,
    record_outcome,
)

R1 = "github.com~acme~alpha"
R2 = "github.com~acme~beta"
R3 = "gitlab.com~other~gamma"


def _db(*repo_ids):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db.execute(KospexSchema.SQL_CREATE_OBSERVATIONS)
    for rid in repo_ids:
        server, owner, repo = rid.split("~")
        db[KospexSchema.TBL_REPOS].insert(
            {"_repo_id": rid, "_git_server": server,
             "_git_owner": owner, "_git_repo": repo},
            pk="_repo_id",
        )
    return db


# --- the outcome record ------------------------------------------------------

def test_an_examined_repo_gets_an_outcome_record():
    db = _db(R1)

    record_outcome(db, R1, {"requirements.txt": "extracted"}, when="2026-10-07T09:00:00Z")

    outcome = get_outcome(db, R1)
    assert outcome is not None
    assert outcome["examined_at"] == "2026-10-07T09:00:00Z"
    assert outcome["files"] == {"requirements.txt": "extracted"}


def test_a_never_examined_repo_has_no_outcome():
    db = _db(R1)
    assert get_outcome(db, R1) is None


def test_a_repo_with_no_dependency_files_still_gets_a_record():
    """The whole point: 'examined, found nothing' must be recordable.

    Without this the 9 repositories that genuinely have no dependency files are
    indistinguishable from never-scanned ones, and the queue never terminates.
    """
    db = _db(R1)

    record_outcome(db, R1, {}, when="2026-10-07T09:00:00Z")

    outcome = get_outcome(db, R1)
    assert outcome is not None, "a repo with nothing to find must still be marked examined"
    assert outcome["files"] == {}


def test_recording_an_outcome_survives_a_third_run():
    """observations' PK includes `latest`, so demote-then-insert breaks on run 3.

    `UPDATE ... SET latest = 0` then insert at latest=1 succeeds twice and then
    raises, because demoting run 2 collides with run 1 already occupying the
    latest=0 slot:

        UNIQUE constraint failed: observations._repo_id, observations.hash,
        observations.file_path, observations.observation_key, observations.latest

    So the record is replaced in place and never demoted. This test exists because
    the demote pattern is idiomatic elsewhere in kospex and will be reintroduced.
    """
    db = _db(R1)

    for n in range(1, 6):
        record_outcome(db, R1, {"a.txt": "extracted"}, when=f"2026-10-0{n}T00:00:00Z")

    rows = list(db.query(
        "SELECT created_at FROM observations WHERE _repo_id = ? AND observation_key = ?",
        [R1, OBSERVATION_KEY]))
    assert len(rows) == 1, f"expected one current row, got {len(rows)}"
    assert get_outcome(db, R1)["examined_at"] == "2026-10-05T00:00:00Z"


# --- the queue ---------------------------------------------------------------

def test_never_examined_repos_come_first():
    db = _db(R1, R2, R3)
    record_outcome(db, R1, {}, when="2026-10-07T09:00:00Z")

    assert next_repos(db, 3)[:2] == [R2, R3] or next_repos(db, 3)[:2] == [R3, R2]
    assert next_repos(db, 3)[2] == R1, "the examined repo must sort last"


def test_examined_repos_are_ordered_oldest_first():
    db = _db(R1, R2, R3)
    record_outcome(db, R1, {}, when="2026-10-05T00:00:00Z")
    record_outcome(db, R2, {}, when="2026-10-01T00:00:00Z")
    record_outcome(db, R3, {}, when="2026-10-03T00:00:00Z")

    assert next_repos(db, 3) == [R2, R3, R1]


def test_the_batch_size_is_respected():
    db = _db(R1, R2, R3)
    record_outcome(db, R1, {}, when="2026-10-05T00:00:00Z")
    record_outcome(db, R2, {}, when="2026-10-01T00:00:00Z")
    record_outcome(db, R3, {}, when="2026-10-03T00:00:00Z")

    assert next_repos(db, 2) == [R2, R3]
    assert next_repos(db, 1) == [R2]


def test_an_examined_repo_is_not_selected_again_before_the_others():
    """A tick must advance the queue, not re-serve the same repo."""
    db = _db(R1, R2, R3)

    first = next_repos(db, 1)[0]
    record_outcome(db, first, {}, when="2026-10-07T09:00:00Z")

    assert next_repos(db, 1)[0] != first


def test_the_queue_can_be_scoped():
    """`-next N REQUEST_ID` batches within a scope rather than the whole estate."""
    db = _db(R1, R2, R3)

    scoped = next_repos(db, 10, request_id={"org_key": "github.com~acme"})

    assert set(scoped) == {R1, R2}, f"got {scoped}"


def test_an_empty_estate_yields_an_empty_batch():
    """Nothing due is not an error -- cron must not see a failure."""
    db = _db()
    assert next_repos(db, 5) == []


def test_ordering_does_not_use_nulls_first_syntax():
    """NULLS FIRST needs SQLite 3.30; kospex supports 3.26 (RHEL 8).

    conftest's guard only refuses newer *functions*, not newer syntax, so this
    would pass CI here and fail on a customer's box. Asserted against the SQL
    rather than trusted to review.
    """
    from kospex.osi_queue import QUEUE_SQL
    assert "NULLS FIRST" not in QUEUE_SQL.upper()
    assert "NULLS LAST" not in QUEUE_SQL.upper()


def test_a_scope_the_queue_cannot_honour_raises_instead_of_widening():
    """Regression guard, not TDD -- the raise was written with the module.

    A scope that cannot be applied must not fall through to an unscoped query.
    That silent widening is #158: /osi/ and /dependencies/ given a base64
    author_email ignored it and returned every row in the table.
    """
    import pytest
    db = _db(R1, R2, R3)

    with pytest.raises(ValueError, match="cannot scope"):
        next_repos(db, 5, request_id={"author_email": "someone@example.com"})
