"""krunner repo-size summary (#420, #424): sizes are kilobytes from `du -sk`."""

import io
import os
import subprocess

import pytest
from click.testing import CliRunner
from rich.console import Console

import krunner
from kospex_git import KospexGit

MB = 1024  # sizes are in KB


def repo(repo_id, total_kb, git_kb=None):
    """A repo-size result; .git defaults to half the total."""
    git_kb = total_kb // 2 if git_kb is None else git_kb
    return {
        "repo_id": repo_id,
        "total_size": total_kb,
        "git_size": git_kb,
        "workspace_size": total_kb - git_kb,
    }


# --- git_ratio ---------------------------------------------------------------


def test_git_ratio_is_git_over_checkout():
    assert krunner.git_ratio(3200, 100) == 32.0
    assert krunner.git_ratio(50, 100) == 0.5


def test_git_ratio_of_an_empty_checkout_is_none_not_a_division_error():
    assert krunner.git_ratio(100, 0) is None


def test_format_git_ratio():
    assert krunner.format_git_ratio(32.0) == "32.0x"
    assert krunner.format_git_ratio(None) == "n/a"


# --- repo_size_summary -------------------------------------------------------


def test_summary_counts_totals_mean_and_median():
    results = [repo("a", 1 * MB), repo("b", 3 * MB), repo("c", 1024 * MB)]
    s = krunner.repo_size_summary(results)
    assert s["repos"] == 3
    assert s["total_mb"] == pytest.approx(1028)
    assert s["total_gb"] == pytest.approx(1028 / 1024)
    assert s["mean_mb"] == pytest.approx(1028 / 3)
    assert s["median_mb"] == pytest.approx(3)


def test_summary_median_of_even_count_averages_middle_pair():
    results = [repo(str(i), kb * MB) for i, kb in enumerate((1, 2, 4, 8))]
    assert krunner.repo_size_summary(results)["median_mb"] == pytest.approx(3)


def test_percentiles_on_evenly_spaced_sizes():
    # 0, 1, ... 100 MB: with the inclusive method Pk is exactly k MB
    results = [repo(str(i), i * MB) for i in range(101)]
    s = krunner.repo_size_summary(results)
    assert s["p25_mb"] == pytest.approx(25)
    assert s["median_mb"] == pytest.approx(50)
    assert s["p75_mb"] == pytest.approx(75)
    assert s["p90_mb"] == pytest.approx(90)


def test_max_names_the_largest_repo():
    results = [repo("small", 1 * MB), repo("big", 500 * MB), repo("mid", 20 * MB)]
    s = krunner.repo_size_summary(results)
    assert s["max_mb"] == pytest.approx(500)
    assert s["max_repo"] == "big"


def test_top_is_the_ten_largest_and_its_share_of_the_total():
    # 12 repos of 1..12 MB: top 10 are 3..12 MB = 75 of 78 MB
    results = [repo(f"r{i}", i * MB) for i in range(1, 13)]
    s = krunner.repo_size_summary(results)
    assert [r["repo_id"] for r in s["top"]] == [f"r{i}" for i in range(12, 2, -1)]
    assert s["top_share"] == pytest.approx(75 / 78)


def test_git_share_is_git_over_total():
    results = [repo("a", 100 * MB, git_kb=90 * MB), repo("b", 100 * MB, git_kb=10 * MB)]
    s = krunner.repo_size_summary(results)
    assert s["git_share"] == pytest.approx(0.5)
    assert s["git_gb"] == pytest.approx(100 / 1024)


def test_history_heavy_needs_both_above_p75_and_ratio_at_least_10():
    total = 1100  # .git 1000 + checkout 100 = ratio exactly 10x
    results = [repo(f"filler{i}", 10 * MB) for i in range(6)] + [
        repo("big_heavy", total * MB, git_kb=1000 * MB),  # above P75, 10x: flagged
        repo("big_light", total * MB, git_kb=500 * MB),  # above P75, ~0.8x: not flagged
        repo("big_empty_checkout", total * MB, git_kb=total * MB),  # ratio n/a: flagged
        repo("small_heavy", 1 * MB, git_kb=1000),  # 1000x but below P75: not flagged
    ]
    s = krunner.repo_size_summary(results)
    assert s["p75_mb"] < total  # guard: the big repos really are above P75
    assert {r["repo_id"] for r in s["history_heavy"]} == {"big_heavy", "big_empty_checkout"}


def test_history_heavy_excludes_ratio_just_below_10():
    results = [repo(f"filler{i}", 10 * MB) for i in range(6)] + [
        repo("big_9x", 1000 * MB, git_kb=900 * MB),  # 900 / 100 = 9x
    ]
    s = krunner.repo_size_summary(results)
    assert s["p75_mb"] < 1000
    assert s["history_heavy"] == []


def test_top_n_parameter_limits_the_list_and_the_share():
    results = [repo(f"r{i}", i * MB) for i in range(1, 13)]
    s = krunner.repo_size_summary(results, top_n=3)
    assert [r["repo_id"] for r in s["top"]] == ["r12", "r11", "r10"]
    assert s["top_share"] == pytest.approx(33 / 78)


def test_heavy_ratio_parameter_moves_the_threshold():
    results = [repo(f"filler{i}", 10 * MB) for i in range(6)] + [
        repo("big_9x", 1000 * MB, git_kb=900 * MB),
    ]
    assert krunner.repo_size_summary(results)["history_heavy"] == []
    flagged = krunner.repo_size_summary(results, heavy_ratio=5)["history_heavy"]
    assert [r["repo_id"] for r in flagged] == ["big_9x"]


