# Summary command: rich output + status legend

## Overview

Convert the `kospex summary` command output to use [`rich`](https://github.com/Textualize/rich)
instead of `PrettyTable`, and add a short legend describing the four repo
development statuses (Active, Aging, Stale, Unmaintained).

`rich` is already a dependency and is used by `kospex stats` and other CLI
commands (`Console`/`Table` imported in `kospex_cli.py`).

## Status definitions

Statuses come from `KospexUtils.development_status()` (days since last commit):

| Status        | Meaning                          |
|---------------|----------------------------------|
| Active        | commit within last 90 days       |
| Aging         | last commit 91–180 days ago      |
| Stale         | last commit 181–365 days ago     |
| Unmaintained  | no commit in over 365 days       |

The thresholds (`active_limit=90`, `aging_limit=180`, `stale_limit=365`) are
the existing defaults; the legend reflects these.

## Files changed

- **`src/kospex_core.py`**
  - `summary()` (~line 417): replace the per-repo `PrettyTable` with a
    `rich.table.Table`. Add a module-level `Console`. Numeric columns
    right-justified, `repo`/`status` left.
  - `PrettyTable` auto-sorts by `last_commit`; `rich` does not, so the
    `results` list is sorted by `last_commit` (ascending) before rows are added,
    preserving current ordering. The sort key is `None`-safe (rows with a missing
    `last_commit` sort to the tail).
  - Cell values are coerced with `"" if v is None else str(v)` so empty cells
    render blank rather than the literal `"None"`.
  - The other `PrettyTable` usages in this file are left untouched.

- **`src/kospex_utils.py`**
  - `get_status_table()` (line 1291): return a `rich.Table` instead of a
    `PrettyTable`. Same two-row layout (counts row + percentage row) and
    columns (`Active`, `Aging`, `Stale`, `Unmaintained`, `Total`).
  - New `get_status_legend()`: returns a small `rich` renderable describing the
    four statuses (per the table above).

- **`src/kospex_cli.py`**
  - `summary()`: print the status legend (via `get_status_legend()`) once below
    the status-count summary. Legend is summary-only.
  - All callers of `get_status_table()` (summary, docker, dependencies, and the
    caller near line 636) updated from `print(...)` to `console.print(...)`,
    since the helper now returns a `rich.Table`.

## Scope notes

- Converting the shared `get_status_table()` changes the *table styling* of the
  docker and dependencies sub-summaries too. This is intentional and keeps the
  CLI consistent. Only the **legend** is summary-specific.
- No data/logic changes — rendering only. Status thresholds, CSV output, and
  returned `results` are unchanged.
- No colour-coding in this change (plain legend); can be added later.
- Rendering width: `rich` fits the table to the detected terminal width. In an
  interactive terminal the full repo table shows; when output is piped to a
  narrow (80-col) non-tty, `rich` ellipsizes long `repo_id`s and dates (the old
  `PrettyTable` always emitted full width). Use the `-out` CSV option for
  machine-consumable output.

## Testing

- Existing tests in `tests/`. Check for coverage of `get_status_table()` /
  `development_status()`; adjust assertions if they depend on `PrettyTable`
  string output, and add a test for `get_status_legend()` if warranted.
