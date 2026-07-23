"""Tests for KospexGit.clone_repo.

subprocess.run is monkeypatched throughout — no test in this file performs a
real clone or touches the network. HabitatConfig.with_overrides points the code
directory at pytest's tmp_path; tests/conftest.py resets the singleton around
every test.
"""
import os
import subprocess

import pytest

from kospex.habitat_config import HabitatConfig
from kospex_git import KospexGit


class _FakeCompletedProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode


@pytest.fixture
def captured_runs(monkeypatch):
    """Capture subprocess.run calls instead of executing them."""
    runs = []

    def fake_run(cmd, **kwargs):
        runs.append({"cmd": cmd, "cwd": kwargs.get("cwd")})
        return _FakeCompletedProcess(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return runs


@pytest.fixture
def code_dir(tmp_path):
    """Point KOSPEX_CODE at a throwaway directory for the duration of a test."""
    config = HabitatConfig.get_instance()
    with config.with_overrides(KOSPEX_CODE=str(tmp_path)):
        yield tmp_path


def test_clone_repo_accepts_scp_style_ssh_url(code_dir, captured_runs):
    """The bug this change exists to fix: SSH URLs used to return None."""
    kg = KospexGit()
    url = "git@github.com:company-org/dashboard.git"

    result = kg.clone_repo(url)

    expected = code_dir / "github.com" / "company-org" / "dashboard"
    assert result == str(expected)
    assert isinstance(result, str), "kgit.py:264 concatenates this with a str"
    assert len(captured_runs) == 1
    assert captured_runs[0]["cmd"] == ["git", "clone", "--", url, "dashboard"]
    assert captured_runs[0]["cwd"] == expected.parent


def test_clone_repo_does_not_shell_out(code_dir, captured_runs):
    """argv is a list and the URL sits after '--', so no shell or argument
    injection is possible."""
    kg = KospexGit()

    kg.clone_repo("git@github.com:company-org/dashboard.git")

    cmd = captured_runs[0]["cmd"]
    assert isinstance(cmd, list)
    assert cmd[:3] == ["git", "clone", "--"]


def test_clone_repo_leaves_working_directory_unchanged(code_dir, captured_runs):
    """clone_repo must not os.chdir — kospex-api's sync agent calls it as a
    library, where mutating process-global state is a threading hazard."""
    kg = KospexGit()
    before = os.getcwd()

    kg.clone_repo("git@github.com:company-org/dashboard.git")

    assert os.getcwd() == before


def test_clone_repo_creates_nested_org_directories(code_dir, captured_runs):
    """GitLab orgs are multi-segment (group/sub) and must nest."""
    kg = KospexGit()

    result = kg.clone_repo("git@gitlab.com:group/sub/repo.git")

    expected = code_dir / "gitlab.com" / "group" / "sub" / "repo"
    assert result == str(expected)
    assert expected.parent.is_dir()


def test_clone_repo_pulls_when_the_repo_already_exists(code_dir, captured_runs):
    repo_dir = code_dir / "github.com" / "company-org" / "dashboard"
    repo_dir.mkdir(parents=True)
    kg = KospexGit()

    result = kg.clone_repo("git@github.com:company-org/dashboard.git")

    assert result == str(repo_dir)
    assert captured_runs[0]["cmd"] == ["git", "pull"]
    assert captured_runs[0]["cwd"] == repo_dir


def test_clone_repo_refuses_destination_outside_the_code_directory(code_dir, captured_runs):
    """parse_ado_git_url joins remaining path segments straight from urlparse,
    which does not normalise, so '..' survives into the repo name."""
    kg = KospexGit()

    result = kg.clone_repo(
        "https://dev.azure.com/myorg/myproj/_git/../../../../../../etc/passwd")

    assert result is None
    assert captured_runs == [], "git must never be invoked for a traversal URL"


def test_clone_repo_returns_none_for_an_unparseable_url(code_dir, captured_runs):
    kg = KospexGit()

    assert kg.clone_repo("not-a-url") is None
    assert captured_runs == []


def test_clone_repo_returns_none_when_git_fails(code_dir, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kwargs: _FakeCompletedProcess(1))
    kg = KospexGit()

    assert kg.clone_repo("git@github.com:company-org/dashboard.git") is None
