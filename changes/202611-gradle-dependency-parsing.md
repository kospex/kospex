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
`gradle/libs.versions.toml` next to the build's `settings.gradle(.kts)` as
`libs`, plus any `versionCatalogs` entry in that settings file whose
`from(files("..."))` is a literal path inside the repository. A build file's
`libs` is the catalog of the nearest directory above it that has one, not
always the repository root's: a repository can hold a Gradle build in a
subdirectory (measured in phase 2, below). A catalog outside the repository, or one published
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

### Repository hosts (phase 2)

A build also says where its packages come from. Gradle declares this in
`repositories { }` blocks: in `settings.gradle(.kts)` under
`dependencyResolutionManagement` and `pluginManagement`, and in build files
(including `buildscript { repositories { } }`). A pom does it in
`<repositories>` and `<pluginRepositories>`, including inside `<profiles>`.
Phase 2 records these hosts for both Gradle and Maven. It records them only:
it doesn't label any host or change which rows are looked up.

Why record them:

- **Lookup misses get context.** A package deps.dev can't find is expected
  when the build fetches from JitPack or a company repository that deps.dev
  doesn't index. Today such a miss gets an outcome label and nothing else.
- **Which builds use which registries** is useful across an estate on its own.
- **Enrichment can use it later.** Any rule based on where a package comes
  from belongs in enrichment, next to the existing lookup entry point, not in
  the parser.

The hosts can't go on each `dependency_data` row. Gradle tries the
repositories in order and decides which one serves a dependency at resolution
time. The only exception is a `content { includeGroup(...) }` filter. So this
is a fact about the build file, not about each dependency.

**Storage: `observations`, no migration.** One row per file that declares
repositories:

| column | value |
|---|---|
| `observation_key` | `BUILD_REPOSITORIES` |
| `observation_type` | `FILE` |
| `file_path` | `settings.gradle.kts`, `app/build.gradle`, `pom.xml`, ... |
| `hash` | `''`, as `OSI_EXTRACTION` uses. A commit hash would add a row for every commit, because `hash` is in the primary key |
| `latest` | `1`, replaced in place, no history (as `record_outcome` in `osi_queue.py`, which explains why) |
| `format` | `JSON` |
| `source` | `krunner osi` |
| `data` | a list in declaration order, below |

```json
[
  {"block": "dependencyResolutionManagement", "kind": "mavenCentral", "host": "repo.maven.apache.org"},
  {"block": "dependencyResolutionManagement", "kind": "google", "host": "maven.google.com"},
  {"block": "dependencyResolutionManagement", "kind": "maven",
   "url": "https://repo.example.com/releases", "host": "repo.example.com",
   "include_groups": ["com.example"]},
  {"block": "pluginManagement", "kind": "maven", "host": null, "unresolved": true}
]
```

- `block`: where it was declared. For Gradle that's `dependencyResolutionManagement`,
  `pluginManagement`, `buildscript` or `repositories`. For a pom it's
  `repositories` or `pluginRepositories`, plus `profile` with the profile id
  when the declaration is inside one.
- `kind`: the declaration form. For Gradle that's `mavenCentral`, `google`,
  `gradlePluginPortal`, `mavenLocal`, `maven`, `ivy` or `flatDir`. For a pom
  it's `repository`, and the pom's `<id>` is kept as `id`.
- `host`: for the shortcut forms, filled in from a constant table in the
  extractor (`mavenCentral()` is `repo.maven.apache.org`), not looked up.
- `unresolved: true`: the URL comes from a property, a variable or string
  building. It's recorded as present but unreadable, not guessed.
- `include_groups`: from a `content { }` filter, the only case where a host is
  tied to particular dependencies.
- `conditional: true`: declared inside an `if (...)`, so whether the build uses
  it depends on a property or environment variable that isn't evaluated.

Reading it needs one `json_each`, which SQLite 3.26 has. For example, each host
and how many repositories declare it:

