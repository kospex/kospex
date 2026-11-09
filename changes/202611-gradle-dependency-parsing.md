# Gradle: reading `build.gradle(.kts)` and the version catalog

The last open part of #229. Follows the pom parser (`202611-pom-xml-parser.md`)
and its BOM lookup (`202611-maven-bom-version-resolution.md`), whose rules this
reuses. **Status: design, not started. The three decisions below were made on 2026-11-09.**

## Problem

Gradle is now the largest dependency format kospex classifies but can't read.
The registry has three entries for it, all with no parser: `gradle-build`
(`build.gradle`, `build.gradle.kts`), `gradle-version-catalog`
(`libs.versions.toml`) and `gradle-lockfile`. Every Gradle project reports
`unsupported`, with no dependency rows.

Measured on 2026-11-09, on every repository cloned under `~/code`:

| file | count |
|---|---|
| `build.gradle` | 1,067 |
| `build.gradle.kts` | 428 |
| `libs.versions.toml` (and other `*.versions.toml`) | 18 (20) |
| `settings.gradle(.kts)` | 79 |
| `gradle.lockfile` | 1 |
| `pom.xml`, for comparison | 14 |

The build files are spread across 15 repositories, but most are in two:
androidx (993) and kotlin (204).

## Why not run Gradle

Running Gradle (`gradle dependencies`) is the only way to get the complete
answer. That rules it out: a build script is a Groovy or Kotlin program, so
asking Gradle executes the repository's code, needs a JDK and the right Gradle
version, and downloads from the network. A scanner pointed at arbitrary
repositories can't do that. The parser reads files; it does not evaluate them.

So, as `docs/dependency-details.md` already warns, this is the format where
"parsed it" and "read what it declares" come apart most. The design has to make
that gap visible, not hide it.

## How the build files declare dependencies

Every dependency-configuration line in the 1,495 build files, classified
(`implementation(...)`, `api`, `testImplementation`, `kapt`, `classpath`, ...):

| form | example | count | readable statically |
|---|---|---|---|
| catalog reference | `implementation(libs.okhttp)` | 6,616 | yes, via the catalog |
| project | `implementation(project(":core"))` | 4,387 | first-party, no row |
| literal coordinate | `"com.squareup:javapoet:1.13.0"` | 3,150 | yes (2,900+ of them in androidx) |
| literal with a variable | `"g:a:$kotlinVersion"` | 152 | sometimes (`ext`, `gradle.properties`) |
| platform (BOM) | `platform(libs.compose.bom)` | 62 | yes, via the BOM lookup |
| literal, no version | `"g:a"` | 39 | only if a platform supplies it |
| other | | 1,842 | mostly no, see below |

"Other" is smaller than it looks. About 770 are not dependencies at all: the
count's pattern for `compile` also matched `compileSdk` and `compilerOptions`
lines. The remainder, by size:

- **Type-safe project accessors** (`projects.retrofit`, 332) and
  `testFixtures(project(...))` (179): first-party, no row.
