# file_metadata: record Bytes for every file, not only the ones scc counts

**Status:** researched / not yet implemented
**Date:** 2026-10
**Tracked in:** kospex/kospex#334
**Related:** kospex/kospex#230 (scc 4.x compatibility), kospex/kospex#274 (per-file extraction outcomes), kospex/panopticas#46 (panopticas's file selection differs from git's), kospex/panopticas#79 (kind tags for archives, dumps and databases)

## Problem

`file_metadata.Bytes` is declared `INTEGER -- Number of bytes in the file`,
but it is only filled in when scc reports the file. `Kospex.file_metadata()`
(`src/kospex_core.py`) runs `scc --by-file -f csv` and keeps its `Bytes` column
in `scc_metrics`. `KospexSchema.build_file_metadata_rows()`
(`src/kospex_schema.py`) copies those metrics onto a row only when
`scc_metrics.get(provider)` finds the path. Every file panopticas lists but scc
does not gets a row with `Bytes = NULL`, as well as NULL line counts.

The files scc skips are the ones where size matters most: binaries, archives,
data dumps and anything with an unrecognised extension. A 2 GB memory dump
committed to a repository, which is easy to spot by size alone, is
currently stored with no size at all.

## What the data shows

A read-only look at a local kospex database (376 repositories, 551,376
`latest = 1` rows, scc 3.7.0):

| `Bytes` | Rows | Share |
|---|---|---|
| Populated (> 0) | 455,012 | 82.5% |
| `0` | 5,155 | 0.9% |
| `NULL` | 91,209 | 16.5% |

- The NULLs are spread across 370 of the 376 repositories, so they are not
  a few failed syncs. Every repository has at least some scc data.
- The largest stored value is 50,173,280 bytes (~50 MB). Nothing larger is
  visible, because a file that scc skips has no size at all.
- The zeros are mostly `Text` (4,054), `Python` (465) and `JavaScript` (334)
  files, which are probably empty files such as `__init__.py`. They are real
  sizes and need no change.
- `_metadata_source` is `NULL` on every row, so a row does not say where its
  metrics came from.

NULL rows by panopticas `Language`:

| Language | NULL rows | Why scc skipped them |
|---|---|---|
| Unknown | 68,224 | No scc language for the extension |
| PNG / JPEG / ICO | 11,547 / 733 / 229 | Binary, not counted |
| JavaScript | 5,112 | Mostly scc's `.gitignore` matching differs from git's (below) |
| Gitignore | 1,066 (326 repositories) | scc skips ignore files unless `--count-ignore` is set |
| TypeScript, YAML, Python | 718, 406, 201 | Clustered in a few repositories (6, 21, 6); cause not checked per file |

Lock files are never sized: package-lock.json (78 of 78), yarn.lock (156 of
156), pnpm-lock.yaml (29 of 29), Cargo.lock (14 of 14) and Podfile.lock (3 of
3) are all `NULL`. scc's `--exclude-file` default lists
`package-lock.json,Cargo.lock,yarn.lock,pubspec.lock,Podfile.lock,pnpm-lock.yaml`.

## Why scc leaves a file out

1. **It is not a language scc counts.** This covers binaries, archives, images,
   dumps and unknown extensions. It is expected, and it is most of the NULLs.
   scc 3.7.0 has no option to report them; see *scc 4.x* below.
2. **It has no extension and no name scc knows.** scc tries to read it as a
   `#!` script, fails, and drops it. A 3.7.0 run on a two-line `NOEXTFILE`
   reports nothing for it.
3. **scc's default exclusions:** lock files (`--exclude-file`) and ignore files
   (`.gitignore` and `.ignore` are counted only with `--count-ignore`).
4. **scc honours ignore files, and its `.gitignore` matching differs from git's.**
   By default scc applies `.gitignore`, `.ignore` and `.sccignore` to the
   working directory. A committed file matched by any of them is skipped,
   including one listed in a `.sccignore` that the repository itself ships.
   On top of that, scc's matcher disagrees with git's. Reproduced with scc 3.7.0:

   ```
   $ cat .gitignore
   /test*.*
   $ git status --short
   A  .gitignore
   A  b.js
   A  tests/unit/a.js
   $ git check-ignore tests/unit/a.js; echo $?
   1                               # git: not ignored
   $ scc --by-file -f csv | cut -d, -f1,2
   Language,Provider
   JavaScript,b.js                 # scc: tests/unit/a.js is missing
   ```

   In the database this pattern accounts for most of the 5,112 JavaScript
   NULLs. One public repository with this exact root pattern has 4,437
   JavaScript files under `tests/` that scc never reports. With
   `--no-gitignore`, scc reports them. These are not committed build outputs
   that happen to match `.gitignore`: git does not ignore them at all. So the
   gap also covers source files with line counts and complexity, not only size.

