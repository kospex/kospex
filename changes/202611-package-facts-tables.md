# Package facts tables: store what we look up once per package, not once per row

Status: **plan, not started.** Agreed direction: two shared tables (`packages`,
`package_versions`) with a `source` column and a `purl` from the start, landing as
migration `0014` in a minor release (0.5.0). The offline version functions this builds
on ship first, in a patch release (see `202611-version-diff.md`).

Related: `202611-versions-behind-design.md` (what `versions_behind` counts and why),
`202611-version-diff.md` (`parse_release`, `version_diff`, `change_level`,
`releases_between`).

## Problem

Everything kospex learns about a package from deps.dev is stored **per dependency
row**: `dependency_data.advisories`, `versions_behind`, `published_at`, `default`,
`source_repo` and `last_checked` are written onto every row where a repo uses that
package, each with its own lookup time. The raw responses sit in `url_cache`, keyed by
URL, which is searchable with `json_each` but is an HTTP cache, not a model.

That has three costs, and all three grow with the estate:

1. **Repetition.** The same facts are written once per using row. Across a 1,000 to
   5,000 repo estate, a dependency bump in a popular package rewrites thousands of rows
   with identical values.
2. **Inconsistency.** Two repos on the same version can show different
   `versions_behind` or `advisories`, because each row was checked at a different time.
3. **No package-centric view.** "Where is this package used, at which versions, and
   how far behind is each?" needs the facts in one place. It is the rollup that is hard
   to get out of commercial SCA tools, and it is the first page the new dependencies UI
   wants.

The next feature, a per-level "behind" breakdown (major / minor / patch, the latest
version it was measured against, how far behind on the major in use), would add six
more per-row columns to `dependency_data` and make all three worse.

## Measurements

From a local estate DB of 302 repos, read-only, 2026-11-19:

| | count |
|---|---:|
| `dependency_data` rows, `latest = 1` | 95,128 |
| distinct (ecosystem, package) | 19,030 |
| distinct (ecosystem, package, version) | 36,217 |
| of which `resolution = 'resolved'` | 24,714 |
| deps.dev package listings in `url_cache` | 12,840, 610 MB of JSON |
| releases inside those listings | 2,303,228 (max 26,854 for one package) |

So a (package, version) appears on ~2.6 rows and a package on ~5. Rows grow linearly
with repos; distinct packages grow much more slowly, so the ratio widens with estate
size.

Rows by ecosystem: npm 91,170, go 2,068, pypi 1,611, maven 271, nuget 8.

## Options considered

1. **Package table + used-version table.** One row per package, one row per
   (package, version) that some repo actually uses. ~19k + ~25k rows here. Behind
   counts are computed in Python from the release list when a package is refreshed.
2. **Option 1 + a full release table.** One row per release deps.dev lists, with parsed
   major / minor / patch and a version-order rank, so "behind" becomes a SQL count and a
   package page can show a release timeline from SQL. 2.3M rows here, plausibly ~10M at
   5,000 repos, and the most refresh work.
3. **Source-agnostic facts.** A `source` column on every fact table, a `purl` alongside
   (ecosystem, name), and advisories in their own tables keyed by advisory id, so
   deps.dev, PyPI, OSV and later sources can contribute without a schema change.

**Decision: Option 1, with Option 3's `source` columns and `purl` from the start.**

Option 2 is deferred, not rejected. The package detail page needs only one package's
releases, which can be read from `url_cache` on demand. Add `package_releases` when a
feature needs SQL across every release (release cadence, "abandoned" signals across
the estate).

## Proposed schema (migration 0014)

Columns are added in the migration only, never in `kospex_schema.py` (see CLAUDE.md,
"Adding a Database Migration"). Must run on SQLite 3.26: no `RETURNING`, no `->>`,
no `unixepoch()`.

```sql
CREATE TABLE IF NOT EXISTS packages (
    ecosystem         TEXT NOT NULL,   -- lowercase registry key: npm, pypi, maven, go, nuget, cargo
    name              TEXT NOT NULL,   -- as deps.dev keys it (packageKey.name)
    purl              TEXT,            -- pkg:npm/semver, no version
    latest_version    TEXT,            -- highest stable release (_latest_release), flavour-agnostic
    default_version   TEXT,            -- deps.dev isDefault, may be a pre-release
    release_count     INTEGER,         -- stable releases
    first_release_at  TEXT,
    latest_release_at TEXT,
    is_deprecated     INTEGER,
    source            TEXT NOT NULL,   -- deps.dev | pypi | osv | ...
    status            TEXT,            -- ok | not_found | error
    fetched_at        TEXT NOT NULL,   -- ISO 8601 UTC
    PRIMARY KEY (ecosystem, name)
);

CREATE TABLE IF NOT EXISTS package_versions (
    ecosystem         TEXT NOT NULL,
    name              TEXT NOT NULL,
    version           TEXT NOT NULL,   -- deps.dev canonical (versionKey.version)
    purl              TEXT,            -- pkg:npm/semver@7.5.0
    published_at      TEXT,
    is_default        INTEGER,
    is_deprecated     INTEGER,
    advisory_count    INTEGER,
    source_repo       TEXT,
    -- behind, measured against computed_against (a snapshot, see "Freshness")
    computed_against  TEXT,            -- the latest version of the same flavour
    behind_level      TEXT,            -- major | minor | patch | none | ahead
    behind_major      INTEGER,
    behind_minor      INTEGER,
    behind_patch      INTEGER,
    behind_other      INTEGER,         -- 4th-component-only releases, so the counts sum
    behind_releases   INTEGER,         -- stable releases between, version order
    behind_on_major   INTEGER,         -- newer releases on the major in use
    latest_on_major   TEXT,
    source            TEXT NOT NULL,
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (ecosystem, name, version)
);

CREATE INDEX IF NOT EXISTS idx_package_versions_level
    ON package_versions (behind_level);
```

