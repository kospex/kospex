# krunner repo-size: summary stats, largest repos and .git ratio

Issues #420 and #424, PRs #421 and #425 (wording adjusted in a later commit, test
fixed in #427). Shipped in 0.3.1.

## Overview

`krunner repo-size` printed one row per repo and nothing else, so the questions
people actually ask of it (how big is the estate, what does a typical repo look
like, which repos make it big) meant exporting the CSV and working it out by hand.
It now summarises the run under the table.

## The change

**Table and `-csv`:** a new `.git ratio` column, `.git` size divided by the
checkout size (`32.0x` means the history is 32 times the current code). An empty
checkout shows `n/a` (an empty cell in the CSV) rather than dividing by zero.

**Under the table:**

```
# Repos: 403
Total repo size: 37.38 GB (38,276.2 MB)
Mean repo size: 95.0 MB
P25 / Median / P75: 0.41 / 2.30 / 12.82 MB
P90: 96.6 MB
Max: 5,652.5 MB (github.com~maplibre~maplibre-native)
Top-10 by size share: 72.7% of total
.git share: 82.3% of total (30.77 GB)

Top 10 largest repos:
     5,652.5 MB  .git   17.0x  github.com~maplibre~maplibre-native
     ...

History-heavy repos (above P75 and .git >= 10x checkout): 13
     3,663.9 MB  .git   28.4x  github.com~recharts~recharts
     ...
```

**Options:** `-top N` (default 10, minimum 1) sets how many of the largest repos
are listed and used for the share. `-heavy-ratio X` (default 10, above 0, decimals
allowed) sets the history-heavy threshold. An invalid value is a Click usage error
before any repo is measured.

Figures above are from a 403-repo development DB.

## Decisions

- **Mean and median are both shown, because they disagree.** On the 403 repos the
  mean is about 40 times the median, and P90 is roughly the mean: 90% of repos are
  smaller than the average. The mean alone would mislead; next to the median it
  shows how skewed the sizes are.
- **The top-N share is the line people act on.** Ten repos (2.5%) held 73% of the
  space. Listing them by name, not only their share, is what makes it actionable.
- **A ratio per repo, not a percentage.** `.git` as a percentage of the total is
  bounded at 100% and bunches up there: maplibre-native (94%) and recharts (97%)
  look close, but their ratios are 17.0x and 28.4x. The ratio separates the heavy
  repos; the estate-wide `.git` share stays a percentage because it's a single
  figure.
- **No `.git` share broken down by quartile.** It was measured and dropped. The
  size-weighted share was higher in the top quartile (83% against 64% for the
  middle half), but the median repo's `.git` percentage barely moved (72% against
  68%). A few very large repos caused the gap, and the per-repo ratio shows them
  directly.
- **History-heavy needs both conditions.** Above P75 in size *and* a ratio of at
  least the threshold. The ratio alone flags tiny repos, where the fixed `.git`
  baseline (hooks, config, index) is most of the size. The size condition alone
  flags large repos with ordinary history. An empty checkout counts as
  history-heavy if it's above P75. P75 is fixed rather than an option, to avoid a
  second setting that interacts with the first.
- **Percentiles use `statistics.quantiles(..., method="inclusive")`.** One repo
  uses its size for every percentile, because `quantiles` needs two data points
  on Python 3.12. No repos prints only `# Repos: 0`.
- **No untracked-files caveat.** Sizes come from `du -sk` on the clone, which would
  count build output or `node_modules`. kospex clones are clean, so the checkout
  size is what's committed at HEAD.
- **Output safety.** Summary lines print with `markup=False`, because repo ids come
  from remote URLs (CWE-150).

## Files

- `src/krunner.py`: `git_ratio()`, `format_git_ratio()`, `repo_size_summary()`,
  `print_repo_size_summary()`, the `REPO_SIZE_TOP_N` and `HISTORY_HEAVY_RATIO`
  defaults, and the `-top` / `-heavy-ratio` options.
- `src/kospex_git.py`: `get_repo_size()` docstring corrected; its values are
  kilobytes, not bytes.
- `tests/test_krunner_repo_size_summary.py`: 25 tests, including the command run
  on three real git repos with controlled sizes so each option is checked end to
  end.

## What it surfaced

On the development DB the top-10 list showed two repos synced twice under old and
new names after a transfer: `facebook/react` / `react/react` and `square/okhttp` /
`lysine-dev/okhttp`, about 2.2 GB between them. Both are covered by #348 (repo
lineage). Merging the two `repo_id`s of a moved repo stays a manual step.
