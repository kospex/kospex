"""Regression tests for `krunner branches` against repos that are no longer on disk.

The repos table records the local clone path for each repo. That path can stop
existing at any time - the clone is deleted, moved, or was only ever a throwaway
directory. Before the fix, KospexGit.get_branches() chdir'd into the path with no
guard, so a single missing clone raised FileNotFoundError and aborted the whole
`krunner branches` run part-way through the repo list.
"""
import os
import subprocess

import pytest
from click.testing import CliRunner

import krunner
from kospex_git import KospexGit


def _git_repo(path):
    """Create an empty git repo (no remotes, so `git branch -r` is empty)."""
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_get_branches_restores_cwd_when_the_git_command_fails(tmp_path, monkeypatch):
    """A failing `git branch -r` must not leave the process in the repo directory."""
    repo = _git_repo(tmp_path / "repo")

    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "branch", "-r"])

    monkeypatch.setattr(subprocess, "run", boom)

    before = os.getcwd()
    with pytest.raises(subprocess.CalledProcessError):
        KospexGit.get_branches(str(repo))
    assert os.getcwd() == before


def test_branches_skips_missing_clones_and_processes_the_rest(tmp_path, monkeypatch):
    """One repo whose directory is gone must not abort the run for the others."""
    present = _git_repo(tmp_path / "present")
    missing = tmp_path / "gone"  # deliberately never created

    repos = [
        {"_repo_id": "github.com~org~gone", "file_path": str(missing)},
        {"_repo_id": "github.com~org~present", "file_path": str(present)},
    ]
    monkeypatch.setattr(krunner, "get_repos", lambda request_id: repos)
    monkeypatch.setenv("COLUMNS", "400")

    result = CliRunner().invoke(krunner.cli, ["branches"])

    assert result.exit_code == 0, result.output
    assert result.exception is None, result.exception
    flat = "".join(result.output.split())
    assert "github.com~org~present" in flat  # the healthy repo was still processed
    assert "github.com~org~gone" in flat  # the missing one was reported, not silently dropped


def test_branches_skips_paths_that_are_not_git_repos(tmp_path, monkeypatch):
    """A path that exists but isn't a git repo must not abort the run either."""
    present = _git_repo(tmp_path / "present")
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()  # exists, but no .git

    repos = [
        {"_repo_id": "github.com~org~notarepo", "file_path": str(not_a_repo)},
        {"_repo_id": "github.com~org~present", "file_path": str(present)},
    ]
    monkeypatch.setattr(krunner, "get_repos", lambda request_id: repos)
    monkeypatch.setenv("COLUMNS", "400")

    result = CliRunner().invoke(krunner.cli, ["branches"])

    assert result.exit_code == 0, result.output
    assert result.exception is None, result.exception
    assert "github.com~org~present" in "".join(result.output.split())


def test_branches_reports_an_error_summary_by_type(tmp_path, monkeypatch):
    """The end of the scan must say how many errors happened, broken down by type."""
    present = _git_repo(tmp_path / "present")
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    repos = [
        {"_repo_id": "github.com~org~gone1", "file_path": str(tmp_path / "gone1")},
        {"_repo_id": "github.com~org~gone2", "file_path": str(tmp_path / "gone2")},
        {"_repo_id": "github.com~org~notarepo", "file_path": str(not_a_repo)},
        {"_repo_id": "github.com~org~present", "file_path": str(present)},
    ]
    monkeypatch.setattr(krunner, "get_repos", lambda request_id: repos)
    monkeypatch.setenv("COLUMNS", "400")

    result = CliRunner().invoke(krunner.cli, ["branches"])

    assert result.exit_code == 0, result.output
    flat = "".join(result.output.split())
    assert "Errors(3)" in flat
    # summary table cells, box-drawing characters and all
    assert "MISSING_CLONE│2" in flat  # two clones missing from disk
    assert "GIT_ERROR│1" in flat  # one path that isn't a git repo


def test_branches_prints_no_error_summary_for_a_clean_run(tmp_path, monkeypatch):
    present = _git_repo(tmp_path / "present")
    monkeypatch.setattr(
        krunner,
        "get_repos",
        lambda request_id: [{"_repo_id": "github.com~org~present", "file_path": str(present)}],
    )
    monkeypatch.setenv("COLUMNS", "400")

    result = CliRunner().invoke(krunner.cli, ["branches"])

    assert result.exit_code == 0, result.output
    assert "Errors" not in result.output


def test_branches_strict_exits_non_zero_when_a_repo_failed(tmp_path, monkeypatch):
    present = _git_repo(tmp_path / "present")
    repos = [
        {"_repo_id": "github.com~org~gone", "file_path": str(tmp_path / "gone")},
        {"_repo_id": "github.com~org~present", "file_path": str(present)},
    ]
    monkeypatch.setattr(krunner, "get_repos", lambda request_id: repos)
    monkeypatch.setenv("COLUMNS", "400")

    result = CliRunner().invoke(krunner.cli, ["branches", "-strict"])

    assert result.exit_code != 0
    # the healthy repo is still processed - strict changes the exit code, not the work
    assert "github.com~org~present" in "".join(result.output.split())


def test_branches_strict_exits_zero_when_every_repo_is_healthy(tmp_path, monkeypatch):
    present = _git_repo(tmp_path / "present")
    monkeypatch.setattr(
        krunner,
        "get_repos",
        lambda request_id: [{"_repo_id": "github.com~org~present", "file_path": str(present)}],
    )
    monkeypatch.setenv("COLUMNS", "400")

    result = CliRunner().invoke(krunner.cli, ["branches", "-strict"])

    assert result.exit_code == 0, result.output
