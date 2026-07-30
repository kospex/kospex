"""`kospex sync-directory` handles a repo that is already synced from elsewhere.

sync-directory walks every git repo under a directory, so pointing it at a
second copy of a tree would repoint many repos rows at once. One conflicting
repo must be skipped, not abort the walk.
"""
from click.testing import CliRunner

import kospex_cli
import kospex_utils as KospexUtils
from kospex_core import RepoPathConflict


def _two_repos(monkeypatch, tmp_path):
    first, second = str(tmp_path / "first"), str(tmp_path / "second")
    monkeypatch.setattr(KospexUtils, "find_repos", lambda directory: [first, second])
    return first, second


def _sync_spy(monkeypatch, raises_for=None):
    """Record sync_repo calls; raise RepoPathConflict for one path."""
    seen = []

    def fake_sync(directory, **kwargs):
        seen.append((directory, kwargs.get("force", False)))
        if directory == raises_for:
            raise RepoPathConflict("github.com~org~repo", "/elsewhere", directory)

    monkeypatch.setattr(kospex_cli.kospex, "sync_repo", fake_sync)
    return seen


def test_a_conflicting_repo_is_skipped_and_the_walk_continues(tmp_path, monkeypatch):
    first, second = _two_repos(monkeypatch, tmp_path)
    seen = _sync_spy(monkeypatch, raises_for=first)

    result = CliRunner().invoke(kospex_cli.cli, ["sync-directory", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert [path for path, _ in seen] == [first, second]  # second still attempted
    flat = "".join(result.output.split())
    assert "/elsewhere" in flat  # the conflict is reported, not swallowed


def test_force_is_passed_through_to_every_sync(tmp_path, monkeypatch):
    first, second = _two_repos(monkeypatch, tmp_path)
    seen = _sync_spy(monkeypatch)

    result = CliRunner().invoke(kospex_cli.cli, ["sync-directory", str(tmp_path), "-force"])

    assert result.exit_code == 0, result.output
    assert seen == [(first, True), (second, True)]


def test_without_force_sync_is_called_normally(tmp_path, monkeypatch):
    first, second = _two_repos(monkeypatch, tmp_path)
    seen = _sync_spy(monkeypatch)

    result = CliRunner().invoke(kospex_cli.cli, ["sync-directory", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert seen == [(first, False), (second, False)]
