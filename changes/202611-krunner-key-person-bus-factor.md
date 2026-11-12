# krunner key-person (rework) and krunner bus-factor

Status: **implemented**. See "Implementation notes" at the end for where the
build differs from this design.

## Overview

Two estate-wide commands. Each shows a table on screen and can also write a CSV
for downstream processes:

- **`krunner key-person`**: the per-repo key-person table from `kospex key-person`,
  run over a scope of repos. The output is one CSV row per (repo, author), always
  carrying `repo_id`.
- **`krunner bus-factor`**: one CSV row per repo. It gives the smallest number of
  authors whose commits make up at least Y% of the repo's commits (default 50%),
  calculated for all time and for the last X days (default 90).

Both commands use the scope convention of `kospex rebuild`: **exactly one** of
`REPO_ID`, `-all`, `-org ORG_KEY` or `-server SERVER`.

## Output model (both commands)

| invocation | screen | file |
|---|---|---|
| *(default)* | Rich table plus a summary | none |
| `-out FILENAME` | Rich table plus a summary | the CSV is written to FILENAME |
| `-stdout` | **CSV only** on stdout; progress and the summary go to stderr | none |

- `-out` and `-stdout` are mutually exclusive, and giving both is a usage error.
- `-out` refuses to overwrite an existing file unless `-overwrite` is given.
- There is no default filename. A file is written only when it is asked for.
- In `-stdout` mode nothing except CSV may reach stdout. Progress output (today's
  `console.log`) goes through a stderr `Console`, so `krunner bus-factor -all
  -stdout | other-tool` is clean.
- Tables follow the existing rules: `Console(markup=False)`, `Text` cells (author
  emails are git metadata, CWE-150), and printing via `KospexUtils.print_table`
  (80 columns when piped).
- The table and the CSV come from the same rows. The table may show a subset of
  the columns and format them (e.g. `12.3%`), but the CSV holds the raw values.

## What exists today

`krunner key-person` already exists (`src/krunner.py:571`), but it is not fit to be
a data feed:

| Problem | Where |
|---|---|
| The scope is a positional `request_id` that `KospexWeb.get_id_params` parses. With no argument it runs on **all repos** implicitly. | `krunner.py:62` |
| It copies the whole `commits` table into an in-memory DB, then runs 2 queries per repo. | `krunner.py:586` |
| The CSV holds *display* values: `"12.3%"` strings, dates reformatted by `extract_db_date`, and column names containing spaces and `%` (`% commits`, `% active`). | `kospex_query.py:2215` |
| `repo_id` is appended as the **last** column. | `krunner.py:603` |
| `-overwrite` is accepted but does nothing, the CSV is always written, and the filename logic is duplicated. | `krunner.py:609-616` |
| `write_dict_to_csv` crashes on an empty result (`data[0]`). | `krunner_utils.py:357` |
| `KospexQuery.key_person(days=...)` ignores `days` and hardcodes 90. | `kospex_query.py:2203` |
| A repo whose only commits are merges divides by zero (`total_commits == 0`). | `kospex_query.py:2213` |

## Shared foundation: one aggregate query per scope

Both commands need the same numbers per (repo, author). One grouped scan over the
scope replaces the memory-DB copy and the 2×N per-repo queries. Migration `0011`
adds a covering index on commits that is led by `_repo_id`.

```sql
SELECT _repo_id, author_email,
       SUM(CASE WHEN {AUTHORSHIP_COMMIT} THEN 1 ELSE 0 END)                         AS commits,
       SUM(CASE WHEN {AUTHORSHIP_COMMIT} THEN 0 ELSE 1 END)                         AS merges,
       SUM(CASE WHEN {AUTHORSHIP_COMMIT} AND committer_when > :since THEN 1 ELSE 0 END) AS active_commits,
       MIN(author_when) AS first_commit, MAX(author_when) AS last_commit
FROM commits
WHERE _repo_id IN (...)          -- omitted for -all
GROUP BY _repo_id, author_email
```

