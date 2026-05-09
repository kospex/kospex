# 202605 — PrettyTable → Rich table migration (CLI)

## Status

**Skeleton / planning** — not yet implemented. Inventory complete, design
work pending.

## Overview

Migrate every CLI table currently rendered with `prettytable.PrettyTable`
to `rich.table.Table`, matching the pattern established by:

- `kospex stats` (`src/kospex_cli.py:827-876`) — first Rich table.
- `kospex orgs` (`src/kospex_cli.py:879-906`) — second, with prior
  PrettyTable removed. See `changes/202605-orgs-rich-table.md`.

Goal: a single rendering library across the CLI, so output style is
consistent, helpers can share configuration, and `prettytable` can
eventually be dropped from `pyproject.toml`.

## Reference pattern

Use `kospex orgs` as the canonical reference:

- Module-level `console = Console()` (already imported in `kospex_cli.py`
  at line 30; other modules will need the import added).
- `Table(title="...")` with a clear title.
- `add_column(name, justify="right" | default, style="cyan" for key cols)`.
- `str(value or "")` row construction with `dict.get(...)` defaults.
- Wrap output with `console.print()` blank lines.
- Inline the table in the command — drop helper factories that are only
  called once.

## Truncation behavior

Rich diverges from PrettyTable here: when the rendered table is wider
than the terminal, Rich proportionally shrinks columns and truncates
cell values with `…`. PrettyTable instead lets the table extend past
the terminal width and relies on the terminal to wrap or scroll.

Observed in the `kospex orgs` migration on a standard ~100-column
terminal:

```
┃ org_key ┃ org     ┃ commits ┃ ... ┃ last_c… ┃
┡━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━╇━━━━━━━━━┩
│ github… │ apache  │   18625 │ ... │ 2026-0… │
```

`github.com~apache` shows as `github…`; full ISO timestamps become
`2026-0…`. Acceptable for browsing, problematic when a user needs to
copy/grep an identifier or pipe the output to another tool.

### Tables most affected

Any table whose columns hold:

- `repo_id` (`server~owner~repo`, typically 30-60 chars) or `org_key`
  (`server~owner`, typically 15-30 chars).
- File paths.
- Author / committer emails.
- Raw ISO datetime strings (`last_commit`, `first_commit`,
  `committer_when`, `author_when`).

### Mitigation options

In rough order of preference:

1. **Render shorter values to begin with.** For dates, use
   `KospexUtils.extract_db_date()` — `kospex stats` already does this
   and its `First Commit` / `Last Commit` columns stay narrow as a
   result. Cleanest fix; no Rich-specific knobs to tune.

2. **`no_wrap=True` on the column.** Tells Rich not to wrap that
   column's text. Combined with default ellipsis overflow, the column
   either fits or truncates — but it won't shrink mid-word. Useful for
   identifier columns where partial values are useless.

   ```python
   table.add_column("repo_id", no_wrap=True)
   ```

3. **`overflow="fold"` on the column.** Wraps long values onto extra
   lines within the cell instead of truncating. Use for free-text
   columns (messages, descriptions) where multi-line cells are
   acceptable.

   ```python
   table.add_column("message", overflow="fold")
   ```

4. **Force a wider `Console`.** Bypasses terminal autodetection. Useful
   when a specific report is intended for redirection to a file, or
   when running in a context where horizontal scrolling is expected.

   ```python
   wide = Console(width=200)
   wide.print(table)
   ```

5. **Accept truncation.** For browse-only summary tables where the
   truncated text is incidental (e.g. the count columns dominate and
   the org name is recognizable from a few characters), leave defaults.
   The `orgs` migration shipped this way.

### Per-migration guidance

When converting a table:

- Identify which columns hold identifiers (must be exact) vs counts
  (numeric, narrow by nature) vs free text (can wrap).
- For identifier columns the user will likely want to copy, grep, or
  pipe (`repo_id`, `org_key`, file paths, emails), prefer option 1
  (shorten upstream) or option 2 (`no_wrap=True`).
- Eyeball the resulting table at 80, 120, and 160 columns before
  committing — 80 is the constraining case.
- Note the truncation choice in the commit message so it's an
  intentional decision, not an oversight. If you accept truncation,
  say so explicitly.

### Follow-up for `orgs`

`kospex orgs` shipped with default Rich truncation; both `org_key` and
`last_commit` are commonly truncated on narrow terminals. Worth
revisiting:

- `org_key` — apply `no_wrap=True`, since `github~…` alone is useless.
- `last_commit` — pipe through `KospexUtils.extract_db_date()` like
  `stats` does, dropping it to `YYYY-MM-DD` so the column is narrow by
  design.

Tracked here rather than as a separate change.

## Inventory

All `PrettyTable` sites in `src/`. ✅ = already migrated.

### CLI command sites (direct in command body)

- [ ] `src/kgit.py:67` — `kgit status` command. Inline `PrettyTable()`.
- [ ] `src/kgit.py:458` — `kgit bitbucket` command. Inline `PrettyTable()`.
- [ ] `src/kospex_cli.py:813` — `kospex key-person` command. Uses
  `KospexUtils.key_person_prettytable()`.