- **Gradle helpers** that resolve to a known coordinate: `kotlin("test")`
  (`org.jetbrains.kotlin:kotlin-test`, at the Kotlin plugin's version),
  `gradleTestKit()`, `gradleApi()`.
- **Local files**: `files(...)`, `fileTree(...)`. Not packages.
- **Repository-specific helpers**: `intellijCore()`, `commonDependency(...)`,
  `androidx.projectOrArtifact(...)`. Almost all in the kotlin and androidx
  repositories. Unreadable without running the build.

## The version catalog

A catalog is TOML, so it parses deterministically. Gradle imports
`gradle/libs.versions.toml` automatically as the accessor `libs`. Other
catalogs are declared in `settings.gradle(.kts)` under
`dependencyResolutionManagement { versionCatalogs { create("x") { from(files(...)) } } }`
(12 settings files do; besides `libs`, there's one `firebaseLibs` and one
`importMavenLibs`).

The catalog sections are `[versions]`, `[libraries]`, `[bundles]` (named groups
of libraries) and `[plugins]`. On disk there are 1,170 libraries in 20 catalogs,
written as:

| form | count |
|---|---|
| `{ module = "g:a", version.ref = "x" }` | 687 |
| `{ module = "g:a", version = "1.2" }` | 211 |
| `{ group = "g", name = "a", version.ref = "x" }` | 124 |
| `"g:a:1.2"` | 110 |
| no version (supplied by a platform/BOM) | 27 |
| `{ group, name, version = "1.2" }` | 9 |
| `version = { strictly = "..." }` | 2 |

All 504 `[versions]` entries are plain strings. Gradle also allows rich
versions there (`{ strictly, require, prefer, reject }`), so the parser must
accept them.

**How a build file names an entry:** the alias's `-`, `_` and `.` separators
all become `.` in the accessor, so `androidx-core-ktx` is `libs.androidx.core.ktx`,
and `libs.bundles.x` is the bundle `x`.

**88% of catalog libraries are used** by a build file in the same repository
(1,039 of 1,170). That's a lower bound: the count only searched `.gradle` and
`.gradle.kts` files, and convention plugins in `buildSrc/` or `build-logic/`
(`.kt` files) also use the catalog. The clearest unused catalog is androidx's
`importMaven.versions.toml` (9 libraries, 0 used), which feeds a tool rather
than the build.

## deps.dev covers Gradle's repositories

Gradle rows are `package_type="maven"`, and deps.dev's Maven system covers
more than Maven Central. Checked 2026-11-09, all answered 200: `androidx.core:core-ktx`,
`androidx.compose.ui:ui` and `com.google.firebase:firebase-common` (Google's
Maven repository), `com.android.tools.build:gradle` (the Android Gradle
plugin), and the plugin marker artifact
`org.jetbrains.kotlin.jvm:org.jetbrains.kotlin.jvm.gradle.plugin` (the Gradle
plugin portal).

Two of those show the #377 problem with deps.dev's default version: the Android
Gradle plugin's default is `2.3.0` (from 2017) and Compose UI's is a beta.
`versions_behind` for them will be wrong until #377 and #276 are fixed. Not this
design's problem, but it will be visible as soon as Gradle rows exist.

## Proposed design

A pure extractor, `src/kospex/extractors/gradle.py`, following `maven.py`: no
DB, no network, standard library only (`tomllib`, `re`). Registered for both
`sca` and `osi`, `package_type="maven"`.

**Catalog reader.** Parses a `*.versions.toml` into
`{accessor: (group, artifact, version, version_kind)}`, expanding `version.ref`
and bundles. Finds catalogs the way Gradle does:
`<repo root>/gradle/libs.versions.toml` as `libs`, plus any `versionCatalogs`
entry in the root `settings.gradle(.kts)` whose `from(files("..."))` is a literal
path inside the repository. A catalog outside the repository, or one published
as an artifact (`from("g:a:v")`), is not read. The settings file is matched with
a regular expression, not evaluated.

**Build-file reader.** Scans each dependency-configuration call and
classifies its argument:

- **Literal coordinate** `"g:a:v"` (or Groovy `group: 'g', name: 'a', version: 'v'`):
  a row. `$var` / `${var}` is filled from `gradle.properties` and simple
  `ext { x = "..." }` / `val x = "..."` assignments in the same file. Otherwise
  the version is left as written and lookup reports `unresolved_spec`, as for
  `${revision}` in a pom.
- **Catalog reference** `libs.x` or `libs.bundles.x`: a row for each library,
  with the catalog's version.
- **`platform(...)` / `enforcedPlatform(...)`**: a `bom` row, so the existing
  BOM lookup in enrichment fills versionless rows in the same file. This reuses
  `maven_bom_versions` unchanged.
- **`project(...)`, `projects.x`, `testFixtures(project(...))`**: first-party,
  no row.
- **`kotlin("x")`**: a row for `org.jetbrains.kotlin:kotlin-x`, with no version.
  The Kotlin plugin supplies it.
- **`files(...)`, `fileTree(...)`, `gradleApi()`, `gradleTestKit()`, `localGroovy()`**:
  not packages, no row.
- **Anything else**: counted as unreadable (see decision 3) and logged with the
  file and line.

**Scope.** `declared_scope` gets the configuration name as written
(`testImplementation`, `kapt`, ...), raw per migration 0009. `is_dev` is True for
configurations that only build or run tests: those starting `test`,
`androidTest`, `testFixtures`, and their `debug*`/`release*`-prefixed variants
(`debugTestImplementation`). `compileOnly`, `kapt`, `ksp` and
`annotationProcessor` are build-time but not tests. Following the pom
parser's decision on `provided`, they are not marked dev; that matches
`docs/dependency-flags.md`.

**`buildscript { dependencies { classpath(...) } }` and `[plugins]`**: build
tooling, not the product. Recorded with `declared_scope="classpath"` or
`"plugin"` and `is_dev=True`. A plugin `id("x") version "1.2"` becomes its marker
artifact `x:x.gradle.plugin`, which deps.dev knows.

**Error contract** (from `docs/dependency-details.md`): a build file or catalog
that can't be read, or is not valid TOML, raises `UnreadableManifest`. A build
file with no `dependencies { }` block returns `[]`, recorded as `empty`.

**Lockfiles.** `gradle.lockfile` is exact and easy, but there's one on disk.
Out of scope; it can be added when one matters.

## Decisions needed

**1. What does a catalog library produce?** **Decided 2026-11-09: (c), with a new
`package_use` value `version-catalog`.** The catalog is where the version a
build uses is set, so its libraries must get advisories even before build files
are read. Details under "Rows" below. The options as weighed:

- **(a) A row in the catalog file.** One row per `[libraries]` entry, with
  `file_path` = `gradle/libs.versions.toml`. Build files produce rows only for
  literal coordinates. This is simplest, and could ship on its own as phase 1,
  about a day. The catch: about 12% of entries (an upper bound) are unused but
  would still count as dependencies. Also, a catalog doesn't say whether a library
  is for tests, so `is_dev` would be unknown for every catalog row.
- **(b) A row only where a build file uses it.** `implementation(libs.okhttp)`
  in `app/build.gradle.kts` is the row, with the catalog's version. The
  catalog itself has no rows. This is how the pom parser treats
  `<dependencyManagement>`: a managed version is a source of versions, not a
  dependency, and the child's row carries it. It gets scope right (a
  `testImplementation` use is dev) and per-module usage right. But nothing ships
  until build-file parsing exists, and a library used in 40 modules is 40 rows
  (androidx: 5,168 catalog references).
