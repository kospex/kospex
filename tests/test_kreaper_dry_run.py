"""Tests for `kreaper delete-repo -dry-run` and the counts behind it.

delete-repo clears a repo from every table carrying a _repo_id column, which
includes `repos` — so the sync provenance goes too and the next sync walks full
history. That makes it the supported way to reset a repo before a re-sync, and
a destructive operation worth being able to inspect first.
"""
import os

import pytest
from click.testing import CliRunner


@pytest.fixture
def kospex_env(tmp_path, monkeypatch):
    """A throwaway KOSPEX_HOME + DB. Kospex() connects on construction, so the
    env has to be set before it is built."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    monkeypatch.setenv("KOSPEX_CODE", str(tmp_path / "code"))
    from kospex.habitat_config import HabitatConfig
    HabitatConfig.reset_instance()
    yield home
    HabitatConfig.reset_instance()


def _seed(kospex, repo_id="github.com~acme~demo", other="github.com~acme~other"):
    """Rows in three repo tables, plus a second repo that must survive."""
    import kospex_schema as KospexSchema

    kospex.kospex_db.table(KospexSchema.TBL_COMMITS).upsert_all([
        {"hash": "a1", "_repo_id": repo_id, "author_email": "a@e.com"},
        {"hash": "a2", "_repo_id": repo_id, "author_email": "a@e.com"},
        {"hash": "b1", "_repo_id": other, "author_email": "b@e.com"},
    ], pk=["_repo_id", "hash"])

    kospex.kospex_db.table(KospexSchema.TBL_COMMIT_FILES).upsert_all([
        {"hash": "a1", "file_path": "x.py", "_repo_id": repo_id},
        {"hash": "a1", "file_path": "y.py", "_repo_id": repo_id},
        {"hash": "a1", "file_path": "z.py", "_repo_id": repo_id},
        {"hash": "b1", "file_path": "q.py", "_repo_id": other},
    ], pk=["hash", "file_path", "_repo_id"])

    kospex.kospex_db.table(KospexSchema.TBL_REPOS).upsert_all([
        {"_repo_id": repo_id, "last_sync_hash": "deadbeef"},
        {"_repo_id": other, "last_sync_hash": "cafe"},
    ], pk=["_repo_id"])

    return repo_id, other


def test_counts_report_rows_per_table(kospex_env):
    from kospex_core import Kospex

    k = Kospex()
    repo_id, _ = _seed(k)

    counts = k.repo_id_row_counts(repo_id)

    assert counts["commits"] == 2
    assert counts["commit_files"] == 3
    assert counts["repos"] == 1


def test_counts_omit_tables_with_no_rows(kospex_env):
    """A repo touches a handful of the 13 repo tables; listing the empty ones
    is noise in a confirmation prompt."""
    from kospex_core import Kospex

    k = Kospex()
    repo_id, _ = _seed(k)

    counts = k.repo_id_row_counts(repo_id)

    assert all(v > 0 for v in counts.values())
    assert "file_metadata" not in counts


def test_counts_for_unknown_repo_are_empty(kospex_env):
    from kospex_core import Kospex

    k = Kospex()
    _seed(k)

    assert k.repo_id_row_counts("github.com~nobody~nothing") == {}


def test_counts_do_not_include_another_repo(kospex_env):
    from kospex_core import Kospex

    k = Kospex()
    repo_id, other = _seed(k)

    assert k.repo_id_row_counts(repo_id)["commits"] == 2
    assert k.repo_id_row_counts(other)["commits"] == 1


def _run(monkeypatch, kospex, args):
    import kreaper
    monkeypatch.setattr(kreaper, "kospex", kospex)
    return CliRunner().invoke(kreaper.cli, args)


def test_dry_run_deletes_nothing(kospex_env, monkeypatch):
    from kospex_core import Kospex

    k = Kospex()
    repo_id, _ = _seed(k)

    result = _run(monkeypatch, k, ["delete-repo", "-repo_id", repo_id, "-dry-run"])

    assert result.exit_code == 0
    assert k.repo_id_row_counts(repo_id)["commits"] == 2
    assert k.repo_id_row_counts(repo_id)["commit_files"] == 3


def test_dry_run_reports_what_would_go(kospex_env, monkeypatch):
    from kospex_core import Kospex

    k = Kospex()
    repo_id, _ = _seed(k)

    result = _run(monkeypatch, k, ["delete-repo", "-repo_id", repo_id, "-dry-run"])

    assert "commits" in result.output
    assert "commit_files" in result.output
    assert "6" in result.output, "expected a total row count"


def test_dry_run_does_not_require_yes(kospex_env, monkeypatch):
    """-dry-run writes nothing, so demanding the destructive confirmation flag
    would just train people to type it."""
    from kospex_core import Kospex

    k = Kospex()
    repo_id, _ = _seed(k)

    result = _run(monkeypatch, k, ["delete-repo", "-repo_id", repo_id, "-dry-run"])

    assert result.exit_code == 0
    assert "Please specify -yes" not in result.output


def test_delete_still_deletes(kospex_env, monkeypatch):
    from kospex_core import Kospex

    k = Kospex()
    repo_id, other = _seed(k)

    result = _run(monkeypatch, k, ["delete-repo", "-repo_id", repo_id, "-yes"])

    assert result.exit_code == 0
    assert k.repo_id_row_counts(repo_id) == {}
    assert k.repo_id_row_counts(other)["commits"] == 1, "other repo must survive"


def test_no_stale_not_implemented_warning(kospex_env, monkeypatch):
    """The -repo_id path does clear every repo table; the old warning claimed
    otherwise on every invocation and put people off using it."""
    from kospex_core import Kospex

    k = Kospex()
    repo_id, _ = _seed(k)

    result = _run(monkeypatch, k, ["delete-repo", "-repo_id", repo_id, "-dry-run"])

    assert "only implements table" not in result.output
