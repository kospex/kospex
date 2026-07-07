"""Tests for the `kospex upgrade-db` CLI command and the shipped migrations."""
from pathlib import Path

from click.testing import CliRunner


def _setup_kospex_home(monkeypatch, tmp_path: Path) -> Path:
    """Point KOSPEX_HOME/KOSPEX_DB at an isolated tmpdir so the CLI uses a
    throwaway DB (HabitatConfig singleton reset is handled by the autouse
    fixture in conftest.py)."""
    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    return home


def test_upgrade_db_status_is_dry_run(monkeypatch, tmp_path):
    """Status mode prints the version + backup warning and exits cleanly."""
    _setup_kospex_home(monkeypatch, tmp_path)

    from kospex_cli import cli
    result = CliRunner().invoke(cli, ["upgrade-db"])

    assert result.exit_code == 0, result.output
    assert "Kospex CLI version" in result.output
    assert "WARNING: backup your database" in result.output


def test_shipped_0003_adds_repos_provenance_columns(tmp_path):
    """The shipped 0003 migration applies via the real migrator and adds the
    sync-provenance columns to repos. Driven directly (not through the CLI) so
    it doesn't depend on global KOSPEX_HOME/DB path resolution."""
    import sqlite_utils
    import kospex_schema as KospexSchema
    from kospex.db.migrator import Migrator

    db = sqlite_utils.Database(tmp_path / "kospex.db")
    db.execute(KospexSchema.SQL_CREATE_REPOS)
    db.execute(
        "CREATE TABLE schema_migrations ("
        "id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, checksum TEXT NOT NULL, "
        "applied_at TEXT NOT NULL, duration_ms INTEGER, has_python INTEGER NOT NULL)"
    )

    Migrator(db).apply_pending()  # default dir = the shipped migrations (incl 0003)

    cols = {r[1] for r in db.execute("PRAGMA table_info(repos)")}
    assert {"last_sync_hash", "last_panopticas_version", "last_scc_version"} <= cols

    # Re-applying is a clean no-op (already recorded in schema_migrations).
    assert Migrator(db).apply_pending() == []
