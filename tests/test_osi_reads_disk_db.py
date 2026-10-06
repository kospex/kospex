"""`krunner osi` must query the on-disk DB, not a copy of the estate in RAM.

`load_dependency_memory_db()` copied commit_files, file_metadata, repos and
url_cache into an in-memory database, and it ran BEFORE osi did any scoping --
so `krunner osi <repo_id>` paid a whole-estate copy to look at one repo.

Traced against a live 167-repo DB, the osi path issued 5 statements against
file_metadata and 1 against repos, and **zero** against commit_files or
url_cache. url_cache could not be used even in principle: KospexDependencies is
constructed on the on-disk handle, so the deps.dev cache reads and writes go to
disk regardless. The copy cost 8.74s and ~840MB to save 2.77s of scanning.

There was no test over this at all -- `test_osi_dispatch.py` calls
`extract_dependency_file` and the enrichment loop directly, neither of which
touches the memory DB. So this is also the first test that drives the real `osi`
command end to end.
"""
import json
import os

import pytest
import sqlite_utils
from click.testing import CliRunner

import kospex_schema as KospexSchema

REPO_ID = "github.com~acme~svc"


@pytest.fixture
def osi_env(tmp_path, monkeypatch):
    """A file-backed kospex DB with one repo whose requirements.txt is on disk."""
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    db_path = home / "kospex.db"

    repo_dir = tmp_path / "code" / "github.com" / "acme" / "svc"
    repo_dir.mkdir(parents=True)
    (repo_dir / "requirements.txt").write_text("requests==2.31.0\nclick==8.1.7\n")

    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(db_path))
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path / "code"))
    HabitatConfig.reset_instance()

    # Build it the way a clean install does -- baseline CREATEs then the
    # migration bootstrap -- rather than hand-ALTERing the 0006 columns on top
    # of the baseline, which collides with the bootstrap and warns
    # `duplicate column name`. That is the two-places trap CLAUDE.md names.
    db = KospexSchema.connect_or_create_kospex_db()

    db[KospexSchema.TBL_REPOS].insert({
        "_repo_id": REPO_ID, "_git_server": "github.com",
        "_git_owner": "acme", "_git_repo": "svc",
        "file_path": str(repo_dir),
        "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-02T00:00:00Z",
    }, pk="_repo_id")
    db[KospexSchema.TBL_FILE_METADATA].insert({
        "_repo_id": REPO_ID, "_git_server": "github.com",
        "_git_owner": "acme", "_git_repo": "svc",
        "Provider": "requirements.txt", "Filename": "requirements.txt",
        "hash": "h1", "latest": 1, "tech_type": "|Python|dependencies|",
        "Language": "Python",
    }, pk=["Provider", "hash", "_repo_id"])
    db.close()

    yield db_path
    HabitatConfig.reset_instance()


def _run_osi(monkeypatch, db_path, tmp_path):
    """Drive the real `krunner osi` command, with deps.dev stubbed out."""
    import importlib

    import krunner
    importlib.reload(krunner)   # rebind module-level Kospex() to the test DB

    from kospex_dependencies import KospexDependencies
    monkeypatch.setattr(
        KospexDependencies, "depsdev_record",
        lambda self, ptype, name, version: {
            "package_name": name, "package_version": version,
            "package_type": ptype, "versions_behind": 1,
            "advisories": 0, "resolution": "resolved",
            "published_at": "2026-01-01T00:00:00Z",
        },
    )

    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_path)          # osi writes CSVs relative to cwd
    try:
        return runner.invoke(krunner.cli, ["osi", REPO_ID])
    finally:
        os.chdir(cwd)


def test_osi_writes_dependency_rows_from_the_on_disk_db(osi_env, tmp_path, monkeypatch):
    """The rewired queries must actually find the repo and its manifest."""
    result = _run_osi(monkeypatch, osi_env, tmp_path)

    assert result.exit_code == 0, result.output

    db = sqlite_utils.Database(str(osi_env))
    rows = list(db.query(
        "SELECT package_name, source FROM dependency_data WHERE latest = 1"))
    db.close()

    names = sorted(r["package_name"] for r in rows)
    assert names == ["click", "requests"], f"got {names}"
    assert all(r["source"] == "krunner osi" for r in rows)


def test_osi_does_not_copy_the_estate_into_memory(osi_env, tmp_path, monkeypatch):
    """osi must not build an in-memory copy of the estate to scan one repo.

    Fails if anything in the osi path calls create_memory_kospex_query(). The
    other krunner commands (key-person, developer-tech, dependencies,
    devs-by-tag, authors) legitimately still use it -- this guards osi only.
    """
    from kospex_query import KospexQuery

    calls = []

    def _refuse(self, table_names):
        calls.append(table_names)
        raise AssertionError(
            f"osi built an in-memory DB of {table_names}; it must query the "
            "on-disk DB instead"
        )

    monkeypatch.setattr(KospexQuery, "create_memory_kospex_query", _refuse)

    result = _run_osi(monkeypatch, osi_env, tmp_path)

    assert not calls, f"create_memory_kospex_query called with {calls}"
    assert result.exit_code == 0, result.output
