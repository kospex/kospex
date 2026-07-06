"""End-to-end sync tests for the single current-state row-per-file behaviour.

Drives the real Kospex.sync_repo() against a throwaway git repo + a throwaway
KOSPEX_HOME (no shared DB touched), exercising panopticas + scc + the commit
lookup + the single-row builder together.
"""
import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.integration

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
}


@pytest.fixture(autouse=True)
def _isolate_kospex_env():
    """Kospex()/KospexUtils init writes KOSPEX_CONFIG/KOSPEX_LOGS/etc. straight to
    os.environ (not via monkeypatch). Snapshot and restore all KOSPEX_* vars, and
    reset the HabitatConfig singleton, so this test doesn't leak into others."""
    keys = ("KOSPEX_HOME", "KOSPEX_DB", "KOSPEX_CONFIG", "KOSPEX_CODE", "KOSPEX_LOGS")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    from kospex.habitat_config import HabitatConfig
    HabitatConfig.reset_instance()


def _git(repo, *args, date=None):
    env = {**os.environ, **_GIT_ENV}
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, env=env,
    )


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "https://github.com/test/repo.git")
    (repo / "app.py").write_text("def hi():\n    return 1\n")
    (repo / "README.md").write_text("# hello\n")
    _git(repo, "add", "-A")
    # Explicit, distinct commit dates: latest_commit_file_map orders by
    # committer_when, so same-second commits would tie ambiguously.
    _git(repo, "commit", "-q", "-m", "init", date="2025-01-01T00:00:00")
    return repo


def _kospex(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path / "code"))
    # Pin KOSPEX_DB explicitly: KospexUtils init writes it straight to os.environ
    # (not via monkeypatch), so it would otherwise leak a prior test's path, and
    # HabitatConfig.db_path reads KOSPEX_DB ahead of KOSPEX_HOME.
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    # get_kospex_db_path() resolves via the HabitatConfig singleton, which caches
    # its config from first construction — reset it so each test gets its own DB.
    from kospex.habitat_config import HabitatConfig
    HabitatConfig.reset_instance()
    from kospex_core import Kospex
    return Kospex()


def _latest(db):
    return list(db.query(
        "SELECT Provider, hash, committer_when, Lines, latest "
        "FROM file_metadata WHERE latest = 1"
    ))


def test_sync_writes_exactly_one_row_per_file(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    k = _kospex(tmp_path, monkeypatch)

    k.sync_repo(str(repo))

    rows = _latest(k.kospex_db)
    by = {r["Provider"] for r in rows}
    assert by == {"app.py", "README.md"}
    assert len(rows) == 2  # exactly one latest=1 row per file

    # committer_when populated for ALL files (not just scc-known ones)
    assert all(r["committer_when"] for r in rows)

    # scc knows Python -> app.py carries metrics
    app = next(r for r in rows if r["Provider"] == "app.py")
    assert app["Lines"] is not None


def test_resync_after_change_churns_only_the_changed_file(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    k = _kospex(tmp_path, monkeypatch)
    k.sync_repo(str(repo))
    before = {r["Provider"]: r["hash"] for r in _latest(k.kospex_db)}

    # change one file and commit at a later date -> HEAD moves
    (repo / "app.py").write_text("def hi():\n    return 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change app", date="2025-06-01T00:00:00")

    k.sync_repo(str(repo))
    after = {r["Provider"]: r["hash"] for r in _latest(k.kospex_db)}

    # unchanged file keeps its hash (no churn); changed file gets a new hash
    assert after["README.md"] == before["README.md"]
    assert after["app.py"] != before["app.py"]

    # still exactly one latest=1 row per file
    counts = {
        r["Provider"]: r["n"] for r in k.kospex_db.query(
            "SELECT Provider, SUM(latest) AS n FROM file_metadata GROUP BY Provider"
        )
    }
    assert counts == {"README.md": 1, "app.py": 1}
