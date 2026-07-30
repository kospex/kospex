"""Tests for the repo path-conflict guard.

`repos._repo_id` is the primary key and `update_repo_status()` upserts
`file_path` into that row, so syncing the same repo from a second location used
to silently repoint the DB at the newest clone - losing which path the data was
actually built from. repo_path_conflict() decides whether a sync would wrongly
repoint an existing row.

Refuse only when BOTH paths exist on disk: that's the ambiguous case where a
throwaway copy could overwrite the real one. A recorded path that no longer
exists is a genuine move, so repointing self-heals the row.
"""
import os

import pytest

from kospex_core import RepoPathConflict, repo_path_conflict


def test_a_repo_never_synced_before_is_not_a_conflict(tmp_path):
    new = tmp_path / "clone"
    new.mkdir()
    conflict, reason = repo_path_conflict(None, str(new))
    assert conflict is False
    assert "not" in reason.lower()


def test_syncing_the_same_path_again_is_not_a_conflict(tmp_path):
    path = tmp_path / "clone"
    path.mkdir()
    conflict, reason = repo_path_conflict(str(path), str(path))
    assert conflict is False


def test_two_paths_that_resolve_to_the_same_place_are_not_a_conflict(tmp_path):
    """/tmp is a symlink to /private/tmp on macOS - compare resolved paths."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    conflict, reason = repo_path_conflict(str(real), str(link))
    assert conflict is False


def test_a_different_path_while_the_recorded_clone_still_exists_is_a_conflict(tmp_path):
    recorded = tmp_path / "recorded"
    recorded.mkdir()
    new = tmp_path / "new"
    new.mkdir()

    conflict, reason = repo_path_conflict(str(recorded), str(new))
    assert conflict is True
    assert str(recorded) in reason


def test_force_overrides_the_conflict(tmp_path):
    recorded = tmp_path / "recorded"
    recorded.mkdir()
    new = tmp_path / "new"
    new.mkdir()

    conflict, reason = repo_path_conflict(str(recorded), str(new), force=True)
    assert conflict is False
    assert "force" in reason.lower()


def test_a_recorded_path_that_no_longer_exists_repoints_without_a_conflict(tmp_path):
    """The stale-row case: recorded clone is gone, so the move is genuine."""
    recorded = tmp_path / "gone"  # never created
    new = tmp_path / "new"
    new.mkdir()

    conflict, reason = repo_path_conflict(str(recorded), str(new))
    assert conflict is False
    assert "no longer exists" in reason.lower()


def test_the_exception_names_the_repo_and_both_paths():
    exc = RepoPathConflict("github.com~kospex~panopticas", "/old/path", "/new/path")
    message = str(exc)
    assert "github.com~kospex~panopticas" in message
    assert "/old/path" in message
    assert "/new/path" in message
    assert exc.repo_id == "github.com~kospex~panopticas"
    assert exc.recorded_path == "/old/path"
    assert exc.new_path == "/new/path"
