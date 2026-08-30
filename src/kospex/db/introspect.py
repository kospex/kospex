"""Runtime introspection helpers for the kospex database.

Replaces the hand-maintained KOSPEX_TABLES / REPO_TABLES list constants
that used to live in kospex_schema.py. Reads sqlite_master and PRAGMA
table_info directly, so migrations that add new tables are picked up
automatically.

Results are cached per database FILE. In-memory databases are deliberately not
cached — see _db_key().
"""

_TABLE_CACHE: dict[str, set[str]] = {}
_REPO_TABLE_CACHE: dict[str, set[str]] = {}


def _db_key(db):
    """Cache key for a sqlite_utils Database: its file path, or None if in-memory.

    A None key means "do not cache". In-memory databases have no stable identity
    to key on. This previously fell back to f"<mem:{id(db)}>", described as a
    per-instance key — but id() is unique only among *live* objects, and CPython
    reuses addresses aggressively. A new in-memory Database landing on a freed
    address inherited the dead one's table set: measured at 292 stale reads in
    300, from only 8 distinct keys.

    That is not a test-only concern. KospexData validates every table name
    against get_kospex_tables() before interpolating it into SQL, so a stale
    answer rejects tables that exist — it surfaced as an intermittent
    `ValueError: Table 'commits' is not a known Kospex table` (#184).
    KospexQuery.create_memory_kospex_query(), which `krunner osi` uses, builds
    exactly such a database.

    Not caching them costs ~1.1us per call against ~10us for a file-backed read,
    so there is nothing to protect. Caching an in-memory database would also be
    wrong in principle: they are built up table by table at runtime, so a cached
    set would need invalidating on every schema change, where a file-backed
    schema only moves under a migration (which already calls invalidate_cache).
    """
    row = db.execute("PRAGMA database_list").fetchone()
    file_path = row[2] if row else ""
    return file_path or None


def get_kospex_tables(db) -> set[str]:
    """Return the set of user tables in the kospex database."""
    key = _db_key(db)
    if key is not None and key in _TABLE_CACHE:
        return _TABLE_CACHE[key]

    rows = db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {r[0] for r in rows}

    if key is not None:
        _TABLE_CACHE[key] = tables
    return tables


def get_repo_tables(db) -> set[str]:
    """Return tables that have a _repo_id column (auto-detected via PRAGMA)."""
    key = _db_key(db)
    if key is not None and key in _REPO_TABLE_CACHE:
        return _REPO_TABLE_CACHE[key]

    out = set()
    for t in get_kospex_tables(db):
        cols = [c[1] for c in db.execute(f"PRAGMA table_info([{t}])").fetchall()]
        if "_repo_id" in cols:
            out.add(t)

    if key is not None:
        _REPO_TABLE_CACHE[key] = out
    return out


def invalidate_cache(db=None) -> None:
    """Clear cached table lists. Call after applying migrations.

    A no-op for in-memory databases, which are never cached.
    """
    if db is None:
        _TABLE_CACHE.clear()
        _REPO_TABLE_CACHE.clear()
        return

    key = _db_key(db)
    if key is not None:
        _TABLE_CACHE.pop(key, None)
        _REPO_TABLE_CACHE.pop(key, None)
