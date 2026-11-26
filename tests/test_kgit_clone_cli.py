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

    # The fake clone returns None, so each URL counts as failed.
    assert result.exit_code == 1
    assert seen == urls


def test_clone_failure_in_sync_does_not_call_sync_repo(monkeypatch):
    """A failed clone must not reach sync_repo(None), which raises MissingGitDirectory."""
    monkeypatch.setattr(kgit_module.kgit, "clone_repo", lambda url: None)

    synced = []
    monkeypatch.setattr(
        kgit_module.kospex, "sync_repo", lambda path: synced.append(path))

    result = CliRunner().invoke(
        kgit_module.cli, ["sync", "git@github.com:company-org/dashboard.git"])

    assert result.exit_code == 0
    assert synced == [], "sync_repo must not be called after a failed clone"
    assert result.exception is None or isinstance(result.exception, SystemExit)


# `kgit github ORG -sync` now runs `kgit sync --org` (tests/test_kgit_github_listing.py).
# A repo that fails to clone is recorded as failed and not synced, while the rest
# continue: tests/test_org_sync.py ("clone or pull failed").


class _FakeKospex:
    """Stands in for Kospex() so `clone -filename` touches no database."""

    fail_on = ()
    error = RuntimeError("boom")

    def __init__(self):
        self.git = type("Git", (), {"get_repo_id": staticmethod(lambda: "x~y~z")})()
        self.kospex_query = type(
            "Query", (), {"set_repo_last_fetch": staticmethod(lambda repo_id: None)})()

    def sync_repo(self, path, **kw):
        if any(name in path for name in self.fail_on):
            raise self.error


def _clone_file(tmp_path, monkeypatch, lines, clone=None, fail_on=(), error=None):
    cloned = []

    def clone_repo(url):
        cloned.append(url)
        return clone(url) if clone else f"/code/{url}"

    fake = type("Fake", (_FakeKospex,), {"fail_on": fail_on})
    if error:
        fake.error = error
    monkeypatch.setattr(kgit_module, "Kospex", fake)
    monkeypatch.setattr(kgit_module, "clone_path_conflict", lambda *a, **k: False)
    monkeypatch.setattr(kgit_module.kgit, "clone_repo", clone_repo)
    repo_list = tmp_path / "repos.txt"
    repo_list.write_text("".join(f"{line}\n" for line in lines))
    result = CliRunner().invoke(kgit_module.cli, ["clone", "-filename", str(repo_list)])
    return result, cloned


URLS = [f"https://gitlab.example.com/acme/{name}.git" for name in ("one", "two", "three")]


def test_clone_filename_carries_on_after_a_repo_that_raises(tmp_path, monkeypatch):
    result, cloned = _clone_file(tmp_path, monkeypatch, URLS, fail_on=("two",))

    assert cloned == URLS
    assert result.exit_code == 1
    summary = result.output.split("1 failed")[1]
    assert URLS[1] in summary
    assert URLS[0] not in summary


def test_clone_filename_counts_a_clone_that_returns_nothing(tmp_path, monkeypatch):
    result, cloned = _clone_file(
        tmp_path, monkeypatch, URLS,
        clone=lambda url: None if "one" in url else "/code/ok")

    assert cloned == URLS
    assert result.exit_code == 1
    assert "1 failed" in result.output


def test_clone_filename_skips_blank_and_comment_lines(tmp_path, monkeypatch):
    result, cloned = _clone_file(
        tmp_path, monkeypatch, ["# header", "", URLS[0], "   ", URLS[1]])

    assert cloned == URLS[:2]
    assert result.exit_code == 0
    assert "failed" not in result.output


def test_clone_filename_stops_on_ctrl_c(tmp_path, monkeypatch):
    result, cloned = _clone_file(
        tmp_path, monkeypatch, URLS, fail_on=("two",), error=KeyboardInterrupt())

    assert cloned == URLS[:2]
    assert result.exit_code != 0


def test_clone_filename_summary_lists_refused_repos(tmp_path, monkeypatch):
    """A refusal is not a failure, but it must not vanish from a long run."""
    cloned = []
    monkeypatch.setattr(kgit_module, "Kospex", _FakeKospex)
    monkeypatch.setattr(kgit_module, "clone_path_conflict",
                        lambda kospex, url, force=False: "two" in url)
    monkeypatch.setattr(kgit_module.kgit, "clone_repo",
                        lambda url: cloned.append(url) or "/code/x")
    repo_list = tmp_path / "repos.txt"
    repo_list.write_text("".join(f"{url}\n" for url in URLS))

    result = CliRunner().invoke(kgit_module.cli, ["clone", "-filename", str(repo_list)])

    assert cloned == [URLS[0], URLS[2]]
    assert result.exit_code == 0
    assert URLS[1] in result.output.split("1 refused")[1]


def test_clone_filename_summary_leaves_out_url_credentials(tmp_path, monkeypatch):
    url = "https://user:s3cret@gitlab.example.com/acme/one.git"
    result, _ = _clone_file(tmp_path, monkeypatch, [url], clone=lambda u: None)

    assert "s3cret" not in result.output.split("1 failed")[1]