- New method: `KospexQuery.author_commit_aggregates(repo_ids=None, days=90)`. It
  returns rows grouped by repo. `key_person()` and both krunner commands are built
  on it, so `kospex key-person` and `krunner key-person` cannot drift apart.
- Merges are excluded through `AUTHORSHIP_COMMIT`, the same rule as #170.
- The active window uses `committer_when`, as `commit_stats()` does today.
  first/last use `author_when`, as `key_person` does today. The goal is parity
  with the existing command; any change to this is a separate decision.
- The SQL must run on SQLite 3.26: no `unixepoch()`. The `conftest.py` guard
  enforces this.

The ranking, percentages and bus factor are **pure Python functions over those
rows**. That makes them trivially unit-testable without a DB.

Scope resolution: move `resolve_scope()` out of `kospex_rebuild.py` into a shared
location (or import it from there). Then `kospex rebuild`, `krunner key-person`
and `krunner bus-factor` all use the same "exactly one scope" rule.

## `krunner key-person`

```
krunner key-person (REPO_ID | -all | -org ORG_KEY | -server SERVER)
                   [-top 4 | -all-authors] [-days 90]
                   [-out FILENAME [-overwrite] | -stdout]
```

- `-top N`: the top N all-time authors plus any of the top N active authors not
  already included. This is the current behaviour.
- `-all-authors`: every author in every repo. This is useful when the consumer
  does its own ranking.
- `-days`: the active window. It finally reaches `key_person()`.
- `-out` / `-stdout`: see "Output model" above.
- Screen table columns: `repo_id`, `author`, `rank`, `commits`, `merges`,
  `% commits`, `active_commits`, `% active`, `last_commit`. This is the
  `kospex key-person` table with `repo_id` added in front.
- An empty result writes a header-only CSV and does not crash. The fix goes in
  `write_dict_to_csv` (it takes explicit `fieldnames`).

### CSV contract (column order fixed, pinned by a test)

| column | type | notes |
|---|---|---|
| `repo_id` | str | **first column** |
| `git_server`, `git_owner`, `git_repo` | str | taken from the `repos` row, not by splitting `repo_id` (`~~` nested groups) |
| `rank` | int | all-time rank by commits, 1-based; ties broken by `author` |
| `author` | str | `author_email` as stored |
| `is_bot` | 0/1 | `EmailAnalyzer`. Bots are **kept and flagged**, never dropped |
| `commits` | int | authorship commits (merges excluded) |
| `merges` | int | |
| `pct_commits` | float | 0–100, 2 dp, numeric (no `%`) |
| `active_commits` | int | within `active_days` |
| `pct_active` | float | empty when the repo has no active commits |
| `in_top_all_time`, `in_top_active` | 0/1 | why the row was included |
| `first_commit`, `last_commit` | ISO-8601 UTC | not reformatted for display |
| `tenure_years` | float | 3 dp |
| `repo_commits`, `repo_active_commits` | int | repo totals, so consumers need not re-aggregate |
| `active_days` | int | the window used |
| `generated_at` | ISO-8601 UTC | same value on every row of a run |

After the table comes a short summary: repos in scope, repos with no commits,
rows, and the file path when `-out` is given.

### Compatibility: clean break (decided)

The product is early and its main user is its author, so there is no
compatibility shim:

- `krunner key-person` with no argument becomes an error ("specify a scope").
- `krunner key-person github.com` is an error too. It prints a hint suggesting
  `-server` or `-org` when the value parses as one.
- The CSV is no longer written by default (it was always written as
  `key-people*.csv`).
- The CSV columns change.

## `krunner bus-factor`

```
krunner bus-factor (REPO_ID | -all | -org ORG_KEY | -server SERVER)
                   [-threshold 50[,80[,90]]] [-days 90]
                   [-out FILENAME [-overwrite] | -stdout]
```

