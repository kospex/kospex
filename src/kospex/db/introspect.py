"""Runtime introspection helpers for the kospex database.

Replaces the hand-maintained KOSPEX_TABLES / REPO_TABLES list constants
that used to live in kospex_schema.py. Reads sqlite_master and PRAGMA
table_info directly, so migrations that add new tables are picked up
automatically.
"""

_TABLE_CACHE: dict[str, set[str]] = {}
_REPO_TABLE_CACHE: dict[str, set[str]] = {}


def _db_key(db) -> str:
    """Stable cache key for a sqlite_utils Database. Uses the underlying file path.

    Falls back to a per-instance key for in-memory databases (where the
    PRAGMA returns an empty path).
    """
    row = db.execute("PRAGMA database_list").fetchone()
    file_path = row[2] if row else ""
    return file_path or f"<mem:{id(db)}>"


def get_kospex_tables(db) -> set[str]:
    """Return the set of user tables in the kospex database."""
    key = _db_key(db)
    if key not in _TABLE_CACHE:
        rows = db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        _TABLE_CACHE[key] = {r[0] for r in rows}
    return _TABLE_CACHE[key]


def get_repo_tables(db) -> set[str]:
    """Return tables that have a _repo_id column (auto-detected via PRAGMA)."""
    key = _db_key(db)
    if key not in _REPO_TABLE_CACHE:
        out = set()
        for t in get_kospex_tables(db):
            cols = [c[1] for c in db.execute(f"PRAGMA table_info([{t}])").fetchall()]
            if "_repo_id" in cols:
                out.add(t)
        _REPO_TABLE_CACHE[key] = out
    return _REPO_TABLE_CACHE[key]


def invalidate_cache(db=None) -> None:
    """Clear cached table lists. Call after applying migrations."""
    if db is None:
        _TABLE_CACHE.clear()
        _REPO_TABLE_CACHE.clear()
    else:
        key = _db_key(db)
        _TABLE_CACHE.pop(key, None)
        _REPO_TABLE_CACHE.pop(key, None)
