# Breadcrumbs, per-file drill-down and freshness on /dependencies/ and /osi/

## Overview

Three related web UI changes, one commit each:

1. **Breadcrumbs and scope label.** `/tenure/` and `/dependencies/` carry the
   repo view's breadcrumb, extended with the page name
   (`github.com / pydantic / pydantic / Tenure`), and a label under the
   heading naming the scope: Repository, Organisation, Server, Developer or
   All repositories.
2. **/dependencies/.**
   - Per-file drill-down via `?file_path=`.
   - A "Last checked" line with a stale warning, and the files whose rows
     have no check date, with the reason.
   - An Advisories column, and a fixed-width Released column.
   - The beta banner removed.
3. **/osi/.**
   - The "Dependency extraction coverage" card moved into
     **Dependency Files and Coverage**, behind a show / hide details toggle.
     The page now opens on its title.
   - In Individual Files, an extracted file's name links to that file's
     dependencies.

## Files changed

| File | Change |
|---|---|
| `src/templates/_breadcrumb.html` | New: breadcrumb moved out of `_entity_header.html`, plus `page` and `sub_page` crumbs |
| `src/templates/_scope_label.html` | New: scope type and name under a heading, plus the file when narrowed |
| `src/templates/_entity_header.html` | Includes `_breadcrumb.html`; repo and org views render unchanged |
| `src/templates/tenure.html` | Breadcrumb and scope label |
| `src/templates/dependencies.html` | Breadcrumb and label, file links, Last checked, no-check-date box, Advisories, mono Released, banner removed |
| `src/templates/osi.html` | Coverage folded into Dependency Files and Coverage with toggle; file links |
| `src/kospex_web.py` | `scope_context()`, `dependency_freshness()`, `never_checked_files()` |
| `src/kospex_query.py` | `get_dependencies(file_path=None)` |
| `src/kweb2.py` | Wire the above into `/tenure/` and `/dependencies/` |

## Usage

```
/dependencies/github.com~pydantic~pydantic                         # whole repo
/dependencies/github.com~pydantic~pydantic?file_path=pyproject.toml # one file
/dependencies/github.com~pydantic                                   # org; file links go to each row's own repo
```

## Implementation notes

### Freshness: `dependency_freshness()`

- **Oldest check, not newest.** "Last checked" is the oldest
  `dependency_data.last_checked` among the rows shown, so one stale file
  cannot hide behind fresh ones. The newest is in the hover text when it
  differs.
- **The threshold is 2 days.** That is a plain age check, not a risk score.
- **NULL is "no check date", never "checked long ago"**, per the 0006
  migration contract. These rows also trigger the warning.
- **`last_checked` is written on every save.** That includes a save that
  didn't query deps.dev, but such a save resets the version and advisory
  fields to NULL rather than leaving old values under a fresh date.
- **The date is not when deps.dev answered.** Lookups are cached for up to
  `KOSPEX_URL_CACHE_SECONDS`, so deps.dev may have answered up to that long
  before the date shown.

### Rows with no check date: `never_checked_files()`

These rows are grouped by file. Each is explained by the repo's latest
`OSI_EXTRACTION` outcome, read with `kospex.osi_queue.get_outcome()`. That
outcome can say:

- the file was `empty` or a `parse_error`;
- the file is missing from the latest extraction;
- no outcome was recorded at all.

Only repos that have such rows are looked up.

### Advisories

| Value | Shown as | Sort value |
|---|---|---|
| `> 0` | red count | the count |
| `0` | green `0` (a measured none) | 0 |
| NULL, or a legacy `""` | yellow `Unknown` (deps.dev was not asked) | -1 |

### `file_path` is a FastAPI query parameter

It is declared in the handler signature rather than read from
`request.query_params`. Route tests call the handlers directly with
`request=None`.

## Follow-ups noticed, not fixed here

- **An `empty` extraction outcome does not retire a file's previous rows.**
  pypa/build's `tests/packages/test-invalid-requirements/pyproject.toml` (a
  deliberately invalid fixture) still has a 2026-07-15 junk row at
  `latest=1`, package `requires`. The 2026-10-17 extraction recorded that
  file as `empty`. The no-check-date box now makes this visible. The save
  path has not been confirmed as the cause.
- **The "re-run krunner osi" hint is wrong for rows that should not be in the
  DB at all.** Example: rows saved by a one-off dependency scan of a repo
  that was never synced.
