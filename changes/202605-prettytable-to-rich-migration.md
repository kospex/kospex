# 202605 — PrettyTable → Rich table migration (CLI)

## Status

**In progress.** Some tables are migrated (see *Update — 2026-10-23*,
*Update — 2026-10-22* and *Update — 2026-10-20*, below). The May inventory and suggested order further
down are kept for history; their line numbers are out of date, and the
updates' lists replace them.

## Update — 2026-10-23

### Done

- **`kospex key-person`** (`kospex_cli.py:917`): "Key Person". The table is
  built inline and `key_person_prettytable()` is deleted, as step 3 of
  *Order of work* asks. Columns and alignment are as before. `author` is
  cyan and `no_wrap`, because it is an email, which counts as an identifier.
  The table prints through `print_table`. A `None` (for example `merges` on
  an older row) prints as an empty cell, not as the string `None`.
- **Removed the raw dump.** The command used to `console.print(authors)`, so
  the list of dicts was printed above the table. It looked like leftover
  debug output, and it also made the piped test pass on the old code without
  testing anything, because the full email was already in the dump. The test
  asserts that the email appears exactly once.

Test: `tests/test_key_person_cli.py::test_key_person_prints_complete_author_when_piped`,
with an author email of more than 80 characters and the query stubbed. It
fails on the old code.

### Still to do in step 3

`author-tech`, `metadata`, `secrets-hotspots`, `kgit bitbucket`.

## Update — 2026-10-22

### Done

- **The printing helper (step 1 of *Order of work*).**
  `KospexUtils.print_table(table, console)` (`kospex_utils.py:1379`). On a
  terminal it is `console.print(table)`. Otherwise it measures the table's
  natural width and prints it at that width (or the console's, if that is
  wider), through a `Console` on the same file. That is option 1 under
  *Piped output*. It keeps PrettyTable's guarantee that piped output is
  complete, and leaves terminal output as rich's fit-to-width.
- **`kgit status`** (`kgit.py:169`): "Repo Status", with Subject (cyan,
  `no_wrap`) and Value (right-aligned, as before). Values go through `str()`
  because the stats dict holds ints.
- **`kgit pull --check`** (`kgit.py:441`): "Repo Staleness". `repo_id` is
  cyan and `no_wrap`. The `age` column was renamed `fetch_age`, because it
  is days since `last_fetch` and not since the last sync or commit. That is
  a user-visible change, recorded in the CHANGELOG.

Both print through `print_table`. Each has a `CliRunner` test, as *Tests*
asks: `tests/test_kgit_pull.py::test_pull_check_prints_complete_repo_id_when_piped`
uses a `repo_id` longer than 80 characters, and
`tests/test_kgit_status.py` does the same with a long `remote_url`.

### Why the helper had to come first

`kgit pull --check` was migrated before the helper existed, with a bare
`console.print` and `no_wrap=True` on `repo_id`. Piped, an 80-character
`repo_id` then took the whole width, and the other four columns collapsed to
zero:

```
┃ repo_id                                                                  ┃┃┃┃┃
│ gitlab.com~example-organisation~~platform-subgroup~a-fairly-long-reposi… │││││
```

So `no_wrap` alone doesn't protect an identifier column: it only decides
which column loses. It still matters on a terminal, where it stops the
`repo_id` being wrapped mid-id. Every remaining table with an identifier
column should print through `print_table`.

### Learned along the way

- `console.print(table, width=N)` doesn't widen past the console's own
  width. The helper builds a second `Console(file=console.file, width=N)`
  for that reason.
- Measure with `console.options.update(width=...)` set very large. With the
  default options the measurement is capped at 80, the width being avoided.
- A `Console()` made at import time with no `file` writes to whatever
  `sys.stdout` is when it prints, so `CliRunner` captures module-level
  consoles without patching.

### Still to do in kgit

- `kgit bitbucket` (`kgit.py:669`) is the last PrettyTable in `kgit.py`, and
  then the `from prettytable import PrettyTable` at `kgit.py:10` can go. Its
  `clone_url` column is an identifier: print it through `print_table`.

## Update — 2026-10-20

### Why Rich rather than PrettyTable

