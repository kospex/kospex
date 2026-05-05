# 20260505 — krunner osi: graceful handling of malformed pyproject.toml

Tracking issue: https://github.com/kospex/kospex/issues/97

## Overview

`krunner osi github.com~pypa~build` crashed midway through dependency parsing
when it hit `tests/packages/test-bad-syntax/pyproject.toml` — a deliberately
malformed TOML fixture used by pypa/build's own test suite. `tomllib.load`
raised `TOMLDecodeError`, the exception propagated out of `parse_pyproject_file`,
and the whole OSI run aborted before any results were written.

This is a direct follow-up to the 0.0.37 `package.json` fix
(`changes/20260420-krunner-osi-all-fix.md`), whose Notes section explicitly
flagged that `parse_pyproject_file` had the same risk if a malformed file
ever surfaced. It now has.

## Repro

```
$ krunner osi github.com~pypa~build
...
Should parse pyproject.toml tests/packages/test-bad-syntax/pyproject.toml
Traceback (most recent call last):
  ...
tomllib.TOMLDecodeError: Unclosed array (at line 2, column 19)
```

The file (`pyproject.toml`):

```toml
[build-system]
requires = ['bad' 'syntax']
```

## Fix

`src/kospex_dependencies.py` — `KospexDependencies.parse_pyproject_file`:

- Wrap `tomllib.load` in `try / except (tomllib.TOMLDecodeError, OSError)`.
  On failure, log a warning (`Skipping malformed pyproject.toml ...`) and
  return `[]` so the caller can continue with the next file.
- Wrap each `Requirement(dep)` call in `try / except InvalidRequirement` so
  a single malformed dependency string in an otherwise-valid pyproject
  doesn't take down the whole file's parse — the bad entry is logged and
  skipped, the rest still come through.
- Added module-level logger `log = KospexUtils.get_kospex_logger("kospex_dependencies")`.

The parser-level wrap is preferred over wrapping at the `krunner osi` caller
(the pattern used for `package.json` in the previous fix), because every
caller of `parse_pyproject_file` now benefits, not just `krunner osi`. The
`package.json` caller-side wrap can be migrated next time that parser is
touched.

### Incidental cleanup in the same function

Removed a stray `print(pyproject)` debug leak that dumped the full parsed
TOML to stdout on every successful parse, and removed two adjacent blocks
of commented-out alternative implementations. Both lived inside the function
being modified and were noise that compounded with this fix's goal of clean
batch runs.

## Files changed

- `src/kospex_dependencies.py` — `parse_pyproject_file`, imports, module logger

## Verification

```python
>>> from kospex_dependencies import KospexDependencies
>>> kd = KospexDependencies()
>>> kd.parse_pyproject_file('.../test-bad-syntax/pyproject.toml')
[]                                 # was: TOMLDecodeError
>>> len(kd.parse_pyproject_file('.../pypa/build/pyproject.toml'))
9                                  # good file still parses
>>> kd.parse_pyproject_file('/no/such/file')
[]                                 # OSError handled too
```

The warning is emitted to `~/kospex/logs/kospex_dependencies.log` (and
console if `--log-console` is set).

## Not fixed here

- `parse_pip_requirements_file` still has the same risk — no repro has
  surfaced yet, and the user's bug was specifically pyproject. Worth doing
  next time it bites.
- The debug `print(pyproject)` removal is the only behaviour change beyond
  error handling; flagged in case a future debug session was leaning on it.
