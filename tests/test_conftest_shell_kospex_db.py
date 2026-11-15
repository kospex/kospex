"""A KOSPEX_DB exported in the shell must not reach the tests.

HabitatConfig reads KOSPEX_DB before KOSPEX_HOME, so a test isolated by
KOSPEX_HOME alone would write to the DB the shell names. Runs pytest in a
subprocess with KOSPEX_DB pointing at a migrated DB, over a test that deletes
every schema_migrations row from its own DB, and checks the shell's DB kept them.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _migration_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    finally:
        conn.close()


def test_a_shell_kospex_db_is_not_used_by_the_tests(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("KOSPEX_")}
    env["PYTHONPATH"] = str(ROOT / "src")
    shell_home = tmp_path / "shell"
    shell_home.mkdir()
    subprocess.run(
        [sys.executable, "-c", "import kospex_schema; kospex_schema.connect_or_create_kospex_db()"],
        env=env | {"KOSPEX_HOME": str(shell_home)}, cwd=ROOT, check=True,
    )
    shell_db = shell_home / "kospex.db"
    before = _migration_rows(shell_db)
    assert before > 0

    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_migration_banner_cli.py"],
        env=env | {"KOSPEX_DB": str(shell_db)}, cwd=ROOT, capture_output=True, text=True,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert _migration_rows(shell_db) == before
