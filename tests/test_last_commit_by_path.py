"""Tests for KospexGit._last_commit_by_path() and get_repo_files().

get_repo_files() used to spawn one `git log` per file, which made a
file-metadata rebuild over a modest estate take hours. It now resolves every
path once, in a single `git log --name-only` walk per repo, and looks each
file up in that map.

These tests pin the four things the walk has to get right:

  - newest-first, first-write-wins, so each path maps to its last commit
  - merge commits contribute the files they actually changed (content that
    only ever existed in a conflict resolution), but do NOT claim files they
    merely forwarded from the merged branch
  - non-ASCII paths come back unquoted, so they match what panopticas walked
  - files git does not track are absent, and get_repo_files() drops them
"""
import os
import subprocess

import pytest

import kospex_utils as KospexUtils
from kospex_git import KospexGit

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
}


def _git(cwd, *args, date=None, check=True):
    env = {**os.environ, **_GIT_ENV}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    return subprocess.run(["git", "-C", str(cwd), *args], check=check,
                          capture_output=True, text=True, env=env)


def _new_repo(tmp_path, name="repo"):
    path = tmp_path / name
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "remote", "add", "origin", "https://github.com/test/repo")
    return path


def _commit(path, message, date=None):
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message, date=date)
    return _git(path, "rev-parse", "HEAD").stdout.strip()


def _walk(path):
    kg = KospexGit()
    kg.repo_dir = str(path)
    return kg._last_commit_by_path()


def test_each_path_maps_to_its_newest_commit(tmp_path):
    repo = _new_repo(tmp_path)
    (repo / "app.py").write_text("v1\n")
    (repo / "LICENSE").write_text("MIT\n")
    _commit(repo, "first", date="2024-01-01T00:00:00+00:00")
    (repo / "app.py").write_text("v2\n")
    second = _commit(repo, "second", date="2025-06-01T00:00:00+00:00")

    last = _walk(repo)

    assert last["app.py"]["commit_hash"] == second
    assert last["app.py"]["committer_when"].startswith("2025-06-01")
    assert last["LICENSE"]["committer_when"].startswith("2024-01-01")


def test_file_added_only_in_a_merge_resolution_is_found(tmp_path):
    """Content that exists in no parent — added while resolving a conflict —
    is invisible to a plain `git log --name-only`, which shows no diff at all
    for merge commits."""
    repo = _new_repo(tmp_path)
    (repo / "shared.txt").write_text("base\n")
    _commit(repo, "base", date="2024-01-01T00:00:00+00:00")

    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "shared.txt").write_text("side\n")
    _commit(repo, "side edit", date="2024-02-01T00:00:00+00:00")

    _git(repo, "checkout", "-q", "main")
    (repo / "shared.txt").write_text("main\n")
    _commit(repo, "main edit", date="2024-03-01T00:00:00+00:00")

    _git(repo, "merge", "side", check=False)  # conflicts, left unresolved
    (repo / "shared.txt").write_text("resolved\n")
    (repo / "resolution-note.md").write_text("only ever existed here\n")
    merge = _commit(repo, "merge side", date="2024-04-01T00:00:00+00:00")

    last = _walk(repo)

    assert last["resolution-note.md"]["commit_hash"] == merge
    assert last["shared.txt"]["commit_hash"] == merge


def test_merge_does_not_claim_files_it_only_forwarded(tmp_path):
    """A merge's first-parent diff includes everything the branch changed, so
    attributing it would hand nearly every file in a PR-merge workflow the
    merge commit instead of the commit that actually touched it."""
    repo = _new_repo(tmp_path)
    (repo / "untouched.py").write_text("v1\n")
    _commit(repo, "base", date="2024-01-01T00:00:00+00:00")

    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "untouched.py").write_text("v2\n")
    side = _commit(repo, "side edit", date="2024-02-01T00:00:00+00:00")

    _git(repo, "checkout", "-q", "main")
    (repo / "other.py").write_text("unrelated\n")
    _commit(repo, "main edit", date="2024-03-01T00:00:00+00:00")
    _git(repo, "merge", "--no-ff", "-m", "merge side", "side")

    last = _walk(repo)

    assert last["untouched.py"]["commit_hash"] == side


def test_change_abandoned_on_a_branch_is_a_known_divergence(tmp_path):
    """Documented limitation, pinned so it can't drift unnoticed.

    Path-limited `git log` simplifies history: a branch whose changes to a path
    did not survive the merge is pruned, so it reports the commit that set the
    path's current content. An unlimited walk still sees those commits and
    reports the newer, abandoned one. Measured at 0-1.3% of paths across
    react/babel/pydantic/kospex.
    """
    repo = _new_repo(tmp_path)
    (repo / "config.yaml").write_text("original\n")
    original = _commit(repo, "original config", date="2024-01-01T00:00:00+00:00")

    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "config.yaml").write_text("experiment\n")
    _commit(repo, "try something", date="2024-02-01T00:00:00+00:00")
    (repo / "config.yaml").write_text("original\n")  # reverted before landing
    abandoned = _commit(repo, "revert the experiment", date="2024-03-01T00:00:00+00:00")

    _git(repo, "checkout", "-q", "main")
    (repo / "unrelated.py").write_text("x = 1\n")
    _commit(repo, "unrelated", date="2024-04-01T00:00:00+00:00")
    _git(repo, "merge", "--no-ff", "-m", "merge side", "side")

    per_file = _git(repo, "log", "-1", "--pretty=format:%H", "--",
                    "config.yaml").stdout.strip()
    assert per_file == original

    assert _walk(repo)["config.yaml"]["commit_hash"] == abandoned


