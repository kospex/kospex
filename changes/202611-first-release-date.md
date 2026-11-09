# First release date of a package (#390)

## Overview

`kospex sca -package TYPE:NAME` looks up a single package, instead of a
dependency file, and reports when it was first released, using deps.dev's
package-level version list. deps.dev has no "first release" field, so it is the
oldest `publishedAt` in that list.

Two answers are reported, because the oldest version is often a pre-release:

| package | first stable release | first version of any kind |
|---|---|---|
| `maven:io.netty:netty-handler` | 4.0.0.Final, 2013-07-15 | 4.0.0.Alpha1, 2012-07-10 |
| `pypi:requests` | 0.2.0, 2011-02-14 | same |

Nothing is stored: whether the date belongs in `dependency_data` (a migration)
depends on a kweb use case that is not settled yet, so #390 stays open for that.

## Why `sca -package`, not a new command

`sca` is already the deps.dev lookup command, and it had no way to look up one
package: its only input is a dependency file. A `-package` mode adds that
without another top-level command, and gives later per-package facts (default
version, advisories) somewhere to go. First release is the first thing it shows.

## Files changed

- `src/kospex_dependencies.py`: `KospexDependencies.first_release()`, built on
  `release_list()` from #276.
- `src/kospex_cli.py`: `sca -package`, handled by `_sca_package()`.
- `tests/test_first_release.py`: the helper against synthetic listings and real
  deps.dev fixtures (numpy, react), plus `sca -package`.

## Usage

```
kospex sca -package pypi:requests
kospex sca -package maven:io.netty:netty-handler
```

- TYPE is any `package_type` in the extractor registry, in any case: today
  cargo, go, maven, npm, nuget, pypi, rubygems. The list comes from
  `kospex.extractors.registry.package_types()`, not a second copy in the CLI,
  because those values already double as deps.dev system names.
- The name is everything after the **first** colon, so a Maven
  `group:artifact` keeps its own colon.
- Exits 1 when deps.dev has no record of the package, and 2 (a usage error) for
  a malformed spec or an unknown TYPE.
- `FILE_PATH`, `-repo`, `-save`, `-out`, `-json` and an explicit `-scope` are
  refused with `-package`, before deps.dev is called, rather than silently
  ignored.

## Implementation notes

- **One definition of a release.** Pre-releases are classified by
  `kospex.prerelease.is_prerelease()` through `release_list()`, the same list
  `get_versions_behind()` counts, so the two agree. Versions `packaging` cannot
  parse (numpy's `1.0.1.dev3460.win32-py2.4`) count as pre-releases and are
  never the first stable release.
- **Undated versions.** `release_list()` gives a missing `publishedAt` as `""`
  and sorts it last, the oldest position of its newest-first list, so the last
  entry is not the first release. `first_release()` skips undated entries and
  reports how many there were. They can be older than every dated version
  (numpy has 37, including 1.0.1.dev3460), so the date is the earliest *known*
  release.
- **Ties.** Versions published at the same moment are in version order in
  `release_list()`, so the first release of a tie is the lowest version.
- **Removed versions** (npm unpublishes) are not in deps.dev's list at all.
- **npm aliases** (#350): the date is for the name looked up. Until aliases
  are resolved to their target, an alias name gets that name's date.
- **No extra request.** The package URL is the same cached one
  `get_versions_behind()` fetches.
