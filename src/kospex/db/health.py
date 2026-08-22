"""Read-only health reporting for the kospex database.

Single source of DB status for `kospex init --validate`, `kospex init` and
`kospex system-status`, so the three cannot drift. Nothing here creates or
modifies the database.
"""
import os
import sqlite3


def db_status(db=None) -> dict:
    """Describe the kospex database without touching it.

    Every import is inside the function: kospex_schema imports kospex_utils,
    and kospex/db/migrator.py imports kospex_schema, so module-level imports
    here would cycle.

    `created_this_run` / `migrations_applied_this_run` come from the bootstrap
    record rather than being re-derived. The module-level Kospex() in each CLI
    runs at import, so "does the DB exist?" is always true by the time anything
    asks — the record is what makes the answer honest.
    """
    import sqlite_utils
    import kospex_schema as KospexSchema
    import kospex_utils as KospexUtils
    from kospex.db.migrator import Migrator, _current_version

    path = KospexUtils.get_kospex_db_path()
    exists = os.path.isfile(path)
    parent = os.path.dirname(path) or "."

    status = {
        "path": path,
        "exists": exists,
        "writable": os.access(path, os.W_OK) if exists else os.access(parent, os.W_OK),
        "version": "unknown",
        "applied_count": 0,
        "pending_count": 0,
        "pending_ids": [],
        "schema_migrations_present": False,
        "created_this_run": KospexSchema.LAST_BOOTSTRAP["created"],
        "migrations_applied_this_run": KospexSchema.LAST_BOOTSTRAP["migrations_applied"],
        "migration_error": KospexSchema.LAST_BOOTSTRAP["migration_error"],
    }

    if not exists:
        return status

    if db is None:
        # Safe: the file exists, so this opens rather than creates.
        db = sqlite_utils.Database(path)

    status["version"] = _current_version(db)

    migrator = Migrator(db)
    try:
        applied = migrator.applied()
        status["schema_migrations_present"] = True
        status["applied_count"] = len(applied)
    except sqlite3.OperationalError:
        applied = []

    try:
        discovered = migrator.discover()
    except Exception:
        discovered = []

    pending = [m for m in discovered if m.id not in set(applied)]
    status["pending_count"] = len(pending)
    status["pending_ids"] = [m.id for m in pending]

    return status
