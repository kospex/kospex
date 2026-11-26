# Subprocess output that isn't UTF-8

Closes #479. Related: #116 (`core.quotePath=false`), #478 (the TODO scan's
`grep`, same problem with file contents, not fixed here).

## Overview

git prints path bytes as they are with `core.quotePath=false`, and prints
commit metadata that has no encoding header as raw bytes. kospex decoded the
output of every subprocess strictly (`text=True`, or a bare `.decode()`), and
no caller caught `UnicodeDecodeError`. So one filename or author name that
isn't UTF-8, anywhere in a repository's history, failed the whole command for
that repository.

Seen on androidx/androidx: a stray file named with the bytes `C0 6C ED 04` was
added and then removed in 2022 ("Remove stray "Ali" file."). It's the only
invalid path in 92 MB of `git log --name-only` output, and `krunner
file-metadata` failed on it after 16 minutes, rebuilding nothing. Its files kept
their old `tech_type`, so `krunner osi` skipped every Gradle script panopticas
0.0.24 newly tags in that repository.

All subprocess output is now decoded as `encoding="utf-8", errors="replace"`,
from one setting, `KospexUtils.SUBPROCESS_TEXT`.

## Files changed

- `src/kospex_utils.py`: `SUBPROCESS_TEXT`; `run_git_command()` and the `du`
  and `scc` estimate calls use it.
- `src/kospex_core.py`: `_git_log()` (commit sync, merge content mask, closest
  parent counts) and `scc --version` use it. The `scc --by-file` call moves out
  of `file_metadata()` into `scc_metrics_by_file(repo_dir)`, unchanged apart
  from the decoding and an explicit `cwd`, so it can be tested.
- `src/kospex_git.py`: `_last_commit_by_path()` (the walk behind
  `get_repo_files()`), `git branch -r` and both `du` calls.
- `src/kgit.py`: `_git_pull()`'s pull, `rev-parse` and `rev-list`, and
  `git branch --all` in the commented-out `branches` command's function.
- `src/kospex_agent.py`: the background agent's pull.
- `src/kospex_dependencies.py`: `git_out()`.
- `tests/test_non_utf8_git_output.py`: new.

## Decisions

1. **`replace`, not `surrogateescape`.** `commit_files` already holds the
   androidx path with each invalid byte replaced by U+FFFD
   (`"�l�\004"`), so `replace` gives the same string and the paths
   from `get_repo_files()` and commit sync still match. `surrogateescape`
   would round-trip the bytes, but the strings would match none of the
   existing rows, and `sqlite3` refuses to bind a string containing lone
   surrogates.
2. **Every call site, not just the ones that read paths.** `rev-parse`, `du`
   and `scc --version` can't carry such bytes, but one rule is easier to keep
   than a judgement per call, and the guard test can then be a plain search.
3. **A guard test.** `test_no_subprocess_output_is_decoded_strictly` fails on
   any new `text=True`, `universal_newlines=True` or
   `check_output(...).decode()` under `src/`. Allowed: the parked DuckDB proof
   of concept (`src/kospex/git_ingest.py`), and the `krunner` TODO grep, which
   decodes file contents and is #478.
4. **Replaced bytes are lossy.** Two names that differ only in their invalid
   bytes become the same string. That's acceptable for a path that, in
   practice, is a stray file, and it was already true of `commit_files`.
5. **UTF-8 always, whatever the locale.** `text=True` decoded with the
   locale's encoding. On macOS and modern Linux that is UTF-8, so nothing
   changes. Under a Latin-1 locale (`LANG=en_US.ISO-8859-1`) it never raised,
   but decoded UTF-8 names wrongly (`café` as `cafÃ©`), and on Windows it used
   cp1252. git outputs UTF-8, so those names are now decoded correctly, and a
   re-sync on such a machine stores different, correct strings than earlier
   syncs did.

## Tests

The repositories are built with git plumbing (`update-index --cacheinfo`, a
raw commit object for the Latin-1 author), so the name exists only inside git:
macOS refuses to create such a file on disk. Two tests need one on disk and
skip on macOS: `scc` on a current file, and `kgit pull` with
`core.quotePath=false` (without it git quotes the path in pull's diffstat in
ASCII, and the test passed even with strict decoding, so it sets it).

All 9 pass in a `python:3.12-slim` container with scc 3.5.0, and 7 of them on
macOS. With the setting switched to strict decoding, the same container fails
8 of 9; the ninth, the guard, checks the source, not the decoding.

## Not done

- #478, the TODO scan's `grep`: fixed afterwards, see
  `changes/202611-todo-scan-non-utf8.md`.
- The androidx path is still stored quoted in `commit_files`
  (`"�l�\004"`, with the quotes and a literal `\004`) because
  `parse_commit_log` doesn't unquote control characters, while
  `_last_commit_by_path()` does. That predates this change and only affects
  paths containing control characters.
