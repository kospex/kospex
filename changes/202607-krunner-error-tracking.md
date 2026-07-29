# krunner — survive missing clones + track errors by type

**Status:** Done
**Owner:** Peter
**Date:** 2026-07-29

## Context

`krunner branches` aborted mid-run with a traceback:

```
File "src/krunner.py", line 177, in branches
    branches = KospexGit.get_branches(r["file_path"])
File "src/kospex_git.py", line 380, in get_branches
    os.chdir(directory)
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/kgit-ssh-test/github.com/kospex/panopticas'
```

`repos.file_path` records where a repo's clone lives on disk, and that path can
stop existing at any time — the clone is deleted, moved, or was only ever a
throwaway directory (here, a `/tmp` path from a manual `kgit clone` experiment).
`KospexGit.get_branches()` chdir'd into the path with no guard, so a **single**
missing clone killed the run for **every remaining repo**. 104 healthy repos went
unprocessed because of one stale row.

Two further problems in the same code:

- No `try`/`finally` around the chdir. A failing `git branch -r` (`check=True`)
  raised before the restore, leaving the process cwd stranded inside the repo.
- A path that exists but isn't a git repo raised `CalledProcessError`, aborting
  the run the same way.

## Changes

### `src/kospex_git.py` — `get_branches()`

Runs git with `cwd=directory` instead of `os.chdir()`. No process-global state is
mutated, so neither a missing directory nor a failing git command can strand the
cwd. Behaviour is otherwise identical; errors still propagate to the caller.

### `src/krunner_utils.py` — new `RunErrors` tracker

A scan walks many repos and one bad repo is not a failed run. `RunErrors`
collects per-item failures, logs each at ERROR level, echoes it to the console,
and reports a count-by-type summary at the end.

```python
MISSING_CLONE = "MISSING_CLONE"   # the repo's local clone is not on disk
GIT_ERROR     = "GIT_ERROR"       # the path exists but a git command failed on it

class RunErrors:
    def __init__(self, logger=None, console=None)
    def add(self, error_type, item, message)   # records + logs + prints
    def counts_by_type(self)                   # {type: count}, count desc then name
    def summary_table(self)                    # rich Table, or None when clean
    def __len__(self) / __bool__(self)
```

Error types are **caller-chosen names**, not exception class names, so the
summary and the log read in kospex terms rather than Python internals. `add()` is
the single call at each failure site — it owns both outputs so call sites don't
duplicate logging and printing.

### `src/krunner.py` — `branches`

Skips repos whose `file_path` is missing or unreadable, records the failure
against the tracker, and carries on. Adds `-strict`.

## Usage

```
$ krunner branches
...
Total number of repositories checked: 104

       Errors (1)
┏━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Type          ┃ Count ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━┩
│ MISSING_CLONE │     1 │
└───────────────┴───────┘
```

Nothing is printed when the run is clean. Each error also lands in
`~/kospex/logs/krunner.log`:

```
2026-07-29 18:01:00 [   ERROR] [krunner] MISSING_CLONE github.com~kospex~panopticas: no local clone at /tmp/kgit-ssh-test/github.com/kospex/panopticas
```

`-strict` exits 1 if any repo errored; the default stays 0 so existing scripts and
cron jobs are unaffected.

```
$ krunner branches -strict ; echo $?
1
```

## Tests

- `tests/test_krunner_run_errors.py` — tracker: grouping, ordering (count desc,
  then name), ERROR-level logging, console output, no-logger/no-console case,
  empty summary.
- `tests/test_krunner_branches_missing_repo.py` — `get_branches` leaves cwd alone
  when git fails; `branches` skips a missing clone and a non-git path and still
  processes healthy repos; summary counts by type; no summary on a clean run;
  `-strict` exit codes both ways.

## Notes / follow-ups

- `RunErrors` is deliberately wired into `branches` only. Other repo-looping
  krunner commands (`osi`, `dependencies`, `file-metadata`, `repo-size`,
  `key-person`, …) have the same one-bad-repo-aborts-the-run fragility and can
  adopt the tracker one at a time as they're touched.
- A stale `repos` row is not itself cleaned up by this change — the run reports it
  and moves on. Deciding whether a missing clone should be re-cloned or deleted
  from the DB is a separate question (`kreaper` territory).
