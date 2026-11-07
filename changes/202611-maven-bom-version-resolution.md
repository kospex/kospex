# Maven: versions from an imported BOM (tier 3)

Follows `202611-pom-xml-parser.md` (#229, #373).

## Problem

The pom parser fills a missing `<version>` from the file's own properties
(tier 1) and from parent poms inside the repository (tier 2). A version that
only an **imported BOM** supplies is left blank and reported as `no_version`,
so the dependency gets no advisories and no `versions_behind`.

jsoup imports `io.netty:netty-bom 4.2.18.Final` and declares `netty-handler`
and `netty-codec-http` with no version. Maven takes 4.2.18.Final for both from
the BOM. kospex records both as `no_version`.

On the 14 poms measured, 13 dependencies are blank:

| supplied by | blanks | fillable from deps.dev |
|---|---|---|
| jsoup ← `netty-bom 4.2.18.Final` | 2 | yes |
| gson graal test ← `junit-bom 6.1.3` | 2 | yes |
| okhttp ×2 ← `okhttp-bom [5.2.0-SNAPSHOT,6.0.0-SNAPSHOT)` | 6 | no, a range of unpublished snapshots |
| jackson-core ← external parent `jackson-base 3.3.0-SNAPSHOT` | 3 | no, a snapshot parent |

The nine that can't be filled are repositories building against their own
unreleased code. No outside source knows those versions, so they stay
`no_version`.

## Approach: deps.dev, not our own Maven

deps.dev's requirements endpoint returns a BOM's `dependencyManagement` with
the versions already worked out, including the BOM's own properties and
parents:

```
GET https://api.deps.dev/v3alpha/systems/maven/packages/io.netty%3Anetty-bom/versions/4.2.18.Final:requirements
→ maven.dependencyManagement[]: {name: "io.netty:netty-handler",
                                 version: "${project.version}",
                                 resolvedVersion: "4.2.18.Final", ...}
```

Checked on 2026-11-07: netty-bom returns 73 entries, junit-bom 6.1.3 returns 16,
and a snapshot okhttp-bom returns 404. No authentication is needed.

Fetching BOM poms from Maven Central and resolving them ourselves was ruled
out. It would need the BOM's parents and its own imports, recursively, which
reimplements a good part of Maven.

## Where it runs: enrichment, not the parser

The parser stays offline and deterministic. Both enrichment paths,
`KospexDependencies._enrich_dependency_records` (`sca`) and
`krunner.enrich_dependency_records` (`osi`), call one shared helper before
their per-record loop. It finds the version each blank Maven row should be
looked up with. The loop then uses that in place of the blank, and everything
after it (advisories, `versions_behind`, `resolution`) is the normal lookup.

The helper:

1. Groups Maven records by file (`_repo_id`, `file_path`). A BOM only
   supplies versions to the pom that imports it.
2. In each file, takes the `bom` rows (`requirements_type="bom"`) whose version
   is concrete, in declaration order. A snapshot or range BOM version can't be
   looked up.
3. Fetches each BOM's `:requirements` through `url_request_with_status`, which
   is cached like every other deps.dev call.
4. For each row with a blank version, takes the `resolvedVersion` of the
   matching `groupId:artifactId` from the **first** BOM that lists it. When
   two imports manage the same artifact, Maven uses the first declared.

## Decisions (2026-11-07)

**1. A filled-in version is stored in `resolved_version`; the declaration stays blank.**

| package | package_version | version_kind | resolved_version | resolution |
|---|---|---|---|---|
| io.netty:netty-handler | *(blank)* | none | 4.2.18.Final | resolved |

`package_version` and `version_kind` describe what the file declared.
`resolved_version` is the version sent to deps.dev (migration 0006). Writing
4.2.18.Final into `package_version` would claim jsoup's pom says it, and the
BOM could change it without that line changing. "Declared no version, looked
one up" is how a filled-in version is recognised. No new value, no migration,
and `package_version` stays blank, which also keeps the primary key stable.

The row does not record *which* BOM supplied the version. With one BOM in the
file it is the file's `bom` row. All four importing poms measured import
exactly one. A file with two BOMs is the trigger to record the source on the
row.

**2. A failed BOM lookup leaves the row `no_version`; the reason is only logged.**

The three failures are: the BOM is not on deps.dev (404), the BOM doesn't list
the artifact, and a network error, rate limit or 5xx. In all three the version
is unknown, so the row is exactly what it is today. The reason goes to
`~/kospex/logs/kospex_dependencies.log`. No new `resolution` value, because a
network failure on one run is not a fact about the dependency. That is the same
rule `versions_behind` follows when deps.dev doesn't answer.

## Limits

- **A BOM imported by an in-repo parent** is not applied to the child modules.
  `bom` rows come only from the file's own `<dependencyManagement>`. None of the
  measured poms do this. Adding it means the parser carrying the ancestors' BOM
  imports.
- **Matching is on `groupId:artifactId` only.** A BOM that manages the same
  artifact under two classifiers or types gives the first one's version.
- **External parents** (`<parent>` outside the repository) are not fetched.
  deps.dev's `:requirements` for a *released* parent would cover them the same
  way; all three measured are snapshots, so it isn't worth doing yet.

## Monitoring

The ongoing data-quality counts are tracked privately: how many versions get
filled in, how many blank rows sit next to a BOM that couldn't be used, and
how many files import more than one BOM.
