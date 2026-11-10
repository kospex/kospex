"""krunner repo-size summary (#420): sizes are kilobytes from `du -sk`."""

import pytest

import krunner


def test_summary_counts_totals_and_median():
    results = [{"total_size": kb} for kb in (1024, 3072, 1024 * 1024)]
    summary = krunner.repo_size_summary(results)
    assert summary["repos"] == 3
    assert summary["total_mb"] == pytest.approx(1 + 3 + 1024)
    assert summary["total_gb"] == pytest.approx((1 + 3 + 1024) / 1024)
    assert summary["median_mb"] == pytest.approx(3)


def test_summary_median_of_even_count_averages_middle_pair():
    results = [{"total_size": kb} for kb in (1024, 2048, 4096, 8192)]
    assert krunner.repo_size_summary(results)["median_mb"] == pytest.approx(3)


def test_summary_of_no_repos_is_zero_not_an_error():
    assert krunner.repo_size_summary([]) == {
        "repos": 0,
        "total_mb": 0,
        "total_gb": 0,
        "median_mb": 0,
    }
