# /repos/ reads developer_stats

## Overview

`/repos/` aggregated the whole commits table three times on a cold load. It now reads
`developer_stats`, which sync already maintains.

| Data | Before | After |
|---|---|---|
| Developer status counts | `developers()` | `developer_statuses()` |
| Commits, authors, last commit | `repos()` | `repo_summaries()` |
| Committers, and which repos are listed | `repos()` | `repo_summaries()`, from commits |
| Active developers | `active_devs()` | `active_devs_from_stats()` |

## Files changed

- `src/kospex_query.py`: `repo_summaries()`, `active_devs_from_stats()`
- `src/kweb2.py`: `_repos_page_figures()`
- `tests/test_repos_page_stats.py`

## Notes

- Falls back to the commits queries while `developer_stats_coverage()` reports a gap.
- Coverage does not detect rows that lag behind commits (a sync that failed before the
  stats step, or `kreaper delete-repo -table commits`). A re-sync or
  `kospex rebuild developer-stats` fixes it.
- `commits_last_90_days` is not used: it is as of when the row was built. Active
  developers are counted from `last_commit` against a cutoff taken at request time.
- `active_devs()` still compares timestamps as text, and is still used by the fallback,
  `/orgs/`, one other kweb page, the JSON API and `kospex_core`.