def test_non_ascii_paths_are_returned_unquoted(tmp_path):
    """git quotes non-ASCII paths as "caf\\303\\251.py" by default, which never
    matches the filesystem path panopticas produced."""
    repo = _new_repo(tmp_path)
    (repo / "café.py").write_text("x = 1\n")
    commit = _commit(repo, "add cafe", date="2024-01-01T00:00:00+00:00")

    last = _walk(repo)

    assert "café.py" in last
    assert last["café.py"]["commit_hash"] == commit


def test_renamed_file_maps_to_the_rename_commit(tmp_path):
    repo = _new_repo(tmp_path)
    (repo / "old_name.py").write_text("x = 1\n")
    _commit(repo, "add", date="2024-01-01T00:00:00+00:00")
    _git(repo, "mv", "old_name.py", "new_name.py")
    renamed = _commit(repo, "rename", date="2025-01-01T00:00:00+00:00")

    last = _walk(repo)

    assert last["new_name.py"]["commit_hash"] == renamed


def test_untracked_file_is_absent_from_the_map(tmp_path):
    repo = _new_repo(tmp_path)
    (repo / "tracked.py").write_text("x = 1\n")
    _commit(repo, "add", date="2024-01-01T00:00:00+00:00")
    (repo / "scratch.log").write_text("build output\n")

    last = _walk(repo)

    assert "tracked.py" in last
    assert "scratch.log" not in last


def test_repo_without_commits_returns_an_empty_map(tmp_path):
    repo = _new_repo(tmp_path)

    assert _walk(repo) == {}


def test_get_repo_files_does_not_spawn_a_git_log_per_file(tmp_path, monkeypatch):
    """The whole point of the change: no per-file subprocess."""
    repo = _new_repo(tmp_path)
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text("x = 1\n")
    _commit(repo, "add", date="2024-01-01T00:00:00+00:00")

    def _boom(*args, **kwargs):
        raise AssertionError("per-file git log should no longer be used")

    monkeypatch.setattr(KospexUtils, "get_last_commit_info", _boom)

    kg = KospexGit()
    kg.set_repo(str(repo))
    files = kg.get_repo_files()

    assert set(files) >= {"a.py", "b.py", "c.py"}
    assert files["a.py"]["committer_when"].startswith("2024-01-01")
    assert files["a.py"]["status"] is not None


def test_get_repo_files_excludes_and_logs_untracked_files(tmp_path, caplog):
    """A non-zero untracked count means the sync came from a working directory
    rather than a clean clone — worth a log line, not a silent drop."""
    repo = _new_repo(tmp_path)
    (repo / "tracked.py").write_text("x = 1\n")
    _commit(repo, "add", date="2024-01-01T00:00:00+00:00")
    (repo / "scratch.py").write_text("local only\n")

    kg = KospexGit()
    kg.set_repo(str(repo))
    with caplog.at_level("WARNING", logger="kospex_git"):
        files = kg.get_repo_files()

    assert "tracked.py" in files
    assert "scratch.py" not in files
    assert "1 untracked" in caplog.text


def test_get_repo_files_stays_quiet_on_a_clean_clone(tmp_path, caplog):
    repo = _new_repo(tmp_path)
    (repo / "tracked.py").write_text("x = 1\n")
    _commit(repo, "add", date="2024-01-01T00:00:00+00:00")

    kg = KospexGit()
    kg.set_repo(str(repo))
    with caplog.at_level("WARNING", logger="kospex_git"):
        kg.get_repo_files()

    assert "untracked" not in caplog.text


def test_skip_last_commit_keeps_untracked_files_and_skips_the_walk(tmp_path):
    """skip_last_commit means "don't ask git anything" — including which files
    it tracks, so nothing is filtered out."""
    repo = _new_repo(tmp_path)
    (repo / "tracked.py").write_text("x = 1\n")
    _commit(repo, "add", date="2024-01-01T00:00:00+00:00")
    (repo / "scratch.py").write_text("local only\n")

    kg = KospexGit()
    kg.set_repo(str(repo))
    files = kg.get_repo_files(skip_last_commit=True)

    assert {"tracked.py", "scratch.py"} <= set(files)
    assert files["tracked.py"]["committer_when"] is None
    assert files["tracked.py"]["status"] is None


def test_walk_matches_per_file_git_log_on_this_repo():
    """Ground truth: the single walk must agree with `git log -1 -- <path>`,
    the semantics the per-file implementation had."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.exists(os.path.join(repo_root, ".git")):
        pytest.skip("not a git checkout")

    last = _walk(repo_root)
    tracked = subprocess.run(["git", "-C", repo_root, "ls-files"],
                             capture_output=True, text=True, check=True).stdout.split("\n")

    for path in [p for p in tracked if p][:150]:
        expected = subprocess.run(
            ["git", "-C", repo_root, "log", "-1", "--pretty=format:%H",
             "--date=iso-strict", "--", path],
            capture_output=True, text=True, check=True).stdout.strip()
        assert last.get(path, {}).get("commit_hash") == expected, path
