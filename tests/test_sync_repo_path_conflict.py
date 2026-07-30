"""End-to-end tests: Kospex.sync_repo() refuses to repoint a repo at a new clone.

Drives the real sync_repo() against throwaway git repos + a throwaway
KOSPEX_HOME. Two clones of the same remote share a _repo_id, so the second sync
used to silently overwrite repos.file_path - and, because commits are ingested
before update_repo_status(), it merged the second clone's commit data under that
repo_id first.
"""
import os
import shutil
import subprocess

import pytest

from kospex_core import RepoPathConflict

pytestmark = pytest.mark.integration

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(repo, *args, date=None):
    env = {**os.environ, **_GIT_ENV}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _make_repo(path, message="init", date="2025-01-01T00:00:00"):
    """A git repo with a fixed remote, so every copy shares one _repo_id."""
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "remote", "add", "origin", "https://github.com/test/repo.git")
    (path / "app.py").write_text("x = 1\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message, date=date)
    return path


def _kospex(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path / "code"))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    from kospex.habitat_config import HabitatConfig
    HabitatConfig.reset_instance()
    from kospex_core import Kospex
    return Kospex()


def _recorded_path(k, repo_id="github.com~test~repo"):
    row = k.kospex_query.get_repo_by_id(repo_id)
    return row["file_path"] if row else None


def test_first_sync_of_a_new_repo_records_its_path(tmp_path, monkeypatch):
    first = _make_repo(tmp_path / "first")
    k = _kospex(tmp_path, monkeypatch)

    k.sync_repo(str(first))

    assert _recorded_path(k) == str(first)


def test_second_clone_at_a_different_path_is_refused(tmp_path, monkeypatch):
    first = _make_repo(tmp_path / "first")
    second = _make_repo(tmp_path / "second", message="other", date="2025-02-01T00:00:00")
    k = _kospex(tmp_path, monkeypatch)
    k.sync_repo(str(first))

    with pytest.raises(RepoPathConflict) as excinfo:
        k.sync_repo(str(second))

    assert excinfo.value.repo_id == "github.com~test~repo"
    assert excinfo.value.recorded_path == str(first)
    assert excinfo.value.new_path == str(second)
    # the original row is untouched
    assert _recorded_path(k) == str(first)


def test_the_refused_sync_ingests_no_commits(tmp_path, monkeypatch):
    """Commits are written before update_repo_status(), so the guard has to run
    early - refusing at the upsert would leave the second clone's data merged in."""
    first = _make_repo(tmp_path / "first")
    second = _make_repo(tmp_path / "second", message="other", date="2025-02-01T00:00:00")
    k = _kospex(tmp_path, monkeypatch)
    k.sync_repo(str(first))
    before = list(k.kospex_db.query("SELECT hash FROM commits"))

    with pytest.raises(RepoPathConflict):
        k.sync_repo(str(second))

    after = list(k.kospex_db.query("SELECT hash FROM commits"))
    assert after == before


def test_the_refused_sync_restores_the_working_directory(tmp_path, monkeypatch):
    """set_repo_dir() chdirs into the repo - raising must not strand the process."""
    first = _make_repo(tmp_path / "first")
    second = _make_repo(tmp_path / "second", message="other", date="2025-02-01T00:00:00")
    k = _kospex(tmp_path, monkeypatch)
    k.sync_repo(str(first))

    cwd_before = os.getcwd()
    with pytest.raises(RepoPathConflict):
        k.sync_repo(str(second))
    assert os.getcwd() == cwd_before


def test_force_repoints_the_repo_at_the_new_path(tmp_path, monkeypatch):
    first = _make_repo(tmp_path / "first")
    second = _make_repo(tmp_path / "second", message="other", date="2025-02-01T00:00:00")
    k = _kospex(tmp_path, monkeypatch)
    k.sync_repo(str(first))

    k.sync_repo(str(second), force=True)

    assert _recorded_path(k) == str(second)


def test_a_repo_that_moved_repoints_without_force(tmp_path, monkeypatch):
    """The self-healing case: the recorded clone is gone, so the move is genuine."""
    first = _make_repo(tmp_path / "first")
    k = _kospex(tmp_path, monkeypatch)
    k.sync_repo(str(first))

    moved = tmp_path / "moved"
    shutil.move(str(first), str(moved))

    k.sync_repo(str(moved))

    assert _recorded_path(k) == str(moved)


def test_resyncing_the_same_path_is_unaffected(tmp_path, monkeypatch):
    first = _make_repo(tmp_path / "first")
    k = _kospex(tmp_path, monkeypatch)
    k.sync_repo(str(first))

    k.sync_repo(str(first))  # must not raise

    assert _recorded_path(k) == str(first)
