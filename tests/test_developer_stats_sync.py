"""developer_stats written by a real sync, and by the rebuild task (#352).

Drives the real Kospex.sync_repo() against a throwaway git repo and a throwaway
KOSPEX_HOME, then runs `kospex rebuild developer-stats` over the same DB. A sync
and a rebuild go through KospexQuery.refresh_developer_stats(), so they must
write the same rows, with every column filled in.
"""
import os
import subprocess

import pytest

pytestmark = pytest.mark.integration

_GIT_ENV = {"GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}

FILE_COLUMNS = ("additions", "deletions", "unique_files")


@pytest.fixture(autouse=True)
def _isolate_kospex_env():
    """Kospex() writes KOSPEX_* straight to os.environ, not via monkeypatch.
    Restore them, and reset the HabitatConfig singleton, so nothing leaks."""
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


def _commit(repo, author, date, files):
    for name, text in files.items():
        (repo / name).write_text(text)
    env = {**os.environ, **_GIT_ENV, "GIT_AUTHOR_NAME": author.split("@")[0],
           "GIT_AUTHOR_EMAIL": author, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    for args in (["add", "-A"], ["commit", "-q", "-m", f"change by {author}"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def synced(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "https://github.com/acme/repo.git"], check=True, capture_output=True)
    _commit(repo, "dev0@example.com", "2025-01-01T00:00:00", {"a.py": "a = 1\n", "b.py": "b = 1\n"})
    _commit(repo, "dev1@example.com", "2025-02-01T00:00:00", {"a.py": "a = 2\nc = 3\n"})

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path / "code"))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    from kospex.habitat_config import HabitatConfig
    HabitatConfig.reset_instance()
    from kospex_core import Kospex

    k = Kospex()
    k.sync_repo(str(repo))
    return k, "github.com~acme~repo"


def _rows(db, repo_id):
    return sorted(
        (r["author_email"], r["total_commits"], *(r[c] for c in FILE_COLUMNS))
        for r in db.query("SELECT * FROM developer_stats WHERE _repo_id = ?", [repo_id])
    )


def test_sync_writes_every_developer_stats_column(synced):
    k, repo_id = synced
    assert _rows(k.kospex_db, repo_id) == [
        # author, commits, additions, deletions, unique_files
        ("dev0@example.com", 1, 2, 0, 2),
        ("dev1@example.com", 1, 2, 1, 1),
    ]


def test_rebuild_writes_the_same_rows_as_a_sync(synced):
    import kospex_rebuild as KospexRebuild
    from kospex_query import KospexQuery

    k, repo_id = synced
    after_sync = _rows(k.kospex_db, repo_id)

    kq = KospexQuery(kospex_db=k.kospex_db)
    results = KospexRebuild.run_task(KospexRebuild.TASKS["developer-stats"],
                                     KospexRebuild.resolve_scope(kq, repo_id=repo_id), kq)
    assert [r.status for r in results] == ["ok"]
    assert _rows(k.kospex_db, repo_id) == after_sync


def test_sync_uses_refresh_developer_stats(synced, monkeypatch):
    # Sync and rebuild share one method so they can't drift apart again (#352).
    from kospex_query import KospexQuery

    k, repo_id = synced
    called = []
    real = KospexQuery.refresh_developer_stats
    monkeypatch.setattr(KospexQuery, "refresh_developer_stats",
                        lambda self, rid: called.append(rid) or real(self, rid))
    k.sync_repo(k.kospex_db.execute(
        "SELECT file_path FROM repos WHERE _repo_id = ?", [repo_id]).fetchone()[0])
    assert called == [repo_id]
