# advisory-history: advisories per release, and the next clean version

Status: design, awaiting approval. Replaces the disabled stub (#282, #283).
Builds on `changes/202611-osv-integration.md`.

## Why

`kospex sca -package TYPE:NAME@VERSION -osv` answers "what affects this version".
It does not answer the questions that come before an upgrade decision:

- Across the releases people actually run, how many carry a known advisory? In some
  Java packages nearly every release in a major line does, which changes the advice
  from "upgrade a bit" to "change major or change library".
- From the version I run, what is the first release with **no** known advisories?

A fix is not the same thing. Dependabot's `patched_version` and OSV's `fixed` event
say the *one advisory* is fixed there. That release can still carry others.

**Definition.** In `kospex advisory-history`, "clean" means OSV lists no
non-withdrawn advisory for that release, at any severity. It is not a claim that the
release is safe: it says nothing about vulnerabilities nobody has reported, or
advisories OSV does not carry. The CLI output, `-json` and docs say "no known
advisories" for this state, and use the field name `next_clean` only because it is
short.

## Goals

- `kospex advisory-history -package TYPE:NAME[@VERSION]` shows a window of recent
  stable releases with their advisories, a percentage summary, and the next clean
  version from the starting point.
- Cheap: about three HTTP calls however many releases are covered, so the summary
  can cover every stable release.
- Honest: unknown is never zero, and anything excluded is counted.

## Non-goals

- No DB writes (`sca -package` doesn't write either).
- No estate or repo scope (that is the phase-2 estate work in the OSV doc).
- No scoring or risk weighting. Severity is shown, not summed. Methodology belongs
  outside this repo.
- No purl argument in the first cut (see calls below).

## Anti-goals

- A "100% clean" reading when OSV simply did not answer.
- Calling a patched release clean because one advisory names it as a fix.
- Version ordering by publish date. Backports (2.17.1 after 2.18.0) make that wrong.

## Approaches considered

1. **deps.dev per-version advisory counts** (the design in #282).
   One call per version, about 21 for a window, hundreds for a major. deps.dev
   only, no fixed versions. Rejected: too slow for the all-releases summary.
2. **OSV `querybatch`** (chosen). deps.dev supplies the release list; one OSV batch
   call returns advisory IDs for every version; `vuln()` is called once per distinct
   ID (url_cache shares those). Constant call count.
3. **OSV per-package query, match `affected` ranges locally.** Needs a version
   comparator per ecosystem and Go's `versions[]` is empty. Rejected: fails unsafe.

## Behaviour

```
kospex advisory-history -package maven:org.apache.logging.log4j:log4j-core
kospex advisory-history -package pypi:requests@2.25.0 -limit 10
kospex advisory-history -package npm:@babel/core -pre -json
kospex advisory-history -ecosystem npm -package @babel/core     # old form still works
```

- **Input:** `-package TYPE:NAME[@VERSION]`, parsed exactly as `sca -package` does
  (`_split_package_version`, `registry.package_types()`). `-ecosystem E -package NAME`
  stays accepted so existing invocations keep working. Private packages are refused
  by `lookup_permitted()` as in `sca`.
- **Window:** starts at `@VERSION` if given, else the latest release
  (`_latest_release()`, the rule `sca` uses). Goes back `-limit` releases (default 20),
  ordered by **version number** (`_version_order_key`), not publish date. Pre-releases
  and Go pseudo-versions are excluded unless `-pre`; the excluded count is printed.
  A requested `@VERSION` that is itself a pre-release is shown regardless.
- **Table:** version, published date, advisory count, worst severity, notes
  (`current`, `latest`, `deprecated`, `pre-release`). Unknown shows `?`.
- **Summary** (stable releases only unless `-pre`), over **all** releases, not just
  the window:
  `142 of 160 releases (89%) have a known advisory`, then one line per major:
  `2.x  31/31 (100%)   3.x  4/9 (44%)`. Major = first version segment.
  Releases whose lookup failed are reported separately and excluded from the
  percentage.
- **Next clean:** the first release **newer by version number** than the start, stable,
  not deprecated, with zero non-withdrawn advisories. Printed under the table with its
  date, and what it skips. If none, say so (`no clean release newer than X`). If the
  starting version is itself clean, say that instead. Unknown-lookup releases are
  skipped over and named, never treated as clean.
- **`-json`:** stdout only. Top level: `package`, `window`, `summary` (totals,
  per-major, unknown count, excluded pre-releases), `next_clean`, `sources` (deps.dev
  lookup URL, OSV batch endpoint). Per version: `version`, `published_at`,
  `advisory_count` (`null` when unknown), `advisory_ids`, `worst_severity`,
  `is_current`, `is_deprecated`, `is_prerelease`.
- **Withdrawn** advisories are not counted, matching `sca -osv`.

## Implementation

- `OsvClient.querybatch(ecosystem, name, versions)`: POST `/querybatch`, chunks of 1000
  queries, follows `next_page_token` per query, same retry and pacing as `_request`.
  Returns `{version: [ids] | None}`, `None` for a version OSV failed on.
- `integrations/osv/queries.py`: pure functions for the per-release rows, major
  grouping, summary and `next_clean`, all unit-testable without network.
- `kospex_cli.py`: replace the stub body and `ADVISORY_HISTORY_NOTICE`; keep the
  command name and `-ecosystem`/`-package` options.
- Release list from `KospexDependencies.release_list()` (already used by `sca`).
- Severity for the table reuses `queries.severity()` over the fetched `vuln()` records.

## Calls made without asking (change them if wrong)

- Purl input is deferred; `TYPE:NAME` matches `sca`, and it avoids the `extract_purl`
  rewrite. #282's purl task stays open as a follow-up.
- #244 (`extract_purl` mishandles namespaced purls) is separate. The redesigned command
  never parses a purl, so it stops crashing, but `extract_purl` stays wrong and has no
  caller. This change does not close #244: fix it in its own PR, or delete the function
  if purl input is dropped for good.
- Default window 20, pre-releases excluded, `-pre` to include.
- Clean means zero non-withdrawn OSV advisories, any severity.
- Summary covers all releases; the table covers the window.
- Network failure partway: whatever OSV did not answer is `?`; if the whole batch
  fails the command exits 1 with the error rather than printing a table of unknowns.

## Left to the builder

Table column widths and wording, the exact `-json` key order, chunking when a
package has more than 1000 releases, and the Go v-prefix mapping between deps.dev
and OSV (noted in `fixed_versions()`).

## Docs and tests to update

Remove `tests/test_advisory_history_stub.py` and replace with tests over fixture
responses (scoped npm, Maven `group:artifact`, Go, a package where every release in
a major is affected, unknown lookups, no clean release, backport ordering). Add a
`## advisory-history` section to `docs/kospex.md` and link from `docs/commands.md`.
CHANGELOG entry under Unreleased replacing the "disabled" note. Update the OSV doc's
"separate command" line to point here.