This is a separate command rather than a `key-person` mode because the output
grain differs: one row per repo, not one per author.

### Definition

For a set of per-author commit counts, sorted descending (ties broken by email):
**bus factor = the smallest n such that the top n authors' commits ≥ threshold% of
total commits.**

```python
def bus_factor(counts, threshold_pct):
    total = sum(counts)
    if total == 0:
        return None                      # undefined, not 0
    target = total * threshold_pct / 100
    running = 0
    for n, c in enumerate(sorted(counts, reverse=True), start=1):
        running += c
        if running >= target:
            return n
```

- `-threshold` takes **1 to 3** comma-separated integers, each in `1..100`
  (default `50`). They are de-duplicated and sorted ascending. More than 3 values,
  a non-integer or an out-of-range value is a usage error. The cap is there
  because each threshold adds two columns to the screen table, and 3 is the most
  that stays readable.
- The function computes all thresholds in a single pass over the sorted counts.
- Merges are excluded, so a maintainer who only merges does not count toward it.
- **Two variants**, following the bots-are-signal rule: `bus_factor` counts every
  author; `human_bus_factor` excludes bots, both from the numerator and from the
  total. `bot_commit_pct` is reported alongside, so a heavily bot-maintained repo
  is visible rather than hidden.
- No commits in the window gives an **empty** cell, not 0. A 0 would read as "no
  one is needed", and empty plus `active_commits = 0` is unambiguous.

### CSV contract (one row per repo)

`{T}` stands for each requested threshold, so `-threshold 50,80` gives
`bus_factor_50` and `bus_factor_80`. The thresholds are in the column names, so a
file describes itself, and the columns stay stable for a given `-threshold`.

| column | notes |
|---|---|
| `repo_id`, `git_server`, `git_owner`, `git_repo` | |
| `active_days` | the window used |
| `authors`, `commits` | all time |
| `bus_factor_{T}`, `human_bus_factor_{T}` | all time, per threshold |
| `bot_commits`, `bot_commit_pct` | all time |
| `top_author`, `top_author_pct` | all time |
| `active_authors`, `active_commits` | last `active_days` |
| `active_bus_factor_{T}`, `active_human_bus_factor_{T}` | per threshold |
| `active_top_author`, `active_top_author_pct` | |
| `last_commit` | ISO-8601 UTC, so a consumer can tell "dormant" from "concentrated" |
| `generated_at` | |

The `{T}` columns are grouped by metric and ordered by ascending threshold. The
header test pins the order for one, two and three thresholds.

### Screen table

```
repo_id | commits | bf 50% | bf 80% | active | active bf 50% | active bf 80% | bot % | last_commit
```

- That is one column per threshold for all time and one for the active window: 4
  to 10 columns in all, depending on how many thresholds are given.
- The table shows the **human** bus factor, with bot % beside it, so a repo that
  bots maintain is visible without its bus factor being inflated. The all-authors
  variant is only in the CSV.
- The summary line counts repos with no active commits and gives the median bus
  factor at each threshold. These are counts and medians only, with no bands.

A wide layout (rather than one row per repo and window) is used because there are
exactly two windows, and one row per repo joins cleanly against other per-repo CSVs
(e.g. `repo-sizes.csv`).

## Known limitations (documented, not solved here)

