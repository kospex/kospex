"""The work queue for `krunner osi -next`, and the extraction-outcome record.

Design: changes/202610-krunner-osi-next.md

`osi -all` is a single sweep that takes hours on a large estate, so it cannot run
from cron. `-next N` takes the N repositories least recently examined instead, and
a schedule of small runs converges on full coverage.

The queue cannot come from `dependency_data`. Ordering rows by `last_checked`
never selects a repository that produced no rows, so a newly added manifest in a
previously dependency-free repository is never discovered. The mirror-image
mistake -- ordering `repos` by MAX(last_checked) -- leaves those repositories
permanently at the head and never reaches the rest. On a 167-repo estate that was
85 repositories, only 9 of which genuinely have no dependency files.

Both failures need the same thing: a record that a repository was examined and
what happened. That is #148, and this module writes it.
"""
import json
from datetime import datetime, timezone
from typing import Optional

import kospex_schema as KospexSchema
import kospex_utils as KospexUtils

# The observation_key under which an extraction outcome is recorded. One row per
# repository, replaced in place -- see record_outcome().
OBSERVATION_KEY = "OSI_EXTRACTION"

# Repo-level observations: the PK is
# (_repo_id, hash, file_path, observation_key, latest), and neither a commit hash
# nor a file path identifies a whole-repository fact. Empty strings rather than
# NULL, because NULL columns in a SQLite primary key do not compare equal and so
# would let duplicate rows accumulate.
_REPO_LEVEL_HASH = ""
_REPO_LEVEL_FILE_PATH = ""

# ORDER BY puts never-examined repositories first, then least recently examined.
#
# `(o.created_at IS NULL) DESC` rather than `ORDER BY o.created_at ASC NULLS
# FIRST`: NULLS FIRST requires SQLite 3.30 and kospex supports 3.26 (RHEL 8).
# conftest's guard refuses newer *functions* but not newer syntax, so the
# unportable form would pass CI here and fail on a customer's box.
#
# The trailing `r._repo_id ASC` makes the order total, so a batch is reproducible
# and the round-robin cannot stall between repositories sharing a timestamp.
QUEUE_SQL = """
SELECT r._repo_id
  FROM {repos} r
  LEFT JOIN {observations} o
         ON o._repo_id = r._repo_id
        AND o.observation_key = ?
        AND o.latest = 1
 {where}
 ORDER BY (o.created_at IS NULL) DESC, o.created_at ASC, r._repo_id ASC
 LIMIT ?
""".format(
    repos=KospexSchema.TBL_REPOS,
    observations=KospexSchema.TBL_OBSERVATIONS,
    where="{where}",
)


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scope_clause(request_id):
    """Return (sql_fragment, params) for a scope, or raise if unsupported.

    A falsy request_id means "no scope requested", which is the whole estate.
    A request_id carrying a scope this query cannot honour raises rather than
    falling through to an unscoped query -- silently widening is the defect #158
    was about, where /osi/ given an author email returned every row.
    """
    if not request_id:
        return "", []

    if repo_id := request_id.get("repo_id"):
        return "WHERE r._repo_id = ?", [repo_id]

    if org_key := request_id.get("org_key"):
        # Parsed, never split by hand: a '/' in the owner (GitLab subgroups) is
        # encoded '~~', so an org_key can hold more than two segments.
        from kospex_query import _org_key_parts
        server, org = _org_key_parts(org_key)
        return "WHERE r._git_server = ? AND r._git_owner = ?", [server, org]

    if server := request_id.get("server"):
        return "WHERE r._git_server = ?", [server]

    raise ValueError(
        f"cannot scope the osi queue by {sorted(request_id)} "
        "-- supported scopes are repo_id, org_key, server"
    )


def next_repos(db, limit, request_id=None):
    """The next `limit` repositories due for extraction, in priority order.

    Never examined first, then least recently examined, then by repo_id so the
    order is total. Returns a list of repo_id strings, possibly empty -- an empty
    batch means nothing is due, which is success, not an error.
    """
    where, params = _scope_clause(request_id)
    sql = QUEUE_SQL.format(where=where)
    rows = db.query(sql, [OBSERVATION_KEY] + params + [limit])
    return [r["_repo_id"] for r in rows]


def record_outcome(db, repo_id, outcomes, when=None):
    """Record that `repo_id` was examined, and what each of its files produced.

    `outcomes` maps file_path -> outcome from the vocabulary in the design doc
    (extracted, empty, unsupported, unclassified, not_a_manifest, parse_error).
    An empty mapping is meaningful and must be recorded: it says "examined, has
    no dependency files", which is what stops those repositories being retried
    forever.

    Replaced in place at latest=1, never demoted. `latest` is part of the
    observations primary key, so the usual kospex demote-then-insert pattern
    raises on the THIRD write for a given key -- demoting the current row to
    latest=0 collides with the previous one already in that slot:

        UNIQUE constraint failed: observations._repo_id, observations.hash,
        observations.file_path, observations.observation_key, observations.latest

    The consequence is that this table holds no history. That is fine for the
    queue, which only needs the current state, and is why run-level diagnostics
    need a table of their own.
    """
    row = {
        "_repo_id": repo_id,
        "hash": _REPO_LEVEL_HASH,
        "file_path": _REPO_LEVEL_FILE_PATH,
        "observation_key": OBSERVATION_KEY,
        "observation_type": "REPO",
        "latest": 1,
        "format": "JSON",
        "data": json.dumps(outcomes, sort_keys=True),
        "source": "krunner osi -next",
        "created_at": when or _utc_now_iso(),
    }

    # parse_repo_id returns None for a nested-group id (#94), so the _git_*
    # columns are left unset rather than guessed. They are derived convenience
    # columns; _repo_id is the identity and is always present.
    if parts := KospexUtils.parse_repo_id(repo_id):
        row["_git_server"] = parts["git_server"]
        row["_git_owner"] = parts["org"]
        row["_git_repo"] = parts["repo"]

    db[KospexSchema.TBL_OBSERVATIONS].upsert(
        row,
        pk=["_repo_id", "hash", "file_path", "observation_key", "latest"],
        alter=False,
    )


def get_outcome(db, repo_id) -> Optional[dict]:
    """The current extraction outcome for `repo_id`, or None if never examined."""
    sql = (
        f"SELECT created_at, data FROM {KospexSchema.TBL_OBSERVATIONS} "
        "WHERE _repo_id = ? AND observation_key = ? AND latest = 1"
    )
    row = next(iter(db.query(sql, [repo_id, OBSERVATION_KEY])), None)
    if row is None:
        return None
    return {
        "repo_id": repo_id,
        "examined_at": row["created_at"],
        "files": json.loads(row["data"]) if row["data"] else {},
    }
