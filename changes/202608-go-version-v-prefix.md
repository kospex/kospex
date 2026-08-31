# Go dependency lookups lost the `v` prefix

## Overview

`clean_version_spec()` strips a leading `v` from a version. That is correct for
npm and pypi, where `v1.2.3` is decoration, and wrong for Go, where the `v` is
part of the canonical module version. Both scan paths routed every ecosystem's
deps.dev lookup through it, so **every Go dependency silently failed its
lookup**.

## Problem

Measured against the live API:

```
/systems/go/packages/github.com%2Fpkg%2Ferrors/versions/v0.9.1  -> HTTP 200
/systems/go/packages/github.com%2Fpkg%2Ferrors/versions/0.9.1   -> HTTP 404
```

And what kospex was sending:

```
clean_version_spec('v0.9.1') = '0.9.1'
```

A 404 is not an error the caller sees — `depsdev_record()` classifies it, so the
row was written with no `advisories`, no `versions_behind`, and a `resolution`
of `package_not_found` or `version_yanked`. Wrong data rather than missing data,
which is worse: nothing looks broken.

### Where it came from

Two commits, both merged, each correct in isolation:

- **#185 (C1)** added `go.mod` to `krunner osi`. `osi` already normalised every
  version through `clean_version_spec()`, so Go inherited the stripping the
  moment it was supported there.
- **#188 (C2a)** unified `assess()`'s five per-ecosystem enrichment paths into
  one. `gomod_assess()` had passed the raw version and was correct; the unified
  path normalises, so `assess()` acquired the same defect.

The irony is that the defect arrived *through* the deduplication work — the old
duplicated code happened to be right for Go, and consolidating onto the shared
path propagated the wrong behaviour to it.

### Why the tests did not catch it

The existing Go coverage called `gomod_assess()` directly, which was not the
code path either scanner used any more. The verification for #185 stubbed
`depsdev_record`, so it proved extraction end to end and never exercised a real
lookup. Extraction was verified; enrichment was assumed.

## Fix

`clean_version_spec()` and `extract_version_from_constraint()` take an optional
`package_type`. A leading `v` is preserved for ecosystems where it is canonical,
listed in `_V_PREFIX_ECOSYSTEMS` — currently `go`.

Putting the knowledge in the helper rather than at each call site is deliberate:
a conditional at two call sites is exactly the kind of duplication that drifts,
and drift between the two scan paths is what sub-project C exists to remove.

`krunner osi`'s enrichment loop is extracted as `enrich_dependency_records()` so
its call site is reachable from a test. It was inline in `osi()`, which needs a
synced database, so the version passed to the lookup could not be asserted
without driving the whole command.

## Files changed

- `src/kospex_dependencies.py` — `_V_PREFIX_ECOSYSTEMS`, `_keeps_v_prefix()`,
  `package_type` threaded through `clean_version_spec` and
  `extract_version_from_constraint`, and passed by `_enrich_dependency_records`
- `src/krunner.py` — `enrich_dependency_records()` extracted, passes
  `package_type`
- `tests/test_go_version_lookup.py` — **new**

## Testing

674 passed, 75 skipped (666 before).

Both scan paths are covered, plus the helper directly. Real lookups after the
fix:

```
go    github.com/pkg/errors    declared='v0.9.1'    sent='v0.9.1'   HTTP 200
go    github.com/spf13/cobra   declared='v1.8.0'    sent='v1.8.0'   HTTP 200
npm   express                  declared='^4.18.0'   sent='4.18.0'   HTTP 200
pypi  requests                 declared='==2.31.0'  sent='2.31.0'   HTTP 200
```

`test_npm_lookup_is_unaffected` passed before the fix and after it, which is the
point — it pins that the ecosystems already working stay working.

## Impact on existing databases

Any Go dependency row written since #185 carries wrong enrichment: no
advisories, no `versions_behind`, and a misleading `resolution`. Re-syncing
affected repos corrects them — `krunner osi` demotes by `(_repo_id, file_path)`
and rewrites.

In the reference estate this is latent rather than damaging: `package_type`
holds only `npm` and `pypi` rows, so no Go rows have been persisted yet. The
175 Go and NuGet rows #185 made possible would have been the first, and they
would have been wrong.

## Still outstanding

A second regression from #188, not fixed here: `clean_version_spec('>=1.0,<2.0')`
returns `'1.0'`, which is concrete, so multi-specifier declarations now attempt a
lookup instead of being classified `unresolved_spec`. That contradicts #108,
whose regression test fails when repointed at `assess()`. Held back so this fix
can ship on its own — see the follow-up for sub-project C2b.
