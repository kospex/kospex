# `kospex sca -dependabot`: enriching Dependabot alerts

Notes on **what `kospex sca -dependabot` adds to a Dependabot alerts file, what each
added field means, and when a field is empty**. Internal notes, like
[Dependency file details](dependency-details.md): not linked from other pages and not in
the sitemap.

## Purpose

`kgit github --dependabot` writes one JSONL line per open alert (see
[the queryables page](kgit-github-queryables.md)). Dependabot says an installed version is
vulnerable and names the first fixed version. It does not say:

- how far the installed version is from the **latest** release;
- how big the **minimum upgrade** (installed to patched) is;
- whether the **patched version itself** still carries advisories.

`kospex sca -dependabot` copies each line through unchanged and appends `sca_*` fields
that answer those three questions, using deps.dev. Nothing is written to the kospex
database.

## Usage

```bash
kospex sca -dependabot alerts.jsonl -out alerts-enriched.jsonl [-enrich]
```

- `-out` is required. The output is JSONL: same number of lines, same order, every input
  key unchanged, then every `sca_*` key (null when unknown). Every output line has the
  same keys.
- A summary of `sca_status` counts goes to stderr when the run finishes, with a progress
  line every 25 alerts. Failed lookups are summarised on stderr too.
- Lookups are the point of the command, so there is no parse-only form. If lookups are
  off (`KOSPEX_DEPS_ENRICH=false`) and `-enrich` is not given, it exits 1 with:

  ```
  -dependabot looks alerts up on deps.dev, and lookups are off (KOSPEX_DEPS_ENRICH=false). Add -enrich to look it up anyway.
  ```

  `-enrich` overrides the setting for one run, the same rule and wording as `-package`.
  With lookups on, `-enrich` is harmless. `-no-enrich` is refused the same way as a
  disabled setting.
- Refused with `-dependabot` (a usage error): `-json`, `-scope`, `-save`, `-package`,
  `-repo` and a `FILE_PATH` argument. `-out` missing is also a usage error.

### Input keys the enricher reads

Only four keys of each input line are read:

| Key | Used for |
|---|---|
| `package_ecosystem` | GitHub's ecosystem name, mapped to a package type (below) |
| `package_name` | The package name |
| `current_version` | The installed version; empty or null means unknown |
| `patched_version` | The first fixed version; empty or null means no fix |

Other keys are copied through and ignored.

These are the names `kgit github --dependabot` writes (`ALERT_KEYS` in
`src/kospex/github_dependabot.py`); the enricher exposes the ones it reads as
`ALERT_KEYS_READ`, and a test checks they are all in `ALERT_KEYS`.

## Output fields

All fields are appended to every line. "Null" cases are listed per field; a row that
could not be enriched keeps its reason in `sca_status` and leaves the rest null.

### Outcome

