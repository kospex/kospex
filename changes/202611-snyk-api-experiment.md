# Snyk API experiment (SCA first)

Status: **exploration / pre-code**. Nothing implemented yet.

## Overview

Add read-only Snyk API queries to the kospex CLI, so you can ask an existing
Snyk estate questions from the command line, such as:

- Which projects have a vulnerable `express`?
- Which vulnerabilities do we know about, and how many projects does each one affect
  (for example, everything on `com.fasterxml.jackson.core:jackson-databind`)?

Scope:

- **SCA only** (`type=package_vulnerability`) for phase 1. Code findings
  (`type=code`) are phase 2.
- **Query and print only.** Nothing is written to the kospex DB and nothing appears in kweb.
  Output is a table, or CSV to a file or stdout. You can combine several CLI runs
  to build up a project's picture.
- **"Which projects *use* package X", vulnerable or not, is out of scope.** kospex
  already answers it from synced repositories (`dependency_data`), so Snyk only adds
  what kospex can't see: Snyk's vulnerability findings and the projects they apply to.

## Constraint: plan tiers

Development and testing is on a **free** Snyk account. The intended users are
**Enterprise** accounts.

Snyk's docs are explicit that the API is mostly Enterprise-only:

> "The majority of Snyk APIs are restricted to use by Enterprise plan customers only."
> — [Snyk API overview](https://docs.snyk.io/developer-tools/snyk-api/snyk-api.md)

> "To use the Snyk API, you must be an Enterprise plan customer and have a token from Snyk."
> — [Authentication for API](https://docs.snyk.io/developer-tools/snyk-api/authentication-for-api.md)

Design rules that follow from this:

1. **Prefer endpoints with no stated plan or feature restriction**: orgs, projects,
   issues and targets. Avoid the ones that are documented as gated:
   - SBOM (`GET /orgs/{org_id}/projects/{id}/sbom`) is Enterprise-only per its how-to.
   - `POST /orgs/{org_id}/packages/issues` is "not available to all customers".
   - `GET /groups/*` is group-scoped, and groups are an Enterprise concept. Keep
     org-scoped calls as the default path, and use the group path only where it saves calls.
2. **Probe first.** Before designing further, run a read-only probe against the free
   account to see which endpoints answer 200 and which return 401/403. Record the
   results here. Assume nothing about free-tier access.
3. **Handle 403 cleanly.** A gated endpoint is reported as "not available on this Snyk
   plan or token" with the endpoint named. It must not raise a traceback or be
   mistaken for "no results".
4. **Build tests from the schema, not a live account.** Pin the REST `version` and
   build fixtures from that version's OpenAPI spec
   (`https://api.snyk.io/rest/openapi/<version>`). Then the code is testable even if
   the free account can't reach an endpoint.

## API surface (as of spec `2026-09-09`)

Basics:

- Base URL: `https://api.snyk.io/rest`. Regional hosts exist (EU, AU), so make the
  host configurable rather than hardcoded. The regional hostnames haven't been checked yet.
- Every call needs `?version=YYYY-MM-DD`. Pin one GA version in code.
- Auth header: `Authorization: token $SNYK_TOKEN`.
- Responses are JSON:API, paged with `starting_after` / `ending_before` cursors and
  `links.next`.

| Purpose | Endpoint | Notes |
|---|---|---|
| Orgs | `GET /orgs` | All orgs the token can access |
| Orgs in a group | `GET /groups/{group_id}/orgs` | Group (Enterprise) |
| Projects | `GET /orgs/{org_id}/projects` | Filters: `ids`, `names`, `names_start_with`, `origins`, `types`, `tags`, `target_id`, `target_reference`, `target_file`, `lifecycle`, `business_criticality`, `environment`. `meta.latest_issue_counts=true` returns severity counts per project, so a summary needs no issue fetch at all |
| Targets (repos) | `GET /orgs/{org_id}/targets` | Maps a project to its repository |
| **Issues** | `GET /orgs/{org_id}/issues` | Filters: `type`, `status` (`open`/`resolved`), `effective_severity_level`, `ignored`, `scan_item.id` + `scan_item.type=project`, `created_before/after`, `updated_before/after`. **`limit` is 10–100, in multiples of 10** |
| Issues (group) | `GET /groups/{group_id}/issues` | Same filters, all orgs in one walk |
| Package lookup | `GET /orgs/{org_id}/packages/{purl}/issues` | Known vulnerabilities for one purl. **Does not say which projects use it.** Overlaps with what `kospex sca` already gets from deps.dev |
| Export | `POST /orgs/{org_id}/export`, poll `GET /orgs/{org_id}/jobs/export/{id}`, then `GET /orgs/{org_id}/export/{id}` | CSV, 20 starts per hour, data about 2 hours old. Plan restriction not stated. A candidate for whole-estate runs, see below |
| Dependencies (v1) | `POST /org/{orgId}/dependencies` | v1, "will eventually be deprecated", and v1 is documented as Enterprise-only. Not needed, because kospex already holds usage data |

The fields of an issue that matter for SCA:

- `attributes.coordinates[].representations[].dependency.package_name` / `.package_version`
- `attributes.problems[]`: `id` + `source` (Snyk ID, CVE)
- `attributes.effective_severity_level`, `status`, `ignored`, `risk.score`,
  `coordinates[].reachability`, `coordinates[].is_upgradeable` / `is_fixable_*`
- `relationships.scan_item.data.id` gives the **project id**, and
  `relationships.organization.data.id` gives the org

Not in the REST issue response: the fixed-in version and the path the package was
introduced through. The Export API has them (`FIXED_IN_VERSION`,
`EXISTS_IN_DIRECT_DEPENDENCY`).

**There is no server-side filter by package name.** Package queries fetch the whole
issue list and filter it locally.

## How the two target queries map

### "Which projects have a vulnerable express?"

1. `GET /orgs/{org_id}/issues?type=package_vulnerability&status=open`, following
   every page (`limit=100`).
2. Keep issues where any `representations[].dependency.package_name == "express"`.
3. Group by `scan_item` id, then get names with
   `GET /orgs/{org_id}/projects?ids=...`.
4. Output one row per project and vulnerability: org, project, package version,
   Snyk ID or CVE, severity.

Across several orgs, repeat this per org from `GET /orgs`.

### "Every vulnerability, and how many projects it affects"

Use the same issue fetch, grouped by `problems[].id` (or package + Snyk ID), counting
distinct `scan_item` ids. Example output:

```
package                                     vuln (Snyk ID / CVE)                 severity  projects
com.fasterxml.jackson.core:jackson-databind SNYK-JAVA-...  / CVE-20xx-xxxxx     high      37
```

### Cost and the Export API alternative

The issue fetch is `ceil(issues / 100)` calls per org. That's fine for a small estate
and slow for a large one. The Export API answers both queries from one CSV
(`PROBLEM_ID`, `CVE`, `PACKAGE_NAME_AND_VERSION`, `PROJECT_NAME`,
`PROJECT_PUBLIC_ID`, `ISSUE_SEVERITY`, `FIXED_IN_VERSION`, ...). It's asynchronous,
limited to 20 starts per hour, and its plan restriction is unknown. **Decide after the
probe:** if Export works on the target accounts, it may be the better engine for the
estate-wide aggregate, with live issue fetches for targeted queries.

## CLI shape

**Decided:** a `kospex snyk` command group, the same pattern as `kospex rebuild`. It
sits next to the related `sca`, `deps` and `advisory-history` commands, and adds one
top-level name rather than several.

Rejected:
- `kgit snyk`: kgit's vendor commands list a host's *repos* so they can be cloned, and
  Snyk is not a repo source.
- `krunner`: every krunner command runs over a kospex scope (`REPO_ID|-all|-org|-server`),
  which Snyk queries do not take.
- A `ksnyk` entry point: it would need its own logger and banner wiring, and suggests
  one command per vendor.
- An option on `kospex sca`: different inputs and output.

Subcommands (names still a draft):

```
kospex snyk orgs                                    # list orgs the token can see
kospex snyk projects  [-org ORG]                    # projects + latest issue counts
kospex snyk issues    [-org ORG] [-package NAME] [-severity high,critical] [-project ID]
kospex snyk vulns     [-org ORG] [-package NAME]    # aggregate: vuln -> project count
kospex snyk probe                                   # which endpoints this token/plan can reach
```

## Code layout

**Decided:** vendor integrations live in their own subpackage, so they don't add to the
flat `src/kospex_*.py` modules or to the top level of `src/kospex/`:

```
src/kospex/integrations/
  __init__.py            # empty: keep it import-light (see the lazy __getattr__
                         #   note in kospex/__init__.py on circular imports)
  snyk/
    __init__.py
    client.py            # SnykClient: token, base URL/region, pinned REST version,
                         #   cursor paging, 403 -> a clear "not available" error
    queries.py           # pure functions: filter by package, vuln -> project counts
                         #   (same pattern as kospex/authorship.py)
    cli.py               # the `snyk` Click group
```

- `kospex_cli.py` only does `cli.add_command(snyk)`. The command code stays in the
  subpackage.
- No `pyproject.toml` change is needed: `packages.find` already includes `kospex*`.
  The hand-maintained `py-modules` list is only for flat modules. Still, check the
  **built wheel** contains `kospex/integrations/snyk/`, because an editable install
  hides packaging gaps.
- A later integration (e.g. Checkmarx) gets `kospex/integrations/<vendor>/` with the
  same three files.
- No optional extra (`kospex[snyk]`) and no entry-point plugins for now. Snyk needs only
  `requests`, which kospex already depends on. Revisit if an integration needs its own SDK.
- The existing `src/kospex/github_dependabot.py` / `github_properties.py` are left
  where they are. Moving them under `integrations/github/` is a separate follow-up.

Common behaviour:

- Token from `SNYK_TOKEN`, with no token arguments on the command line. Read-only
  calls only.
- Table by default. `-out FILE [-overwrite]` / `-stdout` for CSV, escaped with
  `krunner_utils.spreadsheet_safe` (CWE-1236) and `-raw` to disable, the same as
  `krunner key-person` / `bus-factor`.
- Rich tables with `markup=False`, because package names and titles contain brackets.
- No new Python dependency. Use plain `requests`; don't add `pysnyk`.

## Phase 2: code findings

The same `GET /orgs/{org_id}/issues` with `type=code` (plus `include_code_flows`)
returns `representations[].sourceLocation` (`file`, `region`, `commit_id`). So most
of the phase 1 plumbing carries over.

## Open questions

- [ ] Probe results on the free account: which endpoints return 200 and which 403?
- [ ] Is the Export API available on Enterprise accounts by default, or a separate entitlement?
- [ ] Confirm the regional base URLs and how a user selects one (env var such as `SNYK_API`?).
- [x] Command placement: `kospex snyk` group (see CLI shape).
- [x] Code layout: `src/kospex/integrations/snyk/` (see Code layout).
- [ ] Is an issue returned once per project, or once per project *and* version? Count
      distinct projects either way, but confirm before reporting counts.

## Sources

- Live REST OpenAPI spec: `https://api.snyk.io/rest/openapi/2026-09-09`
- [Snyk API overview](https://docs.snyk.io/developer-tools/snyk-api/snyk-api.md)
- [Authentication for API](https://docs.snyk.io/developer-tools/snyk-api/authentication-for-api.md)
- [Issues API reference](https://docs.snyk.io/snyk-api/reference/issues)
- [V1 Reporting/Aggregated Issues → REST migration guide](https://docs.snyk.io/api-end-of-life-eol-process-and-migration-guides/guides-to-migration/v1-reporting-issues-and-v1-aggregated-issues-apis-to-rest-issues-api-migration-guide)
- [Export API: columns and filters](https://docs.snyk.io/snyk-api/using-specific-snyk-apis/export-api-specifications-columns-and-filters)
- [List issues for a package](https://docs.snyk.io/snyk-api/list-issues-for-a-package-endpoint)
- [Project SBOM how-to](https://docs.snyk.io/developer-tools/snyk-api/using-specific-snyk-apis/sbom-apis/rest-api-get-a-projects-sbom-document.md)