```sql
SELECT json_extract(r.value, '$.host') AS host,
       COUNT(DISTINCT o._repo_id)      AS repos
FROM observations o, json_each(o.data) r
WHERE o.observation_key = 'BUILD_REPOSITORIES' AND o.latest = 1
GROUP BY host ORDER BY repos DESC;
```

Rules:

1. **Replace the repository's whole set on each run.** Delete its
   `BUILD_REPOSITORIES` rows, then insert the current ones, in one
   `db.atomic()`. Otherwise a deleted or moved build file leaves a stale row.
   The OSI outcomes avoid the same problem by using one blob per repository.
   Here, one row per file keeps the query simple.
2. **Never store credentials.** Repository blocks often carry
   `credentials { username = ...; password = ... }` or a `user:token@` in the
   URL, and a pom's `<repository>` can sit next to `<server>`-style settings.
   Keep the scheme, host and path, strip userinfo and query strings, and never
   copy the credentials block or raw expression text. A test pins this.
3. **Parse only.** A separate function per format (`extract_repositories`)
   beside the dependency extractor, so the dependency extractor's return shape
   doesn't change. `krunner osi` calls both and writes both in one transaction.
   No network access, like every extractor.
4. **Facts, not labels.** Nothing like `internal`, `public` or `first-party`.
   Those are decisions for enrichment.

What isn't visible in the repository isn't recorded: repositories added by an
init script or `~/.gradle/`, and Maven mirrors in `~/.m2/settings.xml`. A
build with none of its own records nothing, and that doesn't mean it uses only
Maven Central.

Nothing in kweb shows these rows until a view is built. If they later need a
table of their own, they can be backfilled from `observations` in that
migration.

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
addition, alongside marking Maven sibling modules first-party: #399.

**No row for `files(...)` / `fileTree(...)`**: a jar checked into the
repository (35 such declarations on disk). It has no coordinates, so it can't
be looked up. It is a risk, though: a vendored jar of unknown version is invisible
to advisory checks. So each one is logged with its file and line rather than
skipped silently. Recording them as rows is #398.

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
   unreadable declarations per repository. Also the repository hosts that
   Gradle builds and poms declare, stored in `observations` (see
   "Repository hosts" above).
3. **Docs**: `docs/dependency-details.md` (the `build.gradle` section, the
   catalog and lockfile entries, and a drift watch for Gradle 9 DSL changes and
   the catalog format), `docs/dependency-flags.md` for configurations, and the
   CHANGELOG.

Estimate: phase 1 about a day; phases 2 and 3 two to three days.

## Phase 1 as built (2026-11-09)

`src/kospex/extractors/gradle.py`, `extract_version_catalog`, registered as
`gradle-version-catalog` for `sca` and `osi`. The parser's rules and the
measurements are in `docs/dependency-details.md`; what building it turned up:

- **All 20 catalogs on disk parse, none raises.** 1,237 rows: 1,152 with a
  version (all `gte`), 85 without one. 63 of the 85 are lottie-android's
  refreshVersions placeholder `_`. The other 22 are libraries whose version a
  `platform(...)` supplies, which phase 2 reads.
- **Classifying as Gradle needed plumbing.** `classify_constraint` is given
  `package_type`, which is `maven` for Gradle rows. The parser sets
  `ecosystem="gradle"`, which isn't a column. Both write paths now pass
  `gradle` from it (`KospexDependencies._constraint_system`), and in
  `save_dependencies` that happens before the field is dropped.
  `constraints.py` also knows Gradle's `+`, `1.+`, `latest.*` and `!!`, for
  phase 2.
- **`strictly` is recorded as its plain value, so it's `gte`.** Gradle's `!!`
  shorthand would classify as `pinned`, but `clean_version_spec` would send
  `1.2.3!!` to deps.dev and the lookup would fail. Two entries on disk.
- **An existing database needs `krunner file-metadata` first.** `osi` only reads
  files tagged `dependencies`, and panopticas started tagging catalogs in
  0.0.22/0.0.23. timber, synced with 0.0.19, showed only its four `build.gradle`
  files as `unsupported`. After re-tagging, `osi` saved its 18 catalog rows, all
  resolved.
