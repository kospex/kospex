# Package names that are never looked up

## Overview

`KOSPEX_DEPS_ENRICH=false` / `-no-enrich` turns every dependency lookup off.
That's all or nothing. In practice, the packages an organisation doesn't want
named to a public index are its own, and they're easy to recognise: a Maven
groupId under the organisation's reverse domain, or a Go module under its Git
host. Public packages can still be looked up safely. `KOSPEX_DEPS_PRIVATE`
names those prefixes once, and kospex never sends a matching name anywhere.

## The change

- **Setting:** `KOSPEX_DEPS_PRIVATE`, in the environment or
  `~/kospex/kospex.env` (`HabitatConfig.deps_private`). It takes
  comma-separated `ecosystem:prefix` entries, e.g.
  `maven:com.example,go:github.com/example`. Unset or empty means none.
  `kospex init`'s template lists it, commented out.
- **Ecosystems:** `maven` and `go`. `gradle` is accepted as another name for
  `maven`. Any other ecosystem is an error rather than being ignored, so
  nobody believes `npm:@example` protects them before it does. A malformed
  entry is an error too, and both are reported before a scan starts.
  `enrich_decision()` reads the setting.
- **Matching** (`kospex/private_packages.py`): a name matches a prefix it
  equals, or one it continues at a boundary. For Maven the boundary is `.` or
  `:`, for Go it is `/`, so `com.example` doesn't match `com.examples:x`.
  Matching ignores case: a wrong match costs one lookup, but a missed match
  sends the name. A whole `group:artifact` matches only that artifact.
  Entries are prefixes, not globs. One containing `*`, `?` or `[` is an
  error, because taken literally it would never match; Go globs go in
  `GOPRIVATE`.
- **Gradle needs nothing of its own.** Every Gradle extractor records
  `package_type` `maven` with `group:artifact` names. `platform()` is a BOM
  row, and a plugin is its marker artifact `<id>:<id>.gradle.plugin`, so a
  plugin id under the domain is covered too.
- **`GOPRIVATE`** is honoured for Go modules, read from the environment only.
  It uses Go's syntax: each glob pattern is matched against as many leading
  path elements as it has (`module.MatchPrefixPatterns`). `GONOPROXY` and
  `GONOSUMDB` are not read, because they are about proxies and checksums,
  not privacy.
- **Nothing overrides it:** not `-enrich`, and not `KOSPEX_DEPS_ENRICH=true`.

## Where it is checked

- **The decision.** `KospexDependencies.lookup_permitted(type, name)` decides.
  `lookup_dependency()` calls it first, before the range floor's version-list
  fetch, and returns a private row with no lookup. Both scan paths go through
  it. `maven_bom_versions()` skips a private BOM, so its rows stay
  `no_version`.
- **A guard in every method that sends a name:**
  - `deps_dev`, `deps_dev_status`, `deps_dev_package`, `_published_versions`,
    `release_list` and `depsdev_record`;
  - `_bom_managed_versions`;
  - `get_depsdev_info`;
  - `package_dependencies`, for the root and for each node of the graph;
  - `check_malware`;
  - the two PyPI lookups.

  A caller that skips the decision still sends nothing.
- **Callers:**
  - kweb's `/supply-chain/` says a private package is private rather than
    looking it up.
  - `kospex sca -package` refuses a private name.
- **Tests:**
  - The HTTP layer is patched at `requests.Session.request`, below the
    url_cache, and records every URL.
  - `kospex sca` and `krunner osi` run with lookups on over a `pom.xml` with a
    private BOM, a `build.gradle` with a private platform and plugin, a
    version catalog and a `go.mod`.
  - No URL may carry a private name, and the public names must still be
    requested.
  - A control run without the setting shows that the same files do send the
    names.

## Rows

- **New resolution `private_skipped`** (`RESOLUTION_PRIVATE_SKIPPED`; plain
  TEXT, no migration). Its lookup columns are as for `not_checked`: empty,
  with NULL counts and a NULL `last_checked`.
- **Lookups off.** A private row is still `private_skipped`, not
  `not_checked`, so the saved rows show what the prefixes cover before lookups
  are turned on.
- **No earlier lookup is kept.** Unlike `not_checked`, a private row does not
  keep a lookup saved under the same key. The prefix is the current statement
  of what is private, and a stored lookup means the name was already sent;
  `krunner osi` reports that instead.

## Reporting

- **`krunner osi`** (all, scope and `-next`), when prefixes are set:
  - prints `Private packages: N prefixes (maven a, go b) from
    KOSPEX_DEPS_PRIVATE; GOPRIVATE set.`;
  - then either says that no current rows in scope matching a prefix have been
    looked up, or warns how many have, and when most recently.
  - The warning doesn't stop the run.
  - The count uses `KospexQuery.looked_up_dependency_names()`, grouped by
    name, and matches the names in Python with the same rule. SQL `LIKE` would
    duplicate the boundary and glob logic.
- **`kospex sca`** says how many packages were private.
- **kweb:**
  - shows the resolution as **Private**;
  - in *No check date recorded*, explains a file whose rows are all private as
    private;
  - explains a file that mixes private and not-checked rows as parsed with
    lookups off.

## Not in this change

- npm, NuGet and PyPI prefixes. Their names need different boundaries, and
  PyPI and unscoped npm names have no namespace at all.
- Reading per-project registry configuration (`.npmrc` scopes,
  `nuget.config` source mapping, uv index sources).
- Suggesting prefixes automatically, e.g. from a pom's own groupId or a
  go.mod's module path.
- Running `go env` to find a `GOPRIVATE` set with `go env -w`. The docs say
  to export it instead.
