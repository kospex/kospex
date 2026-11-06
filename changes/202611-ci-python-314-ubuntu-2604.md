# CI: test on Python 3.14 and Ubuntu 26.04's system Python

## Overview

CI previously ran a single job on Python 3.12 only. kospex needs to run on Ubuntu
26.04 (including under WSL), whose system Python is 3.14, so CI now covers it.

## Files changed

- `.github/workflows/python-app.yml`
  - `build` job: matrix over Python `3.12` (the `requires-python` floor), `3.13`
    and `3.14`, via `actions/setup-python`. (`3.13` was added in a follow-up PR.)
  - New `ubuntu-system-python` job: runs in an `ubuntu:26.04` container and uses
    the distro's own `python3` and SQLite, installed into a venv. This is how a user
    on that OS installs kospex: the system interpreter is externally managed
    (PEP 668), so `pip install` needs a venv. `setup-python` ships its own
    separately built interpreter, so the matrix alone does not cover this.

## Verification (local, before the workflow change)

In a `ubuntu:26.04` container (Ubuntu 26.04.1 LTS, Python 3.14.4, SQLite 3.46.1):

- `pytest -m "not web"`: 2091 passed, 27 skipped, 68 deselected, 1 xfailed
- `./test-cli.sh`: OK
- Full suite with a live `kweb`: 2157 passed, 27 skipped, 2 failed. Both failures
  are `/tenure/` returning 500 on an **empty** database. This also happens on
  Python 3.12 and is not related to 3.14: `KospexQuery.get_activity_stats()` passes
  `None` to the date parser when there are no commits. CI does not run the web
  tests, so it does not hit this.

## Notes

- Web tests (`-m web`) stay excluded in CI, as before. They need a live `kweb`.
