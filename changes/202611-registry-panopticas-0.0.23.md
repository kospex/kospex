# Registry entries for files newly tagged by panopticas 0.0.22 / 0.0.23

Fixes [#349](https://github.com/kospex/kospex/issues/349).

## Overview

panopticas 0.0.22 and 0.0.23 tag about 40 more filenames `dependencies`: Rust, Ruby,
PHP, Elixir, Dart, Gradle, sbt, Mill, Python lockfiles, Bun, Deno, pnpm, Yarn PnP and a
set of .NET formats. The extractor registry knew none of them. After a re-sync on the
new panopticas, each would have shown on `/osi/` and `/dependencies/` as an
unrecognised dependency file, so a `.ruby-version` pin was reported the same way as an
unread `Cargo.lock`.

Every newly tagged name now has a registry entry, and a test walks panopticas' rules so
the next release that adds a tag fails the suite rather than surfacing on a page.

Scope is classification only: new parsers belong under #229. Cargo (#359) and Ruby's
Gemfile, `Gemfile.lock` and gemspec (#360) have their own issues, split from #229 so
they can be scheduled separately. `main`'s `requirements.txt`
already pins panopticas 0.0.23, so the issue was widened from 0.0.22 to cover 0.0.23's
.NET tags too.

## Behaviour

- **One existing parser fits.** `npm-shrinkwrap.json` is npm's `package-lock.json`
  format under a publishable name, and the parser never reads the filename, so it is
  routed to `extract_package_lock` and becomes supported.
- **A runtime pin.** `.ruby-version` is `Kind.RUNTIME`, alongside `.python-version` and
  `.nvmrc`. Reading runtime versions is #351.
- **Not a source.** Yarn's `.pnp.cjs` / `.pnp.loader.mjs` are generated from the tree
  `yarn.lock` declares, so they are `Kind.LOCKFILE`, a companion like `go.sum`. They are
  not reported as lacking a parser.
- **No parser yet**, for everything else, with `package_type` set to the deps.dev system a
  parser would query (`cargo`, `rubygems`, `maven`, `pypi`, `npm`, `nuget`), or `None`
  where deps.dev has none (Packagist, Hex, Pub, JSR).
- The three Gradle files (`gradle.lockfile`, `buildscript-gradle.lockfile`,
  `libs.versions.toml`) are no-parser entries too, agreed with the session working on
  the `pom.xml` parser under #229. A Gradle parser replaces them.
- **The panopticas floor is now `>=0.0.23`,** so the new tags never arrive without the
  entries that handle them. It also brings `get_deprecated_tags()`, which #317 waits on.

## Decisions

- **`pnpm-workspace.yaml` has no parser yet.** It is mostly settings. Its `catalog:`
  section declares version ranges that `package.json` refers to as `catalog:`, but only
  4 of 48 files on the development estate have one (53 entries in total).
- **`*.fsproj` / `*.vbproj` are not routed to the `.csproj` parser,** although they use
  the same `PackageReference`. No real file was available to measure it against. It is a
  one-line change once one is.
- **Two local patterns copy panopticas.** `is_pylock` and `is_nuget_project_lock` exist
  in `panopticas.core` but are not exported from the package, so delegating would
  silently match nothing. Raised as kospex/panopticas#110.
- **The registry entries sit after `gradle-build`,** not `maven-pom`, because the
  `pom.xml` parser work moves that line. Whichever PR merges second rebases.

## Measured

On the development estate, after a re-sync the new entries cover: `Cargo.toml` 126,
`pnpm-workspace.yaml` 48, `*.sbt` 32, `libs.versions.toml` 19, `Cargo.lock` 15,
`Gemfile` 15, `*.gemspec` 9, `composer.json` 5, `pylock*.toml` 4, Mill 4, `Gemfile.lock` 3,
`bun.lock` 3, and one or two each of the rest. No real basename matches more than one
registry entry.

Still unregistered and tagged before #349: `setup.py` / `setup.cfg` (#137),
`packages.config` (#182), and Swift, CocoaPods and Carthage files, which have no issue.
They are listed in the test's `KNOWN_UNREGISTERED`.

## Files changed

- `src/kospex/extractors/registry.py` - 32 entries, and the `LOCKFILE` kind comment now
  covers a generated companion as well as a checksum file.
- `pyproject.toml` - `panopticas>=0.0.23`.
- `tests/test_extractor_registry.py` - a `CASES` row per new name, a test that
  `npm-shrinkwrap.json` reads through the `package-lock.json` parser, and
  `test_every_panopticas_dependency_file_has_a_registry_entry`, checked to fail when an
  entry is removed.
- `tests/test_osi_outcomes.py` - the unregistered-file example is `Podfile.lock`, since
  `Gemfile.lock` is now registered.
- `docs/dependency-details.md` - a section for the new entries with measurements and
  upstream links, `.ruby-version` in the runtime section, the PnP loaders beside
  `go.sum`, a note on the remaining unregistered files, and a `npm-shrinkwrap.json` row
  in the format table.
- `CLAUDE.md` - the dependency-details summary no longer counts entries, which went
  stale with every registry change.

## Testing

Tests run against panopticas 0.0.23 installed in an isolated directory, since the shared
development venv has 0.0.21. Full suite: 2,151 passed, 23 skipped, 1 xfailed.
