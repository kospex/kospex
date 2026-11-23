# OSV integration (MVP: `kospex sca -package ... -osv`)

Status: **design only**, nothing implemented.

## Overview

Add [OSV](https://osv.dev) as a second advisory source, starting with the smallest
useful surface: a `-osv` switch on `kospex sca -package TYPE:NAME@VERSION`. The
command prints deps.dev's output exactly as today, then **appends an OSV section**.
Nothing is stored and no existing output changes without the switch.

Why `-osv` on `-package` rather than a new `kospex osv` command: one command already
answers "tell me about this package version", and the two sources are most useful
side by side. It also follows the project rule of adding a mode to an existing
command rather than a new base command.

What OSV adds over deps.dev's advisories table:

- **Fixed version.** `affected[].ranges[].events` carries `fixed`, so the answer to
  "what do I upgrade to" is in the record. deps.dev's table has ID, CVE, CVSS 3 and
  title only.
- **Other databases.** RUSTSEC, GO, PYSEC, distro feeds and others, which makes it a
  cross-check on deps.dev's list.
- **Withdrawn advisories** are marked (`withdrawn` timestamp).
- **CVSS 4.** deps.dev reports `cvss3Score: 0` with no vector for advisories scored
  only under CVSS 4. OSV carries the vector.

## Usage

```
kospex sca -package pypi:requests@2.25.0 -osv
kospex sca -package maven:io.netty:netty-handler@4.2.18.Final -osv
```

- `-osv` requires `@VERSION`. OSV answers "which advisories affect this version";
  without a version the question is "every advisory ever", which is a different
  report. `-osv` with no version is a usage error (see open questions).
- `-osv` is refused without `-package`: with a file, there is no section to append
  to, and a per-row OSV lookup is the phase-2 estate work below.
- The existing `-package` refusals (`-repo`, `-save`, `-out`, `-json`, `-scope`,
  FILE_PATH) apply unchanged.

## Output: appended after the deps.dev content

The deps.dev output is untouched and comes first: Releases table, requested-version
lines, advisories table. The OSV block follows it:

```
OSV advisories for pypi requests 2.25.0: 4

  ID                   Also              Severity    Fixed in   Published    Summary
  GHSA-9hjg-9r4m-mvj7  CVE-2024-47081    MEDIUM 5.3  2.32.4     2025-06-09   Requests vulnerable to .netrc ...
  ...

Compared with deps.dev: 4 in both, 0 only in OSV, 0 only in deps.dev.
```

Reasons for appending rather than merging into one table:

- **Provenance stays visible.** The two sources disagree for real reasons (different
  aliasing, different databases, different scoring), and a merged table would hide
  which source said what.
- **Nothing existing moves.** deps.dev's table, its CVSS `-` handling and its tests
  stay as they are.
- **A failure is contained.** If OSV is down or slow, the deps.dev output has
  already printed. The OSV block becomes one line, `OSV did not answer (HTTP 503).`,
  and the exit code is unchanged. A deps.dev failure still exits 1 as today, and
  OSV is then not asked.

The comparison line is the part that makes the second source worth having. It is
computed on the **alias set** (GHSA, CVE and other IDs from both sides, merged by
shared alias), not on raw IDs, because the same vulnerability is listed under
different IDs by each source. Anything only one side lists is named underneath.

## Files

```
src/kospex/integrations/osv/__init__.py
src/kospex/integrations/osv/client.py    # OsvClient
src/kospex/integrations/osv/queries.py   # pure functions: raw JSON -> rows
src/kospex_cli.py                        # -osv option; _print_osv_section()
tests/osv_fixtures.py
tests/test_osv_client.py
tests/test_osv_queries.py
tests/test_sca_package_osv.py
```

This mirrors `integrations/snyk/` (client / queries; vendor code under
`kospex/integrations/<vendor>/`). There is no `cli.py` yet because there is no
`kospex osv` group. The printing lives beside `_sca_package` in `kospex_cli.py`
because it shares that function's console and refusal handling.

### `client.py`

- `OsvClient`, `requests` only. No auth and no token. Base URL
  `https://api.osv.dev/v1`.
- `query(ecosystem, name, version)` -> `POST /query`, following `next_page_token`
  until exhausted. Returns the full vulnerability records (the single-package
  `/query` endpoint returns them, unlike `/querybatch`).
- `vuln(osv_id)` -> `GET /vulns/{id}`, for phase 2 and for a cross-reference.
- Timeout, bounded retries on 429 and 5xx honouring `Retry-After`, a request
  counter, and `OsvError` carrying status and path. Same shape as `SnykClient`.
- Refuses a paging or redirect target on another host (CWE-201), as the Snyk
  client does.

### `queries.py` (pure, so testable without the network)

- `ecosystem_for(package_type)` -> OSV ecosystem name; `None` when unmapped.
- `advisory_rows(records, ecosystem, name, version)` -> one row per vulnerability:
  `id`, `aliases`, `cves`, `severity`, `fixed_in`, `published`, `withdrawn`,
  `summary`, `url`.
- `fixed_versions(record, ecosystem, name)` -> the `fixed` events from the
  `affected[]` entry for **this package** only. A record can list several packages.
- `cvss_score(vector)` -> numeric score from a CVSS v3 vector, computed locally
  (OSV gives a vector, not a number). v4 vectors are shown as the vector's severity
  where `database_specific.severity` supplies one, else `-`.
- `compare(osv_rows, depsdev_advisories)` -> `(both, only_osv, only_depsdev)` by
  alias set.

### Ecosystem mapping

`registry.package_types()` returns deps.dev system names. Today:
`cargo, go, maven, npm, nuget, pypi, rubygems`.

| kospex TYPE | OSV ecosystem | Name form |
|---|---|---|
| cargo | `crates.io` | crate name |
| go | `Go` | module path |
| maven | `Maven` | `group:artifact` |
| npm | `npm` | package name, scope kept |
| nuget | `NuGet` | package id |
| pypi | `PyPI` | name (OSV normalises) |
| rubygems | `RubyGems` | gem name |

A type with no mapping prints `OSV has no ecosystem for TYPE.` and skips the block.
The mapping is one dict next to `ecosystem_for()`, with a test asserting every entry
of `package_types()` is either mapped or listed as deliberately unmapped, so a new
parser cannot be added without someone deciding.

## Privacy and safety

- **`lookup_permitted()` runs first.** A package matching `KOSPEX_DEPS_PRIVATE` or
  `GOPRIVATE` is never sent to OSV, and `-osv` does not override it. The existing
  `-package` check already runs before deps.dev is asked, so the OSV call sits behind
  the same gate. A test asserts no request is made for a private package.
- **`-no-enrich` / `KOSPEX_DEPS_ENRICH=false`** already stops `-package`; `-osv`
  follows it. `-enrich` is needed to look anything up, as it is for deps.dev.
- **CWE-150.** OSV `summary` and `details` are third-party text. Everything goes
  through `KospexUtils.terminal_safe()` and rich with `markup=False`.
- **No query sent beyond what's needed.** Ecosystem, name and version only. Nothing
  about the repository, user or machine goes in the request body.
- **Response size.** `details` can be large markdown. The MVP table shows `summary`
  only and does not store or print `details`.

## Caching

None in the MVP. `-package` already bypasses `url_cache` for deps.dev because a
stale "latest" misleads, and the same reasoning applies: one request per
invocation. The `Fresh data` note in the `-package` output stays accurate.

Phase 2 (estate) needs a cache keyed by vulnerability ID, since the same record is
shared by many packages. A cache hit means OSV was not contacted, so as with
deps.dev the honest bound on age is `last_checked + TTL`.

## Tests

- **Client:** paging via `next_page_token`; 429 with `Retry-After`; 5xx retries then
  `OsvError`; cross-host paging link refused. HTTP mocked, no live calls in the
  suite.
- **Queries:** fixtures built from real OSV records (requests 2.25.0, a netty
  advisory, a withdrawn one, a multi-package record where only one `affected[]`
  entry is ours, a record with CVSS v4 only). Cases for `fixed_versions` with
  several `introduced`/`fixed` events, `last_affected` with no `fixed`, and a `GIT`
  range, which must not be read as a version.
- **Compare:** the same vulnerability under different IDs on each side counts as
  "both"; a vulnerability on one side only is named.
- **CLI:** `-osv` without `-package`, without `@VERSION`, with a private package
  (no request), with an unmapped type, and with OSV failing after deps.dev succeeds
  (deps.dev output intact, exit code 0).
- **Drive the production call.** Tests call `_print_osv_section()` / `advisory_rows()`
  on fixtures, not a local copy of their logic.

## Phase 2 (not in this change)

Listed so the MVP does not close them off.

- **Estate lookup** from `dependency_data` triples via `POST /querybatch` (up to
  1000 queries per call; returns IDs and modified times only, so details come from
  `GET /vulns/{id}`, cached by ID). Whether results are stored or only reported is
  undecided, and storing means a migration and a decision on which source wins when
  they disagree.
- **Distro packages / container images.** OSV has distro ecosystems (Debian, Ubuntu,
  Alpine and others) but no image concept: it needs an inventory from the image,
  per-release ecosystems (`Debian:12`) and source-package names. Out of scope for
  a tool that reads source repositories.
- **`-osv` only** (skip the deps.dev section) once someone wants it.

## Open questions

1. **`-osv` without `@VERSION`:** usage error (proposed), or list every advisory for
   the package with its affected and fixed ranges? The second is useful for "has this
   package ever had problems" but is a different table.
2. **CVSS v3 scoring:** compute locally from the vector (proposed; small, pure,
   testable) or depend on a library? Per the dependency-floor rule, a library is
   only worth it if it is maintained; the v3.1 base-score formula is about 40 lines.
3. **Which severity to show** when a record carries several (v3 and v4, or a
   database label that disagrees with the vector). Proposal: v3 score if present,
   else the database label, else `-`, and say which it is.
4. **Withdrawn advisories:** show, struck or marked, in the table (proposed) or hide
   unless asked.
