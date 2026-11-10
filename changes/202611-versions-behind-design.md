# versions_behind: what to count, and how to find the version in use

Status: **#378, #379, #388 and #276 implemented** (see the "Implemented" sections
below); #377 still design only. Pulls together #276 (pre-releases),
#377 (older maintenance lines) and the `version_fuzzy_match` bug (#378), because all three
are fixed in the same ~80 lines of `KospexDependencies.get_versions_behind()` and
the fixes interact.

## Overview

`versions_behind` is meant to answer "how many releases newer than mine could I have
taken?". Today `get_versions_behind()` (`src/kospex_dependencies.py`) takes every
version deps.dev lists for the package, sorts by `publishedAt`, and counts from the
`isDefault` release down to the first release that `version_fuzzy_match()`es the
version in use.

Four things distort that number:

1. **Pre-releases are counted** (#276). deps.dev has no pre-release flag — a version
   record is only `versionKey`, `purl`, `publishedAt`, `isDefault`, `isDeprecated`,
   `deprecatedReason` — so canaries, nightlies, `0.0.0-experimental-<hash>` builds and
   Go pseudo-versions all count as releases you are behind.
2. **Releases on other lines are counted** (#377). Sorting by publish date counts a
   backport to an older line (netty 4.1.x while you are on 4.2.x, `@types/node@20`
   while you are on 22) as newer.
3. **The version in use is sometimes never found** (`version_fuzzy_match`, #378). The
   walk then runs to the end of the list, so the count becomes "every release older
   than the default" — the package's whole history.
4. **A pre-release can be the default.** deps.dev marks e.g. `duckdb@2.0.1.dev…`,
   `httpx@1.0.0.dev6`, `protobuf-java@4.37.0-RC1` as `isDefault`, which moves the
   anchor of the count. About 0.7% of cached packages (npm 72 of ~11k, PyPI 16 of
   ~860, Maven 4 of 20). It can push the anchor *below* the version in use:
   `io.netty:netty-handler`'s default is `5.0.0.Alpha2`, published 2015-03-03, so a
   project on `4.2.18.Final` (2026-09-09) is never reached after the default and reads
   **0** behind instead of 2.

## Measurements

Two sources, both read-only:

* **A local estate DB:** 24,791 distinct (ecosystem, package, version) pairs with a
  stored `versions_behind`, recomputed from the deps.dev package records already in
  `url_cache` (so no network, and the same input kospex used). Version in use =
  `coalesce(nullif(resolved_version, ''), package_version)`, which is what kospex
  passes.
* **A live sample:** 50 popular packages across npm, PyPI, Maven, Cargo, NuGet,
  RubyGems and Go, each measured at the newest stable release published at least a
  year before its default.

Three counts are compared: **current** (today's algorithm), **stable/date** (same
date walk, pre-releases skipped), **stable/ordered** (stable releases with
`in_use < v <= default` by version order — the #377 proposal).

### Estate totals

| ecosystem | pairs | pkgs with pre-releases | current | stable/date | stable/ordered |
|---|---:|---:|---:|---:|---:|
| npm   | 22,763 | 3,465 / 8,571 | 1,787,899 | 545,376 | 533,366 |
| go    |    917 |   571 / 573   |   109,613 |   7,595 |   7,550 |
| pypi  |  1,086 |   228 / 510   |    37,282 |  32,531 |  22,111 |
| nuget |      7 |     6 / 7     |       391 |     307 |     262 |
| maven |     18 |    15 / 18    |        43 |      37 | (not attempted) |

kweb status tiers (`>6` Outdated, `>2` Behind) under **stable/date**: 1,560 of 24,613
pairs move, *all* towards current — 992 Outdated→Behind, 327 Outdated→Current, 241
Behind→Current. Under **stable/ordered** 1,725 improve but 179 get worse (see
"Version order is a decision", below).

Largest npm corrections (current → stable/date):

| package@version in use | current | stable | releases listed / of which pre-release |
|---|---:|---:|---|
| `@typescript-eslint/typescript-estree@2.34.0` | 4,241 | 352 | 4,967 / 4,553 |
| `typescript@2.2.2` | 3,108 | 131 | 3,872 / 3,703 |
| `next@10.0.4` | 2,976 | 283 | 4,017 / 3,593 |
| `react@16.12.0` | 2,730 | 43 | 2,999 / 2,856 |

### Live sample (one year behind default)

| ecosystem | package | % pre-release | current | stable/date | stable/ordered | dominant labels |
|---|---|---:|---:|---:|---:|---|
| npm | react 19.2.0 | 95 | 442 | 26 | 11 | experimental, canary, next, rc |
| npm | next 16.0.1 | 89 | 587 | 89 | 46 | canary |
| npm | typescript 5.8.3 | 96 | 363 | 5 | 5 | dev, insiders |
| npm | lodash 4.17.21 | 3 | 3 | 3 | 3 | — |
| pypi | apache-airflow 3.1.2 | 69 | 49 | 15 | 13 | rc |
| pypi | django 4.2.25 | 13 | 37 | 32 | **71** | a/b, rc |
| pypi | boto3 1.40.67 | 0 | 240 | 240 | 240 | — |
| maven | hibernate-core 7.1.5.Final | 17 | 126 | 115 | — | CR, alpha, beta |
| maven | guava 33.5.0-jre | 17 | 10 | 10 | — | rc (and `-jre`/`-android` twins) |
| cargo | rand 0.9.2 | 24 | 17 | 10 | 7 | pre, rc |
| cargo | tokio 1.47.2 | 3 | 24 | 24 | 24 | — |
| nuget | Serilog 4.3.0 | 69 | 17 | 2 | 2 | dev |
| rubygems | rails 8.1.1 | 33 | 13 | 13 | — | rc, beta |
| go | gin v1.10.0 | 99 | 211 | 3 | 3 | pseudo-versions |
| go | golang.org/x/net v0.46.0 | 98 | 264 | 16 | 16 | pseudo-versions |

By ecosystem:

* **npm** — the worst for volume. Framework teams publish a build per commit:
  `0.0.0-experimental-<hash>-<date>`, `-canary.N`, `-next.N`, `-dev.<date>`,
  `-insiders.<date>`, `-nightly.<date>`.
* **Go** — the worst for proportion. deps.dev lists **pseudo-versions**
  (`v0.0.0-20240829181941-546108da6839`) alongside tags: 315k vs 21k tagged releases
  across the estate.
* **PyPI** — mostly clean; `rc`/`a`/`b`/`.dev` in a minority of packages (airflow, duckdb
  nightlies). Its bigger distortion is maintenance lines (Django LTS) and the
  `version_fuzzy_match` bug. Legacy non-PEP 440 uploads (`0.10.0.win32-py2.7`, 226 of
  pandas' 347 versions) don't parse; they are old and safe to drop.
* **NuGet** — `-preview.N` (Microsoft.Extensions.*, EF Core), `-dev-NNNNN` (Serilog).
* **Maven** — milestones (`7.0.0-M1`, `2.0-m1`), `.CR1`, `-RC1`, `-beta-1`. **Release
  qualifiers must survive**: `.Final`, `.RELEASE`, `.GA`, and *flavour* suffixes like
  Guava's `-jre` / `-android`, which are two artifacts of one release.
* **Cargo** and **RubyGems** — light; mostly `rc`/`beta` near majors.

## Design

### 1. Find the version in use by deps.dev's canonical spelling, not by fuzzing (#378)

By the time `get_versions_behind()` runs, `depsdev_record()` has already done the
exact-version lookup (`/versions/{v}`) and holds its response. That response's
`versionKey.version` is deps.dev's canonical spelling of the version, after applying
**that ecosystem's own equivalence rules**:

| ecosystem | asked for | deps.dev answer |
|---|---|---|
| pypi | `3.0`, `3`, `6` | 200 → `3.0.0`, `3.0.0`, `6.0.0` (PEP 440 zero-padding) |
| pypi | `2026.3.post1` | listed as `2026.3.0.post1` |
| nuget | `13.0.1.0` | 200 → `13.0.1` (NuGet normalisation) |
| nuget | `13` | 404 |
| npm, cargo | `18`, `18.0`, `1.0` | 404 (semver needs `x.y.z`) |
| maven | `4.13` | 200 → `4.13`; `4.13.0` → 404 (coordinates are exact strings) |
| rubygems | `7` | 404, although `Gem::Version("7") == Gem::Version("7.0.0")` |
| go | `v1.10.0` | 200; `1.10.0` and `v1.10` → 404 |

So: pass `deps_info["versionKey"]["version"]` into `get_versions_behind()` and match
the package's version list by **exact string**. kospex then needs no per-ecosystem
equality logic at all — deps.dev already applied it, and where an ecosystem has no
equivalence (Maven, Go, semver) deps.dev doesn't invent one. `version_fuzzy_match()`
has one caller and can be deleted.

**If the canonical version is not in the list, return `versions_behind = None`**, not
the count to the end of the list. That is the honest "unknown" the column already
supports, and it makes any future mismatch visible instead of maximal.

Today's failure, for the record: `version_fuzzy_match` strips leading zeros per segment
with `part.lstrip("0")`, which turns a segment that *is* `0` into `""` (`3.0` → `3.`).
It also has no case for a single segment (`6`), and none for PEP 440 spelling variants
(`.post1` vs `.0.post1`). On the estate that is **164 current PyPI rows, stored total
17,143 vs 5,070** when matched by PEP 440 equality — e.g. `sphinx>=9` 347 → 7,
`setuptools>=60` 703 → 174, `pytz==2026.3.post1` 541 → 2. They are nearly all range
floors (`>=6`, `~=6.0`, `>=0.20,<1`), because `clean_version_spec()` records the floor
as written.

### 2. Classify pre-releases by each ecosystem's own grammar (#276)

#276 proposes a single heuristic, `^\d+(\.\d+)*$`. Measured against the data, that is
wrong in three ecosystems: it drops Maven's `.Final` / `-jre`, Go's `v` prefix, and PEP
440 `.post1` (a release). Use each grammar instead — it is short:

| ecosystem | pre-release when |
|---|---|
| npm, cargo, nuget | the version (before any `+build`) contains `-` after `x.y.z` (semver §9) |
| go | as semver, plus the pseudo-version form `-[0.]YYYYMMDDhhmmss-<12 hex>` |
| pypi | `packaging.version.Version(v).is_prerelease` (covers `.devN`, `aN`, `bN`, `rcN`; keeps `.postN`). Unparseable legacy versions: drop |
| rubygems | any letter in the version (RubyGems' own rule) |
| maven | a qualifier matching `alpha|a\d|beta|b\d|rc|cr|m\d|milestone|snapshot|preview|pre|ea|dev|nightly`; `Final`, `RELEASE`, `GA`, `SP`, `jre`, `android` are releases |

A word list (`canary`, `nightly`, …) is the wrong tool: react alone uses five words,
and the grammar rule catches all of them plus the ones nobody has invented yet.

Rules that go with it:

* **Re-anchor a pre-release default** to the newest stable release, as #276 suggests.
* **Version in use is itself a pre-release** (e.g. a project on
  `react@0.0.0-experimental-…`): still locate it in the list, but report it as such
  rather than as a number — today it reads as `0` behind; by version order it reads as
  143. Neither is true. An `in_prerelease` flag, or `None` plus a reason, is honest.
* **Maven flavours** (Guava `-jre`/`-android`): count only releases with the in-use
  version's flavour, or every Guava release counts twice.

### 3. Version order is a decision, not a bug fix (#377)

Counting by version order fixes the backport case in #377 (netty 4.2.18 → 2, not 4) and
`@types/node@20.x` (815 → 536 on the estate). But it makes some rows *worse*, and the
estate shows why:

* **LTS users** — `django 4.2.25` goes 32 → **71**: all of 5.x counts against them, though
  they are on the current patch of a supported line.
* `@types/react@18.3.31`, `@types/node@24.19.2`: date says 2, version order says 53–59.

The two counts answer different questions — "how many releases have shipped since
mine" vs "how many versions are above mine". The first is what someone updating within
a line wants; the second is what someone planning a major upgrade wants. A clean way out
is to store both dimensions instead of choosing: **releases behind within the in-use
line** (same major, or same major.minor for 0.x) and **majors behind**. That needs a
migration and is beyond this note; the order-based count needs a comparator per
ecosystem (`packaging` for PyPI, semver for npm/cargo/nuget/go, `ComparableVersion`
semantics for Maven, `Gem::Version` for RubyGems), and where none exists the date walk
with pre-releases removed is the safe fallback. Same-timestamp releases (netty 4.2.20 and
4.1.140 on 2026-11-03) make the date walk order-dependent, so a stable tie-break
(version order) is needed regardless.

### 4. Related, not part of this fix: npm/cargo short floors are recorded as yanked (#379)

`clean_version_spec("^19")` returns `19`. In PyPI that is a valid version (`19.0.0`), but
deps.dev 404s `19` for npm and cargo, and `_classify_lookup_miss()` reads a 404 for an
existing package as **`version_yanked`**. On the estate **1,227 of the 1,417 current npm
`version_yanked` rows** are this — `^19`, `^1`, `^3`, `^1.9`, … — none of them yanked.

**What the floor should be.** semver zero-fills a partial version; it never shifts it
right, so `^19` is `>=19.0.0 <20.0.0-0`, not `0.0.19`. node-semver 7.6.3 `minVersion`:
`^19`, `19`, `~19`, `19.x` → `19.0.0`; `^1.9` → `1.9.0`; `^0.9` → `0.9.0` (range
`<0.10.0-0`). Cargo follows the same rule (`"1"` is `^1`).

**Zero-fill alone fixes about 70%.** Checked against each package's deps.dev release list:

| outcome | rows |
|---|---:|
| zero-filled floor (`19.0.0`) is published | 867 |
| zero-filled floor never published (`@playwright/test ^1`: no `1.0.0`, first 1.x is `1.12.0`) | 348 |
| npm alias (`npm:prettier@^2`): the version belongs to another package | 8 |
| nothing satisfies: sentinels like `react: 1000000000000000000` | 4 |

So the floor should be the **lowest published stable version that satisfies the range**
(node-semver `minSatisfying` semantics), taken from the package version list that
`get_versions_behind()` already fetches. That covers all 1,215 non-alias rows with a
satisfying release, and is what `resolved_version` is for: the worst case the constraint
permits. Strip `npm:<name>@` and look up `<name>` for aliases; record a distinct resolution
(e.g. `no_satisfying_version`) rather than `version_yanked` when nothing satisfies.

## Implemented: #378 and #379

**#378.** `depsdev_record()` passes deps.dev's canonical `versionKey.version` from
its exact-version lookup to `get_versions_behind()`, which matches it exactly and
returns `versions_behind = None` when the version is not listed.
`version_fuzzy_match()` is removed. `tests/test_versions_behind_canonical.py`.

**#379.** Both scan paths now call one helper,
`KospexDependencies.lookup_dependency(type, name, declared, bom_version=None)`,
which returns the deps.dev record and the version sent (`resolved_version`):

1. a Maven BOM version, when there is one — already concrete, never re-floored;
2. else `resolve_lookup_version()`: `clean_version_spec()`'s floor, except that for
   an **npm range** (`caret`, `tilde`, `gte`, `bounded`) whose floor is not a
   published version, the lowest published stable version the range permits;
3. a 404 for an npm range is recorded as `no_satisfying_version`, not
   `version_yanked`.

Range evaluation is `src/kospex/npm_range.py`, a ~150-line port of node-semver 7's
desugaring for stable versions only. It is not a dependency because the one Python
port of node-semver had a three-year release gap. It is checked against node-semver's
own verdicts (`tests/data/npm_range_golden.json`, 278 real declarations, one or two
per syntactic shape, none containing `:` or `/`); rebuild with
`tests/manual/build_npm_range_golden.py`. A mutation pass confirmed the golden
catches a caret/tilde mix-up (81 cases), the 0.x caret rule (21), undesugared
operator x-ranges (23) and stable/pre-release ordering (242); the 2**53 limit,
which no corpus range exercises, has its own test.

Left alone on purpose:

* **A floor that is published is kept**, even where a range evaluator would pick
  differently — `^19.0.0-rc.1` stays `19.0.0-rc.1`. So no currently resolving row
  changes.
* **A full-version ceiling** (`<4.0.0` alone) is still looked up at `4.0.0`, the
  ceiling, because `4.0.0` is published. 2 npm rows on the estate.
* **npm aliases** (#350) and **Cargo**, whose range syntax differs (bare `1` is
  `^1`, comma is AND). No Cargo short floors on the estate.

## Implemented: #388 (PyPI ranges)

The same step for PyPI, in `KospexDependencies._range_floor()`, which both
ecosystems now share; `_RANGE_KINDS` lists each one's range kinds. PyPI uses
`packaging.specifiers.SpecifierSet` (already a dependency), not a port:

* a written floor that is a published version the specifier allows is kept,
  as sent today (`>=6` still sends `6`);
* otherwise the lowest version in **deps.dev's** list `SpecifierSet.filter()`
  accepts. deps.dev's, not PyPI's: deps.dev has no numpy 1.0, so `numpy>=1`
  is `1.3.0`. `filter()` applies pip's pre-release rule. A first cut filtered
  every pre-release and turned 6 nightly rows (`keras-nightly~=2.14.0.dev`,
  1,828 releases and none stable) into `no_satisfying_version`; pip installs them;
* entries `packaging` cannot parse (`1.0.1.dev3460.win32-py2.4`) are skipped.

A range nothing satisfies is now `no_satisfying_version` **without a lookup**,
for npm too: looking up `>9999` at `9999` could resolve and return numbers for a
version the range excludes. `resolved_version` is then empty.

Offline A/B over the 580 current PyPI range rows (`url_cache`, no network):
534 unchanged; 21 formerly `version_yanked` now resolve; 3 wildcards formerly
`unresolved_spec` now resolve; 21 formerly resolved at a version the range
excludes (`<2`, `!=1.0`, `>4`) move to one it allows -- for a ceiling-only
range, the first release (`attrs!=21.1.0`: 21.1.0 → 15.0.0), so those rows can
show more versions behind after a re-scan; 1 not evaluated (no cached list).

## Implemented: #276 (pre-releases)

* `src/kospex/prerelease.py`: `is_prerelease(system, version)`, one rule per
  ecosystem as in section 2 above. Checked over the 2.28M versions in a dev
  estate's cache. Reading every lettered Maven version it called stable found
  two misses, now handled: Jackson's `.prN`, and a qualifier straight after a
  digit (`3.2.0rc2`, as Maven's own parser splits).
* `KospexDependencies.release_list(type, name)`: every listed version with
  `published_at`, `is_default`, `is_prerelease`, newest first. Same-moment
  releases are ordered by version (PEP 440, or numeric semver), so the order
  is the same on every run. Undated versions (`""`) sort last, so `list[-1]` is
  not the first release. Shared with #390 (first release date), so both
  agree on what a release is.
* `get_versions_behind()` reads it: it counts releases only, starts at the
  default or at the newest release when the default is a pre-release, and
  returns None when the version in use is a pre-release.

Not done here: the `in_prerelease` flag (would need a column), maintenance
lines (#377; netty-handler 4.2.18 now reads 5, of which three are 4.1.x
backports), and Maven flavour twins (`-jre` / `-android` both count).

A/B, old against new algorithm on the **same** cached deps.dev data:

| ecosystem | total behind, old → new | rows lower | rows higher | now unknown |
|---|---|---:|---:|---:|
| npm | 4,382,556 → 1,097,604 | 13,428 | 0 | 423 |
| go | 169,299 → 9,925 | 909 | 1 | 11 |
| pypi | 16,362 → 14,699 | 204 | 2 | 0 |
| nuget | 391 → 304 | 6 | 0 | 0 |
| maven | 49 → 55 | 2 | 4 | 1 |

kweb status: 3,633 rows improve and 4 get worse. The 7 package versions whose
count rises all re-anchor from a pre-release default that hid newer releases
(google-auth, netty-handler), or are same-timestamp releases now ordered
deterministically (netty-bom, guava-testlib). Comparing the new counts with
the **stored** values instead shows 1,650 increases, but most of those are
releases published since the rows were stored. Compare on the same data.

## Implemented: #414 (Maven/NuGet intervals, npm ceilings)

* `src/kospex/extractors/constraints.py`: `interval_lookup_version(text)` reads
  interval notation: `[1.5]` is a pin, `[1.0,…` an inclusive floor, and
  anything else is "not evaluated". It shares `_match_interval()` with
  `classify_constraint`, a linear scan that replaced a backtracking pattern.
* `KospexDependencies._range_floor()` uses it for `maven` and `nuget` before
  `clean_version_spec`, which also uses it, so it no longer returns fragments
  such as `2.0)`.
* `lookup_dependency()` records `range_not_evaluated`, with nothing looked up,
  when there is no inclusive floor. It also records it when the inclusive
  floor 404s, because the range may still allow later versions.
* Full evaluation needs Maven's `ComparableVersion` ordering. It isn't
  implemented, because the dev estate has no interval rows. Revisit if Gradle
  build files show ranges are common.
* npm: the written floor is kept only if `satisfies()` allows it. Over the
  36,181 npm, Maven and NuGet declarations in a dev DB, compared on the same
  cached data, one row changes: `@types/chai <5.2.0`, from 5.2.0 to 2.0.0.

## Suggested order

1. #378 — canonical-spelling match, `None` when not found. Small, uncontroversial, and the
   other two fixes depend on the version in use being located.
2. #276 — per-ecosystem pre-release classifier; re-anchor a pre-release default; flag an
   in-use pre-release.
3. #377 — decide date vs version order (or both dimensions) once 1 and 2 have landed and
   the remaining difference is only maintenance lines.

All three only change newly enriched rows: stored `versions_behind` stays as it is until
a repo is re-enriched (`krunner osi`). The tests should drive `get_versions_behind()` with
recorded deps.dev package responses (react for canaries, gin for pseudo-versions, duckdb
for a pre-release default, netty for lines, sphinx for a short floor), not re-implement
the classifier.

## Files

* `src/kospex_dependencies.py` — `get_versions_behind()`, `version_fuzzy_match()`,
  `depsdev_record()` (holds the canonical version), `clean_version_spec()`,
  `_classify_lookup_miss()`.
* `src/kweb2.py` — status tiers consume `versions_behind`; no change needed.

## Implementation notes

* The experiment ran entirely from deps.dev package records already in `url_cache`, so it
  can be repeated without network. Three counts were computed for every pair; the
  current-algorithm count reproduced the stored value for 22,060 of 24,791 pairs, and every
  mismatch was the cached record having gained releases since enrichment (current >
  stored in all 2,731 cases).
* `url_cache` holds some package records twice under differently-cased system segments
  (`/systems/NPM/…` and `/systems/npm/…`, `PyPi` and `pypi`). Harmless, but it means two
  fetches for one package.