- [ ] `src/kospex_cli.py:1154` — `kospex orphans` command. Uses
  `KospexUtils.orphan_prettytable()`. Note: `orphans` also has `-csv`
  output (see issue #67) — only the on-screen table needs migrating.
- [x] `src/kospex_cli.py:879` — `kospex orgs` command (done; reference
  example).

### `kospex_core.py` methods (called from various CLI commands)

- [ ] `src/kospex_core.py:363` — `Kospex.author_tech_pretty_table()`
  → backs `kospex author-tech`.
- [ ] `src/kospex_core.py:416` — `Kospex.summary()` → backs
  `kospex summary`.
- [ ] `src/kospex_core.py:604` — `Kospex.active_developers()` → backs
  `kospex developers`.
- [ ] `src/kospex_core.py:676` — `Kospex.list_repos()` → backs
  `kospex list-repos`.
- [ ] `src/kospex_core.py:857` — `Kospex.cli_file_metadata()` (uses
  `file_metadata_prettytable` helper) → backs `kospex metadata`.
- [ ] `src/kospex_core.py:1235` — `Kospex.get_kospex_table_summary()`
  → backs `kospex system-status`.
- (Note: `src/kospex_core.py:818` is commented-out PrettyTable code —
  delete during migration.)

### Library helpers / factories (`kospex_utils.py`)

These are called from one or more CLI sites above. Decide per-helper
whether to (a) inline at the call site (preferred when single-use, like
`orgs_prettytable` was) or (b) keep as a Rich `Table` factory if reused.

- [ ] `src/kospex_utils.py:854` `get_repo_stats_table()` — used in sync /
  health-check flows (e.g. `src/kospex_cli.py:625`, `:639`).
- [ ] `src/kospex_utils.py:877` `get_dependency_files_table()` — used by
  dependency-related commands.
- [ ] `src/kospex_utils.py:1220` `key_person_prettytable()` — single
  caller (`kospex key-person`); inline.
- [ ] `src/kospex_utils.py:1238` `file_metadata_prettytable()` — caller
  is `Kospex.cli_file_metadata()`; inline.
- [ ] `src/kospex_utils.py:1260` `get_keyvalue_table()` — check callers.
- [ ] `src/kospex_utils.py:1291` `get_status_table()` — used in repo
  status displays (e.g. `src/kospex_cli.py:347`, `:635`).
- [ ] `src/kospex_utils.py:1317` `orphan_prettytable()` — single caller
  (`kospex orphans`); inline.

### Other library modules

- [ ] `src/kospex_dependencies.py:204`
  `KospexDependencies.get_cli_pretty_table()` — backs `kospex deps` /
  `kospex sca` CLI table output.
- [ ] `src/kospex_git.py:61` `KospexGit.get_repos_pretty_table()` —
  static helper for rendering a repo list.
- [ ] `src/krunner_utils.py:141` `get_secrets_heatmap_table()` — backs
  krunner secrets reporting.

### Imports / dependency

- [ ] After all of the above: remove `from prettytable import ...` lines
  from each migrated file. Current importers:
  `kgit.py:10`, `kospex_core.py:17` (also imports `from_db_cursor` —
  check it's actually used; if not, drop it too), `kospex_utils.py:14`,
  `kospex_dependencies.py:22`, `kospex_git.py:11`, `krunner_utils.py:9`.
- [ ] Once no source imports remain: drop `prettytable` from
  `pyproject.toml` dependencies.

## Suggested order

1. Inline-only, single-caller helpers — `key-person`, `orphans` (mirrors
   the `orgs` change exactly). Lowest risk, lets us delete two helpers.
2. `kospex_core.py` command-backing methods one at a time — each is
   user-visible output and warrants a manual visual check before commit.
3. Multi-caller helpers in `kospex_utils.py` (`get_repo_stats_table`,
   `get_status_table`, `get_dependency_files_table`) — convert factory
   to return a Rich `Table`; update all call sites in one PR per helper.
4. `kgit.py`, `kospex_dependencies.py`, `kospex_git.py`,
   `krunner_utils.py` — remaining outliers.
5. Drop the `prettytable` dependency.

Each step is independently shippable; smaller commits make visual
regression easier to bisect.

## Out of scope

- Web UI rendering — the Flask/FastAPI templates don't use PrettyTable.
- CSV outputs (e.g. the `-csv` flag on `orphans`, krunner CSV writers) —
  unchanged.
- Re-styling / re-coloring beyond the `style="cyan"` key-column
  convention. Keep columns and alignment matching the existing
  PrettyTable output unless there's a specific reason to change.
- Adding sort options, pagination, or new columns — separate issues.

## Testing

For each migrated table:

- Manual: run the command against a populated `~/kospex/kospex.db` and
  visually confirm columns, alignment, and row data match the prior
  PrettyTable output.
- Empty case: command on a fresh / empty DB still renders a header-only
  table without raising.
- `pytest` after each change — most CLI table output isn't covered by
  tests today, but any failing existing test is a regression.
