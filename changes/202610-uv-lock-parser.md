# A parser for `uv.lock`

## Overview

`uv.lock` was registered (`uv-lock`, `package_type="pypi"`) but had no parser, so
every one reported `unsupported`. It was the recommended next pick in sub-project D
(#229): 24 files across 23 repositories on a development estate, one per repository,
so it reaches more repositories than any other unread format.

The new extractor reads the full locked closure — **2,406 rows from the 24 files**,
which is 2,434 `[[package]]` entries less the 28 that are the projects themselves and
their workspace members — and populates `is_dev`, `is_optional`, `declared_scope` and
`requirements_type` by walking the dependency graph from the project.

## Files changed

- `src/kospex/extractors/uvlock.py` — new; `extract_uv_lock(path)`.
- `src/kospex/extractors/registry.py` — `uv-lock` gains `scanners=("sca", "osi")` and
  its `parse_ref`, so both scan paths dispatch to it.
- `tests/test_extractors_uvlock.py` — new; 46 tests, every raise path on a synthetic
  fixture (every real file is well-formed).
- `tests/test_extractor_registry.py`, `tests/test_osi_outcomes.py`,
  `tests/test_osi_extraction_view.py` — `uv.lock` moves from the "no parser" examples
  to the parsed ones; `pom.xml` takes its place as the no-parser example.
- `docs/dependency-details.md` — a `uv.lock` section under *Supported formats*, the
  format-version table and the byte-order-mark table.
- `docs/dependency-flags.md` — what uv.lock states about install state (nothing per
  package; it is a graph).

## Usage

Nothing new to invoke. `krunner osi` and `kospex sca` pick `uv.lock` up through the
registry:

```bash
krunner osi github.com~acme~service      # uv.lock now reports `extracted`
kospex sca -file path/to/uv.lock
```

## Implementation notes

The decisions, with the measurement behind each, are in `docs/dependency-details.md`.
In short:

- **Error contract.** Unreadable or uninterpretable raises `UnreadableManifest`; `[]`
  only for a lockfile that locks nothing but the project.
- **Gates were checked against uv 0.11.11**, using `uv export --frozen` on perturbed
  copies of a real lockfile. uv refuses `version` ≠ 1 (and `"1"`/`true`), a
  non-integer `revision`, an empty file and UTF-16; the parser refuses the same.
  uv **reads** `revision = 99`, a missing `revision` and a UTF-8 BOM, and so does the
  parser. The unknown-`revision` case deliberately departs from the `package-lock.json`
  precedent of refusing an unknown version field, because here refusing would be a
  false positive against the tool that owns the format.
- **First-party entries** (`source = { editable }` / `{ virtual }`) produce no record,
  agreeing with pnpm's handling of local workspace directories. All 13 version-less
  entries on the estate are first-party, so no version fallback is needed.
- **Git-sourced entries are recorded** (2 on the estate). deps.dev answers by name and
  version, not source: the unpublished one is `package_not_found`, but a git fork of
  `hypercorn` resolved against PyPI's release of the same version, so its advisories
  are upstream's. The source is not stored — a possible follow-up.
- **Install state is validated against uv's own closures, exactly**: keyed by
  `(name, version)`, all 2,406 rows carry the `is_dev` / `is_optional` that uv's
  runtime, `--all-extras` and `--all-groups` exports imply (388 runtime, 430 optional,
  1,446 dev, 142 both).
- **`declared_scope` over-lists on 15 rows** (2,391 match uv's per-extra and per-group
  exports). The walk ignores markers, so it lists a group whose path uv prunes as
  impossible — a Python-version contradiction (13) or a uv `conflicts` declaration
  (2, urllib3). It never under-lists and never changes a flag. Documented rather than
  fixed: marker-aware walking needs an environment enumeration that could under-list.
- The template uses `None`, not `""`, for `advisories` and `versions_behind`, so these
  rows do not join #258.

### Production-path check

Written into a copy of a populated `kospex.db` through `extract_repo` →
`enrich_dependency_records` → `save_dependencies`, the calls `krunner osi` makes:
24 of 24 files `extracted`, 2,406 rows offered, 2,406 distinct primary keys, and
2,406 stored at `latest = 1`. `advisories` / `versions_behind` stored as `integer`
(2,404) or `null` (2), never `''`. `resolution`: 2,404 `resolved`, 1
`package_not_found`, 1 `version_yanked`.

### Not read (drift watch)

`[manifest] requirements` / `dependency-groups` (roots of a workspace whose root is not a
project) and `group = [...]` edges are not followed; packages reached only that way
report NULL install state rather than a wrong one. Neither occurs on the estate.
