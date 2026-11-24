# `kospex sca -dependabot`: enrich Dependabot alerts

Status: built (see docs/sca-dependabot.md).

## Overview

`kgit github --dependabot` writes one JSONL line per open alert. Dependabot gives
no upgrade-distance figures, and cannot say whether the *fixed* version is itself
vulnerable. `kospex sca -dependabot IN.jsonl -out OUT.jsonl -enrich` copies each
line through and appends `sca_*` fields answering:

- how far is the installed version from the latest release?
- what is the upgrade to the patched version (the minimum fix)?
- does the patched version still carry advisories?

## Usage

    kospex sca -dependabot alerts.jsonl -out alerts-enriched.jsonl [-enrich]

- `-out` is required; output is JSONL, same order, input keys unchanged.
- Every output line has every new key (null when unknown).
- Lookups are the whole point. If lookups are off (`KOSPEX_DEPS_ENRICH=false`)
  the command exits with an error unless `-enrich` is passed, the same rule and
  wording as `-package`. With `KOSPEX_DEPS_ENRICH=true`, `-enrich` is harmless.
- Refused with `-dependabot`: `-json`, `-scope`, `-save`, `-package`, `-repo`, FILE_PATH.
- A line that is not JSON is an error naming the line number.

## Fields added

Current -> latest release (existing `behind_fields()`, by version number):

- `sca_latest_version`, `sca_behind_level`, `sca_behind_major`, `sca_behind_minor`,
  `sca_behind_patch`, `sca_behind_releases`
- `sca_current_age_days`: age of the installed version
- `sca_latest_age_days`: age of the latest release, from the release list (no extra request)
- `sca_patched_age_days`: age of the patched version, from the patched-version lookup
  already made for its advisories

Current -> patched version (existing `version_diff()` / `change_level()` in
`kospex.dependencies.version_diff`, offline, raw per-component difference):

- `sca_patched_diff_level` (major / minor / patch / none / downgrade)
- `sca_patched_diff_major`, `sca_patched_diff_minor`, `sca_patched_diff_patch`
- `sca_patched_releases`: how many releases lie between the installed and the patched
  version, counted like `sca_behind_releases` (see "Added after the design")

Raw means 1.9.9 -> 3.0.0 is major 2, minor -9, patch -9; read the level for
direction. This is the rough size of the minimum upgrade. The `behind_*` fields
are the distance to *latest*, a different question.

Patched version still vulnerable (deps.dev, `version_detail()` advisories):

- `sca_patched_version_vulnerabilities`: count of advisories on `patched_version`,
  GHSA/PYSEC aliases merged. Counts all of them: the patched version cannot
  contain its own fix's advisory, so nothing is subtracted.
- `sca_patched_version_max_cvss`: highest CVSS 3 score among them; null when
  unscored (deps.dev sends 0 for unscored, never take it at face value).
- `sca_patched_ghsa_ids`: which advisories remain.

Outcome:

- `sca_status`: `ok`, `no_current_version`, `no_patched_version`, `private_skipped`,
  `unsupported_ecosystem`, `not_found`, `lookup_error`. A row we cannot enrich is
  kept, with its reason, never dropped or left silently blank.

## Approaches

One sensible way: reuse the existing release / advisory / diff functions per
unique (ecosystem, package) and (package, version), in a pure module. Alternatives
rejected: storing alerts in the DB (nothing queries them yet, and it needs a
migration); a separate top-level command (project preference is a mode on `sca`).

## Implementation notes

- Logic in `src/kospex/dependabot_enrich.py` (pure, tested); thin Click branch in `sca`.
- GitHub ecosystem names are mapped to `registry.package_types()`; an unmappable one
  is `unsupported_ecosystem`.
- Private packages skipped via `lookup_permitted()` -> `private_skipped`.
- Lookups deduplicated per package / version; url_cache makes repeats cheap.
- `current_version` is null for ranges: diff and behind fields are then null.
- Progress and a summary of statuses go to stderr.
- Tests drive the production function with fixture alerts and stubbed deps.dev
  responses, and assert every status and the all-keys-present contract.

## Docs

Silent for now: documented in a `docs/` page not linked from other pages or
`sitemap.xml`, and no CHANGELOG entry, until the `kgit github --dependabot`
content is released together. Register it as a silent feature.

## Differences from the design

Found comparing the built code to this design (2026-11-24):

- **Input key names.** The enricher reads `package_ecosystem`, `package_name`,
  `current_version` and `patched_version`, the names `kgit github --dependabot` writes
  (`ALERT_KEYS`). An earlier build read `ecosystem` / `package`, which no real file has;
  tests now build alerts from `ALERT_KEYS` and the production `alert_line()`, and check
  `ALERT_KEYS_READ` against `ALERT_KEYS`.
- **Input errors.** Beyond "not JSON", a blank line, invalid UTF-8 and JSON that is not
  an object are also errors naming the line; the partial output is deleted.
- **Same-file refusal.** `-out` equal to the input is refused (not in the design).
- **Patched version unknown to deps.dev.** Reported as `not_found`, not `ok`.
- **Only a 404 is `not_found`;** any other failed lookup is `lookup_error` and is not
  cached.
- **No current-version advisories.** Only the patched version's advisories are looked
  up; the current version is fetched for its age only. The `sca -h` text now says so.

## Added after the design: patched and latest ages

`sca_patched_age_days` and `sca_latest_age_days` were added on request, to sit beside
`sca_current_age_days` so all three versions have an age. Neither costs a request: the
patched version's age comes from the lookup already made for its advisories, the latest
release's from the release list. Whole days to the run date, null when deps.dev has no
publish date.

`sca_patched_releases` was added the same way, because `sca_behind_releases` only measures
to the *latest* release and nothing counted the releases to the *fixed* one. It uses
`releases_between(current, to_version=patched)`: stable releases by version number above the
installed version up to and including the patched one. Null when either version is a
pre-release, on a downgrade, or when either version is missing. The `sca_behind_releases`
wording in `docs/sca-dependabot.md` was also tightened: it counts by version number, not
publish date, and includes patches to superseded minor lines, which is why the
`sca_behind_major` / `_minor` / `_patch` upgrade-path counts do not sum to it.
