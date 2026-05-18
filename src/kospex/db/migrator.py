"""Script-driven SQLite migration runner for kospex.

Replaces the old auto-ALTER `kospex upgrade-db` flow. See
changes/202605-db-migration-system.md for the design.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


FILE_RE = re.compile(r"^(\d{4})_(.+)\.(sql|py)$")


@dataclass(frozen=True)
class Migration:
    id: str             # e.g. '0003_add_widgets'
    sequence: int       # 3
    slug: str           # 'add_widgets'
    sql_path: Path
    py_path: Optional[Path]

    @classmethod
    def from_paths(cls, sql_path: Path, py_path: Optional[Path]) -> "Migration":
        m = FILE_RE.match(sql_path.name)
        if not m:
            raise ValueError(f"Not a migration filename: {sql_path.name}")
        seq, slug, _ext = m.groups()
        return cls(
            id=f"{seq}_{slug}",
            sequence=int(seq),
            slug=slug,
            sql_path=sql_path,
            py_path=py_path,
        )

    def checksum(self) -> str:
        sql_hash = hashlib.sha256(self.sql_path.read_bytes()).hexdigest()
        py_hash = (
            hashlib.sha256(self.py_path.read_bytes()).hexdigest()
            if self.py_path
            else ""
        )
        return f"{sql_hash}:{py_hash}"


def discover_migrations(migrations_dir: Path) -> list[Migration]:
    """Scan a directory for migration files and return them sorted by sequence."""
    if not migrations_dir.exists():
        return []

    sql_files: dict[str, Path] = {}   # id -> path
    py_files: dict[str, Path] = {}    # id -> path

    for path in sorted(migrations_dir.iterdir()):
        m = FILE_RE.match(path.name)
        if not m:
            continue
        seq, slug, ext = m.groups()
        migration_id = f"{seq}_{slug}"
        if ext == "sql":
            sql_files[migration_id] = path
        elif ext == "py":
            py_files[migration_id] = path

    migrations = []
    for migration_id, sql_path in sql_files.items():
        py_path = py_files.get(migration_id)
        migrations.append(Migration.from_paths(sql_path=sql_path, py_path=py_path))

    migrations.sort(key=lambda m: m.sequence)
    return migrations


def validate_migrations(
    migrations: list[Migration],
    migrations_dir: Optional[Path] = None,
) -> None:
    """Run pre-execution validation. Raises ValueError on any fatal issue.

    Fatal issues:
    - Duplicate sequence number
    - Slug mismatch between SQL and Python files (caught by discovery already,
      surfaced again here for defence in depth)
    - Orphan Python file (no matching SQL — requires migrations_dir to detect)
    - Empty SQL file
    - Python file missing `up(db)` function
    """
    # 1. Duplicate sequences
    seen: dict[int, str] = {}
    for m in migrations:
        if m.sequence in seen:
            raise ValueError(
                f"Duplicate migration sequence: {m.sequence} "
                f"('{seen[m.sequence]}' and '{m.id}')"
            )
        seen[m.sequence] = m.id

    # 2. Orphan .py files (need the directory to check)
    if migrations_dir is not None:
        sql_ids = {m.id for m in migrations}
        for path in migrations_dir.iterdir():
            fm = FILE_RE.match(path.name)
            if not fm:
                continue
            seq, slug, ext = fm.groups()
            if ext == "py":
                py_id = f"{seq}_{slug}"
                if py_id not in sql_ids:
                    raise ValueError(
                        f"Orphan Python migration file: {path.name} has no matching .sql"
                    )

    # 3. Empty SQL + 4. Python missing up()
    for m in migrations:
        stripped = "\n".join(
            line for line in m.sql_path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("--")
        )
        if not stripped.strip():
            raise ValueError(f"Migration {m.id} SQL file is empty")

        if m.py_path is not None:
            mod = _load_python_module(m.py_path, m.id)
            if not hasattr(mod, "up") or not callable(mod.up):
                raise ValueError(f"Migration {m.id} Python file is missing 'up' function")


def _load_python_module(py_path: Path, migration_id: str):
    """Load a migration's .py file as a module via importlib."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"_migration_{migration_id}", py_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load Python migration {migration_id} from {py_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Migrator:
    def __init__(self, db, migrations_dir: Optional[Path] = None):
        self.db = db
        self.migrations_dir = migrations_dir or _default_migrations_dir()

    def apply(self, migration: Migration) -> None:
        """Apply one migration in a single transaction. Raises on failure.

        Uses explicit BEGIN / COMMIT / ROLLBACK instead of sqlite3's `with conn:`
        context because `Connection.executescript()` issues an implicit COMMIT
        before running, which would defeat rollback when the Python step fails.
        We also avoid `db["table"].insert()` here because sqlite_utils commits
        its own transaction internally, which would close ours mid-flight.
        """
        started = time.monotonic()
        conn = self.db.conn
        sql = migration.sql_path.read_text()
        try:
            conn.execute("BEGIN")
            # Split on `;` and execute each non-empty statement so DDL stays in tx
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                conn.execute(stmt)

            if migration.py_path is not None:
                mod = _load_python_module(migration.py_path, migration.id)
                mod.up(self.db)

            conn.execute(
                "INSERT INTO schema_migrations "
                "(id, sequence, checksum, applied_at, duration_ms, has_python) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    migration.id,
                    migration.sequence,
                    migration.checksum(),
                    _utcnow_iso(),
                    int((time.monotonic() - started) * 1000),
                    1 if migration.py_path else 0,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def discover(self) -> list[Migration]:
        migrations = discover_migrations(self.migrations_dir)
        validate_migrations(migrations, migrations_dir=self.migrations_dir)
        return migrations

    def applied(self) -> list[str]:
        rows = self.db.execute(
            "SELECT id FROM schema_migrations ORDER BY sequence"
        ).fetchall()
        return [r[0] for r in rows]

    def pending(self) -> list[Migration]:
        applied = set(self.applied())
        return [m for m in self.discover() if m.id not in applied]

    def apply_pending(self) -> list[Migration]:
        """Apply all pending migrations in order. Stop and re-raise on first failure.

        Returns the list of migrations actually applied in this run.
        """
        from kospex.db.introspect import invalidate_cache

        ran: list[Migration] = []
        for migration in self.pending():
            self.apply(migration)
            ran.append(migration)
            invalidate_cache(self.db)

        if ran:
            self._update_version_int()
        return ran

    def _update_version_int(self) -> None:
        """Set KOSPEX_DB_VERSION_KEY in kospex_config to max(baseline, max(sequence))."""
        import kospex_schema as KospexSchema

        max_row = list(self.db.execute(
            "SELECT MAX(sequence) FROM schema_migrations"
        ).fetchall())
        max_seq = max_row[0][0] if max_row and max_row[0][0] is not None else 0
        version = max(KospexSchema.KOSPEX_DB_VERSION, max_seq)

        self.db["kospex_config"].upsert(
            {
                "key": KospexSchema.KOSPEX_DB_VERSION_KEY,
                "value": str(version),
                "format": "INTEGER",
                "latest": 1,
            },
            pk=["key", "latest"],
        )


def _default_migrations_dir() -> Path:
    """Locate the migrations directory shipped with this package."""
    import importlib.resources
    return Path(str(importlib.resources.files("kospex.db") / "migrations"))
