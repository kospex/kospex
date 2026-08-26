# kreaper delete-repo: -dry-run, and drop the stale warning

Supports the backfill sequence in #165: detect → `kreaper delete-repo` → re-sync.

## Overview

`kreaper delete-repo -repo_id <id>` is the supported way to reset a repo before
a full re-sync. It had two problems that made it hard to trust:

1. It printed `Warning : This function only implements table!` on **every**
   invocation, including the `-repo_id` path that demonstrably clears every repo
   table. The accompanying `# TODO - Implement for all tables!` was stale.
2. There was no way to see what it would delete before deleting it.

## Why delete-repo resets a re-sync

`delete_repo()` iterates `get_repo_tables()` — every table carrying a `_repo_id`
column, auto-detected via PRAGMA rather than hardcoded, so a future table is
covered without touching kreaper. Against the current schema that is 13 tables:

```
branch_history  branches      commit_files   commit_metadata  commits
dependency_data developer_stats file_metadata kospex_groups   krunner
observations    repo_hotspots  repos
```

`repos` is included, so `last_sync_hash` and the sync provenance go with it.
`sync_repo()` then finds no prior `MAX(committer_when)`, leaves `from_date`
unset, and walks the full history. That is what makes a re-sync actually
reconstruct data rather than resume from the last commit.

That behaviour was already correct — it was only the warning that said otherwise.

## Change

`-dry-run` reports the row counts per table and deletes nothing:

```
$ kreaper delete-repo -repo_id github.com~c-w~ghp-import -dry-run
Dry run - would delete the following for repo_id github.com~c-w~ghp-import:

  commit_files              191 rows
  commits                   139 rows
  developer_stats            24 rows
  file_metadata              19 rows
  repos                       1 rows

  TOTAL                     374 rows

Nothing deleted. Re-run with -yes to delete.
```

It deliberately does **not** require `-yes`: the dry run writes nothing, and
demanding the destructive confirmation flag to see a preview just trains people
to type it.

Counts come from `Kospex.repo_id_row_counts()`, which walks the same
`get_repo_tables()` list the delete does — so the preview cannot drift from the
deletion. Tables with no rows are omitted; a repo touches a handful of the 13,
and listing the empty ones is noise.

## Files changed

- `src/kospex_core.py` — `repo_id_row_counts()`
- `src/kreaper.py` — `-dry-run`, stale warning removed, docstring explains the
  provenance reset
- `tests/test_kreaper_dry_run.py` — 9 tests

## Notes

- The command help now states that `repos` is cleared and what that means for
  the next sync, since that is the non-obvious part and the reason the command
  matters for #165.
- `test_delete_still_deletes` seeds a second repo and asserts it survives, so
  the delete stays scoped to one `_repo_id`.
