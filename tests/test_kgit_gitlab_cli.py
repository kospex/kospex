"""CLI tests for `kgit gitlab`, against a fake GitLab client."""
import json

import pytest
from click.testing import CliRunner

import kgit as kgit_module
from kospex.integrations.gitlab.client import GitLabAuthError, GitLabError, server_url
from kospex_git import KospexGit

HOST = "https://gitlab.example.com:8929"
TOKEN = "glpat-secret-value"


def project(name, http=None, ssh=None, **kw):
    return {"id": len(name), "path_with_namespace": f"acme/{name}", "visibility": "private",
            "http_url_to_repo": http or f"{HOST}/acme/{name}.git",
            "ssh_url_to_repo": ssh or f"ssh://git@gitlab.example.com:2424/acme/{name}.git",
            "last_activity_at": "2020-01-01T00:00:00Z", **kw}


RECENT = "2099-01-01T00:00:00Z"
PROJECTS = [
    project("app", last_activity_at=RECENT, runners_token="runner-secret"),
    project("old"),
    project("fork", forked_from_project={"id": 9}, last_activity_at=RECENT),
    project("empty", empty_repo=True),
]


class FakeGitLab:
    projects = PROJECTS
    groups = [{"id": 1, "full_path": "acme", "visibility": "private"},
              {"id": 2, "full_path": "acme/platform", "visibility": "internal"}]
    error = None
    calls = []

    def __init__(self, server=None):
        self.server = server_url(server)

    def paginate(self, path, params=None):
        FakeGitLab.calls.append(path)
        if self.error:
            raise self.error
        return list(self.projects if path.endswith("/projects") else self.groups)

    def identity(self):
        if self.error:
            raise self.error
        return {"username": "dev", "version": "17.1.0", "scopes": ["read_api"],
                "expires_at": "2027-01-01"}


@pytest.fixture
def gitlab(monkeypatch):
    monkeypatch.delenv("GITLAB_URL", raising=False)
    monkeypatch.setenv("GITLAB_TOKEN", TOKEN)
    for name in ("projects", "groups", "error"):
        monkeypatch.setattr(FakeGitLab, name, getattr(FakeGitLab, name))
    monkeypatch.setattr(FakeGitLab, "calls", [])
    monkeypatch.setattr(kgit_module, "KospexGitlabClient", FakeGitLab)
    return FakeGitLab


def run(*args, code=0):
    result = CliRunner().invoke(kgit_module.cli, ["gitlab", *args])
    assert result.exit_code == code, result.output
    return result.output


def lines(path):
    return path.read_text().splitlines()


def test_bare_command_shows_help(gitlab):
    assert "Usage:" in run()
    assert gitlab.calls == []


def test_help_names_the_settings(gitlab):
    out = run("--help")
    for text in ("GITLAB_TOKEN", "read_api", "GITLAB_URL", "REQUESTS_CA_BUNDLE",
                 "git's own credentials"):
        assert text in out


@pytest.mark.parametrize("args", [
    ["-test-auth", "--group", "acme"],
    ["-test-auth", "--groups"],
    ["-test-auth", "-out-raw", "x.json"],
    ["--groups", "-active"],
    ["--groups", "-include-forks"],
    ["--groups", "-skip-archived"],
    ["--groups", "-out-repo-list", "x.txt"],
    ["--group", "acme", "-ssh-clone-url"],
    ["--group", "acme", "-active", "0"],
])
def test_option_combinations_that_are_refused(gitlab, args):
    run(*args, code=2)
    assert gitlab.calls == []


def test_clone_list_leaves_out_forks_and_empty(gitlab, tmp_path):
    out_file = tmp_path / "repos.txt"
    out = run("--group", "acme", "--server", HOST, "-out-repo-list", str(out_file))
    assert lines(out_file) == [f"{HOST}/acme/app.git", f"{HOST}/acme/old.git"]
    assert f"GitLab server: {HOST}" in out
    assert "4 projects" in out
    assert "2 in the clone list" in out
    assert "1 empty" in out and "1 fork" in out


def test_ssh_clone_urls_and_org_alias(gitlab, tmp_path):
    out_file = tmp_path / "repos.txt"
    run("--org", "acme", "-out-repo-list", str(out_file), "-ssh-clone-url")
    assert lines(out_file)[0] == "ssh://git@gitlab.example.com:2424/acme/app.git"


