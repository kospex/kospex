"""Tests for the kgit status table."""
import re
import subprocess

from click.testing import CliRunner


def test_status_prints_complete_values_when_piped(tmp_path, monkeypatch):
    import kgit

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    remote = ("https://gitlab.com/example-organisation/platform-engineering-subgroup/"
              "a-fairly-long-repository-name.git")
    stats = {"total_commits": 1043, "remote_url": remote, "status": "Active",
             "total_size": 322052, "git_dir_size": 11120, "repo_size_without_git": 310932}
    monkeypatch.setattr(kgit.KospexUtils, "get_git_stats", lambda repo, days: stats)

    # CliRunner output is never a terminal, so this is the piped case.
    result = CliRunner().invoke(kgit.cli, ["status", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Repo Status" in result.output
    assert remote in result.output
    assert "1043" in result.output

    # du reports KB; kgit status shows MB to 3 decimal places
    assert "314.504" in result.output      # 322052 / 1024
    assert "10.859" in result.output       # 11120 / 1024
    assert "303.645" in result.output      # 310932 / 1024
    assert "directory sizes are in MB." in result.output
    assert re.search(r"Execution time: \d+\.\d{4} seconds", result.output)
