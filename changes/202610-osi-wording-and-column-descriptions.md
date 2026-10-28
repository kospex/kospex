# /osi/ wording and column descriptions

Issue: [#327](https://github.com/kospex/kospex/issues/327). Builds on
`202610-dependencies-empty-state.md` (#326).

## Overview

Three clarity changes to the /osi/ page. No behaviour or data changes.

1. **Coverage details heading.** The list of files with no parser is now headed
   "Dependencies not extracted, format not currently supported" (was "Not
   extracted -- no parser yet"), so it says the format is what is unsupported.
2. **Fix box for unscanned files.** The inline "run `kospex sca` / `krunner osi`"
   is replaced by the /dependencies/ "To fix, run:" box:

   ```
   krunner osi github.com~androidx~androidx    # sync all supported dependencies
   or
   cd $KOSPEX_CODE/github.com/androidx/androidx
   kospex sca -save <path to dependency file>    # sync a single file
   ```

   `kospex sca` takes one file, not a scope, and reads it relative to the
   clone, hence the `cd`. On a repo page the path is that repo's
   (`parse_repo_id` decodes a nested group's `~~` back to `/`); on org, server
   and all-repo pages it is `$KOSPEX_CODE/SERVER/ORG/REPO`. The `krunner osi`
   line uses the page's scope, or `-all`. Both come from
   `KospexWeb.osi_fix_commands()`, which passes the id through `safe_scope_id()`
   (from #326): the id is from the request path and the commands are pasted
   into a shell, so anything but a plain server/org/repo id becomes a
   placeholder.
3. **"Individual Files" column descriptions**, behind an "explain column
   descriptions" link under the heading: Last Commit, Days Ago, Status (each
   band and its day range) and Extracted? (moved from under the table).

## Status bands

The thresholds are now module constants in `kospex_utils.py`,
`DEV_STATUS_ACTIVE_DAYS` / `_AGING_DAYS` / `_STALE_DAYS` (90 / 180 / 365), used
as `development_status()`'s defaults and passed to the template. A test checks
each stated edge against `development_status()` itself, so the text cannot drift
from the behaviour.

"Unknown" is deliberately not listed: `days_ago()` returns 0 for a missing date,
so on this page a file without one shows as Active, 0 days, never Unknown. No
dependency file in the dev DB lacks a date.

## Not changed

The "N supported files not yet scanned" count above the new fix box still
ignores the recorded extraction outcome (see the follow-up in
`202610-dependencies-empty-state.md`), so for files that were examined and are
`empty` or `parse_error` the box's advice does not help.

## Files changed

- `src/kospex_utils.py` - `DEV_STATUS_*_DAYS` constants.
- `src/kospex_web.py` - `osi_fix_commands()`.
- `src/kweb2.py` - `/osi/` passes `osi_fix_commands()` and `status_days`.
- `src/templates/osi.html` - heading, fix box, column descriptions.
- `tests/test_osi_template.py`
