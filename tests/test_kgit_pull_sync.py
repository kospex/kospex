"""End-to-end test for `kgit pull` against throwaway git repos + KOSPEX_HOME."""
import os
import subprocess

import pytest
from click.testing import CliRunner

pytestmark = pytest.mark.integration

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
}


def _git(cwd, *args, date=None):
    env = {**os.environ, **_GIT_ENV}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, env=env)


def _head(path):
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _setup(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    from kospex.habitat_config import HabitatConfig
    HabitatConfig.reset_instance()

    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)

    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    (work / "app.py").write_text("x = 1\n")
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "c1", date="2025-01-01T00:00:00")
    _git(work, "push", "-q", "origin", "main")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    return work, bare, clone


def test_kgit_pull_fast_forwards_stamps_and_syncs(tmp_path, monkeypatch):
    work, bare, clone = _setup(tmp_path, monkeypatch)

    from kgit import cli, kospex as kgit_kospex
    from kospex.db.migrator import Migrator
    import kospex_utils as KospexUtils

    # Ensure kgit's module-level kospex uses the throwaway DB.
    # When kgit is imported for the first time in this process (after _setup sets env
    # vars), kgit_kospex.kospex_db is already connected to the throwaway.
    # When kgit was already imported earlier (e.g. by test_kgit_pull.py), it holds the
    # real DB connection. In that case we reconnect it to the throwaway.
    throwaway_db_path = str(tmp_path / "home" / "kospex.db")
    current_db_path = kgit_kospex.kospex_db.conn.execute(
        "PRAGMA database_list"
    ).fetchone()[2]
    import kospex_schema as KospexSchema
    from kospex_query import KospexQuery as _KQ
    if os.path.realpath(current_db_path) != os.path.realpath(throwaway_db_path):
        # kgit was imported with the real DB; reconnect to throwaway (different file,
        # so no lock conflict).
        kgit_kospex.kospex_db = KospexSchema.connect_or_create_kospex_db()
        kgit_kospex.kospex_query = _KQ(kospex_db=kgit_kospex.kospex_db)

    # Commit any open schema-bootstrap transaction so Migrator can BEGIN cleanly.
    if kgit_kospex.kospex_db.conn.in_transaction:
        kgit_kospex.kospex_db.conn.commit()

    Migrator(kgit_kospex.kospex_db).apply_pending()   # repos.last_fetch exists (0004)
    kgit_kospex.sync_repo(str(clone))                 # register repo in throwaway DB

    # advance upstream by one commit
    (work / "app.py").write_text("x = 2\n")
    _git(work, "add", "-A"); _git(work, "commit", "-q", "-m", "c2", date="2025-06-01T00:00:00")
    _git(work, "push", "-q", "origin", "main")
    upstream_head = _head(work)

    result = CliRunner().invoke(cli, ["pull", "--all"])
    assert result.exit_code == 0, result.output
    assert "updated" in result.output.lower()
    assert _head(clone) == upstream_head              # clone fast-forwarded to c2

    import sqlite3
    db = sqlite3.connect(KospexUtils.get_kospex_db_path())
    row = db.execute("SELECT _repo_id, last_fetch FROM repos").fetchone()
    assert row is not None and row[1] is not None      # last_fetch stamped
