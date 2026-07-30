# Refuse to repoint a repo at a different clone

**Status:** Done
**Owner:** Peter
**Date:** 2026-07-30

## Context

`repos._repo_id` is the primary key, and `Kospex.update_repo_status()`
(`kospex_core.py`) upserts `file_path` into that row on every sync:

```python
details["file_path"] = self.repo_directory
...
self.kospex_db.table(KospexSchema.TBL_REPOS).upsert(details, pk=["_repo_id"])
```

So syncing the same repo from a **second** location silently repointed the row at
the newest clone — no warning, no record of where it pointed before. Combined
with `clone_repo()` resolving its destination from `HabitatConfig.code_dir`, this
sequence loses data provenance:

1. `KOSPEX_CODE=/tmp/kgit-ssh-test`, `kgit clone …/kospex/panopticas`
2. clone lands at `/tmp/kgit-ssh-test/github.com/kospex/panopticas`
3. sync upserts → `github.com~kospex~panopticas.file_path` flips from
   `~/code/…` to `/tmp/…`
4. `/tmp` is cleared; the row is stale even though a good clone still sits in
   `~/code/github.com/kospex/panopticas`

That stale row is what crashed `krunner branches`
(`changes/202607-krunner-error-tracking.md`).

Worse, the repoint is not the first thing that happens. `sync_repo()` ingests
commits and commit_files at `kospex_core.py:340-350` and only calls
`update_repo_status()` at line 364 — so by the time the row is repointed, the
second clone's commit data is already merged under that `repo_id`.

## Design

**Refuse only when both paths exist on disk.** That's the ambiguous case, where a
throwaway copy could overwrite the real one. A recorded path that no longer
exists is a genuine move (deleted or relocated clone), so repointing self-heals
the row — which is exactly what the panopticas row needs.

## Changes

### `kospex_core.py` — the decision, as a pure function

`repo_path_conflict(recorded_path, new_path, force=False) -> (is_conflict, reason)`,
following the `needs_metadata_rebuild()` precedent in the same module. Both sides
are compared with `os.path.realpath()`; an unresolved comparison false-positives
wherever either path crosses a symlink (macOS symlinks `/tmp` → `/private/tmp`).

| recorded | new | on disk | result |
|---|---|---|---|
| none | any | — | no conflict, "repo not previously synced" |
| A | A | — | no conflict, "same path as the last sync" |
| A | B | A gone | no conflict, "recorded clone A no longer exists" (self-heal) |
| A | B | A exists | **conflict** |
| A | B | A exists, `force=True` | no conflict, "force requested (was A)" |

`RepoPathConflict(repo_id, recorded_path, new_path)` carries all three as
attributes and renders a message naming both paths and how to override.

### `kospex_core.py` — `Kospex.check_repo_path()` + `sync_repo(force=False)`

`sync_repo()` calls `check_repo_path()` immediately after `set_repo_dir()` and
**before any ingest**, so a refused sync writes nothing. `set_repo_dir()` chdirs
into the repo, so the raise path calls `chdir_original()` first — refusing must
not strand the process working directory.

Any allowed repoint (forced, or the recorded clone is gone) logs at WARNING with
old → new. The repos row is point-in-time and keeps no history, so that log line
is the only record of where it used to point.

### `kospex_git.py` — `planned_clone_path()`

Extracted from `clone_repo()`: works out where a URL *would* be cloned, without
the network, returning `{"repo_id", "path", "parts"}` or None. `clone_repo()` now
calls it, so the pre-flight and the real clone share one destination calculation
and one containment check — the `is_relative_to(code_root)` guard cannot drift
between them. A test asserts the two agree.

### `kgit.py` — `clone_path_conflict()` + `kgit clone -force`

`kgit clone` checks before cloning, so no network work (and no stray directory)
is spent on a repo the sync would refuse. `-force` clones anyway and is passed
through to `sync_repo(force=...)`, so the sync doesn't then refuse what the clone
was told to do. `kgit clone -filename` and `kgit github` skip a conflicting repo
and carry on with the rest.

### `kospex_cli.py` — `kospex sync-directory -force`

The sleeper: it walks every git repo under a directory, so pointing it at a
second copy of a tree would repoint many rows at once. A conflicting repo is
reported and skipped, the walk continues, and the count is summarised at the end.

### `krunner.py` — `git-pull`

Records conflicts against the `RunErrors` tracker under a new `PATH_CONFLICT`
type and prints the summary table. Also replaced the pre-existing
`os.chdir(d)` + `os.system("git pull")` in that loop with
`subprocess.run(["git", "pull"], cwd=d)` — no shell, and no chdir to strand.

## Paths deliberately left alone

- **`kgit pull`** syncs the path already recorded in the DB, so
  `repo_path_conflict()` always returns "same path". It is also already wrapped
  in `try/except Exception` per repo.
- **`kospex-agent`** already wraps each repo in `try/except Exception` and logs
  the failure, so a conflict is non-fatal and reported. No change needed.

## Parked `kospex sync` (issue #123)

Both commented-out variants at `kospex_cli.py:362-402` call `kospex.sync_repo()`
on a `Kospex()` receiver, so **they inherit the guard for free** when re-enabled.
The only work at that point is adding a `-force` flag to the command, matching
`sync-directory`.

## Tests

- `tests/test_repo_path_conflict.py` — the pure function: never-synced, same
  path, symlink-equivalent paths, conflict, force, recorded-path-gone, and the
  exception's attributes/message.
- `tests/test_sync_repo_path_conflict.py` — end-to-end against throwaway git
  repos + DB: first sync records the path; a second clone is refused; the refused
  sync ingests **no commits** and restores the cwd; `force` repoints; a moved
  repo repoints without force; re-syncing the same path is unaffected.
- `tests/test_kgit_clone_path_conflict.py` — `planned_clone_path` names the
  repo_id and destination, rejects unparseable URLs, and agrees with where
  `clone_repo` actually clones; `kgit clone` refuses **without calling
  clone_repo**, `-force` proceeds, and a gone recorded path proceeds.
- `tests/test_sync_directory_conflict.py` — a conflicting repo is skipped and the
  walk continues; `-force` reaches every sync.

## Notes

The trigger case verified read-only against the real DB: recorded
`/tmp/kgit-ssh-test/…/panopticas` (gone) vs live `~/code/…/panopticas` returns
`(False, 'recorded clone … no longer exists')` — so syncing the live clone
repoints the stale row without `-force`, which is the desired repair.
