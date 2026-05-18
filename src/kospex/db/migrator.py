"""Script-driven SQLite migration runner for kospex.

Replaces the old auto-ALTER `kospex upgrade-db` flow. See
changes/202605-db-migration-system.md for the design.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
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
