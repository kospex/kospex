# 202605 — Convert `kospex orgs` output to Rich

## Overview

Convert the `kospex orgs` command's output table from `PrettyTable` to
`rich.table.Table`, matching the pattern already established by
`kospex stats` (the developer stats / key person analysis table).

This brings `orgs` in line with the project's direction of using Rich for
CLI table output, and removes a small one-call-site helper from
`kospex_utils.py`.

## Files changed

- `src/kospex_cli.py` — replace the `orgs` command body (lines 879-887)
  with an inline Rich `Table`.
- `src/kospex_utils.py` — delete `orgs_prettytable()` (lines 1260-1275),
  which is only referenced by the `orgs` command.

## Current behavior

`src/kospex_cli.py:879-887`:

```python
@cli.command("orgs")
def orgs():
    """List all the organizations in the database."""
    kquery = KospexQuery()
    orgs_list = kquery.orgs()
    table = KospexUtils.orgs_prettytable()
    for o in orgs_list:
        table.add_row(KospexUtils.get_values_by_keys(o, table.field_names))
    print(table)
```

The helper at `src/kospex_utils.py:1260-1275` builds a `PrettyTable` with
the columns: `org_key`, `org`, `commits`, `repos`, `authors`,
`committers`, `days_ago`, `last_commit`. Text columns are left-aligned,
numeric/date columns are right-aligned.

## New behavior

Same columns and alignment, rendered with Rich. Mirrors the structure of
`developer_stats` at `src/kospex_cli.py:827-876`:

- Module-level `console = Console()` is already imported and in use
  (`src/kospex_cli.py:30`); no new imports needed.
- `Table(title="Organizations")` — gives the table a clear heading,
  matching the `Developer Stats: <repo>` title used by `stats`.
- Columns:
  - `org_key` — `style="cyan"` (matches the "key" column treatment in
    `stats`, where `Author` is cyan).
  - `org` — default style, left-aligned.
  - `commits`, `repos`, `authors`, `committers`, `days_ago` —
    `justify="right"`.
  - `last_commit` — `justify="right"` (matches the existing PrettyTable
    alignment).
- Rows: one `add_row(...)` per org dict, with `str(value)` conversions
  and `""` fallback for None values, keyed by the same field list.
- Output wrapped with `console.print()` blank lines before and after the
  table, matching `stats`.

### Sketch

```python
@cli.command("orgs")
def orgs():
    """List all the organizations in the database."""
    kquery = KospexQuery()
    orgs_list = kquery.orgs()

    table = Table(title="Organizations")
    table.add_column("org_key", style="cyan")
    table.add_column("org")
    table.add_column("commits", justify="right")
    table.add_column("repos", justify="right")
    table.add_column("authors", justify="right")
    table.add_column("committers", justify="right")
    table.add_column("days_ago", justify="right")
    table.add_column("last_commit", justify="right")

    for o in orgs_list:
        table.add_row(
            str(o.get("org_key", "")),
            str(o.get("org", "")),
            str(o.get("commits", "")),
            str(o.get("repos", "")),
            str(o.get("authors", "")),
            str(o.get("committers", "")),
            str(o.get("days_ago", "")),
            str(o.get("last_commit", "")),
        )

    console.print()
    console.print(table)
    console.print()
```

## Rationale

- **Consistency** — `stats` already uses Rich; new table commands should
  follow that pattern rather than the older PrettyTable approach.
- **YAGNI on the helper** — `orgs_prettytable()` is only called once.
  Inlining matches `stats` and removes an indirection.
- **No data shape changes** — `KospexQuery().orgs()` is unchanged; only
  the rendering changes.

## Out of scope

- Adding new columns, sort options, or filtering to the `orgs` command.
- Migrating other PrettyTable usages (e.g. in `kospex_cli.py`'s sync /
  health-check / deps tables) — those can follow in separate small
  changes.

## Testing

- Manual: `kospex orgs` against a populated `~/kospex/kospex.db` and
  visually confirm the table renders with the same columns, alignment,
  and row data as before.
- Empty case: `kospex orgs` on a fresh DB still produces a (header-only)
  table without raising.
- `pytest` — no existing tests reference `orgs_prettytable` or the
  `orgs` CLI command, so nothing else needs to be updated.
