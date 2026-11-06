# `pom.xml` parser (#229)

## Overview

kospex recognised `pom.xml` but could not read it, so every Maven project reported
`unsupported`. `kospex.extractors.maven:extract_pom` now parses it for both
`kospex sca` and `krunner osi`. It records one row per `<dependency>` in the
project's own `<dependencies>`, plus one per imported BOM.

The full per-format notes are in the `pom.xml` section of
`docs/dependency-details.md`: what raises, what returns `[]`, the measurements,
and the drift watch. This file records the decisions and where they came from.

## Decisions

- **Names are `groupId:artifactId`**, which is how deps.dev names a Maven
  package.
- **Versions come from two tiers only.**
  1. The file itself: `<properties>`, `${project.*}` and `${project.parent.*}`.
  2. Parent poms inside the same repository, followed through `<relativePath>`
     (default `../pom.xml`), as Maven does.

  Imported BOMs and external parents are not fetched. On the 14 real poms
  measured, 24 of 51 dependencies had no `<version>`. After both tiers: 38
  resolved, 13 blank, 0 unresolved `${...}`.
- **A version kospex cannot find is recorded as `""`**, and enrichment reports it
  as `no_version`. This needs no new flag: Maven refuses to build a pom with an
  unmanaged, unversioned dependency, so a blank always means "a BOM or a parent
  kospex did not read". An unresolvable `${...}` is kept verbatim, so it is
  reported as `unresolved_spec` and still names the property.
- **`declared_scope` is the raw `<scope>`.** `is_dev` is true for `test` only.
  `<optional>` is not `is_optional`: in Maven it means "not passed on to
  consumers".
- **Imported BOMs are rows** (`requirements_type="bom"`), because they are where
  missing versions come from. They get a new `package_use` value, `bom`, not
  `direct`: a bill of materials puts nothing on the classpath, so counting it would
  overstate direct dependencies. `kospex sca` hides them unless `-scope bom` or `all`. Other `<dependencyManagement>` entries, plugin
  dependencies and profiles are not rows.
- **Maven version ranges** (`[1.0,2.0)`) go through the NuGet interval rule in
  `constraints.py`. It is the same notation.

## Security

The input is third-party repository content:

- **A DOCTYPE is refused** (CWE-776), as for `.csproj`.
- **Property expansion is capped** at 10 passes and 1024 characters. Each level
  of `${p0}${p0}` doubles, which is the same blow-up without a DTD.
- **A parent is never read from outside the repository.** The boundary is the
  nearest directory above holding `.git`, and the path is resolved through
  symlinks before the check. With no `.git` above, no parent is read.
- **A parent must carry the coordinates `<parent>` names.** Cycles stop, and
  chains stop at a depth of 10.

## Files changed

- `src/kospex/extractors/maven.py`: the new parser
- `src/kospex/extractors/registry.py`: `maven-pom` moves to the supported
  entries
- `src/kospex/extractors/constraints.py`: interval rule gated on
  `("nuget", "maven")`
- `tests/test_extractors_maven.py`: new tests
- `tests/test_constraint_classifier.py`: Maven range cases
- Registry, outcome and empty-state tests: `pom.xml` moves from "no parser" to
  "parsed", and `Cargo.toml` is now the no-parser example
- `docs/dependency-details.md`: the new `pom.xml` section

## Follow-ups

- **Tier 3, imported BOMs and external parents.** deps.dev's `:requirements`
  endpoint returns a BOM's `dependencyManagement` with versions already
  resolved. That needs the network, so it belongs in enrichment, not the parser.
- **Coordinates a repository publishes.** This would let kospex mark a
  dependency on a sibling module as first-party.
- **Maven version lookups** (`.Final`, `-SNAPSHOT`) are in
  `202611-maven-version-lookup.md`.