- **deps.dev answers for Google's Maven repository.** On timber, retrofit and
  firebase-android-sdk, 212 of 214 rows resolved. The two that didn't are
  versions deps.dev doesn't list, which the existing lookup-miss classification
  records as `version_yanked`.

## Phase 2 measurements (2026-11-09)

Before writing the build-file reader, its rules were applied by a throwaway
script to every `build.gradle(.kts)` and `settings.gradle(.kts)` on disk.
1,291 build files in 20 repositories. One further clone was excluded: its
working tree was incomplete (catalog and settings file missing), so it
measured the damage, not the rules.

| What the declaration would become | Count |
|---|---|
| **A row** | **8,975** |
| ↳ catalog reference, `libs.x` or `libs.bundles.x` | 5,556 |
| ↳ literal `"g:a:v"` | 3,201 |
| ↳ literal with `$var` filled from properties or assignments | 74 |
| ↳ literal with `$var` not found, kept as written (`unresolved_spec`) | 27 |
| ↳ no version, `kotlin("x")`, map notation, dynamic version | 57 |
| First-party: `project(...)`, `projects.x`, no row | 3,695 |
| Not a package: `files`, `fileTree`, `gradleApi`, ..., no row | 66 |
| `platform(...)`, a `bom` row | 13 |
| **Unreadable** | **122**, 1.3% of package declarations |

- **By repository:** 9 are fully readable, 7 at 90% or more, 2 at 50–90%,
  and 2 declare no packages. The largest, androidx, is 99.1% readable (7,606
  rows). The lowest is kotlinx.coroutines at 62%, which builds versions with a
  `version("x")` helper from `buildSrc`.
- **The unreadable 122 are mostly project-specific helpers:** androidx's
  `projectOrArtifact(...)`, `SdkHelperKt.getSdkDependency(...)`, and string
  concatenation (`"g:a:" + composeVersion`). None is common enough to support.
- **6 files would read nothing and so raise `UnreadableManifest`** under
  decision 3: five in kotlinx.coroutines and one sample app.
- **The catalog is not always at the repository root.** maplibre-native keeps
  its Android build, catalog included, under `platform/android/`. Looking
  only at the root left it 26% readable. Looking from the nearest directory
  with `gradle/libs.versions.toml` makes it 100%, and is how Gradle finds it.
  The "Catalog reader" paragraph above now says this.

**Repository hosts.** 94 `mavenCentral()`, 75 `google()`, 35
`gradlePluginPortal()`, 11 `mavenLocal()`, 4 `jcenter()` and 45 `maven { }`.
Of the 45, 35 have a literal URL (11 distinct hosts) and 10 don't, mostly a
`$buildDir` local directory. 8 have a `content { }` filter and 1 a credentials
block. No URL has a `user:token@`. About 14 are declared inside an `if`, hence
the `conditional` field.

### Decisions from the measurement

1. **Plugins.** `id("x")` without a version produces no row: it's a core
   Gradle plugin or one whose version is set elsewhere (2,774 on disk). Rows
   come from `id("x") version "y"` (44) and `alias(libs.plugins.x)` (109).
   Only a top-level `plugins { }` applies plugins: the block of the same name
   inside `gradlePlugin { }` defines them and is not read.
2. **Kotlin Multiplatform.** 773 declarations are in
   `kotlin { sourceSets { <name> { dependencies { } } } }`. `declared_scope` is
   `<sourceSet>.<configuration>`, for example `commonMain.implementation`.
   `is_dev` is True for source sets whose name ends in `Test`.
3. **Configurations outside the test rule stay `is_dev=False`.** androidx has
   890 declarations in `docs` and `kmpDocs`, and some in `lintChecks`. They
   aren't tests, so the rule leaves them as product dependencies, which
   raises androidx's direct count. Kept, because a per-project list of
   configuration names would be a policy, not a parse.
4. **`val x = property("y")` is resolved** from `gradle.properties`, the same
   as `$var`, since it costs one more pattern.
5. **`constraints { }` stays out of scope** (42 declarations): it only changes
   versions. **`buildscript` classpath stays in** (20), as planned.

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
