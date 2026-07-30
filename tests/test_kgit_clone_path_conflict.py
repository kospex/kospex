"""`kgit clone` refuses to clone a repo that is already synced from elsewhere.

The check runs before the clone so no network work (and no stray directory) is
wasted on a repo the sync would refuse anyway. clone_repo is monkeypatched
throughout - nothing is cloned.
"""
import pytest
from click.testing import CliRunner

import kgit as kgit_module
from kospex_git import KospexGit

_URL = "https://github.com/test/repo.git"
_REPO_ID = "github.com~test~repo"


@pytest.fixture
def code_dir(tmp_path, monkeypatch):
    """A throwaway KOSPEX_CODE so planned_clone_path resolves somewhere safe."""
    code = tmp_path / "code"
    code.mkdir()
    monkeypatch.setenv("KOSPEX_CODE", str(code))
    from kospex.habitat_config import HabitatConfig
    HabitatConfig.reset_instance()
    return code


def _clone_spy(monkeypatch):
    """Replace clone_repo with a recorder; returns the list of URLs it saw."""
    seen = []
    monkeypatch.setattr(
        kgit_module.kgit, "clone_repo", lambda url: seen.append(url) or None)
    return seen


def _recorded_as(monkeypatch, file_path):
    """Make the DB report this repo_id as already synced from file_path.

    Patched on the class: `kgit clone` builds its own Kospex() instance, so
    patching the module-level one would miss it.
    """
    row = {"_repo_id": _REPO_ID, "file_path": file_path} if file_path else None
    from kospex_query import KospexQuery
    monkeypatch.setattr(KospexQuery, "get_repo_by_id", lambda self, repo_id: row)


def test_planned_clone_path_names_the_repo_id_and_destination(code_dir):
    planned = KospexGit().planned_clone_path(_URL)

    assert planned["repo_id"] == _REPO_ID
    assert planned["path"] == str(code_dir / "github.com" / "test" / "repo")


def test_planned_clone_path_rejects_an_unparseable_url(code_dir):
    assert KospexGit().planned_clone_path("not-a-url") is None


def test_planned_clone_path_matches_where_clone_repo_actually_clones(code_dir, monkeypatch):
    """The pre-flight destination must not drift from the real one."""
    import subprocess

    class _Ok:
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Ok())

    kg = KospexGit()
    assert kg.clone_repo(_URL) == kg.planned_clone_path(_URL)["path"]


def test_clone_is_refused_when_the_repo_is_synced_from_another_live_path(
    code_dir, tmp_path, monkeypatch
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()  # the recorded clone still exists
    _recorded_as(monkeypatch, str(elsewhere))
    seen = _clone_spy(monkeypatch)

    result = CliRunner().invoke(kgit_module.cli, ["clone", _URL])

    assert result.exit_code != 0
    assert seen == []  # refused before any network work
    # rich wraps long paths at the console width, so compare without whitespace
    flat = "".join(result.output.split())
    assert str(elsewhere) in flat
    assert "-force" in flat  # tells the user how to override


def test_force_clones_anyway(code_dir, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _recorded_as(monkeypatch, str(elsewhere))
    seen = _clone_spy(monkeypatch)

    result = CliRunner().invoke(kgit_module.cli, ["clone", _URL, "-force"])

    assert result.exit_code == 0
    assert seen == [_URL]


def test_clone_proceeds_when_the_recorded_clone_is_gone(code_dir, tmp_path, monkeypatch):
    """Self-healing case - the recorded path no longer exists, so repoint it."""
    _recorded_as(monkeypatch, str(tmp_path / "deleted"))  # never created
    seen = _clone_spy(monkeypatch)

    result = CliRunner().invoke(kgit_module.cli, ["clone", _URL])

    assert result.exit_code == 0
    assert seen == [_URL]


def test_clone_proceeds_for_a_repo_never_synced_before(code_dir, monkeypatch):
    _recorded_as(monkeypatch, None)
    seen = _clone_spy(monkeypatch)

    result = CliRunner().invoke(kgit_module.cli, ["clone", _URL])

    assert result.exit_code == 0
    assert seen == [_URL]
