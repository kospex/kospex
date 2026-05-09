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

Caveat: Rich auto-truncates with `…` on narrow terminals. Acceptable for
most tables; if a specific report needs full values, use
`console.print(table, soft_wrap=True)` or `no_wrap=False` on the column.

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