- **Identity**: one person who commits under several emails counts as several
  authors, which *inflates* both commands' counts. `commits_view`/`email_map` exist
  but nothing consumes them (#69). Once that lands, an `-resolve-identities` flag
  could fold the aggregates.
- **HEAD only**: commits on unsynced branches are invisible (the all-branches sync
  is deferred).
- **Squash-merge repos** have no merge commits, so `merges` is comparable only
  within a repo (as in `kospex key-person`).
- **Commit count is a proxy**: it is not lines or files. A later `-weight
  additions` option could use `commit_files`. It is out of scope here.

## Scope boundary

These commands emit **numbers only**: counts, percentages and the bus factor at a
stated threshold. There are no risk bands, no "critical / healthy" labels and no
scoring. Interpreting those numbers belongs outside the OSS data layer.

## Files to change

- `src/kospex_query.py`: `author_commit_aggregates()`; `key_person()` rebuilt on
  it, with `days` honoured and the zero-commit repo guarded.
- `src/krunner.py`: rework `key-person`, add `bus-factor`.
- `src/krunner_utils.py`: `write_dict_to_csv(dest, rows, fieldnames)`, where
  `dest` is a path or a stream (`sys.stdout`) and empty input still writes the
  header. Add a no-overwrite guard and a `-threshold` parser (1–3 integers in
  1..100).
- `src/kospex_rebuild.py` (or a new shared module): export `resolve_scope`.
- Tests:
  - `bus_factor()` cases: empty, a single author, ties, a threshold of 100, a
    threshold exactly hit vs crossed, and bots excluded.
  - The CSV header order for both commands, pinned as a list, because consumers
    depend on it.
  - Scope: exactly-one enforcement and the positional-server hint.
  - Threshold parsing: 4 values, `0`, `101`, `50.5`, `50,50` (de-duplicated) and
    `80,50` (sorted).
  - Output modes: with `-stdout`, stdout parses as CSV and nothing else; with
    `-out`, an existing file is refused unless `-overwrite` is given; `-out` with
    `-stdout` is a usage error.
  - Parity: `kospex key-person` and `krunner key-person` agree on one repo.
  - An empty scope gives a header-only CSV.
- `docs/` krunner page plus CHANGELOG entry at release.

## Implementation notes

- **Files.**
  - `src/kospex/authorship.py`: pure functions `parse_thresholds`,
    `bus_factors`, `key_person_rows`, `bus_factor_row` and `bus_factor_fields`,
    plus `KEY_PERSON_FIELDS`. It sits in the package rather than as a flat
    module so `pyproject.toml`'s `py-modules` list need not change.
  - `KospexQuery.author_commit_aggregates()`.
  - `src/krunner.py`: both commands and the shared option and scope helpers.
  - `krunner_utils.write_dict_to_csv(dest, data, fieldnames=None)`. `dest` may be
    a stream, and empty data writes the header when `fieldnames` is given.
    Existing callers are unchanged.
- **`KospexQuery.key_person()` was not rebuilt on the aggregate**, as proposed
  above. It only gained the `days` and zero-commit fixes. A parity test
  (`test_key_person_rows_agree_with_kospex_key_person`) drives both through a
  seeded DB and compares counts, `% commits`, tenure and first commit. That
  keeps the two from drifting without touching the kweb and `kospex
  key-person` paths.
- **Active authors need at least one authorship commit in the window.** The
  old code could list a merge-only author with 0 active commits when fewer than
  N authors were active. The parity test avoids that case.
- **Scope**: `kospex_rebuild.resolve_scope` is imported, not moved. An empty
  scope is a usage error (exit 2). A scope whose repos have no commits is not an
  error: the key-person CSV is then header-only.
- **Measured on the dev DB** (403 repos, about 1.1M commits): `bus-factor -all
  -threshold 50,80,90 -stdout` took 14.3s for the grouped query. Of the 403
  repos, 260 had no commits in the last 90 days, and 3 more had only bot
  commits in that window, so the active medians cover 140 repos.
- The key-person table for `kospex~kospex` shows the identity limitation
  directly: the same person appears under a noreply address and a personal one.

## Decisions (2026-11-12)

1. **Clean break** for `krunner key-person`, with no compatibility shim.
2. **Several thresholds** for `bus-factor`: up to 3, set with `-threshold 50,80,90`.
3. **Both commands show a table by default.** `-out FILENAME` also writes the CSV,
   and `-stdout` sends only CSV to stdout. Neither form is triggered automatically
   by piping.