| Field | Meaning |
|---|---|
| `sca_status` | `ok` or the reason the row is incomplete. See [Statuses](#statuses) |

### Current version to latest release (the `behind_*` fields)

The distance from the installed version to the **latest** release, using the same rule as
`versions_behind` elsewhere in kospex (pre-releases excluded). Null unless
`current_version` is known and the package was found.

| Field | Meaning |
|---|---|
| `sca_latest_version` | The latest release |
| `sca_behind_level` | `major`, `minor`, `patch` or `none`: the largest component that differs |
| `sca_behind_major`, `sca_behind_minor`, `sca_behind_patch` | How far behind, by version number, per component |
| `sca_behind_releases` | How many releases have been published since the installed one |
| `sca_current_age_days` | Age of the installed version in days. Null when deps.dev has no publish date or that lookup failed; a failed lookup here does not change `sca_status` |

### Current version to patched version (the `sca_patched_diff_*` fields)

The size of the **minimum upgrade**: installed version to the first fixed version. Computed
offline by `kospex.dependencies.version_diff`. Null unless both `current_version` and
`patched_version` are present and both parse as versions.

| Field | Meaning |
|---|---|
| `sca_patched_diff_level` | `major`, `minor`, `patch`, `none` or `downgrade` |
| `sca_patched_diff_major`, `_minor`, `_patch` | The **raw per-component difference**, patched minus current |

**The raw-diff caveat.** The components are subtracted independently, so they can be
negative: `1.9.9` to `3.0.0` is major `2`, minor `-9`, patch `-9`. Read
`sca_patched_diff_level` for the direction and size class, and treat the three numbers as a
rough size, not a distance.

**`behind_*` and `sca_patched_diff_*` answer different questions.** `behind_*` is how far
the installed version is from the *latest* release (how stale). `sca_patched_diff_*` is how
big the *smallest fix* is (the least you must move to clear this alert). A package can be
three majors behind latest while the fix is a patch bump.

### Patched version still vulnerable

From deps.dev's advisories on the **patched** version, with GHSA and PYSEC aliases merged
into one entry each.

| Field | Meaning |
|---|---|
| `sca_patched_version_vulnerabilities` | Count of **all** advisories deps.dev lists on `patched_version`. Nothing is subtracted: the patched version cannot contain the advisory it fixes, so any listed are other advisories |
| `sca_patched_version_max_cvss` | Highest CVSS 3 score among them. **Null when none is scored, never 0**: deps.dev sends `0` with no vector for an unscored advisory, and kospex treats that as unscored |
| `sca_patched_ghsa_ids` | The advisory ids, as a list. An empty list means none |

Null when `patched_version` is missing or its lookup failed. `0` / null / `[]` means
deps.dev knows the patched version and lists no advisories for it. These fields are set
when `patched_version` is present even if `current_version` is not.

## Statuses

`sca_status` is exactly one of:

| Status | When |
|---|---|
| `ok` | Both versions are present, the package was found and deps.dev knows the patched version |
| `no_current_version` | `current_version` is null or empty, typically a ranged requirement. `behind_*` and diff fields are null. The patched-version fields are still filled if `patched_version` is present |
| `no_patched_version` | `current_version` is known but `patched_version` is null: no fix exists. `behind_*` is filled; diff and patched-version fields are null |
| `private_skipped` | The package matches `KOSPEX_DEPS_PRIVATE` (or `GOPRIVATE` for Go). Nothing is sent to deps.dev |
| `unsupported_ecosystem` | The ecosystem is not one of the mapped ones |
| `not_found` | deps.dev **answered 404** for the package (or answered 200 with no versions), or answered 404 for the patched version. Only a 404 counts: it means "deps.dev has no such record". A patched version deps.dev does not know is `not_found`, not `ok` |
| `lookup_error` | Any other failed lookup: network error, TLS error, 5xx, rate limit. These are not cached, so a later alert for the same package retries |

If both `current_version` and `patched_version` are missing, `no_current_version` wins.
Rows are never dropped.

The order in which a row is checked is: ecosystem mapping, private-package check, package
lookup, then the version rules above. A failed package lookup therefore reports
`not_found` / `lookup_error` even when a version is also missing.

### Ecosystems

GitHub's names map as follows; anything else is `unsupported_ecosystem` (matched
case-insensitively).

| GitHub `ecosystem` | Package type |
|---|---|
| `pip` | `pypi` |
| `npm` | `npm` |
| `maven` | `maven` |
| `nuget` | `nuget` |
| `go` | `go` |
| `rubygems` | `rubygems` |
| `rust` | `cargo` |

`composer`, `actions`, `pub`, `swift`, `erlang`, `elm` and the rest are unsupported.

### Private packages

Packages matching `KOSPEX_DEPS_PRIVATE` (comma-separated `ecosystem:prefix`, `maven` and
`go` only; `GOPRIVATE` is honoured for Go) are never looked up, whatever `-enrich` says,
and are written as `private_skipped`. An invalid `KOSPEX_DEPS_PRIVATE` is an error before
anything runs.