Advisories (to confirm, see open questions), same migration so the advisory count has
somewhere to come from other than `advisoryKeys.length`:

```sql
CREATE TABLE IF NOT EXISTS advisories (
    advisory_id  TEXT PRIMARY KEY,  -- GHSA-..., PYSEC-..., as deps.dev / OSV key it
    aliases      TEXT,              -- JSON array, e.g. CVE ids
    severity     TEXT,
    summary      TEXT,
    source       TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS package_version_advisories (
    ecosystem    TEXT NOT NULL,
    name         TEXT NOT NULL,
    version      TEXT NOT NULL,
    advisory_id  TEXT NOT NULL,
    source       TEXT NOT NULL,
    PRIMARY KEY (ecosystem, name, version, advisory_id)
);
```

Expect to bump the hard-coded migration counts in `tests/test_db_health.py`,
`tests/test_kospex_utils.py` and `tests/test_kospex_schema.py`.

## Keys and normalisation

* **Ecosystem** is stored lowercase. `url_cache` URLs mix casing (`npm` / `NPM`,
  `pypi` / `PyPi`), so the key must come from the response's `packageKey.system`,
  lowercased, never from the URL as typed.
* **Name** is deps.dev's `packageKey.name`. Whether PyPI names need PEP 503
  normalisation before the join with `dependency_data.package_name` is an open
  question; measure the misses before deciding.
* **Version** is deps.dev's canonical `versionKey.version`, which is what
  `dependency_data.resolved_version` already holds for resolved rows. The join is
  `(lower(package_type), package_name, coalesce(nullif(resolved_version, ''), package_version))`.
* **purl** is derived, not a key. It is the identifier OSV, deps.dev and commercial SCA
  tools all accept, so storing it makes cross-tool matching a string compare.

## How the tables get filled

During `kospex sca` / `krunner osi` enrichment (`get_depsdev_info()` and
`get_versions_behind()` in `src/kospex_dependencies.py`), which already fetches both the
per-version record and the package listing:

1. On a package listing fetch, upsert `packages` (latest, default, counts, dates).
2. On a resolved version, upsert `package_versions`: the per-version record's fields
   plus the behind breakdown from `releases_between()` and `change_level()`.
3. Skip both when the existing row's `fetched_at` is within `KOSPEX_URL_CACHE_SECONDS`,
   so a 5,000-repo sweep computes each (package, version) once rather than once per row.

No new deps.dev calls. The per-row `dependency_data` columns keep being written as now.

## Freshness

`package_versions.behind_*` is a snapshot against `computed_against`, refreshed with the
package. That keeps every repo on the same version showing the same answer, which the
per-row columns do not. The honest "as of" for a fact is `fetched_at + TTL`, as for
`last_checked` today.

## What stays the same

* `dependency_data` keeps `versions_behind`, `advisories`, `published_at` and friends,
  filled as now. The supply-chain graph (`supply_chain.html`, thresholds at 2 and 6)
  and the `krunner osi` CSV keep reading them until they are moved to the new tables.
* `url_cache` stays the HTTP layer. Once the tables exist, the 610 MB of package
  listings are only needed at refresh time; whether to stop keeping them afterwards is
  a later decision.
* The migration only adds tables, so an existing DB that has not run
  `kospex upgrade-db -apply` keeps working; the new UI shows blanks until the next
  enrichment fills the tables.

## UI that this enables

Three levels, in build order:

1. **/dependencies/ (per scope)**: replaces the "Versions Behind" column with
   **Latest** and **Behind** (level pill, per-level counts, "on your major" line).
   Keeps File, Type, Name, Version (with the "checked at" version for ranges),
   Advisories and Released. Adds a Repo column at org / server scope. Optional count
   cards per level. Sorting Behind orders by level, then count.
2. **Packages rollup**: one row per package with repos, files, versions in use,
   advisories and worst level. A `GROUP BY` over `dependency_data` joined to `packages`.
   Here: eslint in 105 repos / 232 files at 85 versions, semver in 75 repos at 31
   versions.
3. **Package detail** (e.g. `/dependencies/package/npm:semver`): every repo, file and
   version in use, with its behind level and advisories.

## Releases

| release | contents |
|---|---|
| patch (0.4.x) | `kospex.dependencies.version_diff` and `releases_between()`; no schema, no UI |
| 0.5.0 | migration 0014, filling the tables during enrichment, /dependencies/ table change |
| later | Packages rollup and package detail pages; move supply-chain graph and CSV to the new tables; OSV as a second advisory source; `package_releases` if needed |

0.5.0 is a minor release because it adds a migration, as 0013 did in 0.4.0.

## Open questions

1. Include the two advisory tables in 0014, or ship `advisory_count` only and add
   them with OSV?
2. Primary key with or without `source`. Without it (as above) one source wins per
   fact; with it, deps.dev and PyPI can disagree side by side but every join must
   pick a source.
3. PyPI name normalisation for the join (measure first).
4. `latest_version` per Maven flavour (`-jre` / `-android`): one row per package
   records the flavour-agnostic latest, and `computed_against` on the version row
   records the same-flavour one. Is that enough, or does `packages` need a flavour?
5. Should unresolved rows (`unresolved_spec`, 18,538 here) get a `package_versions`
   row for the version kospex checked a range at?
