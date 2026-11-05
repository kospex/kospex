# Commit history: show periods with no commits

## Overview

The commit history page (`/history/REPO_ID`, yearly and `?monthly=true`) plots
one bar per row returned by `KospexQuery.commit_history()`. That query is a
`GROUP BY` over `commits`, so it only returns periods that had commits. A
stretch with no activity therefore vanished from the chart, and the bars on
either side of it sat next to each other.

For example, `github.com~kospex~panopticas` returned 17 months spanning
2024-07 to 2026-11. Filled, that span is 29 months, 12 of them empty.

## Files changed

- `src/kospex_query.py` - `commit_history()` gains `fill_gaps: bool = False`.
  When true, the rows go through a new `_fill_period_gaps()` staticmethod,
  which adds a `commit_count` of `0` for every missing month or year between the
  first and last period.
- `src/kweb2.py` - the `/history` route passes `fill_gaps=True` for both the
  yearly and monthly data.
- `tests/test_commit_history_gaps.py` - covers the unchanged default, month
  filling across a year boundary, year filling, and an unknown repo.

## Usage

```python
KospexQuery().commit_history("github.com~kospex~panopticas", group_by="month", fill_gaps=True)
# [{'month': '2024-07', 'commit_count': 14}, ..., {'month': '2024-11', 'commit_count': 0}, ...]
```

## Implementation notes

- **The fill is done in Python, not SQL.** A recursive CTE would also work on
  SQLite 3.26, but it is harder to read, and the row count is small: one row
  per month of a repo's life.
- **The span runs from the first commit to the last**, not to today. A repo
  whose last commit was a year ago doesn't get trailing zero months. That
  dormancy is visible elsewhere, for example in `last_sync` and the repo's
  status.
- **Rows with a NULL period are dropped** when filling. They come from a
  `committer_when` that `strftime` can't parse, and they have no place on a
  timeline. The unfilled default still returns them, as before.
- **The default is `fill_gaps=False`**, so other callers keep the old shape.
  At the time of this change the `/history` route is the only caller.
- **Side effect:** the page's "average per period" stat is
  `total / number of rows`, so it now counts empty periods as well and is
  lower for a repo with gaps. This is intended: it now means the average per
  month (or year) of the repo's active span.