Both are maintained. Choosing Rich is about having one dependency instead of
two, not about PrettyTable being abandoned. Rich stays regardless, because
the progress bars, consoles and most tables already use it. Figures are from
GitHub and PyPI on 2026-10-20, for the previous 12 months:

| | prettytable | rich |
|---|---|---|
| GitHub stars | 1.8k | 57.6k |
| Commits | 94, 19 of them from bots | 165, none from bots |
| Who commits | One lead maintainer (41) plus two regular contributors (17, 10) | One person wrote 147 of the 165 (89%) |
| Pattern | Some every month | Bursts: 81 in January, 44 in April, 2 in June, none since |
| Last push | 2026-10-06 | 2026-06-23 |
| Releases | 2 (3.18.0 is the latest, 2026-06-22) | 6 (15.0.0 is the latest, 2026-04-12) |
| Open issues and PRs | 55 | 385 |

PrettyTable is the steadier project, with more maintainers and a regular
rhythm, but it's small. Rich is far more widely used, and pip vendors it, so
it's unlikely to be left unmaintained. Day to day it depends on one person,
and it has been quiet for four months. For kospex's needs both are mature
enough that this doesn't change the decision. Worth checking again at each
release.

### Done since May

- `kospex summary`: `Kospex.summary()` builds a Rich table and keeps
  PrettyTable's `sortby="last_commit"` by sorting the rows itself
  (`kospex_core.py:901`).
- The `kospex sca` package table: `get_cli_pretty_table()` became
  `get_cli_table()`, which returns a Rich table
  (`kospex_dependencies.py:298`). `KospexDependencies` no longer imports
  prettytable.
- The status legend and status table: `get_status_legend()` and
  `get_status_table()` (`kospex_utils.py:1362`, `:1377`).

### What's left

Eighteen tables in six modules as of 2026-10-20, all CLI output; sixteen
after the 2026-10-22 update, fifteen after 2026-10-23. kweb doesn't use
prettytable: its `tech_landscape` calls go to `KospexQuery`, which returns
data rather than printing a table.

| Command | Where the table is built | Notes |
|---|---|---|
| `kospex developers`, `kospex orphans` | `Kospex.active_developers()`, `kospex_core.py:987` | Shared by two commands |
| `kospex orphans` | `orphan_prettytable()`, `kospex_utils.py:1396` | Used once: inline it. The `-csv` output is unaffected |
| ~~`kospex key-person`~~ | Done 2026-10-23 | See *Update — 2026-10-23* |
| `kospex author-tech` | `Kospex.author_tech_pretty_table()`, `kospex_core.py:760` | Rename it when migrating |
| `kospex list-repos`, `krunner find-repos` | `Kospex.list_repos()`, `kospex_core.py:1059` | `repo_id` column; see *Piped output* |
| `kospex tech-landscape` | `Kospex.tech_landscape()`, `kospex_core.py:1169` | Uses `from_db_cursor`; see *One helper, or each table inline* |
| `kospex metadata` | `file_metadata_prettytable()`, `kospex_utils.py:1289` | Used once, by `cli_file_metadata()` (`kospex_core.py:1239`) |
| `kospex system-status` | `Kospex.get_kospex_table_summary()`, `kospex_core.py:1626` | |
| `kospex deps`, `krunner find-docker` | `get_dependency_files_table()` `:946`, `get_repo_stats_table()` `:923` | Used by several commands: keep as functions that return a Rich table |
| `kospex health-check`, `krunner find-docker`, config output | `get_keyvalue_table()`, `kospex_utils.py:1311` | Used by several commands: keep as a function |
| ~~`kgit status`, `kgit pull`~~ | Done 2026-10-22 | See *Update — 2026-10-22* |
| `kgit bitbucket` | Inline in `kgit.py:669` | Last PrettyTable in `kgit.py` |
| `kgit github` | `KospexGit.get_repos_pretty_table()`, `kospex_git.py:71` | |
| `krunner secrets-hotspots` | `get_secrets_heatmap_table()`, `krunner_utils.py:203` | |

Clean-up to do while migrating: the commented-out PrettyTable block at
`kospex_core.py:1200`, and the `get_kospex_config_table()` docstring
(`:1654`), which still says it returns a PrettyTable.

