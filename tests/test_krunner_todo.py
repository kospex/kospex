"""`krunner todo` greps repos without chdir'ing the process around them.

The last krunner command to still rely on an implicit working directory: it
called set_repo_dir() for the git metadata (which chdirs) and ran a bare grep,
then chdir'd back by hand at the end of each repo.
"""
import os
import subprocess

import pytest
from click.testing import CliRunner

import krunner
import kospex_utils as KospexUtils

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
}


def _repo(path, files):
    path.mkdir(parents=True)
    env = {**os.environ, **_GIT_ENV}
    run = lambda *a: subprocess.run(
        ["git", "-C", str(path), *a], check=True, capture_output=True, env=env)
    run("init", "-q")
    run("remote", "add", "origin", "https://github.com/test/repo.git")
    for name, content in files.items():
        (path / name).write_text(content)
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    return path


def _observations(monkeypatch):
    """Collect observations instead of writing them to the DB."""
    seen = []
    monkeypatch.setattr(
        krunner.kospex.kospex_query, "add_observation", lambda obs: seen.append(obs))
    return seen


def test_todo_writes_nothing_without_save(tmp_path, monkeypatch):
    """Merely running todo must not touch the observations table - matching
    `branches` and `repo-size`, whose -save also defaults to off."""
    repo = _repo(tmp_path / "repo", {"app.py": "# TODO fix this\n"})
    monkeypatch.setattr(KospexUtils, "find_repos", lambda directory: [str(repo)])
    seen = _observations(monkeypatch)

    result = CliRunner().invoke(krunner.cli, ["todo", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert seen == []
    assert "TODO fix this" in result.output  # still shows what it found


def test_todo_records_the_todo_lines_it_finds(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo", {"app.py": "# TODO fix this\nx = 1\n"})
    monkeypatch.setattr(KospexUtils, "find_repos", lambda directory: [str(repo)])
    seen = _observations(monkeypatch)

    result = CliRunner().invoke(krunner.cli, ["todo", str(tmp_path), "-save"])

    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    assert "TODO fix this" in seen[0]["data"]
    assert seen[0]["observation_key"] == "GREP_TODO"
    assert seen[0]["_repo_id"] == "github.com~test~repo"


def test_todo_matches_a_bare_todo_with_nothing_after_it(tmp_path, monkeypatch):
    """The old pattern was "TODO *" - only matched plain TODO because BRE `*`
    means zero-or-more of the preceding space. Pin the intended behaviour."""
    repo = _repo(tmp_path / "repo", {"app.py": "#TODO\n"})
    monkeypatch.setattr(KospexUtils, "find_repos", lambda directory: [str(repo)])
    seen = _observations(monkeypatch)

    result = CliRunner().invoke(krunner.cli, ["todo", str(tmp_path), "-save"])

    assert result.exit_code == 0, result.output
    assert len(seen) == 1


def test_todo_leaves_the_working_directory_alone(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo", {"app.py": "# TODO fix this\n"})
    monkeypatch.setattr(KospexUtils, "find_repos", lambda directory: [str(repo)])
    _observations(monkeypatch)

    before = os.getcwd()
    result = CliRunner().invoke(krunner.cli, ["todo", str(tmp_path), "-save"])

    assert result.exit_code == 0, result.output
    assert os.getcwd() == before


def test_todo_greps_each_repo_not_the_process_directory(tmp_path, monkeypatch):
    """Two repos, only one with a TODO - the match must be attributed to it."""
    with_todo = _repo(tmp_path / "with", {"app.py": "# TODO here\n"})
    without = _repo(tmp_path / "without", {"app.py": "x = 1\n"})
    monkeypatch.setattr(
        KospexUtils, "find_repos", lambda directory: [str(without), str(with_todo)])
    seen = _observations(monkeypatch)

    result = CliRunner().invoke(krunner.cli, ["todo", str(tmp_path), "-save"])

    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    assert "TODO here" in seen[0]["data"]
