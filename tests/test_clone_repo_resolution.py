"""clone_repo must find an existing clone via repos.file_path, not the layout.

It inferred existence from `planned_clone_path().is_dir()`. That path is built
from the parsed URL, so it changes whenever the derivation changes -- #147
lowercased it. A repo synced before that change sits in a mixed-case directory,
the lowercase path does not exist, and on a case-sensitive filesystem the repo
is cloned a second time.

repos.file_path records where the clone actually is. kospex already treats it
as authoritative everywhere else (kgit pull reads it), so clone_repo should too.
"""
from pathlib import Path

import pytest

from kospex_git import KospexGit


class _RecordingQuery:
    """Stands in for KospexQuery. Records the repo_id it was asked about."""

    def __init__(self, row=None):
        self.row = row
        self.asked_for = None

    def get_repo_by_id(self, repo_id):
        self.asked_for = repo_id
        return self.row


@pytest.fixture
def code_dir(tmp_path, monkeypatch):
    from kospex.habitat_config import HabitatConfig
    d = tmp_path / "code"
    d.mkdir()
    monkeypatch.setattr(HabitatConfig, "get_instance",
                        staticmethod(lambda: type("C", (), {"code_dir": d})()))
    return d


def _clone_at(base, *parts):
    """A directory that looks like a clone."""
    p = base.joinpath(*parts)
    (p / ".git").mkdir(parents=True)
    return p


def test_existing_clone_is_found_via_file_path_not_the_layout(code_dir, monkeypatch):
    """The recorded path differs from the layout the URL would produce."""
    recorded = _clone_at(code_dir, "github.com", "Textualize", "rich")

    kg = KospexGit()
    q = _RecordingQuery(row={"_repo_id": "github.com~textualize~rich",
                             "file_path": str(recorded)})
    kg.kospex_query = q

    calls = []
    monkeypatch.setattr("kospex_git.subprocess.run",
                        lambda argv, **kw: calls.append((argv, kw.get("cwd")))
                        or type("R", (), {"returncode": 0})())

    kg.clone_repo("https://github.com/Textualize/rich")

    assert q.asked_for == "github.com~textualize~rich"
    assert calls, "expected git to be invoked"
    argv, cwd = calls[0]
    assert argv[:2] == ["git", "pull"], f"expected a pull, got {argv[:3]}"
    assert Path(cwd) == recorded, "pulled in the wrong directory"


def test_unknown_repo_still_clones_to_the_planned_path(code_dir, monkeypatch):
    kg = KospexGit()
    kg.kospex_query = _RecordingQuery(row=None)

    calls = []
    monkeypatch.setattr("kospex_git.subprocess.run",
                        lambda argv, **kw: calls.append((argv, kw.get("cwd")))
                        or type("R", (), {"returncode": 0})())

    kg.clone_repo("https://github.com/Textualize/rich")

    argv, cwd = calls[0]
    assert argv[:2] == ["git", "clone"]
    assert argv[-1] == "rich"
    assert Path(cwd) == code_dir / "github.com" / "textualize"


def test_recorded_path_that_no_longer_exists_falls_back_to_cloning(code_dir, monkeypatch):
    """A stale file_path must not stop a fresh clone."""
    kg = KospexGit()
    kg.kospex_query = _RecordingQuery(
        row={"_repo_id": "github.com~textualize~rich",
             "file_path": str(code_dir / "gone" / "rich")})

    calls = []
    monkeypatch.setattr("kospex_git.subprocess.run",
                        lambda argv, **kw: calls.append((argv, kw.get("cwd")))
                        or type("R", (), {"returncode": 0})())

    kg.clone_repo("https://github.com/Textualize/rich")
    assert calls[0][0][:2] == ["git", "clone"]


# --- the coupling that makes the lowercased directory work ------------------

@pytest.mark.parametrize("url,expected_dir", [
    ("https://github.com/chartjs/Chart.js", "chart.js"),
    ("https://github.com/Textualize/rich", "rich"),
    ("https://gitlab.com/Group/SubGroup/Repo.git", "repo"),
])
def test_clone_destination_matches_the_planned_path(code_dir, monkeypatch, url, expected_dir):
    """git must be told the destination, or it derives one from the URL.

    planned_clone_path and clone_repo agree only because both read the parsed
    parts. Drop the explicit destination -- it looks like boilerplate -- and git
    names the directory from the URL, so a repo whose name has a capital lands
    somewhere planned_clone_path will never look. The failure would be a missing
    directory, not an error.
    """
    kg = KospexGit()
    kg.kospex_query = _RecordingQuery(row=None)

    calls = []
    monkeypatch.setattr("kospex_git.subprocess.run",
                        lambda argv, **kw: calls.append((argv, kw.get("cwd")))
                        or type("R", (), {"returncode": 0})())

    planned = Path(kg.planned_clone_path(url)["path"])
    kg.clone_repo(url)

    argv, cwd = calls[0]
    assert argv[-1] == expected_dir
    assert Path(cwd) / argv[-1] == planned