## Errors

- **A bad input line stops the run.** A line that is blank, not valid UTF-8, not valid
  JSON, or JSON that is not an object is an error naming the **1-based line number**, and
  the partial output file is **deleted**, so a half-written file never looks complete.
  The message ends `<out> was not written.` Exit code 1.
- **Same file refused.** `-out` equal to the input (checked through symlinks and hard
  links) is refused before the input is opened or the output touched. An existing file at
  `-out` that is not the input is overwritten.
- **Unreadable input or unwritable output** (missing directory, no permission) is an error
  naming both paths, exit 1; any partial output is removed. A file the run did not truncate
  is never deleted.

## Example

An alert as `kgit github --dependabot` writes it (see the
[queryables page](kgit-github-queryables.md)), enriched on 2026-11-24 with real deps.dev
lookups (abbreviated, `...` marks unchanged input keys):

```json
{"repo_id": "github.com~acme~payments", ..., "package_ecosystem": "npm", "package_name": "cookie", ..., "current_version": "0.4.0", "patched_version": "0.7.0", ..., "sca_status": "ok", "sca_latest_version": "2.0.2", "sca_behind_level": "major", "sca_behind_major": 2, "sca_behind_minor": 4, "sca_behind_patch": 5, "sca_behind_releases": 15, "sca_current_age_days": 2748, "sca_patched_diff_level": "minor", "sca_patched_diff_major": 0, "sca_patched_diff_minor": 3, "sca_patched_diff_patch": 0, "sca_patched_version_vulnerabilities": 0, "sca_patched_version_max_cvss": null, "sca_patched_ghsa_ids": []}
```

Reading it: the installed `cookie` 0.4.0 is two majors behind the latest release (2.0.2)
and is about 7.5 years old. The fix is a small step (0.4.0 to 0.7.0, a `minor` change),
and deps.dev lists no advisories on 0.7.0. The latest-release figures and the minimum-fix
figures differ on purpose. These values come from deps.dev on that date and will change as
releases are published.

## Drift watch

What to check when upstream changes. Run a small alerts file with a known-good package
after any of these.

- **GitHub's alert shape.** The enricher reads four keys (`ALERT_KEYS_READ`), all written
  by `kgit github --dependabot`. Check the key names against `ALERT_KEYS` first, and
  whether GitHub has added ecosystems to Dependabot: a new one is `unsupported_ecosystem` until mapped in
  `ECOSYSTEM_MAP` in `src/kospex/dependabot_enrich.py`. Also check how GitHub spells the
  existing ones (`pip` / `rust` are GitHub's names, not deps.dev's).
- **`current_version` semantics.** The enricher trusts it as one exact version. If GitHub
  starts putting a range or a `v` prefix in it, the diff fields silently go null or wrong.
- **deps.dev status codes.** `not_found` relies on a 404 (or a 200 with no versions); any
  other status is `lookup_error`. If deps.dev starts answering a missing package with a
  different code, rows move from `not_found` to `lookup_error`. The same applies to how
  it answers a missing *version* of a known package.
- **deps.dev advisory scoring.** `cvss3Score: 0` with no vector means unscored and is
  reported as null. If deps.dev adds a CVSS 4 field or changes how unscored is sent,
  `sca_patched_version_max_cvss` may be wrong. Advisory ids come from `version_detail()`
  with GHSA / PYSEC aliases merged: if deps.dev changes its alias keys, counts can double.
- **Release ordering and pre-releases.** The latest release excludes pre-releases, as
  `kospex sca -package` does. A package that publishes only pre-releases reports odd
  `behind_*` values.
- **`KOSPEX_DEPS_PRIVATE` / `GOPRIVATE` rules** (`src/kospex/private_packages.py`): any
  change there changes which rows become `private_skipped`.
