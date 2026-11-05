"""commit_history(fill_gaps=True) includes periods with no commits.

The /history page plots one bar per row. GROUP BY only returns periods that
have commits, so a quiet stretch disappeared from the chart and adjacent
active months sat side by side, hiding the gap. fill_gaps adds a
commit_count of 0 for every missing period between the first and last.
"""
import pytest
from sqlite_utils import Database

from kospex_query import KospexQuery

REPO = "github.com~acme~svc"


@pytest.fixture
def kq():
    db = Database(memory=True)
    db["commits"].insert_all(
        [
            {"hash": "a", "_repo_id": REPO, "committer_when": "2023-11-05T10:00:00+11:00"},
            {"hash": "b", "_repo_id": REPO, "committer_when": "2023-11-20T10:00:00+11:00"},
            {"hash": "c", "_repo_id": REPO, "committer_when": "2024-03-01T10:00:00Z"},
            {"hash": "d", "_repo_id": REPO, "committer_when": "2026-01-01T10:00:00Z"},
            {"hash": "z", "_repo_id": "github.com~acme~other", "committer_when": "2020-01-01T10:00:00Z"},
        ]
    )
    return KospexQuery(kospex_db=db)


def test_default_is_unchanged(kq):
    rows = kq.commit_history(REPO, group_by="month")
    assert [r["month"] for r in rows] == ["2023-11", "2024-03", "2026-01"]


def test_months_filled_across_year_boundary(kq):
    rows = kq.commit_history(REPO, group_by="month", fill_gaps=True)
    months = [r["month"] for r in rows]
    assert months[0] == "2023-11"
    assert months[-1] == "2026-01"
    assert len(months) == 27  # Nov 2023 .. Jan 2026 inclusive
    assert len(set(months)) == len(months)
    counts = {r["month"]: r["commit_count"] for r in rows}
    assert counts["2023-11"] == 2
    assert counts["2023-12"] == 0
    assert counts["2024-01"] == 0
    assert counts["2024-03"] == 1
    assert counts["2026-01"] == 1
    assert sum(counts.values()) == 4


def test_years_filled(kq):
    rows = kq.commit_history(REPO, group_by="year", fill_gaps=True)
    assert rows == [
        {"year": "2023", "commit_count": 2},
        {"year": "2024", "commit_count": 1},
        {"year": "2025", "commit_count": 0},
        {"year": "2026", "commit_count": 1},
    ]


def test_unknown_repo_is_empty(kq):
    assert kq.commit_history("github.com~acme~nope", group_by="month", fill_gaps=True) == []
