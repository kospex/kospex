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
- `sca_current_age_days`

Current -> patched version (existing `version_diff()` / `change_level()` in
`kospex.dependencies.version_diff`, offline, raw per-component difference):

- `sca_patched_diff_level` (major / minor / patch / none / downgrade)
- `sca_patched_diff_major`, `sca_patched_diff_minor`, `sca_patched_diff_patch`

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

- **Input key names.** The design says the enricher reads the alert's ecosystem and
  package, and the code reads `ecosystem` and `package`. The file `kgit github
  --dependabot` writes names them `package_ecosystem` and `package_name`, so a real
  alerts file comes out all `unsupported_ecosystem`. Measured with four alerts built from
  `ALERT_KEYS`: 4 of 4 unsupported; the same alerts with `ecosystem` / `package` added
  enriched normally. Needs a fix in the enricher (read the real key names).
- **Input errors.** Beyond "not JSON", a blank line, invalid UTF-8 and JSON that is not
  an object are also errors naming the line; the partial output is deleted.
- **Same-file refusal.** `-out` equal to the input is refused (not in the design).
- **Patched version unknown to deps.dev.** Reported as `not_found`, not `ok`.
- **Only a 404 is `not_found`;** any other failed lookup is `lookup_error` and is not
  cached.
- **No current-version advisories.** The `sca -h` text says the output has deps.dev
  advisories "for the current and patched versions"; only the patched version's are
  looked up, as the design says.
