"""CLI-level tests for `kgit clone`.

clone_repo is monkeypatched — these tests exercise argument handling only.
"""
from click.testing import CliRunner

import kgit as kgit_module


def test_clone_with_no_arguments_shows_help():
    """Bare `kgit clone` used to fall through both branches and exit silently."""
    result = CliRunner().invoke(kgit_module.cli, ["clone"])

    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_clone_help_documents_ssh_urls():
    result = CliRunner().invoke(kgit_module.cli, ["clone", "--help"])

    assert result.exit_code == 0
    assert "git@github.com:ORG/REPO.git" in result.output


def test_clone_rejects_both_a_url_and_a_filename(tmp_path):
    repo_list = tmp_path / "repos.txt"
    repo_list.write_text("git@github.com:company-org/dashboard.git\n")

    result = CliRunner().invoke(kgit_module.cli, [
        "clone", "git@github.com:company-org/dashboard.git",
        "-filename", str(repo_list),
    ])

    assert result.exit_code == 2
    assert "not both" in result.output


def test_clone_passes_a_positional_ssh_url_through(monkeypatch):
    """The one-off form, not just -filename, must reach clone_repo intact."""
    seen = []
    monkeypatch.setattr(
        kgit_module.kgit, "clone_repo", lambda url: seen.append(url) or None)

    result = CliRunner().invoke(
        kgit_module.cli, ["clone", "git@github.com:company-org/dashboard.git"])

    assert result.exit_code == 0
    assert seen == ["git@github.com:company-org/dashboard.git"]


def test_clone_filename_round_trip_resolves_every_ssh_url(tmp_path, monkeypatch):
    """Regression guard for the reported bug.

    `kgit github -ssh-clone-url -out-repo-list` writes scp-style URLs; feeding
    that file back to `kgit clone -filename` printed 'ERROR with <url>' for every
    line, because clone_repo returned None. Comment lines are still skipped.
    """
    urls = [
        "git@github.com:company-org/dashboard.git",
        "git@github.com:company-org/dashboard.js.git",
        "git@gitlab.com:group/sub/repo.git",
    ]
    repo_list = tmp_path / "repos.txt"
    repo_list.write_text("# a comment\n" + "\n".join(urls) + "\n")

    seen = []
    monkeypatch.setattr(
        kgit_module.kgit, "clone_repo", lambda url: seen.append(url) or None)

    result = CliRunner().invoke(
        kgit_module.cli, ["clone", "-filename", str(repo_list)])

    assert result.exit_code == 0
    assert seen == urls
