"""Tests for the kospex key-person table."""
import subprocess

from click.testing import CliRunner

KNOWN = {"known": True, "has_commits": True, "message": None}

AUTHOR = ("a.very-long-developer-name.with-a-department@"
          "engineering.platform-team.example.com")

AUTHORS = [{"author": AUTHOR, "commits": 1043, "merges": 12, "% commits": "97.5%",
            "last_commit": "2026-10-20", "first_commit": "2019-03-01",
            "active_commits": 37, "% active": "100.0%", "tenure": "7.887"},
           {"author": "dev@example.com", "commits": 27, "merges": None,
            "% commits": "2.5%", "last_commit": "2024-01-02",
            "first_commit": "2023-05-06", "active_commits": 0,
            "% active": "0.0%", "tenure": "0.678"}]


def _stub(monkeypatch, status=KNOWN):
    """Stub the DB: record which repo_id each query was scoped to."""
    import kospex_cli

    seen = {}

    def repo_db_status(repo_id, repo_directory=None):
        seen["status"] = repo_id
        return status

    def key_person(self, repo_id=None, top=None):
        seen["key_person"] = repo_id
        return AUTHORS

    monkeypatch.setattr(kospex_cli.kospex, "repo_db_status", repo_db_status)
    monkeypatch.setattr(kospex_cli.KospexQuery, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(kospex_cli.KospexQuery, "key_person", key_person)
    return kospex_cli, seen


def test_key_person_prints_complete_author_when_piped(tmp_path, monkeypatch):
    kospex_cli, _ = _stub(monkeypatch)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert len(AUTHOR) > 80 - 9  # the other 8 columns can't also fit in 80

    # CliRunner output is never a terminal, so this is the piped case.
    result = CliRunner().invoke(kospex_cli.cli, ["key-person", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Key Person" in result.output
    # once only: the table, not also a raw dump of the rows
    assert result.output.count(AUTHOR) == 1
    for value in ["1043", "97.5%", "2026-10-20", "2019-03-01", "7.887", "dev@example.com"]:
        assert value in result.output
    assert "None" not in result.output


def test_repo_id_needs_no_directory(tmp_path, monkeypatch):
    kospex_cli, seen = _stub(monkeypatch)
    repo_id = "gitlab.com~group~~subgroup~repo"
    # Not a git repo: -repo_id must not look at the current directory at all.
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(kospex_cli.cli, ["key-person", "-repo_id", repo_id])

    assert result.exit_code == 0, result.output
    assert seen == {"status": repo_id, "key_person": repo_id}
    assert f"Key Person: {repo_id}" in result.output
    assert AUTHOR in result.output


def test_unknown_repo_id_says_so_instead_of_an_empty_table(monkeypatch):
    message = "github.com~acme~nope is not in the kospex database."
    kospex_cli, seen = _stub(
        monkeypatch, {"known": False, "has_commits": False, "message": message})

    result = CliRunner().invoke(
        kospex_cli.cli, ["key-person", "-repo_id", "github.com~acme~nope"])

    assert result.exit_code == 1
    assert message in result.output
    assert "key_person" not in seen
    assert "Key Person" not in result.output


def test_directory_and_repo_id_together_is_an_error(tmp_path, monkeypatch):
    kospex_cli, seen = _stub(monkeypatch)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    result = CliRunner().invoke(
        kospex_cli.cli, ["key-person", str(tmp_path), "-repo_id", "github.com~acme~app"])

    assert result.exit_code == 1
    assert "not both" in result.output
    assert seen == {}
