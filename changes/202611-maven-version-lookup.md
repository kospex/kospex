# Maven versions: `.Final` releases and `-SNAPSHOT` builds

## Overview

Running the new `pom.xml` parser through the real `kospex sca` path, with live
deps.dev lookups, showed two Maven versions getting the wrong `resolution`. Both
are in enrichment (`KospexDependencies.depsdev_record()`), not in the parser:

| version | was | should be | why it was wrong |
|---|---|---|---|
| `io.netty:netty-bom` `4.2.18.Final` | `unresolved_spec`, deps.dev never asked | looked up | `is_concrete_version()` did not recognise the version |
| `com.google.code.gson:gson` `2.14.1-SNAPSHOT` | `version_yanked` | `unresolved_spec`, no lookup | deps.dev 404s on a version it never had, and the package exists |

deps.dev returns 200 for `io.netty:netty-bom` at `4.2.18.Final`, and 404 for
`com.google.code.gson:gson` at `2.14.1-SNAPSHOT`. Both were checked live.

## What these versions are

### `.Final`: a production release

`Alpha1` → `Beta2` → `CR1` → `Final` is the release-qualifier convention of the
JBoss / Red Hat projects: netty, Hibernate, WildFly, Undertow, RESTEasy,
Infinispan. Older Spring used `.RELEASE` (`5.3.0.RELEASE`) until about 2020.

These are published to Maven Central and run in production. Netty alone sits
underneath a large share of Java servers. Before this change, kospex never asked
deps.dev about any of them, so they had no advisory count and no versions-behind
figure. That is a coverage gap on production dependencies.

`Alpha`, `Beta` and `CR` already passed, because Python's `packaging` reads
them as pre-release tags (`1.0.0.Beta1`, `1.0.0.CR1`). `Final`, `RELEASE` and
`GA` after a dot did not. The existing fallback pattern allows a suffix only
after `-` or `+`.

### `-SNAPSHOT`: work in progress

A snapshot is "the next version, not released yet". `mvn release` (or
`versions:set`) strips it before publishing. Snapshots are never in Maven
Central's release repository. They live in a snapshot repository or an internal
Nexus / Artifactory, and Maven re-fetches the newest build every time.

On the development estate a snapshot appears in three ways, with different
meanings:

| where | example | meaning |
|---|---|---|
| the project's own version | jsoup `1.24.1-SNAPSHOT` | Normal. A library's default branch sits at the next version between releases. |
| a sibling module in the same build | gson `extras` → `gson:2.14.1-SNAPSHOT` | Normal. First-party, built in the same build. |
| another project's snapshot | jackson-core → parent `jackson-base:3.3.0-SNAPSHOT`; okhttp → BOM `[5.2.0-SNAPSHOT,6.0.0-SNAPSHOT)` | The build depends on whatever that other project built last, so it is not reproducible. `maven-release-plugin` refuses to release with one. |

kospex scans the default branch, so open-source repositories mostly show the
first two kinds. The same repository at a release tag would show none.

## Decisions

- **Maven only.** `is_concrete_version()` gains an optional `package_type`, and
  `depsdev_record()` passes it. A dot-qualifier cannot be accepted in general,
  because npm's `1.2.x` is a wildcard with exactly that shape. Tests pin that
  `1.2.x` and `4.2.18.Final` are still not concrete for `npm`.
- **The snapshot rule is Maven's own**, from `ArtifactUtils.isSnapshot` in
  maven-artifact (3.9.x): the version ends in `SNAPSHOT`, compared ignoring
  case, or matches `Artifact.VERSION_FILE_PATTERN`,
  `^(.*)-(\d{8}\.\d{6})-(\d+)$` — the timestamped form a snapshot build is
  deployed under. It is checked before the dot-qualifier and fallback patterns,
  because `1.0-SNAPSHOT` also matches the existing `-` suffix pattern. That
  match is what sent snapshots to deps.dev in the first place.
- **A snapshot is `unresolved_spec`, not a new `unreleased` value.** A snapshot
  is a moving target that Maven re-resolves on every build, which is what
  `unresolved_spec` means. The declared text is kept in `package_version`, so
  `package_version LIKE '%-SNAPSHOT'` still finds every one. A new value would
  also need adding to kweb's label maps (`kweb2.py`, `dependencies.html`), and
  it would not answer the question that matters: is this snapshot first-party
  or someone else's?
- **No lookup is made for a snapshot.** Before, one dependency cost two
  requests: the version 404, then the package lookup that decided "yanked".
- **`version_kind` is unchanged.** `constraints.py` still classifies
  `2.14.1-SNAPSHOT` as `pinned`, because the declaration names one version.

## Not done here

- **First-party marking.** Telling "a snapshot of my own module" from "a
  snapshot of someone else's" needs to know which coordinates a repository
  publishes (`groupId:artifactId` from its own poms, or from the estate). That
  is the useful split, and it is separate work.
- **Snapshots of other projects as a signal.** Flagging an external snapshot
  dependency is a judgement on top of data that is now queryable. It is not
  part of the lookup.
- **Qualifiers without a dot, such as `1.0-GA` or `1.0-jre`**, already passed
  the existing `-` suffix pattern, and are unaffected.

## Files changed

- `src/kospex_dependencies.py`
  - `is_concrete_version(version, package_type=None)`, with the two Maven rules
  - `_MAVEN_DOT_QUALIFIED` and `_MAVEN_TIMESTAMPED_SNAPSHOT` class patterns
  - `depsdev_record()` passes `package_type` through
- `tests/test_resolution_status.py`: `TestMavenVersions`, covering:
  - dot-qualified releases are concrete
  - the rule is Maven-only
  - all three snapshot forms are refused
  - ordinary Maven versions still pass
  - a snapshot is `unresolved_spec`, not `version_yanked`
  - `4.2.18.Final` is actually looked up

## Verification

Live, through `KospexDependencies.assess()`:

```
github.com/jhy/jsoup/pom.xml
  io.netty:netty-bom      '4.2.18.Final'    res=resolved          scope=import
github.com/google/gson/extras/pom.xml
  com.google.code.gson:gson  '2.14.1-SNAPSHOT'  res=unresolved_spec
```

Before the change, the same two rows were `unresolved_spec` and
`version_yanked`.
