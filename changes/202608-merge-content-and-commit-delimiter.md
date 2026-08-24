# Commit ingest: safe delimiter, unquoted paths, and merge-only content

Closes #163, #116 and #121 — three defects in one command, `git log` at
`kospex_core.py:334`.

## Overview

The commits/commit_files ingest had three problems in the same place:

1. It split git's output on `#`, which is legal in an author or committer email.
   One `#` shifted every later field and the last field silently absorbed the
   remainder, corrupting `author_email` — the primary developer identity key.
2. It let git quote non-ASCII paths, so `"caf\303\251.py"` was stored instead of
   `café.py` and never matched anything else in kospex.
3. It recorded nothing at all for content that a merge introduced but that
   exists in none of its parents.

## 1. Delimiter (#163)

```python
# was
"--pretty=format:%H#%aI#%cI#%aN#%aE#%cN#%cE"        line.split("#", 6)
# now
COMMIT_LOG_FORMAT   # %H\x1f%aI\x1f...\x1f%P        line.split("\x1f")
```

ASCII Unit Separator, because git rejects control characters in name and email
fields where it permits `#`. Real data hits this: `NixOS/nixpkgs` has 97 commits
authored `git#v1@kaction.cc`, which today parse as:

```
author_email     = 'git'                            <- truncated
committer_name   = 'v1@kaction.cc'                  <- an email in the name field
committer_email  = 'KAction#git#v1@kaction.cc'      <- absorbed the remainder
```

The parser now asserts the field count instead of unpacking optimistically. A
silent shift is what kept this invisible — and querying the DB for `#` returns
zero rows, because the parse consumed the delimiter before the insert.

## 2. Unquoted paths (#116)

All walks run with `-c core.quotePath=false`. `file_metadata.committer_when` is
keyed on `commit_files.file_path` via `latest_commit_file_map()`, so a quoted
path there could never match the path panopticas walked, leaving `committer_when`
NULL and the row keyed on repo HEAD.

## 3. Merge content (#121)

`git log --numstat` reports zero files for a merge. **That is correct and is
preserved.** A merge that only combines branches authored nothing, and
attributing files to it double-counts work already credited to the branch
commits. The main walk is unchanged — no `--diff-merges`.

The gap is an *evil merge* — git's own glossary term for "a merge that
introduces changes that do not appear in any parent": a conflict resolution, or
a file added while merging. That content was recorded nowhere.

### Why it needs two extra walks

No single git invocation can both filter to combined-diff files and report
counts. Tested against a clean merge:

```
--name-only --diff-merges=combined   ->  (nothing)      correct
--numstat   --diff-merges=combined   ->  1  1  b.txt    wrong
git show -c / --cc / -m --numstat    ->  1  1  b.txt    wrong
git diff-tree -c / --cc --numstat    ->  1  1  b.txt    wrong
```

`--numstat` has no combined representation and silently degrades to first-parent
— byte-identical to `--diff-merges=first-parent`, verified programmatically. So:

| walk | command | runs |
|---|---|---|
| main | `--numstat` *(unchanged)* | always |
| mask | `--name-only --diff-merges=combined` | always — detection |
| counts | `--numstat -m` | **only if the mask found something** |

### Why the naive fix would have been bad

Adding `--diff-merges=combined` to the numstat command — the shape originally
proposed on #121 — inflates every merge:

| repo | rows today | this fix adds | naive fix would add |
|---|---:|---:|---:|
| kospex | 1,667 | **0** | 215 (12.9%) |
| click | 5,225 | **442** (8.5%) | 4,765 (91.2%) |
| pydantic | 25,858 | **276** (1.1%) | 418 |
| react | 131,811 | **838** (0.6%) | 22,810 (17.3%) |

### Counts come from the closest parent, not the first

`-m` emits one numstat block per parent in a single walk, so the smallest change
for a path is its diff against the nearest parent. That isolates what the merger
actually typed; first-parent counts also include the branch's own work:

```
src/click/shell_completion.py
  first-parent       :  40+ / 16-     <- includes the branch's work
  closest parent     :   2+ /  3-     <- what the merger typed
```

Across evil-merge rows: click 33% of rows differ, total churn 8,469 -> 3,177
(2.7x over-credit avoided); react 29%, 40,168 -> 30,971. This matters for
key-person and bus-factor analysis, where a conflict resolution is real work by
the person with the deepest knowledge of the subsystem — currently invisible.

### Cost

The counting walk is skipped when no merge carries content of its own, so a
clean-merge repo pays only for detection:

| | kospex (0 evil merges) | click (259) | react (357) |
|---|---:|---:|---:|
| main walk (unchanged) | 108 ms | 334 ms | 9,186 ms |
| mask | 23 ms | 92 ms | 1,850 ms |
| counts | *skipped* | 894 ms | 13,589 ms |

## `parents` is now populated

`[parents] INTEGER` has been declared since the Mergestat-derived schema and was
never written — 0 of 254,997 rows. `%P` fills it, so **no migration**.

Stored as a count, since merge detection only asks `parents > 1`. Never `== 2`:
`NixOS/nixpkgs` has 12 octopus merges, one with **16 parents**.

## Backfill

All three fixes apply to newly-synced commits. Commit sync is incremental
(`--since` the last recorded commit), so existing rows keep their mangled emails,
quoted paths and NULL `parents` until affected repos are re-synced with a reset
window. The 34 quoted paths in `commit_files` also change primary key when
unquoted, so a re-sync adds the unquoted row rather than replacing the quoted
one — those need deleting, not just re-syncing.

## Files changed

- `src/kospex_core.py` — `COMMIT_LOG_FORMAT`, `parse_commit_log()`,
  `merge_file_rows()` and helpers; `sync_repo()` rewired
- `tests/test_commit_log_parsing.py` — 13 tests
- `tests/test_merge_file_rows.py` — 7 tests

## Verification

Beyond the unit tests, an end-to-end `sync_repo()` against a throwaway repo and
DB confirms, through the real code path:

```
author_email intact      : 'git#v1@kaction.cc'
committer_name           : 'KAction'
clean merge parents      : 2       nulls remaining: 0
clean merge file rows    : 0       <- the invariant
evil merge paths         : ['café.py', 'shared.txt']
quoted paths in DB       : 0
```