- **(c) Both.** Catalog rows *and* usage rows. Counts per repository double
  unless the catalog rows get their own `package_use` (as `bom` did).

(a) has about five times fewer rows than (b): 1,170 catalog libraries against
at least 6,616 uses in build files.

(c) with its own `package_use` lets the catalog ship first (phase 1), and
build-file rows are added later (phase 2) without moving any existing row.
`bom` was considered as the label and rejected: `maven_bom_versions` treats every
`bom` row in a file as a BOM to fetch from deps.dev. A catalog with an
unversioned entry (27 on disk) would then send a request for each of its
libraries, as if each were a BOM. It would also mix catalog libraries into
`kospex sca -scope bom`. `version-catalog`, not `catalog`, because `version_kind`
already has a value `catalog` (pnpm's `catalog:` versions), in a different
column.

### Rows

**`gradle/libs.versions.toml`** (and any other catalog): one row per
`[libraries]` entry, `package_use="version-catalog"`, `is_dev` None (a catalog
doesn't say whether a library is for tests), checked for advisories like any
row. It answers *where the version is set*, which is where it would be fixed.
`[plugins]` entries become their marker artifact (`id:id.gradle.plugin`), with
the same `package_use`.

**`build.gradle` and `build.gradle.kts`**: the same file in two languages, Groovy
(the original) and Kotlin (Gradle's default for new builds since 8.2). A module
has one or the other: 1,067 and 428 on disk. Rows follow the pom parser's
pattern, where `package_use` says how a package got into the project and
`is_dev` says whether it's test-only:

| declaration | `package_use` | `is_dev` | `declared_scope` |
|---|---|---|---|
| `implementation("g:a:1.2")`, `api(...)`, `runtimeOnly(...)` | `direct` | False | the configuration name |
| `implementation(libs.okhttp)` | `direct` | False | `implementation` |
| `testImplementation(...)`, `androidTestImplementation(...)`, `testFixtures*` | `direct` | True | as written |
| `compileOnly`, `kapt`, `ksp`, `annotationProcessor` | `direct` | False, as Maven's `provided` | as written |
| `buildscript { classpath(...) }`, `plugins { id(...) version "..." }` | `direct` | True (build tooling, not the product) | `classpath` / `plugin` |
| `platform(...)`, `enforcedPlatform(...)` | `bom` | | `platform` / `enforcedPlatform` |
| `project(":core")`, `projects.core`, `testFixtures(project(...))` | no row | | |
| `files(...)`, `fileTree(...)` | no row | | |

A row from a catalog reference takes the catalog's version into its
`package_version`, as a pom child takes its parent's managed version. So okhttp
in a catalog project has two rows with the same version and the same advisories.
`gradle/libs.versions.toml` answers where the version is set;
`app/build.gradle.kts` answers which module uses it and whether only for tests.
Counts of `direct` don't double, because catalog rows have their own
`package_use`, as `bom` rows do.

**No row for `project(...)`**: it depends on another module in the same
repository. Its source is there, not a published package, and nothing can be
looked up. That module's own build file is parsed separately, so what it
depends on is recorded there. Recording first-party links is a possible later
addition, alongside marking Maven sibling modules first-party.

**No row for `files(...)` / `fileTree(...)`**: a jar checked into the
repository (35 such declarations on disk). It has no coordinates, so it can't
be looked up. It is a risk, though: a vendored jar of unknown version is invisible
to advisory checks. So each one is logged with its file and line rather than
skipped silently. Whether to record them as rows is a later decision.

**Phase 1 caveat**: until the build-file reader lands, a catalog project has
only `version-catalog` rows. Advisories work from day one, but its count of
`direct` dependencies reads zero until phase 2.

**2. What `version_kind` is a bare Gradle version?** **Decided 2026-11-09: `gte`**, because
that's how Gradle resolves it.

`implementation("g:a:1.2.3")` is, in Gradle's terms, a `require` version: the
selected version "cannot be lower … but could be higher through conflict
resolution". That's a floor, like Go and NuGet, so `gte`, not `pinned`. Maven
reads the same string as a soft `pinned` requirement, and `constraints.py`
classifies by `ecosystem`, which is `maven` for both. Options: classify Gradle
bare versions as `gte` (needs a Gradle-specific path into `constraints.py`), or
keep `pinned` to match Maven and document that it overstates how fixed the
version is. Chosen: `gte`. `strictly` is
`pinned`, and dynamic versions (`1.+`, `latest.release`) and Maven-style ranges
are `bounded` / `latest`.

**3. What does a build file report when the parser can't read some of it?**
**Decided 2026-11-09: raise `UnreadableManifest`** when a file has dependency
declarations and none could be read, so it reports `parse_error`. Such files
can be listed from the per-file outcomes `osi` stores (one JSON object per
repository in `observations`):

```sql
SELECT o._repo_id, j.key AS file_path
FROM observations o, json_each(o.data) j
WHERE o.observation_key = 'OSI_EXTRACTION' AND o.latest = 1
  AND j.value = 'parse_error';
```

The error contract has two outcomes: raise (unreadable) or return rows.
A build file whose dependencies are all repository helpers returns `[]`, which
is recorded as `empty`: "read, declares nothing". That's false. Options:

- **Raise when nothing was read but something was there.** If a file has
  dependency-configuration calls the parser couldn't read, and no rows, it
  raises `UnreadableManifest` ("declares dependencies kospex cannot read"). It
  then reports `parse_error`. A file that's partly read returns its rows, and
  the unread lines are only logged.
- **Always return rows, log the rest.** Simpler, but an all-helpers file shows
  as `empty`.

Chosen: the first, because it's the case `classify_outcome` can't tell apart
otherwise. A partly read file is still reported as fully read either way; a
per-file "partial" outcome would be new vocabulary. If it's needed, it belongs
with #148.

## Phases

1. **Catalog reader** (pure, tested against all 20 catalogs on disk), the
   `version-catalog` `package_use` value, and the registry change that gives
   `gradle-version-catalog` a parser. Ships `version-catalog` rows with
   advisories.
2. **Build-file reader**: literal coordinates, catalog references, `platform`,
   `project` and helpers, scope, plugins. Measured against every build file on
   disk before merging, as the pom and pnpm gates were: count readable versus
   unreadable declarations per repository.
3. **Docs**: `docs/dependency-details.md` (the `build.gradle` section, the
   catalog and lockfile entries, and a drift watch for Gradle 9 DSL changes and
   the catalog format), `docs/dependency-flags.md` for configurations, and the
   CHANGELOG.

Estimate: phase 1 about a day; phases 2 and 3 two to three days.

## Out of scope

- Running Gradle, or any network access from the parser.
- Convention plugins in `buildSrc/` and `build-logic/` (`.kt`, `.groovy`)
  that add dependencies to every module. Logged as a known gap.
- Catalogs published as artifacts, `gradle.lockfile`, and dependency
  constraints (`constraints { }`) that only change versions.
- `ext` maps of coordinates (`deps.okhttp`), an older idiom from before
  catalogs. Measure on the estate before deciding.
- Other Gradle scripts a build pulls in with `apply from: "..."`
  (`publish.gradle`, `dependencies.gradle`, ...): 151 on disk, applied 55 times
  from build files. They can declare dependencies, but the registry matches only
  `build.gradle(.kts)`. They'd need a registry entry, and a way to tell a script
  that declares dependencies from one that only configures publishing.
