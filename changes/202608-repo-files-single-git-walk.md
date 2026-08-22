# get_repo_files(): one git log walk per repo, not one per file

Closes #152. Also closes #116, and fixes the #121 root cause at this call site.

## Overview

`KospexGit.get_repo_files()` spawned one `git log` subprocess per file, so a
`file-metadata` rebuild over a modest estate took hours. It now resolves every
path once, in a single `git log --name-only` walk per repo, and looks each file
up in that map.

Measured 19-167x faster on real clones, and the cost no longer grows with the
number of files, only with history size.

## Problem

`get_repo_files()` called `KospexUtils.get_last_commit_info(entry)` for every
entry panopticas returned. Per file that is one fork/exec plus two `os.chdir`
calls:

```python
os.chdir(file_dir)
subprocess.check_output("git log -1 --pretty=format:'%H|%ad|%cd' --date=iso-strict -- <file>")
os.chdir(original_dir)
```

The per-file cost is process-spawn overhead, not git work: it barely moves with
repo size (16.4 ms on click, 21.3 ms on babel) while the file count varies by
170x. A `krunner file-metadata` run over ~105 repos was still on repo 21 after
roughly 40 minutes and was abandoned, leaving the estate with a partially
re-tagged `file_metadata` — a hazard in itself, because `tech_type` queries then
silently return a subset.

## Change

`KospexGit._last_commit_by_path()` does one walk and returns
`{path: {"commit_hash", "author_when", "committer_when"}}`. git log is newest
first, so the first commit to mention a path is that path's last commit.

```
git -c core.quotePath=false log --name-only --diff-merges=combined \
    --pretty=format:%x01%H|%ad|%cd --date=iso-strict
```

`get_repo_files()` calls it once before the loop, then does a dict lookup per
file. `cwd=` replaces the `os.chdir` pair — the last per-file `chdir` in the
codebase, after the same fix was applied to `krunner todo` and `get_branches()`.

`KospexUtils.get_last_commit_info()` is unchanged and still used by
`get_all_last_commit_info()` and `kospex_cli`.

### Why `--diff-merges=combined`, not `first-parent`

#152 proposed `--diff-merges=first-parent` (or `-m`). Measured against
`git log -1 -- <path>` on this repo, that is wrong for **91 of 293** files: a
merge's first-parent diff contains everything the branch changed, so in a
PR-merge workflow nearly every file is reattributed to the merge commit instead
of the commit that actually touched it.

`--diff-merges=combined` lists, for a merge, the files that differ from *every*
parent. That is exactly what path-limited `git log` history simplification
shows. It finds content that only ever existed in a conflict resolution (git's
default shows no diff at all for merges — the #121 root cause) without claiming
files the merge merely forwarded.

| variant | missing | mismatched vs `git log -1 -- <path>` |
|---|---:|---:|
| default (no flag) | 0 | 0 — but blind to merge-only content |
| `--diff-merges=first-parent` | 0 | 91 |
| `-m` | 0 | 91 |
| **`--diff-merges=combined`** | **0** | **0** |

### `core.quotePath=false` — closes #116

git quotes non-ASCII paths as `"caf\303\251.py"`, which never matches the
filesystem path panopticas produced, so the lookup misses and `committer_when`
stays NULL. That is #116. Paths git still quotes (control characters, quotes,
backslashes) are unquoted by `_unquote_git_path()`.

### `unmanaged` is now logged

`unmanaged` was incremented and never read — the files were silently dropped and
the count discarded. It is now free to compute (`entry not in last_commits`,
where the old code paid a subprocess per untracked file before discarding it
anyway) and is logged when non-zero:

```
<repo_dir>: N untracked file(s) skipped - synced from a working directory
rather than a clean clone
```

On a clean clone panopticas and git agree, so this is zero and the log stays
silent. A non-zero count means the sync came from a working directory with
untracked files, and the file inventory won't match what is committed. Logged,
not persisted — it is diagnostic, not data, and does not warrant a schema
change. This adds the first logger to `src/kospex_git.py`.

## Measured

Old figures are the per-file cost sampled over 40 files, multiplied by the
tracked-file count. New figures are a complete walk.

| Repo | Files | Commits | Per-file | Projected total | Single walk | Speedup |
|---|---:|---:|---:|---:|---:|---:|
| `pallets/click` | 163 | 3,287 | 16.4 ms | 2.7 s | **0.14 s** | 19x |
| `facebook/react` | 7,270 | 21,577 | 20.1 ms | 2.4 min | **2.55 s** | 57x |
| `babel/babel` | 27,651 | 18,583 | 21.3 ms | 9.8 min | **3.53 s** | 167x |

`--diff-merges=combined` costs more than a bare walk (react: 2.55 s vs 1.4 s),
which is why these are below the projections in #152. It buys the merge-commit
correctness above.

## Known divergence

Path-limited `git log` simplifies history and prunes a branch whose changes to a
path did not survive the merge — an import reverted on the branch before it
landed, say. An unlimited walk still sees those commits, so such a path gets the
abandoned commit rather than the one that set its current content. Both are
commits that really touched the path; the walk's is newer.

Sampled against `git log -1 -- <path>`:

| Repo | Sampled | Divergent |
|---|---:|---:|
| `kospex/kospex` | 293 (all) | 0 |
| `babel/babel` | 300 | 0 |
| `pydantic/pydantic` | 200 | 1 (0.5%) |
| `facebook/react` | 400 | 5 (1.3%) |

All five react cases trace to one reverted devtools-v4 import. No git flag
reproduces the simplification without re-introducing a per-path query, so this
is accepted and pinned by
`test_change_abandoned_on_a_branch_is_a_known_divergence`.

The divergence is not new to `file_metadata`. The hash and date it stores come
from `KospexQuery.latest_commit_file_map()` (a pass over `commit_files`), not
from this walk — `build_file_metadata_rows()` ignores the `committer_when` in
the returned dict, which now feeds only the legacy `kospex file-metadata --sync`
path and the displayed `status`. `commit_files` is itself built from a full
`git log --numstat` walk, so it already ranks a path by every commit that
touched it, simplification included. Checked against the live database, the
walk agrees with what is already stored for **7254/7269** react rows and
**27533/27651** babel rows, and the react cases where the stored value differs
from git are the same abandoned-revert commit, to the second.

The remaining differences are a separate, pre-existing defect in
`latest_commit_file_map()` and not caused by this change (filed as #154): it ranks with
`ORDER BY committer_when DESC` on ISO-8601 **text**, so a commit at
`16:59:54-04:00` sorts below one at `18:34:03+01:00` despite being 3.5 hours
later. Across react's `commit_files`, 61 of 26,577 paths (0.2%) pick the wrong
commit that way, by up to 7.9 hours.

## Files changed

- `src/kospex_git.py` — `_last_commit_by_path()`, `_unquote_git_path()`,
  rewritten `get_repo_files()`, module logger
- `tests/test_last_commit_by_path.py` — new, 13 tests against real git repos

## Behaviour notes

- `skip_last_commit=True` is unchanged: no git call at all, `committer_when` and
  `status` are `None`, and nothing is filtered out (git is never asked what it
  tracks).
- A repo with no commits returns an empty map rather than raising.
- Renames map to the rename commit — `--name-only` reports the post-rename path
  and newest-first first-wins picks it. `--follow` is not an option, it only
  works for a single path.
- Untracked files are absent from the map, so they are skipped before any work,
  matching the old `unmanaged` outcome.
