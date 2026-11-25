# sca -dependabot -next-clean: the next release with no known advisories

Status: approved design, MVP. Builds on `changes/202611-advisory-history.md` and
`docs/sca-dependabot.md`.

## Why

A Dependabot alert names the first fixed version, but that release can still carry other
advisories (`sca_patched_version_vulnerabilities` already says how many, from deps.dev).
`kospex advisory-history` finds, for one package, the first release above a version with
no known advisories in OSV. `-next-clean` gives each alert that answer, so a remediation
list can say where to move to, not just the minimum fix.

## What

```
kospex sca -dependabot alerts.jsonl -out enriched.jsonl -next-clean
```

- Only valid with `-dependabot` (a usage error otherwise). Without the flag the output is
  unchanged, so dropping the flag removes everything this adds, which matters where
  `api.osv.dev` is blocked.
- Per alert, from `current_version`: the first stable, non-deprecated release above it with
  no non-withdrawn OSV advisory. Same definition and functions as `advisory-history`
  (`kospex.integrations.osv.queries`): "clean" means no known advisories, not safe.
- Per package, not per alert: one deps.dev release list (already cached), one OSV
  `querybatch` per 1000 stable releases, one cached `vuln()` fetch per distinct advisory.
- Private packages are gated before any OSV call (`lookup_permitted`, as today).

New fields, present on every row only when the flag is given (null when not attempted or
unknown):

| Field | Meaning |
|---|---|
| `sca_next_clean_version` | The release. Null when `none`, `current_has_no_osv_advisory` or not looked up |
| `sca_next_clean_age_days` | Days since it was published; null when undated |
| `sca_next_clean_affected_between` | Releases with a known advisory between the current version and it (all later ones when `none`) |
| `sca_next_clean_status` | `found`, `found_below_patched` (OSV is clean at a version below the alert's own fixed version), `current_has_no_osv_advisory` (OSV lists nothing for the installed version, though Dependabot raised an alert on it), `none` (every later release is known to have an advisory), `no_current_version`, `current_is_prerelease`, `not_in_release_list`, `lookup_error`. Null when the row never got as far as a release list (see `sca_status`) |

An OSV failure sets only `sca_next_clean_status`; `sca_status` is untouched. After 3
failed OSV batches in a row the run stops calling OSV, says so once on stderr, and the
remaining packages are `lookup_error`: a firewalled `api.osv.dev` must not turn a large
run into a crawl through retries. A release whose advisories could not be read is
skipped, never offered as clean.

## Not in this change (follow-up issue)

- Moving the release/OSV orchestration out of `kospex_cli._advisory_history_data` into a
  shared function. The enricher repeats about 25 lines of it for now.
- Whether this should be on by default, and field naming if more OSV-derived fields
  follow.
- Redirect handling on the cached record fetch (tracked privately).

## Tasks

1. `Enricher(kdeps, next_clean=False, osv_client=None)` adds the fields and lookup, with
   tests (`tests/test_dependabot_next_clean.py`) over a stubbed deps layer and a fake OSV.
2. `-next-clean` on `sca`, usage error without `-dependabot`, stderr summary of
   `sca_next_clean_status` counts and the stopped-OSV warning.
3. `docs/sca-dependabot.md`, CHANGELOG `## Unreleased`, CLAUDE.md bullet.

## Changes after review

An independent review of #494 found two ways a status could mislead, both fixed in
`next-clean-status-fixes`: `none` was returned when later releases were merely unreadable
(now `lookup_error`), and a release below the alert's `patched_version`, or an installed
version OSV has no advisory for, gave no sign that OSV and Dependabot disagree (now
`found_below_patched` and `current_has_no_osv_advisory`; `start_clean` is no longer a value of this
field). Seven smaller points are tracked in a follow-up issue.
