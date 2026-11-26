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


# --- files that aren't UTF-8 (#478) -------------------------------------------
#
# grep prints the matching line of file content. A Latin-1 line used to reach a
# strict decode and raise, so no TODO was recorded for the whole repo. GNU grep
# under a UTF-8 locale instead treats such a file as binary and prints nothing
# on stdout, so its TODO was lost without an error. krunner now runs grep with
# LC_ALL=C (every byte is text; only files with NUL bytes are binary, as with
# macOS grep) and decodes with KospexUtils.SUBPROCESS_TEXT.

def _repo_bytes(path, files):
    """Like _repo(), but writes each file's content as bytes."""
    path.mkdir(parents=True)
    env = {**os.environ, **_GIT_ENV}
    run = lambda *a: subprocess.run(
        ["git", "-C", str(path), *a], check=True, capture_output=True, env=env)
    run("init", "-q")
    run("remote", "add", "origin", "https://github.com/test/repo.git")
    for name, content in files.items():
        (path / name).write_bytes(content)
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    return path


@pytest.mark.parametrize("locale", [None, "C", "C.UTF-8"])
def test_todo_in_a_latin1_file_is_recorded(tmp_path, monkeypatch, locale):
    """Whatever the caller's locale: under C.UTF-8, GNU grep would otherwise
    call the file binary and drop the TODO."""
    for var in ("LC_ALL", "LC_CTYPE", "LANG"):
        monkeypatch.delenv(var, raising=False)
    if locale:
        monkeypatch.setenv("LC_ALL", locale)
    repo = _repo_bytes(tmp_path / "repo", {
        "legacy.py": b"# TODO: caf\xe9 fix\nx = 1\n",
        "app.py": b"# TODO plain\n",
    })
    monkeypatch.setattr(KospexUtils, "find_repos", lambda directory: [str(repo)])
    seen = _observations(monkeypatch)

    result = CliRunner().invoke(krunner.cli, ["todo", str(tmp_path), "-save"])

    assert result.exit_code == 0, result.output
    by_file = {o["file_path"]: o["data"] for o in seen}
    assert "TODO: caf" + chr(0xFFFD) + " fix" in by_file["legacy.py"]
    assert "TODO plain" in by_file["app.py"]


def test_todo_still_skips_binary_files(tmp_path, monkeypatch):
    """LC_ALL=C makes grep read every byte as text, but a file with NUL bytes
    is still binary: grep reports it without a line, which isn't recorded."""
    repo = _repo_bytes(tmp_path / "repo", {
        "blob.bin": b"abc\x00def TODO ghi\x00\n",
        "app.py": b"# TODO plain\n",
    })
    monkeypatch.setattr(KospexUtils, "find_repos", lambda directory: [str(repo)])
    seen = _observations(monkeypatch)

    result = CliRunner().invoke(krunner.cli, ["todo", str(tmp_path), "-save"])

    assert result.exit_code == 0, result.output
    assert [o["file_path"] for o in seen] == ["app.py"]
