# krunner key-person (rework) and krunner bus-factor

Status: **draft / design** — no code yet.

## Overview

Two estate-wide, CSV-first commands for downstream processes:

- **`krunner key-person`**: the per-repo key-person table from `kospex key-person`,
  run over a scope of repos. The output is one CSV row per (repo, author), always
  carrying `repo_id`.
- **`krunner bus-factor`**: one CSV row per repo. It gives the smallest number of
  authors whose commits make up at least Y% of the repo's commits (default 50%),
  calculated for all time and for the last X days (default 90).

Both commands use the scope convention of `kospex rebuild`: **exactly one** of
`REPO_ID`, `-all`, `-org ORG_KEY` or `-server SERVER`.

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
                   [-out FILE | -out -] [-overwrite]
```

- `-top N`: the top N all-time authors plus any of the top N active authors not
  already included. This is the current behaviour.
- `-all-authors`: every author in every repo. This is useful when the consumer
  does its own ranking.
- `-days`: the active window. It finally reaches `key_person()`.
- `-out -`: writes the CSV to stdout so it can be piped. Progress and summary go to
  stderr only.
- Default file: `generate_krunner_csv_filename("key-person", scope)`. It refuses
  to overwrite an existing file unless `-overwrite` is given.
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

The console output is a short summary: repos in scope, repos with no commits, rows
written, and the file path.

### Compatibility

Today `krunner key-person` with no argument means "all repos", and
`krunner key-person github.com` means a server. Under the new rule the first is an
error ("specify a scope"). The second gets a hint: if the positional value parses
as a server or org key, the error suggests `-server` or `-org`. The CSV columns
change, so any existing consumer of `key-people*.csv` breaks. The filename changes
to `key-person-*.csv` to make that explicit.

## `krunner bus-factor`

```
krunner bus-factor (REPO_ID | -all | -org ORG_KEY | -server SERVER)
                   [-threshold 50] [-days 90]
                   [-out FILE | -out -] [-overwrite]
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

- `-threshold` accepts `0 < Y ≤ 100`; anything else is a usage error.
- Merges are excluded, so a maintainer who only merges does not count toward it.
- **Two variants**, following the bots-are-signal rule: `bus_factor` counts every
  author; `human_bus_factor` excludes bots, both from the numerator and from the
  total. `bot_commit_pct` is reported alongside, so a heavily bot-maintained repo
  is visible rather than hidden.
- No commits in the window gives an **empty** cell, not 0. A 0 would read as "no
  one is needed", and empty plus `active_commits = 0` is unambiguous.

### CSV contract (one row per repo)

| column | notes |
|---|---|
| `repo_id`, `git_server`, `git_owner`, `git_repo` | |
| `threshold_pct`, `active_days` | the parameters used, so a file describes itself |
| `authors`, `commits`, `bus_factor`, `human_bus_factor` | all time |
| `bot_commits`, `bot_commit_pct` | all time |
| `top_author`, `top_author_pct` | all time |
| `active_authors`, `active_commits`, `active_bus_factor`, `active_human_bus_factor` | last `active_days` |
| `active_top_author`, `active_top_author_pct` | |
| `last_commit` | ISO-8601 UTC, so a consumer can tell "dormant" from "concentrated" |
| `generated_at` | |

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
- `src/krunner_utils.py`: `write_dict_to_csv(filename, rows, fieldnames)` that
  handles empty input and `-` (stdout), plus a no-overwrite guard.
- `src/kospex_rebuild.py` (or a new shared module): export `resolve_scope`.
- Tests:
  - `bus_factor()` cases: empty, a single author, ties, a threshold of 100, a
    threshold exactly hit vs crossed, and bots excluded.
  - The CSV header order for both commands, pinned as a list, because consumers
    depend on it.
  - Scope: exactly-one enforcement and the positional-server hint.
  - Parity: `kospex key-person` and `krunner key-person` agree on one repo.
  - An empty scope gives a header-only CSV.
- `docs/` krunner page plus CHANGELOG entry at release.

## Open questions

1. Keep a compatibility shim for the positional server/org argument, or make the
   break clean (current proposal: clean break plus a hint)?
2. Should `-out -` be the default when stdout is not a TTY, or only when asked
   (current proposal: only when asked)?
3. Should `bus-factor` also accept several thresholds in one run (e.g.
   `-threshold 50,80`), giving `bus_factor_50`/`bus_factor_80` columns?
