"""Tests for parse_commit_log() — the commits/commit_files ingest parser.

The ingest used to split git's output on "#", which is legal inside an author
or committer name and email. One "#" shifted every later field and the last
field absorbed the remainder, silently (#163). Real data hits this: nixpkgs has
97 commits authored with `git#v1@kaction.cc`.

The format now uses ASCII Unit Separator (\\x1f), which git rejects inside name
and email fields, and the parser asserts the field count rather than unpacking
optimistically — a silent shift is what kept the bug invisible.
"""
import os
import subprocess

import pytest

from kospex_core import COMMIT_LOG_FORMAT, parse_commit_log

US = "\x1f"

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
}


def _git(cwd, *args, env=None, check=True):
    e = {**os.environ, **_GIT_ENV, **(env or {})}
    return subprocess.run(["git", "-C", str(cwd), *args], check=check,
                          capture_output=True, text=True, env=e)


def _repo(tmp_path, name="r"):
    p = tmp_path / name
    p.mkdir()
    _git(p, "init", "-q", "-b", "main")
    return p


def _log(repo):
    """The real ingest command, so tests exercise git's actual output."""
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotePath=false", "log",
         f"--pretty=format:{COMMIT_LOG_FORMAT}", "--numstat"],
        capture_output=True, text=True, check=True).stdout


def _record(**over):
    f = {
        "hash": "abc123", "author_when": "2024-01-01T00:00:00+00:00",
        "committer_when": "2024-01-01T00:00:00+00:00", "author_name": "A",
        "author_email": "a@e.com", "committer_name": "C",
        "committer_email": "c@e.com", "parents": "",
    }
    f.update(over)
    return US.join([f["hash"], f["author_when"], f["committer_when"],
                    f["author_name"], f["author_email"], f["committer_name"],
                    f["committer_email"], f["parents"]])


def test_email_containing_hash_does_not_shift_fields():
    """#163: the exact shape that corrupts 97 nixpkgs commits."""
    commits = parse_commit_log(_record(author_name="KAction",
                                       author_email="git#v1@kaction.cc",
                                       committer_name="KAction",
                                       committer_email="git#v1@kaction.cc"))

    assert len(commits) == 1
    c = commits[0]
    assert c["author_email"] == "git#v1@kaction.cc"
    assert c["committer_name"] == "KAction"
    assert c["committer_email"] == "git#v1@kaction.cc"


def test_name_containing_hash_does_not_shift_fields():
    commits = parse_commit_log(_record(author_name="Bob #1"))

    assert commits[0]["author_name"] == "Bob #1"
    assert commits[0]["author_email"] == "a@e.com"


def test_root_commit_has_zero_parents():
    assert parse_commit_log(_record(parents=""))[0]["parents"] == 0


def test_ordinary_commit_has_one_parent():
    assert parse_commit_log(_record(parents="aaa"))[0]["parents"] == 1


def test_merge_has_two_parents():
    assert parse_commit_log(_record(parents="aaa bbb"))[0]["parents"] == 2


def test_octopus_merge_counts_every_parent():
    """nixpkgs has 12 octopus merges, one with 16 parents — so merge detection
    must be `parents > 1`, never `parents == 2`."""
    parents = " ".join(f"p{i}" for i in range(16))

    assert parse_commit_log(_record(parents=parents))[0]["parents"] == 16


def test_malformed_record_raises_rather_than_shifting():
    truncated = US.join(["abc123", "2024-01-01T00:00:00+00:00", "A"])

    with pytest.raises(ValueError):
        parse_commit_log(truncated)


def test_numstat_lines_become_filenames(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\ny = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add")

    commits = parse_commit_log(_log(repo))

    assert len(commits) == 1
    files = commits[0]["filenames"]
    assert len(files) == 1
    assert files[0]["file_path"] == "a.py"
    assert files[0]["additions"] == 2
    assert files[0]["deletions"] == 0


def test_rename_event_is_unpacked(tmp_path):
    repo = _repo(tmp_path)
    (repo / "old.py").write_text("x = 1\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "add")
    _git(repo, "mv", "old.py", "new.py")
    _git(repo, "commit", "-q", "-m", "rename")

    commits = parse_commit_log(_log(repo))
    renamed = [f for c in commits for f in c["filenames"] if f.get("path_change")]

    assert renamed, "expected a rename to be recorded"
    assert renamed[0]["file_path"] == "new.py"
    assert "=>" in renamed[0]["path_change"]


def test_binary_file_counts_are_zero_not_dashes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "blob.bin").write_bytes(bytes(range(256)))
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "binary")

    files = parse_commit_log(_log(repo))[0]["filenames"]

    assert files[0]["additions"] == 0
    assert files[0]["deletions"] == 0


def test_merge_contributes_no_files(tmp_path):
    """The property the whole design protects: a clean merge is bookkeeping,
    not authorship. Regressing this double-counts every merged branch."""
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("a\n"); (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-qb", "side")
    (repo / "b.txt").write_text("side\n"); _git(repo, "commit", "-qam", "side")
    _git(repo, "checkout", "-q", "main")
    (repo / "a.txt").write_text("main\n"); _git(repo, "commit", "-qam", "main")
    _git(repo, "merge", "--no-ff", "-q", "-m", "merge side", "side")

    commits = parse_commit_log(_log(repo))
    merges = [c for c in commits if c["parents"] > 1]

    assert len(merges) == 1
    assert merges[0]["filenames"] == []


def test_non_ascii_paths_are_unquoted(tmp_path):
    """#116: git quotes non-ASCII paths unless core.quotePath=false."""
    repo = _repo(tmp_path)
    (repo / "café.py").write_text("x = 1\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "add")

    files = parse_commit_log(_log(repo))[0]["filenames"]

    assert files[0]["file_path"] == "café.py"


def test_real_repo_with_hash_in_email_round_trips(tmp_path):
    """End to end against git itself, proving git emits what the parser expects."""
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add",
         env={"GIT_AUTHOR_NAME": "KAction", "GIT_AUTHOR_EMAIL": "git#v1@kaction.cc",
              "GIT_COMMITTER_NAME": "KAction", "GIT_COMMITTER_EMAIL": "git#v1@kaction.cc"})

    c = parse_commit_log(_log(repo))[0]

    assert c["author_email"] == "git#v1@kaction.cc"
    assert c["committer_email"] == "git#v1@kaction.cc"
    assert c["author_name"] == "KAction"