Git never ignores a tracked file, whatever `.gitignore` says. kospex's file
list comes from panopticas, so scc's ignore logic should not decide which of
those files get metrics.

Large files are *not* dropped. scc's `--large-byte-count` (1,000,000) and
`--large-line-count` (40,000) apply only with `--no-large`, which kospex does
not pass.

## scc 4.x

scc 4.1.0 (released 2026-09-07) adds options relevant here. From its README:

- `--count-unsupported`: "count files with an unrecognised language under an
  "Unknown" category as plain text". This gives real line counts for
  text files with an unknown extension (reason 1 above, for text files only).
- `--binary`: "disable binary file detection". Don't use this for coverage.
  Binary files would be counted as text, so their `Lines` would be newline
  bytes in binary data, which would distort any line-based metric.
- **`.sccconfig`:** scc 4.x reads default flags from a `./.sccconfig` in the
  working directory, and from `SCC_CONFIG_PATH` if set. kospex runs scc inside
  the repository, so a repository could ship a `.sccconfig` that changes its own
  counts. `--no-config` "disable[s] auto-discovery of the SCC_CONFIG_PATH global
  and the project ./.sccconfig config". If kospex moves to 4.x, it should
  always pass `--no-config`.

Extensionless files (reason 2) are still dropped in 4.x. Users install scc
themselves (Homebrew still ships 3.7.0), so requiring 4.x is a decision for
#230.

## SQLite can hold the sizes

SQLite stores an `INTEGER` as a signed 64-bit value, so the maximum is
9,223,372,036,854,775,807 bytes (~9.2 EB). Tested on SQLite 3.54.0:

| Value | `typeof()` |
|---|---|
| 2,147,483,648 (2 GiB) | `integer` |
| 2,199,023,255,552 (2 TiB) | `integer` |
| 9,223,372,036,854,775,807 | `integer` |
| 9,223,372,036,854,775,808 | `real` (silently becomes floating point) |

