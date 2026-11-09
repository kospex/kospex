"""Every kospex pull fast-forwards a clone or fails and leaves it as it was.

A kospex clone is a mirror of its remote. A plain `git pull` follows the user's
pull.rebase / pull.ff config, so when the remote's history was rewritten it can
rebase the clone (dropping the old commits from the branch) or merge (adding a
commit the remote never had), and the sync that follows records that.

These tests use real git repositories on local paths - no network - under each
pull config that would change the clone, and check the clone's history
afterwards, not just the command line kospex built.
"""
import os
import subprocess

import pytest
from click.testing import CliRunner

import kgit as kgit_module
import krunner as krunner_module
from kospex.habitat_config import HabitatConfig
from kospex_git import KospexGit

PULL_CONFIGS = [
    None,
    "pull.rebase=true",
    "pull.rebase=merges",
    "pull.rebase=false",
    "pull.ff=false",
]


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _log(path):
    return _git(path, "log", "--format=%s").split()


@pytest.fixture
def git_env(tmp_path, monkeypatch, request):
    """An isolated global git config, with the pull setting under test."""
    gitconfig = tmp_path / "gitconfig"
    lines = ["[user]", "\tname = t", "\temail = t@example.com",
             "[init]", "\tdefaultBranch = main"]
    setting = getattr(request, "param", None)
    if setting:
        section, _, rest = setting.partition(".")
        key, _, value = rest.partition("=")
        lines += [f"[{section}]", f"\t{key} = {value}"]
    gitconfig.write_text("\n".join(lines) + "\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return setting


def _make_clone(tmp_path, clone_dir, change):
    """A remote with commits a, b; a clone of it at clone_dir; then `change`
    applied: 'rewritten' (force-push), 'recreated' (unrelated history at the
    same URL), 'local' (a commit only the clone has, remote moved on) or
    'continued' (the remote gained a commit)."""
    remote = tmp_path / "remote"
    remote.mkdir(parents=True)
    _git(remote, "init", "-q")
    for msg in ("a", "b"):
        _git(remote, "commit", "-q", "--allow-empty", "-m", msg)
    clone_dir.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "clone", "-q", str(remote), str(clone_dir))

    if change == "rewritten":
        _git(remote, "reset", "-q", "--hard", "HEAD~1")
        _git(remote, "commit", "-q", "--allow-empty", "-m", "rewritten")
    elif change == "recreated":
        _git(remote, "checkout", "-q", "--orphan", "fresh")
        _git(remote, "commit", "-q", "--allow-empty", "-m", "new1")
        _git(remote, "branch", "-q", "-M", "main")
    elif change == "local":
        _git(remote, "commit", "-q", "--allow-empty", "-m", "c")
        _git(clone_dir, "commit", "-q", "--allow-empty", "-m", "mine")
    elif change == "continued":
        _git(remote, "commit", "-q", "--allow-empty", "-m", "c")
    return clone_dir


@pytest.fixture
def code_dir(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    with HabitatConfig.get_instance().with_overrides(
            KOSPEX_CODE=str(code), KOSPEX_HOME=str(tmp_path / "home")):
        yield code


def test_pull_command_is_fast_forward_only_and_overrides_pull_rebase():
    argv, env = KospexGit.pull_command("/clone/path")
    assert argv == ["git", "-C", "/clone/path", "pull", "--ff-only", "--no-rebase"]
    assert env == {}


def test_kgit_pull_uses_the_shared_pull_command():
    assert kgit_module._pull_command("/p", no_prompt=True) == \
        KospexGit.pull_command("/p", no_prompt=True)


# --- clone_repo: kgit clone, kgit sync URL / --org, kgit github -sync -------

@pytest.mark.parametrize("git_env", PULL_CONFIGS, indirect=True)
@pytest.mark.parametrize("change", ["rewritten", "recreated", "local"])
def test_clone_repo_leaves_a_clone_whose_remote_diverged(git_env, change, tmp_path, code_dir):
    clone = _make_clone(tmp_path, code_dir / "github.com" / "acme" / "app", change)
    before = _log(clone)

    assert KospexGit().clone_repo("https://github.com/acme/app") is None
    assert _log(clone) == before


@pytest.mark.parametrize("git_env", PULL_CONFIGS, indirect=True)
def test_clone_repo_fast_forwards(git_env, tmp_path, code_dir):
    clone = _make_clone(tmp_path, code_dir / "github.com" / "acme" / "app", "continued")

    assert KospexGit().clone_repo("https://github.com/acme/app") == str(clone)
    assert _log(clone) == ["c", "b", "a"]


# --- krunner git-pull ------------------------------------------------------

@pytest.mark.parametrize("git_env", ["pull.rebase=true", "pull.ff=false"], indirect=True)
def test_krunner_git_pull_does_not_sync_a_clone_it_could_not_fast_forward(
        git_env, tmp_path, monkeypatch):
    work = tmp_path / "work"
    stuck = _make_clone(tmp_path / "s", work / "stuck", "rewritten")
    moved = _make_clone(tmp_path / "m", work / "moved", "continued")
    before = _log(stuck)
    synced = []
    monkeypatch.setattr(krunner_module.kospex, "sync_repo", lambda d: synced.append(d))

    result = CliRunner().invoke(krunner_module.cli, ["git-pull", str(work)])

    assert result.exit_code == 0, result.output
    assert _log(stuck) == before
    assert _log(moved) == ["c", "b", "a"]
    assert [os.path.basename(d) for d in synced] == ["moved"]
    assert "GIT_ERROR" in result.output


# --- kospex-agent ----------------------------------------------------------

@pytest.mark.parametrize("git_env", ["pull.rebase=true"], indirect=True)
def test_agent_does_not_sync_a_clone_it_could_not_fast_forward(git_env, tmp_path, monkeypatch):
    import kospex_agent

    stuck = _make_clone(tmp_path / "s", tmp_path / "work" / "stuck", "rewritten")
    moved = _make_clone(tmp_path / "m", tmp_path / "work" / "moved", "continued")
    before = _log(stuck)
    agent = kospex_agent.agent
    synced = []
    monkeypatch.setattr(agent.kospex, "sync_repo", lambda d: synced.append(d))
    cwd = os.getcwd()

    agent._update_repositories([
        {"_repo_id": "s~o~stuck", "file_path": str(stuck)},
        {"_repo_id": "s~o~moved", "file_path": str(moved)},
    ])

    assert _log(stuck) == before
    assert _log(moved) == ["c", "b", "a"]
    assert [os.path.basename(d) for d in synced] == ["moved"]
    assert os.getcwd() == cwd


@pytest.mark.parametrize("git_env", ["pull.rebase=false"], indirect=True)
def test_agent_never_prompts_for_credentials(git_env, tmp_path, monkeypatch):
    """The agent runs unattended, so its pull must fail rather than wait on a prompt."""
    import kospex_agent

    seen = {}

    def fake_run(argv, env=None, **kwargs):
        seen["argv"], seen["env"] = argv, env
        return subprocess.CompletedProcess(argv, 1, "", "fatal: auth")

    clone = _make_clone(tmp_path, tmp_path / "work" / "app", "continued")
    monkeypatch.setattr(kospex_agent.subprocess, "run", fake_run)
    kospex_agent.agent._update_repositories([{"_repo_id": "s~o~app", "file_path": str(clone)}])

    assert "credential.interactive=false" in seen["argv"]
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
