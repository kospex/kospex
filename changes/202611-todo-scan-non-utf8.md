# `krunner todo`: files that aren't UTF-8

Closes #478. Follows #479 (`changes/202611-non-utf8-subprocess-output.md`),
which left this call on the guard test's exempt list.

## Overview

`krunner todo` runs `grep -Rn --exclude-dir=.git TODO .` in each repository.
grep prints the matching lines of file content, and they were decoded strictly
(`text=True`). What happened to a TODO in a Latin-1 file depended on the grep
and the locale:

| grep | locale | grep prints | kospex |
|---|---|---|---|
| macOS `/usr/bin/grep` | any | the line, as raw bytes | `UnicodeDecodeError`, no TODOs for the repo |
| GNU grep 3.11 | `C` or unset (cron, containers) | the line, as raw bytes | `UnicodeDecodeError`, no TODOs for the repo |
| GNU grep 3.11 | UTF-8 | "binary file matches" on stderr, nothing on stdout | no error; that file's TODOs missing |

grep now runs with `LC_ALL=C` and its output is decoded with
`KospexUtils.SUBPROCESS_TEXT`. Under `C` every byte is text to both greps, and
only a file containing NUL bytes is binary, so both print the line, and each
invalid byte becomes U+FFFD.

## Files changed

- `src/krunner.py`: the `todo` grep.
- `tests/test_krunner_todo.py`: a Latin-1 TODO is recorded under no locale,
  `C` and `C.UTF-8`; a file with NUL bytes is still not recorded.
- `tests/test_non_utf8_git_output.py`: the `krunner.py` grep is no longer
  exempt from the strict-decode guard. Only the parked DuckDB proof of concept
  is.

## Decisions

1. **`LC_ALL=C`, not `grep -a`.** `-a` treats every file as text, so a binary
   file containing the bytes `TODO` would be recorded as a junk line. `C` keeps
   grep's binary check, which only NUL bytes trigger, as on macOS. A binary
   file is reported as "Binary file X matches" (stdout, macOS) or on stderr
   (GNU); neither has the `file:line:` shape, so neither is recorded, as before.
2. **Only `LC_ALL` for grep.** The rest of the environment is passed through.
   `LC_ALL=C` changes nothing else grep does here: the pattern is ASCII.

## Tests

On macOS the three Latin-1 cases failed with `UnicodeDecodeError` before the
change. The `C.UTF-8` case is what fails silently with GNU grep, and it's
checked in a `python:3.12-slim` container (GNU grep 3.11).
