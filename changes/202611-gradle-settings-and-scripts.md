# Gradle: reading settings files and every other Gradle script

Closes #436. Follows #435, which gave these files registry entries with no
parser, and phase 2 of `changes/202611-gradle-dependency-parsing.md` (the
build-file reader). Part of #229.

## Overview

panopticas 0.0.24 tags every `*.gradle` / `*.gradle.kts` file as a dependency
file. #435 recognised them as two "no parser yet" entries, `gradle-settings`
and `gradle-script`. This change points both at the existing build-file
reader, `extract_build_gradle`, with the `sca` and `osi` scanners, as
`gradle-build` has.

The reader doesn't depend on the file name. It reads the declaration forms
measured on the estate's build files, and a settings file or a script plugin
uses the same forms. So no parser change was needed, only the registry.

In the same change, `pylock` and `nuget-lock` ask panopticas' `is_pylock` and
`is_nuget_project_lock` (exported from 0.0.24, panopticas#110) instead of
copies of their patterns.

## Files changed

- `src/kospex/extractors/registry.py`: `gradle-settings` and `gradle-script`
  get the parser and scanners, and move out of the "no parser yet" block.
  `pylock` uses `_panopticas_matcher("is_pylock")`. `nuget-lock` uses
  `_nuget_lock`: plain `packages.lock.json` (an exact-filename rule in
  panopticas) or `is_nuget_project_lock`.
- `tests/test_extractor_registry.py`: the settings and script `CASES` flip to
  supported; every Gradle script resolves to `extract_build_gradle`;
  `test_recognition_follows_the_panopticas_predicate` monkeypatches each
  predicate to False and checks the entry stops matching, which a local copy
  of the pattern would not; `packages.lock.json` still matches.
- `tests/test_extractors_gradle_build.py`: `TestSettingsFiles` (settings
  plugins, settings `buildscript` classpath, `pluginManagement` not read,
  includes only is empty) and `TestOtherScripts` (renamed build file, script
  plugin with no dependencies is empty, convention plugin with only
  `libs.findLibrary(...)` raises). These passed before the registry change:
  they pin that the reader is name-agnostic.
- `docs/dependency-details.md`: the Gradle build-file section covers all
  three entries, the settings-file rules and the measurement; the 0.0.24
  subsection under "Classified, no parser yet" is gone; the pylock / NuGet
  note says they are delegated.
- `CHANGELOG.md`.

## Decisions

1. **Three entries, one parser.** `gradle-build`, `gradle-settings` and
   `gradle-script` could be one entry. Keeping them apart means an extraction
   outcome says what kind of script a file is, which matters when reading
   `parse_error` counts: the refusals below are all scripts, none build files.
2. **No settings-specific reader.** The reader's plugin rule reads only a
   top-level `plugins { }` block, so `pluginManagement { plugins { } }`, which
   sets default versions and applies nothing, is already skipped. A settings
   file's `buildscript` classpath is read like a build file's.
3. **A script's rows belong to the script.** A convention plugin in
   `build-logic/` or an `apply from:` script is reported as its own file.
   Working out which modules apply it would mean evaluating the build, which
   the reader doesn't do. The dependencies still appear in the repository's
   inventory, once per script.
4. **Refusals stay refusals.** A script that declares dependencies only in
   forms the reader doesn't read raises `UnreadableManifest` and reports
   `parse_error`, per the error contract, rather than `empty`.
5. **The NuGet entry keeps a local match for `packages.lock.json`.**
   panopticas matches it by exact filename, not with the predicate, so
   delegating to the predicate alone would stop recognising the common file.

## Measurement

Every Gradle, pylock and NuGet lock file panopticas tags `dependencies`, in
403 synced repositories on the development estate, from `git ls-files`.
Each was classified and parsed through the registry with `main` (after #435)
and with this branch, recording the outcome and a digest of the rows.
2026-11-15, panopticas 0.0.24.

| entry | files | before | after |
|---|---|---|---|
| `gradle-build` | 1,287 | 1,128 extracted, 149 empty, 10 parse_error | identical, every row digest unchanged |
| `gradle-settings` | 77 | unsupported | 10 extracted (19 rows), 67 empty |
| `gradle-script` | 148 | unsupported | 85 extracted (1,043 rows), 48 empty, 15 parse_error |
| `pylock` | 4 | unsupported | unsupported, same files |
| `nuget-lock` | 2 | unsupported | unsupported, same files |

Rows by repository for the newly read files: firebase-android-sdk 982,
androidx 58, kotlinx.coroutines 13, maplibre-native 3, and one each in four
others (settings plugins).

The 15 refusals:

| files | form |
|---|---|
| androidx `buildSrc/apply/*.gradle` (6) | `classpath(project.files("${project.ext[...]}/..."))`, a computed local jar path |
| androidx `car/app/app-samples/**/github_build.gradle` (4) | catalog references in standalone sample builds whose catalog isn't in the repository |
| okhttp `build-logic/**/*-conventions.gradle.kts` (4, two repositories) | `libs.findLibrary(alias)` helpers |
| kotlinx.coroutines `buildSrc/**/bom-conventions.gradle.kts` | `api(project.dependencies.platform(project(...)))` |

The predicates are case-sensitive where the old local patterns were not. The
same 6 pylock and NuGet lock files matched either way.

## Not done

- `gradle.lockfile` is still recognised and not read (#229).
- `gradle.properties` stays a side file for `$var` versions.
- First-party links between modules (#399) and vendored jars (#398).