The column is already `INTEGER`, and every stored value is typed `integer`
(scc's CSV strings are converted by column affinity). No schema change is
needed for size.

## Where the size should come from: `git ls-tree`

`git ls-tree -r -l HEAD` returns the blob size of every tracked file in one
call. A throwaway repository (git 2.54.0) shows how it handles each awkward case:

```
$ git ls-tree -r -l HEAD
100644 blob 8007d41…       9	.gitignore
100644 blob bafc5d9…       4	a.py           # committed size; a later local edit is not counted
100644 blob 93f4959… 3000000	big.zip        # binary
100644 blob 94954ab…      12	NOEXTFILE      # no extension; scc drops it
100644 blob 587be6b…       2	ignored/i.py   # tracked, though .gitignore matches it
120000 blob 8ce4f79…       4	link.py        # symlink: the size of the target path, not followed
```

This is better than `os.stat()` on the working copy:

- **It works on any scc version, or with none installed.** One subprocess
  per repository replaces a `stat` per file.
- **It measures what was committed,** which is what the rest of
  `file_metadata` describes. Local edits don't change it.
- **Symlinks need no special handling.** Git stores the link and never
  follows it out of the repository.
- **It ignores scc's version, its flags, and the repository's ignore and
  config files.**

Things to handle:

- **Parse with `-z`.** Without it, git quotes paths that contain spaces,
  tabs, newlines or non-ASCII characters, and the parsed path won't match
  panopticas's `Provider`.
- **Submodules** appear as `160000 commit <sha> -`. The size is `-`, so store `NULL`.
- **Untracked files** that panopticas lists (not ignored, not committed) have
  no blob. Fall back to `os.stat(path, follow_symlinks=False).st_size`.
- **`core.autocrlf` checkouts:** the blob size can differ from the size on
  disk on Windows. The blob size is arguably the right one for "what was
  committed".
- **A dirty working copy:** scc counts lines in the working copy, while
  `Bytes` would describe HEAD. That's acceptable, but document it.

## Dependency on panopticas#46

panopticas's file list currently leaves out tracked files that `.gitignore`
matches (kospex/panopticas#46). Such a file gets **no `file_metadata` row
at all**, whatever source the size comes from. `ignored/i.py` above is listed by
`git ls-tree` but not by panopticas. So a large dump committed under an
ignored path stays invisible until #46 is fixed.

Until then, kospex could also add a row for any path that `git ls-tree` lists
and panopticas doesn't. Its type would then come from
`get_filename_metatypes()` on the path alone. Whether to do that or wait for
#46 is a decision for this issue.

## Proposed change

1. **Take `Bytes` from `git ls-tree -r -l -z HEAD` for every tracked file,**
   with an `os.stat` fallback for untracked files and `NULL` for submodules and
   any `OSError`.
2. **Keep scc for `Lines`, `Code`, `Comments`, `Blanks` and `Complexity`.**
   Leave them `NULL` for files scc didn't count. They mean "scc counted this
   file", and filling them in from another source would mix two definitions
   in one column.
3. **Record where the metrics came from.** Set `_metadata_source` (currently
   always `NULL`), for example to `scc` when scc counted the file and `git` or
   `stat` when the row has only a size.
4. **Stop scc dropping files that panopticas lists** (a separate PR). Run scc
   with `--no-gitignore --no-ignore --no-scc-ignore`, adding `--count-ignore`
   and `-n ""` if ignore files and lock files should get line counts. panopticas
   has already decided the file list. This widens the scc scan, so measure sync
   time first.
5. **Optionally, move to scc 4.x** with `--count-unsupported --no-config`.
   Never pass `--binary`. Decide in #230 whether that's a minimum version, or
   detected with graceful degradation.

Items 1 to 3 are self-contained and work on any scc version.

## What this enables

Once every row has a size, a large file is one query, whatever its type:

```sql
SELECT _repo_id, Provider, Language, Bytes
FROM file_metadata
WHERE latest = 1 AND Bytes > 100 * 1024 * 1024
ORDER BY Bytes DESC;
```

panopticas tags the type (archive, binary, dump and so on, as its rules grow;
kospex/panopticas#79). kospex is where size thresholds and "should this be
committed?" policy belong. Whether a "large binary checked in" threshold is a
built-in query or a scoring concern is still open.

## Testing

- `build_file_metadata_rows()`: a file scc didn't report gets `Bytes` from the
  git sizes, and its other scc columns stay `NULL`.
- A file scc did report gets the same `Bytes` from git, and keeps scc's line counts.
- An untracked file falls back to `stat`. A submodule, or a missing file, gives
  `Bytes = NULL` with no exception.
- Paths with spaces and non-ASCII characters match their `Provider` (the `-z`
  parsing).
- A size above 2^31 is stored as `integer`. Stub the git output so the test
  doesn't write gigabytes.
- `_metadata_source` is set as expected.
- If item 4 is done: a fixture repository with a root `/test*.*` ignore
  pattern gets line counts for files under `tests/`.

## Sources

| Source | Used for | Checked |
|---|---|---|
| `scc --help` (scc 3.7.0) | Default `--exclude-file` list, `--count-ignore`, `--no-gitignore`, `--no-ignore`, `--no-scc-ignore`, `--no-large` (off by default) | 2026-10-31 |
| <https://raw.githubusercontent.com/boyter/scc/master/README.md> | 4.x `--count-unsupported`, `--binary`, `--no-config`, `.sccconfig` and `SCC_CONFIG_PATH`. The README describes master, labelled 4.1.0 | 2026-10-31 |
| <https://github.com/boyter/scc/releases/tag/v4.1.0> | Release date (2026-09-07, also from the GitHub API). The page does not describe the flags | 2026-10-31 |
| Throwaway repository repros (above) | scc vs `git check-ignore` on `/test*.*`; scc dropping `NOEXTFILE`; `git ls-tree -l` sizes for binaries, symlinks, tracked-but-ignored files and local edits | 2026-10-31 |
| SQLite 3.54.0, `SELECT typeof(...)` | 64-bit integer storage limit | 2026-10-31 |
| Local kospex database, opened read-only | Row counts above. Counts only; no repository is named | 2026-10-31 |
