"""Output that isn't UTF-8 never fails a whole repository (#479).

git prints path bytes as they are with core.quotePath=false (#116), and prints
commit metadata with no encoding header as raw bytes. One filename or author
name that isn't UTF-8, anywhere in the history, used to raise
UnicodeDecodeError from a strict decode and fail the command for the whole
repository: androidx/androidx has one stray file, deleted in 2022, and
`krunner file-metadata` failed on it after 16 minutes.

Output is now decoded with errors="replace". That gives the same string commit
sync already stored for such a path (each invalid byte becomes U+FFFD), so the
paths from get_repo_files() and from commit sync still match each other.

The repositories are built with git plumbing, so the non-UTF-8 name exists only
inside git: macOS refuses to create such a file on disk. The tests that need a
file on disk (scc, pull) skip where the filesystem refuses it.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import kgit as kgit_module
import kospex_utils as KospexUtils
from kospex_core import COMMIT_LOG_FORMAT, _git_log, parse_commit_log, scc_metrics_by_file
from kospex_git import KospexGit

LATIN1_NAME = b"caf\xe9.txt"           # "café.txt" in Latin-1: invalid UTF-8
REPLACED_NAME = "caf�.txt"         # what errors="replace" makes of it

_ENV = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(cwd, *args, stdin=None):
    """Run git with bytes in and bytes out, so a test never decodes itself."""
    env = {**os.environ, **_ENV, "GIT_CONFIG_GLOBAL": os.devnull}
    return subprocess.run(["git", "-C", str(cwd), *args], input=stdin,
                          check=True, capture_output=True, env=env).stdout


def _repo(tmp_path, name="repo"):
    path = tmp_path / name
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "remote", "add", "origin", "https://github.com/test/repo")
    return path


def _add_index_only(repo, name: bytes, content=b"x\n"):
    """Stage a file under `name` without creating it on disk."""
    blob = _git(repo, "hash-object", "-w", "--stdin", stdin=content).strip()
    _git(repo, "update-index", "--add", "--cacheinfo",
         b"100644," + blob + b"," + name)


def _commit(repo, message):
    _git(repo, "commit", "-q", "-m", message)


def _deleted_latin1_file_repo(tmp_path):
    """A kept file, plus a Latin-1-named file that is committed then deleted,
    as in androidx."""
    repo = _repo(tmp_path)
    (repo / "keep.txt").write_text("keep\n")
    _git(repo, "add", "keep.txt")
    _add_index_only(repo, LATIN1_NAME)
    _commit(repo, "add stray file")
    _git(repo, "rm", "-q", "--cached", LATIN1_NAME)
    _commit(repo, "remove stray file")
    return repo


def _latin1_author_repo(tmp_path):
    """One commit whose author name is Latin-1 bytes with no encoding header,
    written as a raw commit object because git won't take such a name from the
    environment on every platform."""
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "a.txt")
    tree = _git(repo, "write-tree").strip()
    body = (b"tree " + tree + b"\n"
            b"author Jos\xe9 <jose@example.com> 1700000000 +0000\n"
            b"committer Jos\xe9 <jose@example.com> 1700000000 +0000\n"
            b"\nfirst\n")
    commit = _git(repo, "hash-object", "-t", "commit", "-w", "--stdin",
                  stdin=body).strip()
    _git(repo, "update-ref", "refs/heads/main", commit)
    return repo


def _disk_supports_latin1_names(tmp_path):
    try:
        probe = os.path.join(os.fsencode(str(tmp_path)), b"probe-\xe9")
        with open(probe, "wb"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


# --- the deleted file: get_repo_files() and commit sync ---------------------

def test_last_commit_walk_survives_a_deleted_non_utf8_path(tmp_path):
    """The androidx failure: get_repo_files() walks the whole history."""
    repo = _deleted_latin1_file_repo(tmp_path)
    kg = KospexGit()
    kg.repo_dir = str(repo)

    last = kg._last_commit_by_path()

    assert "keep.txt" in last
    assert REPLACED_NAME in last


def test_get_repo_files_completes_and_keeps_the_tracked_files(tmp_path):
    repo = _deleted_latin1_file_repo(tmp_path)
    kg = KospexGit()
    kg.set_repo(str(repo))

    files = kg.get_repo_files()

    assert "keep.txt" in files


def test_commit_sync_log_survives_a_non_utf8_path(tmp_path):
    repo = _deleted_latin1_file_repo(tmp_path)

    commits = parse_commit_log(
        _git_log(repo, f"--pretty=format:{COMMIT_LOG_FORMAT}", "--numstat"))

    paths = {f["file_path"] for c in commits for f in c["filenames"]}
    assert paths == {"keep.txt", REPLACED_NAME}


def test_both_walks_give_the_same_string_for_the_path(tmp_path):
    """file_metadata and commit_files rows are joined on the path, so the
    replacement must be identical on both sides."""
    repo = _deleted_latin1_file_repo(tmp_path)
    kg = KospexGit()
    kg.repo_dir = str(repo)

    from_walk = {p for p in kg._last_commit_by_path() if p != "keep.txt"}
    from_sync = {f["file_path"]
                 for c in parse_commit_log(_git_log(
                     repo, f"--pretty=format:{COMMIT_LOG_FORMAT}", "--numstat"))
                 for f in c["filenames"] if f["file_path"] != "keep.txt"}

    assert from_walk == {REPLACED_NAME}
    assert from_walk == from_sync


# --- an author name that isn't UTF-8 -----------------------------------------

def test_commit_sync_log_survives_a_latin1_author(tmp_path):
    repo = _latin1_author_repo(tmp_path)

    commits = parse_commit_log(
        _git_log(repo, f"--pretty=format:{COMMIT_LOG_FORMAT}", "--numstat"))

    assert len(commits) == 1
    assert commits[0]["author_name"] == "Jos�"
    assert commits[0]["author_email"] == "jose@example.com"


def test_run_git_command_survives_a_latin1_author(tmp_path):
    repo = _latin1_author_repo(tmp_path)

    assert KospexUtils.run_git_command(str(repo), ["log", "--format=%aN"]) == "Jos�"


# --- a file that is still there: scc and pull --------------------------------

def test_scc_metrics_survive_a_non_utf8_filename_on_disk(tmp_path):
    if not shutil.which("scc"):
        pytest.skip("scc not installed")
    if not _disk_supports_latin1_names(tmp_path):
        pytest.skip("this filesystem refuses filenames that aren't UTF-8")
    (tmp_path / "ok.py").write_text("print(1)\n")
    with open(os.path.join(os.fsencode(str(tmp_path)), b"caf\xe9.py"), "wb") as fh:
        fh.write(b"print(2)\n")

    metrics = scc_metrics_by_file(str(tmp_path))

    assert "ok.py" in metrics
    assert "caf�.py" in metrics


def test_kgit_pull_survives_a_non_utf8_path_in_the_diffstat(tmp_path, monkeypatch):
    """git quotes such a path in pull's diffstat ("caf\\351.txt", all ASCII)
    unless the user has core.quotePath=false, which is common; with it, the
    raw byte reaches the decoder."""
    if not _disk_supports_latin1_names(tmp_path):
        pytest.skip("this filesystem refuses filenames that aren't UTF-8")
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[core]\n\tquotePath = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    remote = _repo(tmp_path, "remote")
    (remote / "a.txt").write_text("a\n")
    _git(remote, "add", "a.txt")
    _commit(remote, "a")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(remote), str(clone))
    _add_index_only(remote, LATIN1_NAME)
    _commit(remote, "add Latin-1 file")

    ok, detail, count = kgit_module._git_pull(str(clone), no_prompt=True)

    assert ok, detail
    assert count == 1


# --- the rule itself ----------------------------------------------------------

SRC = Path(__file__).resolve().parent.parent / "src"

# Strict decoding still allowed: the parked DuckDB proof of concept, and the
# TODO grep, which decodes file contents and is fixed separately (#478).
STRICT_ALLOWED = {
    ("kospex/git_ingest.py", None),
    ("krunner.py", "grep"),
}


def test_no_subprocess_output_is_decoded_strictly():
    """A new `text=True` or bare `.decode()` on subprocess output brings the
    whole-repo failure back. Use KospexUtils.SUBPROCESS_TEXT instead."""
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if (rel, None) in STRICT_ALLOWED:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(lines, 1):
            code = line.split("#", 1)[0]
            strict = ("text=True" in code or "universal_newlines=True" in code
                      or re.search(r"check_output\(.*\)\.decode\(\)", code))
            if not strict:
                continue
            context = "\n".join(lines[max(0, n - 4):n])
            if any(rel == f and key and key in context
                   for f, key in STRICT_ALLOWED):
                continue
            offenders.append(f"{rel}:{n}: {line.strip()}")
    assert offenders == []