def test_ported_clone_urls_give_a_repo_id_without_the_port(gitlab, tmp_path):
    for ssh in ([], ["-ssh-clone-url"]):
        out_file = tmp_path / "repos.txt"
        run("--group", "acme", "-out-repo-list", str(out_file), *ssh)
        parts = KospexGit.parse_git_remote(lines(out_file)[0])
        assert parts["remote"] == "gitlab.example.com"
        assert parts["org"] == "acme"


@pytest.mark.parametrize("active", [["-active"], ["-active", "30"]])
def test_active_filters_the_clone_list_and_the_table(gitlab, tmp_path, active):
    out_file = tmp_path / "repos.txt"
    out = run("--group", "acme", *active, "-out-repo-list", str(out_file))
    assert lines(out_file) == [f"{HOST}/acme/app.git"]
    days = active[1] if len(active) > 1 else "90"
    assert f"2 active in the last {days} days" in out
    assert "last activity, not last push" in out
    assert "acme/old" not in out


def test_out_raw_keeps_everything_but_secrets(gitlab, tmp_path):
    raw = tmp_path / "raw.json"
    run("--group", "acme", "-active", "-out-raw", str(raw))
    data = json.loads(raw.read_text())
    assert len(data) == 4
    assert "runner-secret" not in raw.read_text()


def test_groups(gitlab, tmp_path):
    raw = tmp_path / "groups.json"
    out = run("--groups", "-out-raw", str(raw))
    assert "acme/platform" in out and "internal" in out
    assert "2 groups" in out
    assert gitlab.calls == ["/groups"]
    assert len(json.loads(raw.read_text())) == 2


def test_groups_under_a_group(gitlab):
    run("--groups", "--group", "acme")
    assert gitlab.calls == ["/groups/acme/descendant_groups"]


def test_every_project_left_out_is_accounted_for(gitlab, tmp_path):
    gitlab.projects = [
        project("good"),
        {**project("nourl"), "http_url_to_repo": None},
        project("bad", http=f"{HOST}/acme/bad.git\nhttps://other.example.com/x.git"),
    ]
    out_file = tmp_path / "repos.txt"
    out = run("--group", "acme", "-out-repo-list", str(out_file))
    assert lines(out_file) == [f"{HOST}/acme/good.git"]
    assert "1 in the clone list" in out
    assert "1 no clone URL" in out and "1 unsafe clone URL" in out


def test_a_failed_listing_writes_nothing(gitlab, tmp_path):
    gitlab.error = GitLabError("page 2 failed")
    keep = tmp_path / "repos.txt"
    keep.write_text("earlier\n")
    raw = tmp_path / "raw.json"
    out = run("--group", "acme", "-out-repo-list", str(keep), "-out-raw", str(raw), code=1)
    assert "Error: page 2 failed" in out
    assert keep.read_text() == "earlier\n"
    assert not raw.exists()


def test_server_text_is_printed_literally_and_escaped(gitlab):
    gitlab.projects = [project("[red]x\x1b[31m")]
    out = run("--group", "acme")
    assert "[red]x\\x1b[31m" in out
    assert "\x1b" not in out


def test_piped_output_keeps_long_paths_whole(gitlab):
    name = "a" * 120
    gitlab.projects = [project(name)]
    assert f"acme/{name}" in run("--group", "acme")


def test_a_path_install_is_warned_about(gitlab):
    assert "under a path" in run("--groups", "--server", "https://host/gitlab")


def test_test_auth(gitlab):
    out = run("-test-auth", "--server", HOST)
    assert "dev" in out and "17.1.0" in out and "read_api" in out and "2027-01-01" in out
    assert TOKEN not in out


def test_test_auth_failure(gitlab):
    gitlab.error = GitLabAuthError("https://gitlab.com rejected the token in GITLAB_TOKEN (401)")
    out = run("-test-auth", code=1)
    assert "Error:" in out and "GITLAB_TOKEN" in out
    assert "Traceback" not in out


def test_no_token_is_an_error_not_a_traceback(monkeypatch):
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_URL", raising=False)
    out = run("--groups", code=1)
    assert "GITLAB_TOKEN" in out
