# Dependency resolution status — categorise & record why a lookup failed

## Overview

When kospex enriches a dependency via deps.dev, the lookup can come back empty.
Today that is flattened to `versions_behind = "Unknown"` (from `krunner osi`) or
`""` (from `sca`) — a single opaque bucket that also crashed the `/dependencies/`
page (fixed minimally in PR #105 with a template guard). Worse, the *reason* is
lost: an unparseable version spec, a yanked version, a non-existent package, and
a transient API error each need a different follow-up, but they all read the same.

This records **why** each unresolved lookup failed, as a queryable category on
`dependency_data`, plus a disk log — so the reasons can be investigated instead of
guessed.

Builds on PR #105 (the `/dependencies/` template guard). Land #105 first; this
extends that same template.

## Scope

- **In:** a `resolution` category per dependency row (five failure categories +
  `resolved`), `versions_behind` normalised to int-or-NULL, a centralised classifier
  used by both DB-writing paths (`krunner osi`, `assess()`/`sca`), a disk log of
  failures, and a `/dependencies/` badge that shows the category instead of the
  misleading "Up to date".
- **Out:** a summary/filter investigation surface on the page; retry logic for
  transient errors; any scoring/prioritisation of which unknowns matter.

## Data model

Migration `0005` adds `dependency_data.resolution TEXT` (nullable). `versions_behind`
is written as an integer when resolved, `NULL` otherwise (no more `"Unknown"`/`""`).

| `resolution` | meaning | `versions_behind` |
|---|---|---|
| `resolved` | deps.dev knew the exact version | integer (0 = up to date) |
| `no_version` | the dependency had no version to look up | NULL |
| `unresolved_spec` | version isn't a concrete version (range `^1.0`, git URL, `workspace:*`, `latest`, …) | NULL |
| `version_yanked` | package exists on deps.dev but that version doesn't | NULL |
| `package_not_found` | the package itself isn't on deps.dev (typo / private / removed) | NULL |
| `lookup_error` | transient deps.dev failure (5xx / timeout / non-404 error) | NULL |
| `NULL` | legacy row, not yet classified (until re-run) | as-was |

## The classifier (core new unit)

A single function in `src/kospex_dependencies.py`:

```
resolve_version_status(package_type, name, version) -> {"resolution": str, "versions_behind": int | None}
```

Decision tree (short-circuits top to bottom):

1. `version` empty/None → `no_version`.
2. `version` is not a concrete version (a spec/range/git/`workspace:`/`latest`) →
   `unresolved_spec`. Uses a `is_concrete_version(version)` helper (the same
   concrete-vs-spec distinction `clean_version_spec` / `extract_purl` needs).
3. Look up the exact version on deps.dev, **capturing the HTTP status**:
   - `200` → compute `versions_behind` via `get_versions_behind` → `resolved`.
   - `404` → package-level lookup (`deps_dev_package`, cached in `TBL_URL_CACHE`):
     package missing → `package_not_found`; package present → `version_yanked`.
   - anything else (5xx / timeout / exception) → `lookup_error`.

**Enabler:** `deps_dev` currently returns `data` (or `None`), swallowing the status.
Give it a status-aware form (e.g. `deps_dev_status(...) -> (data, status_int)`, with the
existing `deps_dev` kept as a thin wrapper returning just `data` so current callers are
unaffected). The extra package-level call happens only on a 404 and is cached, so
re-runs are cheap.

## Wiring + logging

- **Integrate at the deps.dev-enrichment seam, not per call site.** Both DB-writing
  paths already route their version enrichment through `depsdev_record` /
  `get_versions_behind` (`src/kospex_dependencies.py`). Fold the classifier in there so
  `depsdev_record` returns `{resolution, versions_behind}` (resolution `resolved` +
  int, or a failure category + NULL) instead of an ad-hoc dict that omits
  `versions_behind`. Then:
  - `krunner osi` (`src/krunner.py`) — replace `deps_rec.get("versions_behind",
    "Unknown")` with the record's `resolution` + `versions_behind`.
  - `assess()`/`sca` (`src/kospex_dependencies.py`) — each per-ecosystem assessor
    (`pypi_assess*`, `npm_assess`, `gomod_assess`, `nuget_assess`, pnpm) must route its
    version enrichment through the same classifier rather than setting
    `versions_behind` to `""`/`"Unknown"` itself. Audit each assessor for where it
    computes/defaults `versions_behind` and point it at the seam. (If an assessor's
    path is materially different and can't adopt the seam cleanly in this pass, note it
    and scope it to a follow-up rather than half-wiring it.)
- Both paths write `resolution` alongside the int-or-NULL `versions_behind`.
- Every non-`resolved` outcome is logged to disk via `KospexUtils.get_kospex_logger`
  at INFO, one line: `repo_id` (where available), package_type, name, version, category.
  This is the greppable audit trail (the "log it too" half).
- `save_dependencies` / the upsert path must accept `resolution` (add it to the row
  dict; it's a plain column).

## `/dependencies/` badge

Extend the PR #105 template (`src/templates/dependencies.html`, the versions-behind
cell): if `row['resolution']` is one of the five failure categories → a small
gray/amber badge with a short human label ("unresolved spec", "yanked", "not found",
"no version", "lookup error"); elif `versions_behind is number and > 0` → the existing
orange count; else "Up to date". Unresolved rows stop reading as up-to-date.

## Migration & existing rows

`0005` adds the column (all existing rows get `resolution = NULL`). No risky backfill —
existing `"Unknown"`/`""` rows are reclassified on their next `krunner osi` / `sca`
run, which overwrites `versions_behind` + sets `resolution`. (A `krunner osi -all` after
deploy refreshes everything.) The template treats `NULL` resolution as "fall back to the
numeric/`Up to date` logic", so legacy rows render exactly as they do post-#105 until
re-run.

## Testing

- **Classifier units** (stub the deps.dev status/data): one test per branch —
  `no_version`, `unresolved_spec`, `resolved` (with a count), `version_yanked`,
  `package_not_found`, `lookup_error`. Plus `is_concrete_version` unit tests
  (`1.2.3` concrete; `^1.0`, `>=1,<2`, `workspace:*`, git URL, `latest`, `` not).
- **Template**: render `dependencies.html` with rows for each badge branch → correct
  label shown, no crash (extends the #105 test).
- **Migration**: shipped-`0005` adds `resolution` (mirror the `0003`/`0004` tests).
- **Wiring**: the existing `krunner osi` DB-write / `save_dependencies` tests assert a
  `resolution` value is stored.

## Files anticipated

- `src/kospex/db/migrations/0005_dependency_data_resolution.sql`
- `src/kospex_dependencies.py` — `resolve_version_status`, `is_concrete_version`,
  status-aware deps.dev lookup; `assess()` uses the classifier.
- `src/krunner.py` — `osi` enrichment loop uses the classifier.
- `src/templates/dependencies.html` — resolution badge.
- Tests as above.

## Boundary note

This stops at *categorised, queryable, logged* data + a de-misleading badge — the
plumbing. Deciding which unresolved dependencies matter most (ranking/scoring) is
deliberately out of scope.
