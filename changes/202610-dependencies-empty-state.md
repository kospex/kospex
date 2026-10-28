# /dependencies/ explains missing dependency data

Issue: [#326](https://github.com/kospex/kospex/issues/326)

## Overview

A newly synced repository showed an empty dependency table with no explanation.
Sync records file metadata (including which files are dependency files), but
`dependency_data` is only written by `krunner osi`, which runs separately. An empty
table could therefore mean "not analysed yet", "analysed, nothing parseable" or
"genuinely no dependencies", and the page did not say which.

Three additions:

1. **Unsupported-files warning on /dependencies/, on every scope.** Under the page description. Says how many dependency files
   kospex has no parser for yet, most common file names first, and links to Open
   source inventory. Shown whether or not the table has rows, because a repo with
   a few parseable files still renders a table that silently omits the rest.
2. **Empty state.** When there are no rows, a message saying why and the commands
   to run, and the table is not rendered.
3. **/osi/ shows the same warning** under "N dependency files found.", outside
   the coverage details, which start collapsed. Their toggle sits under the
   warning and reads "explain why" when there is something to explain, "show
   details" otherwise. Both pages render the warning from
   `_unsupported_files_warning.html`.

Wording and column descriptions on /osi/ are a separate change:
`202610-osi-wording-and-column-descriptions.md`.

## Diagnosis

Found on a newly synced, large Gradle monorepo (`github.com~androidx~androidx`):

- `file_metadata`: 56,847 latest rows, 996 tagged `|dependencies|`, so sync and
  file metadata completed.
- No `OSI_EXTRACTION` observation and no `osi_runs` row, so `krunner osi` had never
  examined it.
- `dependency_data`: 0 rows, which is why the table was empty.
- Of the 996 dependency files, 991 have no parser (build.gradle 987, yarn.lock 2,
  .nvmrc 2). Running `krunner osi` still yields almost nothing, so the page has to
  say that too, or the user runs the command and sees an empty page again. After
  it runs, the 5 npm files produce rows and the empty state goes away, which is
  why the unsupported warning cannot be part of the empty state.

The signals that tell the cases apart were all already recorded (repos row,
file_metadata, the extraction-outcome observation). Nothing new needed to be stored.

## Unsupported-files warning

`unsupported_dependency_files()` uses the rule behind the /osi/ "no parser yet"
commentary in `osi_extraction_view()`: `classify()` reports no parser, and the kind is not one kospex deliberately does not scan for packages
(`_NOT_A_SOURCE_KINDS`, e.g. `go.sum`, which holds checksums). A test drives both
functions over the same files and asserts the counts agree, so the two pages
cannot drift apart.

Note `yarn.lock` is `Kind.PACKAGE` with no parser yet, so it is counted. The
`lockfile` kind ("Lockfiles" in the /osi/ commentary) is only integrity files such
as `go.sum`, a label that can be misread as "kospex does not scan lockfiles".

With `?file_path=`, the warning narrows to that file.

### Cost

Measured on a 376-repo DB (551,365 latest `file_metadata` rows), best of 3:

| Scope | Dependency files | Query + classify |
|---|---|---|
| repo | 996 | 13 ms |
| org (largest) | 9,032 | 112 ms |
| server / all | ~13,700 | 120-136 ms |

Org and server scopes cannot use the only `file_metadata` index,
`(_repo_id, latest)`, so each is a ~75 ms scan of the whole table regardless of
org size. That is below the page's existing `get_dependencies` query at the same
scopes (50-330 ms). An index on `(_git_server, _git_owner, latest)` would bring
org scope near the repo figure if it is ever needed.

## Empty-state cases (repo scope)

Checked in pipeline order; the first missing step is the answer.

| reason | detected by | action shown |
|---|---|---|
| `not_synced` | no `repos` row | `kgit clone <url>`, `krunner osi <repo_id>` |
| `no_file_metadata` | no latest `file_metadata` rows | `kospex sync-directory <path>`, `krunner osi <repo_id>` |
| `file_not_extracted` | `?file_path=` given | that file's outcome, or `krunner osi` if never examined |
| `no_dependency_files` | never examined, no dependency files | none |
| `osi_not_run` | no `OSI_EXTRACTION` observation | `krunner osi <repo_id>` |
| `nothing_extracted` | examined, no rows | per-outcome counts; `krunner osi` only if synced since examination |

Org, server and all-repo scopes cover repos in mixed states, so their empty state
is a generic message and `krunner osi <scope>` (or `-all`).

## Files changed

- `src/kospex_web.py` - `unsupported_dependency_files()` and
  `dependencies_empty_state()`, pure functions over file metadata rows, the repo
  row and the extraction outcome.
- `src/kweb2.py` - `/dependencies/` fetches the dependency files once per request
  for the warning, and the empty-state inputs only when `data` is empty.
- `src/templates/_unsupported_files_warning.html` - the warning, shared by both pages.
- `src/templates/dependencies.html` - the warning and `#no_dependencies`; table
  and DataTable init skipped when empty.
- `src/templates/osi.html` - the warning above the coverage details; toggle label.
- `tests/test_dependencies_empty_state.py`, `tests/test_osi_template.py`

## Follow-up: /osi/ "not yet scanned" ignores the recorded outcome

Not changed here. The /osi/ coverage details say "N supported files not yet
scanned -- run krunner osi" for every file that has a parser but produced no
rows (`osi_extraction_view()`'s `not_scanned`). It does not consult the
extraction outcome, so files that were examined and declare nothing (`empty`)
or could not be read (`parse_error`) are counted too, and re-running the
command changes nothing. On one 215-file repo examined the week before, all 51
"not yet scanned" files were 49 `empty` and 2 `parse_error`.

This PR deliberately did not promote that line out of the collapsed details,
since it would spread the wrong advice. The fix is to split `not_scanned` by
outcome (never examined / empty / parse_error), as `dependencies_empty_state()`
already does for a repo.
