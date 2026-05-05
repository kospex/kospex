# 20260420 — krunner osi -all crash fix

## Overview

`krunner osi -all` printed a spurious error on startup and then crashed midway through dependency parsing when it encountered a malformed `package.json` test fixture. Two independent bugs were in play — one cosmetic, one fatal — fixed together so `-all` runs cleanly to completion and writes `OSI-all.csv`.

## Bugs

### 1. Spurious `ERROR: can't identify {'request_id': None}`

`KospexQuery.get_repos(**kwargs)` passed `kwargs` (e.g. `{'request_id': None}` when `-all` is used with no `request_id`) directly as the `id_params` argument to `KospexData.set_params_by_id`. That method expects `id_params` to look like `{'repo_id': ...}`, `{'org_key': ...}`, or `{'server': ...}` — a dict keyed on `request_id` doesn't match any branch, so it fell through to the `can't identify` error print. The query still ran (no WHERE clauses added → all repos returned), so the error was misleading noise, not a functional failure.

### 2. `JSONDecodeError` aborts the whole run

`kdeps.parse_package_json(file_path=...)` raises `json.JSONDecodeError` on malformed JSON. When the babel test fixture `packages/babel-core/test/fixtures/config/config-files/pkg-error/package.json` (deliberately malformed — `{ "babel": {235 }`) was encountered, the exception propagated out of the `osi` command and terminated the entire `-all` run before producing `OSI-all.csv`.

## Fix

### `src/kospex_query.py`

- `KospexQuery.get_repos` (line ~1574): pop `request_id` out of `kwargs` and pass it as the named `request_id=` argument to `set_params_by_id`, rather than letting it sit in the `id_params` dict where it doesn't match any filter branch.
- `KospexData.set_params_by_id` (line ~2506): stop printing `ERROR: no id_params or request_id provided` when both are empty — that is the legitimate "all scope" case. Also silently return when `id_params` is present but all values are `None`/empty (same case, different shape).

### `src/krunner.py`

- `osi` command (line ~633): wrap `kdeps.parse_package_json(file_path=full_path)` in `try / except (json.JSONDecodeError, OSError)` so malformed or unreadable dependency files are logged (`Skipping malformed package.json ...`) and skipped, rather than aborting the run.
- Added `import json` at module top.

## Files changed

- `src/kospex_query.py` — `get_repos`, `set_params_by_id`
- `src/krunner.py` — `osi` command, imports

## Verification

Before fix:

```
$ krunner osi -all
ERROR: can't identify {'request_id': None}
...
Should parse package.json packages/babel-core/test/fixtures/config/config-files/pkg-error/package.json
Traceback (most recent call last):
  ...
json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: line 2 column 13 (char 14)
```

After fix:

```
$ krunner osi -all
...
Skipping malformed package.json <path>: Expecting property name enclosed in double quotes: ...
...
Writing dependencies to /Users/.../kospex/assessments/OSI-all.csv
```

Exit code 0; `OSI-all.csv` is written.

## Notes

- The same `JSONDecodeError` risk exists for other dependency parsers (`parse_pip_requirements_file`, `parse_pyproject_file`) if they ever encounter a malformed file — not fixed here because they're less likely to be exercised on test-fixture files, and no repro was observed. Worth revisiting if it surfaces.
- `KospexQuery.get_repos` previously only honoured `request_id` via this buggy path; the fix makes `get_repos(request_id="github.com~org")` actually scope the query as the argument name implies.