def test_summary_of_one_repo_uses_it_for_every_percentile():
    s = krunner.repo_size_summary([repo("only", 4 * MB)])
    assert s["p25_mb"] == s["median_mb"] == s["p75_mb"] == s["p90_mb"] == pytest.approx(4)
    assert s["top_share"] == pytest.approx(1)


def test_summary_of_no_repos_is_zero_not_an_error():
    s = krunner.repo_size_summary([])
    assert s["repos"] == 0
    assert s["total_mb"] == s["mean_mb"] == s["p90_mb"] == s["max_mb"] == 0
    assert s["top_share"] == s["git_share"] == 0
    assert s["max_repo"] is None
    assert s["top"] == [] and s["history_heavy"] == []


# --- print_repo_size_summary -------------------------------------------------


@pytest.fixture
def printed(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(krunner, "console", Console(file=buf, width=200, color_system=None))
    return buf


def test_printed_summary_lines(printed):
    results = [repo(f"filler{i}", 10 * MB) for i in range(6)] + [
        repo("github.com~acme~huge", 1100 * MB, git_kb=1000 * MB)
    ]
    krunner.print_repo_size_summary(krunner.repo_size_summary(results))
    text = printed.getvalue()
    assert "# Repos: 7" in text
    assert "Mean repo size:" in text
    assert "P25 / Median / P75:" in text
    assert "P90:" in text
    assert "Max: 1,100.0 MB (github.com~acme~huge)" in text
    assert "Top-7 share: 100.0% of total" in text
    assert ".git share:" in text
    assert "Top 7 largest repos:" in text
    assert "History-heavy repos (above P75 and .git >= 10x checkout): 1" in text
    assert "10.0x  github.com~acme~huge" in text


def test_printed_summary_of_no_repos_is_just_the_count(printed):
    krunner.print_repo_size_summary(krunner.repo_size_summary([]))
    assert printed.getvalue().strip() == "# Repos: 0"


def test_repo_id_brackets_print_literally_not_as_markup(printed):
    # CWE-150: repo ids come from remote URLs; markup in them must not render
    krunner.print_repo_size_summary(krunner.repo_size_summary([repo("[bold]x[/bold]", MB)]))
    assert "[bold]x[/bold]" in printed.getvalue()


def test_heading_shows_a_fractional_threshold(printed):
    krunner.print_repo_size_summary(krunner.repo_size_summary([repo("a", MB)], heavy_ratio=7.5))
    assert ".git >= 7.5x checkout" in printed.getvalue()


# --- CLI options ---------------------------------------------------------------

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
}


def _git_repo(path):
    path.mkdir(parents=True)
    env = {**os.environ, **_GIT_ENV}
    run = lambda *a: subprocess.run(
        ["git", "-C", str(path), *a], check=True, capture_output=True, env=env)
    run("init", "-q")
    run("remote", "add", "origin", f"https://github.com/acme/{path.name}.git")
    (path / "README").write_text("x\n")
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    return path


@pytest.fixture
def three_repos(tmp_path, monkeypatch):
    """Three real repos with controlled sizes: 10 MB, 20 MB, and 1000 MB at 9x."""
    sizes = {"small": (10 * MB, 5 * MB), "mid": (20 * MB, 10 * MB), "big": (1000 * MB, 900 * MB)}
    repos = [
        {"_repo_id": f"github.com~acme~{name}", "file_path": str(_git_repo(tmp_path / name))}
        for name in sizes
    ]
    monkeypatch.setattr(krunner, "get_repos", lambda request_id: repos)

    def fake_size(directory=None):
        total, git = sizes[os.path.basename(directory)]
        return {"total": total, "git": git, "workspace": total - git}

    monkeypatch.setattr(KospexGit, "get_repo_size", staticmethod(fake_size))


def test_cli_defaults(three_repos):
    result = CliRunner().invoke(krunner.cli, ["repo-size"])
    assert result.exit_code == 0, result.output
    assert "Top 3 largest repos:" in result.output
    assert "History-heavy repos (above P75 and .git >= 10x checkout): 0" in result.output


def test_cli_top_option(three_repos):
    result = CliRunner().invoke(krunner.cli, ["repo-size", "-top", "2"])
    assert result.exit_code == 0, result.output
    top = result.output.split("Top 2 largest repos:")[1].split("History-heavy")[0]
    assert "github.com~acme~big" in top and "github.com~acme~mid" in top
    assert "github.com~acme~small" not in top


def test_cli_heavy_ratio_option(three_repos):
    result = CliRunner().invoke(krunner.cli, ["repo-size", "-heavy-ratio", "5"])
    assert result.exit_code == 0, result.output
    heavy = result.output.split("History-heavy repos")[1]
    assert ".git >= 5x checkout): 1" in heavy
    assert "9.0x  github.com~acme~big" in heavy


@pytest.mark.parametrize("args", [["-top", "0"], ["-heavy-ratio", "0"], ["-heavy-ratio", "-1"]])
def test_cli_rejects_non_positive_values(args):
    result = CliRunner().invoke(krunner.cli, ["repo-size", *args])
    assert result.exit_code == 2  # click usage error, before any repo is measured
    assert "Invalid value" in result.output