prettytable is imported in `kgit.py:10`, `kospex_git.py:11`,
`kospex_core.py:17` (which also imports `from_db_cursor`), `kospex_utils.py:14`
and `krunner_utils.py:9`. When none of those imports remain, the dependency
can go.

### Piped output

*Truncation behavior*, below, covers a narrow **terminal**. There is a second
case: when stdout is a pipe or a file, Rich can't measure a terminal, so it
assumes **80 columns** and cuts long values with `…`. PrettyTable never
truncates. Checked with Rich 15.0:

```
$ python table.py | cat
┃ repo_id                                ┃ last_commit               ┃ commits ┃
│ github.com~example-organisation~a-fai… │ 2026-10-20T08:15:42+10:00 │   12345 │
│ gitlab.com~group~~subgroup~another-lo… │ 2026-09-01T01:02:03+10:00 │       7 │
```

So after a naive migration, `kospex list-repos | grep acme` or
`kgit status > repos.txt` would quietly lose the end of long `repo_id`s.
Nothing would fail; the output would just be wrong. Setting `COLUMNS=200`
widens it, but nobody will know they need to.

Options:

1. **Don't truncate identifier columns when the output isn't a terminal.** A
   small helper prints with `Console(width=...)` sized to the table, or sets
   `overflow="fold"` on identifier columns, whenever `console.is_terminal` is
   false. This keeps today's guarantee that what you pipe is complete.
2. **Point scripts at `-json` / `-csv`**, and treat the table as display
   only. That's the right long-term answer, and it's the direction
   `sca -json` took. But most of the commands above don't have a
   machine-readable option yet.
3. **Accept truncation**, as `orgs` did. Fine for count-heavy summaries; not
   fine for any table whose first column is a `repo_id`.

Recommendation: option 1 now, through a single helper so the rule lives in
one place, with option 2 added per command as each one is touched. That
makes the migration safe without blocking it on `-json` work.

### One helper, or each table inline

*Reference pattern*, below, says to inline any table built in only one
place, and that still applies. About 20 Rich tables are already written
inline. Two places do warrant a shared helper:

- **Printing**: the piped-output rule above, so it isn't re-implemented 18
  times.
- **`tech-landscape`**: `from_db_cursor` takes its columns from the SQL
  result, and Rich has no equivalent. A short function that builds a table
  from `cursor.description` and the rows covers it.

Nothing else needs a framework.

### Tests

No test checks the format of any of these tables. The only table-related
assertions are on kospex's own column names (`get_table_field_names()` in
`tests/test_sca_output_and_save.py`), and they are unaffected. That's why
each table can be migrated without breaking a test, and also why a
regression would go unnoticed. For each table migrated, add one test that
runs the command through `CliRunner` with a `repo_id` longer than 80
characters and asserts the complete id appears in the output. This tests the
piped-output case directly, because `CliRunner` output is never a terminal.

### Order of work

This replaces *Suggested order*, below.

1. **The printing helper and its test**, migrating nothing. It's the piece
   every later step relies on. *Done 2026-10-22, with the two `kgit` tables
   below.*
2. **Tables whose first column is an identifier**: `list-repos`,
   `developers` / `orphans`, ~~`kgit status`~~, ~~`kgit pull`~~,
   `kgit github`.
   These are the ones where piped output matters most.
3. **Tables built in one place**: ~~`key-person`~~, `author-tech`, `metadata`,
   `secrets-hotspots`, `kgit bitbucket`. Inline them and delete the helpers.
4. **Helpers used by several commands**: `get_repo_stats_table`,
   `get_dependency_files_table`, `get_keyvalue_table`, migrated as functions
   that return a Rich table, one PR per helper, covering all its callers.
5. **`tech-landscape`**, together with the cursor helper, and then
   `system-status`.
6. **Drop the dependency**: remove the last imports and the `prettytable`
   line in `pyproject.toml`. `requirements.txt` follows when it's
   regenerated at release.

Each step can ship on its own. The checks under *Per-migration guidance* and
*Testing*, below, still apply to each table.

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

## Inventory (May 2026)

**Out of date: see *What's left* in the 2026-10-20 update for the current
list and line numbers.** Kept as the original record.

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

## Suggested order (May 2026)

Replaced by *Order of work* in the 2026-10-20 update, which puts a
piped-output helper first.

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
