# `sca -package`: a Releases table with first and latest

## Overview

`kospex sca -package TYPE:NAME` (#390) showed two *first* releases: the first
stable release and the first version of any kind. They differ only when a
package's earliest versions were pre-releases (react's `0.0.x` builds), so for
most packages, kospex included, it printed the same row twice and never said
what the latest release was.

It now prints a `Releases` table:

| Row | Meaning | Shown |
|---|---|---|
| First | oldest dated release | always |
| First (any) | oldest dated version, pre-releases included | only when it differs from First |
| Latest | `_latest_release()`: deps.dev's default, or the highest release by version if the default is a pre-release | always |
| Latest (any) | newest pre-release published after Latest | only when one exists |

Columns: Release, Version (with `(deprecated)` when deps.dev marks it),
Published (date), Age (`<1d`, `45d`, `11m`, `2y 4m`).

The summary line underneath:

```
44 versions, 0 pre-releases, 0 deprecated. 11 releases in the last 12 months.
```

A deprecated latest release is flagged with deps.dev's `deprecatedReason`.
The undated-versions note from #390 is kept.

`-package` fetches fresh (`cache=0`). It is one request, and with the default
one-day `KOSPEX_URL_CACHE_SECONDS` the cached record missed a release made the
day before (43 versions instead of 44, no 0.4.0).

## Decisions

- **Latest is the registry's default, not the newest by date.** A backport
  (1.2.9 published after 2.0.0) is newer but not the latest. The rule is
  `KospexDependencies._latest_release()`, factored out of
  `get_versions_behind()`, so the version `-package` calls latest is the
  version versions_behind counts from.
- **When the default is a pre-release, the fallback is the highest release
  by version, not the newest by date.** The first build used the newest by
  date, inherited from versions_behind, and showed netty-handler's Latest as
  4.1.141.Final: netty publishes 4.2.x and 4.1.x together, the 4.1 build
  last, and deps.dev's default is a 2015 alpha. The highest is 4.2.21.Final.
  This changes versions_behind as well (see below).
- **Maven and RubyGems get a numeric version key.** `_version_order_key`
  compared them as strings, so 4.10 sorted below 4.9. They now compare by
  their leading dotted numbers, and the qualifier only breaks a tie. This is
  not Maven's ComparableVersion: qualifiers are not ranked, which is enough
  for choosing among releases, where pre-releases are already excluded.

## versions_behind: before and after

Every distinct (type, package, resolved_version) with a stored
versions_behind on the development database was recomputed with `main` and
with this branch. deps.dev records came from `url_cache` only, read-only, so
both runs saw the same data.

- 24,740 triples, all with a cached record. **3 changed, 3 rows, each down by 1:**
  - `maven io.netty:netty-codec-http 4.2.18.Final`: 5 → 4
  - `maven io.netty:netty-handler 4.2.18.Final`: 7 → 6
  - `pypi litellm 1.84.0`: 112 → 111. Its default is `1.107.0.dev3`, its most
    recently published release the 1.103.5 backport, and its highest release
    1.105.1.
- In all three, the backport that had been the starting point is now counted
  in `versions_before_default` instead.
- The numeric Maven key also changes the tie order in `release_list()` for
  Maven versions published at the same moment. It moved no stored value.

The fixture test for netty-handler 4.2.18 moves from 5 to 4. The expected
value was recomputed from the fixture outside kospex. Two of the remaining
four are still 4.1.x backports; counting only the version's own line is
#377.
- **The "(any)" rows are conditional.** A fixed four-row table would repeat
  itself for nearly every package. Each "(any)" row appears only when it says
  something the row above it does not.
- **Facts, not verdicts.** Age, cadence and deprecation are shown; nothing is
  labelled stale or risky.
- **Ages are whole elapsed days**, so a release under 24 hours old is `<1d`,
  not "today": it may carry yesterday's date.

## Files changed

- `src/kospex_dependencies.py`: `release_list()` takes `cache` and adds
  `is_deprecated` / `deprecated_reason` to each entry; new `_latest_release()`
  and `release_summary()`; `first_release()` is now a projection of
  `release_summary()`, with the same keys and values.
- `src/kospex_utils.py`: `age_label(days)`.
- `src/kospex_cli.py`: the Releases table and summary in `_sca_package()`.
- `tests/test_first_release.py`: `TestReleaseSummary`, `TestAgeLabel`,
  `TestScaPackageReleases`; the `run_sca` fixture passes `release_list`'s
  keyword arguments through.
- `tests/test_versions_behind_prerelease.py`: the exact key set of a
  `release_list()` entry gains the two deprecation keys.
- `docs/kospex.md`: "Looking up one package" under `sca`.

## One version: `-package TYPE:NAME@VERSION`

Added in the same change. The version follows the **last** `@`, and a
leading `@` belongs to the name, so `npm:@babel/core@7.0.0` works and
`npm:@babel/core` has no version (`_split_package_version`). A trailing `@` is
a usage error.

- `release_summary(..., version=)` adds `requested` (and `requested_status`)
  from `version_detail()`. It calls the exact-version endpoint
  (`deps_dev_status`, now with `cache`, fresh for `-package`), and counts
  versions_behind with `get_versions_behind(..., releases=)` against the
  **same** release list as the table. Without that argument it would refetch
  the list, and the count and table could disagree.
- deps.dev canonicalises the version (pypi `2.25` → `2.25.0`). The count
  uses its spelling, as `depsdev_record()` does, and the CLI says when it
  differs.
- **Placement.** If the version is already a row (First, Latest, an "(any)"
  row), that row is labelled `(requested)`. Otherwise a `Requested` row goes
  in publish order. The existing rows are already oldest first, and an
  undated version goes before Latest. A release candidate newer than Latest
  sits between Latest and Latest (any).
- **Advisories** come from `/v3alpha/advisories/{id}`, one request each,
  cached at the normal TTL. deps.dev lists a vulnerability under each
  database's ID: requests 2.25.0 has 8 `advisoryKeys` for 4 vulnerabilities
  (GHSA + PYSEC pairs). `advisories()` folds an ID into an earlier one that
  lists it as an alias, and the CLI prints both IDs in one row.
- **CVSS 3 `0` is not a score.** deps.dev returns `cvss3Score: 0` with an
  empty `cvss3Vector` when there is no CVSS 3 score. netty-handler
  4.1.100.Final has two such advisories, and osv.dev rates one of them
  CRITICAL under CVSS 4. The score is None without a vector, shown as `-`
  with a note, and the table keeps deps.dev's order rather than sorting by
  score, which would push those to the bottom.
- An unknown version exits 1 before printing the table ("Give an exact
  version, not a range").

Not done: the stored `dependency_data.advisories` count is
`len(advisoryKeys)`, so it counts the aliases separately (8 for requests
2.25.0). That changes stored data and is a separate change, #454.

## Checked against deps.dev

- `pypi:kospex`: First 0.0.1, Latest 0.4.0, 11 releases in 12 months.
- `npm:react`: Latest 19.3.1 and Latest (any) an `0.0.0-experimental-*` daily
  build. First (any) is not shown, because 0.0.1 is react's first release of
  either kind.
- `npm:request`: all 126 versions deprecated, and the reason printed.
- `pypi:numpy`: 37 undated versions reported.
