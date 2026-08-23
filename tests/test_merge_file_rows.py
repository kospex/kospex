"""Tests for merge_file_rows() — content a merge introduced that exists in no parent.

`git log --numstat` reports zero files for a merge, which is correct: a merge
that only combines two branches authored nothing, and attributing files to it
would double-count work already credited to the branch commits. But it also
hides an "evil merge" — git's own term for a merge introducing changes that
appear in no parent (conflict resolutions, files added while merging). That
content lands nowhere today (#121).

numstat cannot express a combined diff (it silently degrades to first-parent),
and --name-only cannot express counts, so the two are composed:

    mask   --name-only --diff-merges=combined   which (merge, path) pairs qualify
    counts --numstat -m                         one block per parent; take the min

The min matters. On click, first-parent counts inflate merge churn 2.7x, because
they include the branch's own work on those files.
"""
import os
import subprocess

from kospex_core import merge_file_rows

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
}


def _git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", str(cwd), *args], check=check,
                          capture_output=True, text=True,
                          env={**os.environ, **_GIT_ENV})


def _repo(tmp_path, name="r"):
    p = tmp_path / name
    p.mkdir()
    _git(p, "init", "-q", "-b", "main")
    return p


def _commit(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_clean_merge_contributes_nothing(tmp_path):
    """The invariant the whole design protects. Regressing this double-counts
    every merged branch — on click it would add 4,765 rows where 442 are real."""
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("a\n"); (repo / "b.txt").write_text("b\n")
    _commit(repo, "base")
    _git(repo, "checkout", "-qb", "side")
    (repo / "b.txt").write_text("side\n"); _commit(repo, "side")
    _git(repo, "checkout", "-q", "main")
    (repo / "a.txt").write_text("main\n"); _commit(repo, "main")
    _git(repo, "merge", "--no-ff", "-q", "-m", "merge", "side")

    assert merge_file_rows(str(repo)) == {}


def test_repo_without_merges_is_empty(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("a\n")
    _commit(repo, "one")

    assert merge_file_rows(str(repo)) == {}


def test_repo_without_commits_is_empty(tmp_path):
    assert merge_file_rows(str(_repo(tmp_path))) == {}


def _evil_repo(tmp_path):
    """A merge that resolves a conflict and adds a file of its own."""
    repo = _repo(tmp_path)
    (repo / "shared.txt").write_text("base\n")
    _commit(repo, "base")
    _git(repo, "checkout", "-qb", "side")
    (repo / "shared.txt").write_text("side\n"); _commit(repo, "side")
    _git(repo, "checkout", "-q", "main")
    (repo / "shared.txt").write_text("main\n"); _commit(repo, "main")
    _git(repo, "merge", "side", check=False)          # conflicts
    (repo / "shared.txt").write_text("resolved\n")
    (repo / "note.md").write_text("only here\n")
    return repo, _commit(repo, "merge with resolution")


def test_evil_merge_yields_its_own_content(tmp_path):
    repo, merge = _evil_repo(tmp_path)

    rows = merge_file_rows(str(repo))

    assert set(rows) == {merge}
    assert set(rows[merge]) == {"shared.txt", "note.md"}
    assert rows[merge]["note.md"]["additions"] == 1
    assert rows[merge]["note.md"]["deletions"] == 0


def test_counts_use_the_closest_parent_not_the_first(tmp_path):
    """first-parent counts credit the merger with the branch's work. Here the
    merge result is one line from the side branch and ten lines from main."""
    repo = _repo(tmp_path)
    (repo / "f.txt").write_text("".join(f"{i}\n" for i in range(10)))
    _commit(repo, "base")

    _git(repo, "checkout", "-qb", "side")
    (repo / "f.txt").write_text("".join(f"side{i}\n" for i in range(10)))
    _commit(repo, "side rewrite")

    _git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("".join(f"{i}\n" for i in range(9)) + "main9\n")
    _commit(repo, "main tweak")

    _git(repo, "merge", "side", check=False)
    # Resolve to the side version, with one extra line changed.
    (repo / "f.txt").write_text("".join(f"side{i}\n" for i in range(9)) + "resolved\n")
    merge = _commit(repo, "resolve to side")

    rows = merge_file_rows(str(repo))
    counts = rows[merge]["f.txt"]

    # vs main (parent 1) the whole file changed; vs side (parent 2) one line did.
    assert counts["additions"] + counts["deletions"] <= 4, counts


def test_octopus_merge_is_handled(tmp_path):
    """nixpkgs has 12 octopus merges, one with 16 parents."""
    repo = _repo(tmp_path)
    (repo / "base.txt").write_text("base\n")
    _commit(repo, "base")

    for n in ("one", "two", "three"):
        _git(repo, "checkout", "-q", "main")
        _git(repo, "checkout", "-qb", n)
        (repo / f"{n}.txt").write_text(f"{n}\n")
        _commit(repo, n)

    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "-q", "-m", "octopus", "one", "two", "three")
    (repo / "resolution.txt").write_text("added during merge\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "--no-edit")
    merge = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # main plus the three merged branches
    assert len(_git(repo, "rev-list", "-1", "--parents", merge).stdout.split()) - 1 == 4

    rows = merge_file_rows(str(repo))

    assert merge in rows
    assert "resolution.txt" in rows[merge]


def test_non_ascii_paths_are_unquoted(tmp_path):
    repo = _repo(tmp_path)
    (repo / "shared.txt").write_text("base\n")
    _commit(repo, "base")
    _git(repo, "checkout", "-qb", "side")
    (repo / "shared.txt").write_text("side\n"); _commit(repo, "side")
    _git(repo, "checkout", "-q", "main")
    (repo / "shared.txt").write_text("main\n"); _commit(repo, "main")
    _git(repo, "merge", "side", check=False)
    (repo / "shared.txt").write_text("resolved\n")
    (repo / "café.py").write_text("x = 1\n")
    merge = _commit(repo, "merge")

    rows = merge_file_rows(str(repo))

    assert "café.py" in rows[merge]
