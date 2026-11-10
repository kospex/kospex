# kgit github: queryable exports

Commands that pull data from the GitHub API into a file another script can load. Each
section covers one kind of data: how to run it, what access it needs, and what the file
contains.

- [Custom properties](#custom-properties)
- [Dependabot alerts](#dependabot-alerts)

Both exports key each line by the kospex `repo_id` (`github.com~acme~payments`, lowercased,
the same as in the kospex DB) and by `github_repo_id`, GitHub's numeric repo id, which
doesn't change when a repo is renamed or transferred. Either one joins the two files.

## Custom properties

GitHub [custom properties](https://docs.github.com/en/organizations/managing-organization-settings/managing-custom-properties-for-repositories-in-your-organization)
are org-defined key/value tags on repositories, such as `business_unit` or `asset_id`.
`kgit github --properties` writes each repo's values to a JSONL file.

### Running it

```bash
# Every repo GitHub lists for the org, in one org-wide query
kgit github --properties -org acme -filename acme.jsonl

# The same, one request per repo (when the org-wide query is refused)
kgit github --properties -org acme -level repo -filename acme.jsonl

# One repo
kgit github --properties -repo_id github.com~acme~payments -filename payments.jsonl
```

| Option | |
|---|---|
| `-org ORG` | Every repo GitHub lists for the org. This asks GitHub, not the kospex DB, so repos you haven't synced are included. |
| `-repo_id REPO_ID` | One repo, as a kospex repo_id (`github.com~OWNER~REPO`). It doesn't need to be synced. Costs one extra request, for the repo's GitHub id. |
| `-filename FILE` | Required. The file is overwritten. |
| `-level org\|repo` | How the values are read (below). Defaults to `org` for `-org`. `-repo_id` is always `repo`. |

Exactly one of `-org` or `-repo_id` is required. `-properties` works as well as
`--properties`.

The token comes from `GITHUB_AUTH_TOKEN`, `GH_TOKEN` or `GITHUB_TOKEN`, checked in that
order. To use your `gh` login: `export GH_TOKEN=$(gh auth token)`.

### Levels and access

Both levels write the same lines. They differ in which API they call and the access it needs.

| Level | API | Requests | Access needed |
|---|---|---|---|
| `org` | [`GET /orgs/{org}/properties/values`](https://docs.github.com/en/rest/orgs/custom-properties) | about 1 per 100 repos | Org member. A classic PAT needs `read:org`, plus `repo` to include private and internal repos. |
| `repo` | `GET /orgs/{org}/repos`, then [`GET /repos/{owner}/{repo}/properties/values`](https://docs.github.com/en/rest/repos/custom-properties) for each repo | about 1 per 100 repos, plus 1 per repo | Read access to each repo |

Start with `org`. For a 2,000-repo org, `repo` makes about 2,020 requests against a limit of
5,000 an hour. If the org-level query is refused, it stops with a 404 or 403, writes
nothing and suggests `-level repo`. It never falls back by itself, so the `level` field
always shows which level produced a line.

The Security Manager org role should cover both levels: it gives read access to every repo,
and only org members can hold it.

### The file

JSON Lines: one object per repo, UTF-8, every line ending in a newline.

```json
{"repo_id": "github.com~acme~payments", "github_repo_id": 101, "org": "acme", "repo": "payments", "level": "org", "fetched_at": "2026-11-09T03:12:00Z", "properties": {"business_unit": "Retail", "compliance": ["pci", "sox"]}, "error": null}
{"repo_id": "github.com~acme~docs", "github_repo_id": 102, "org": "acme", "repo": "docs", "level": "org", "fetched_at": "2026-11-09T03:12:00Z", "properties": {}, "error": null}
{"repo_id": "github.com~acme~legacy", "github_repo_id": null, "org": "acme", "repo": "legacy", "level": "repo", "fetched_at": "2026-11-09T03:12:00Z", "properties": null, "error": "HTTP 404 Not Found"}
```

Every line has the same keys. Loaders that work out columns from the first rows (sqlite-utils
reads the first 100) would otherwise fail, or stop part-way through, at the first `error` line.

| Field | |
|---|---|
| `repo_id` | The kospex repo_id, lowercased, so it joins to `repos.repo_id` for synced repos |
| `github_repo_id` | GitHub's numeric repo id. It survives renames and transfers, so it's the key to use against GitHub's own data. `null` only when a single `-repo_id` couldn't be read. |
| `org`, `repo` | GitHub's own casing |
| `level` | `org` or `repo` |
| `fetched_at` | When the run started (UTC). It's the same on every line of a run. |
| `properties` | `{name: value}`. A multi-select value is an array. An unset property is left out. `null` when the repo couldn't be read. |
| `error` | `null`, or why the repo couldn't be read (for example `HTTP 404 Not Found`) |

So:

- `"properties": {}` means the repo has no properties set.
- `"properties": null` with an `error` means the repo couldn't be read. Its properties are
  unknown.
- **Unset properties are left out, not written as `null`.** The org endpoint returns every
  property in the org's schema with `null` for unset ones, but the repo endpoint leaves
  them out. Dropping nulls makes both levels write the same line. To list the properties
  an org defines, use
  [`GET /orgs/{org}/properties/schema`](https://docs.github.com/en/rest/orgs/custom-properties).

If any page of the org query or the repo listing fails, nothing is written. The same
applies if the rate limit is hit during a repo-level run. Rerun to retry.

### What it can't see

Property values are readable by anyone who can read the repo. A public repo's values are
visible even without a token, so `{}` on a public repo really means none are set. A repo
the token can't read returns 404, which becomes a line with an `error`.

**What can go missing without an error is whole repos.** The org query and the repo listing
return only the repos the token can see. If the token isn't an org member, that's public
repos only, and private and internal repos are left out of the file entirely. The command
prints a note saying so after every `-org` run.

### Using the file

Concatenate the files from several orgs. Every line carries its own `repo_id`, `level` and
`fetched_at`:

```bash
cat acme.jsonl acme-labs.jsonl > all.jsonl
```

Load the result into SQLite with [sqlite-utils](https://sqlite-utils.datasette.io/). With
`--replace`, a later line for the same `repo_id` wins, so rerunning an org updates it:

```bash
sqlite-utils insert props.db props all.jsonl --nl --pk repo_id --replace

# Repos that couldn't be read
sqlite-utils props.db "select repo_id, error from props where error is not null" -t
```

Or look one repo up in Python:

```python
import json

props = {}
with open("all.jsonl", encoding="utf-8") as f:
    for line in f:
        row = json.loads(line)
        props[row["repo_id"]] = row

row = props["github.com~acme~payments"]
if row["error"]:
    print("couldn't read:", row["error"])
else:
    print(row["properties"].get("business_unit"))   # None when unset
```

## Dependabot alerts

`kgit github --dependabot` writes every **open** Dependabot alert to a JSONL file, one line
per alert. The same run writes a second file with each repo's Dependabot status, so a repo
with no alerts can be told apart from one where Dependabot is off or the token can't see
alerts.

### Running it

```bash
# Every repo GitHub lists for the org
kgit github --dependabot -org acme -filename acme-alerts.jsonl

# One repo
kgit github --dependabot -repo_id github.com~acme~payments -filename payments-alerts.jsonl
```

| Option | |
|---|---|
| `-org ORG` | Every repo GitHub lists for the org, whether or not it's synced to kospex |
| `-repo_id REPO_ID` | One repo, as a kospex repo_id (`github.com~OWNER~REPO`). It doesn't need to be synced. |
| `-filename FILE` | Required. The alerts file, overwritten if it exists. The status file is written next to it, with `-status` added to the name: `acme-alerts.jsonl` gives `acme-alerts-status.jsonl`. |

- **Scope:** exactly one of `-org` or `-repo_id` is required.
- **Spelling:** `-dependabot` works as well as `--dependabot`.
- **No `-level`:** there is no org-level option. Every run asks each repo for its alerts,
  because that request is also the only reliable way to learn the repo's Dependabot status.
  A 2,000-repo org takes about 2,020 requests, against GitHub's limit of 5,000 an hour.
- **Token:** the same as for `--properties`.

**Progress.** Each repo prints a line as it finishes, and the same line goes to
`~/kospex/logs/kgit.log`, so a long run can be followed from another terminal with
`tail -f ~/kospex/logs/kgit.log`:

```
[412/1300] acme/payments: enabled, 7 open alerts
[413/1300] acme/docs: disabled
```

**Files are written as the run goes.** Each repo's lines are added to
`acme-alerts.jsonl.partial` and `acme-alerts-status.jsonl.partial` as soon as it's read.
Only a finished run renames them to the names you gave, so a file without `.partial` is
always a complete run, and a previous complete file is left alone until a new run
finishes.

If a run stops partway, the `.partial` files keep every repo it finished and the message
says how many. That happens if:
- the GitHub rate limit is hit, including the secondary limits GitHub applies to bursts
  of requests;
- the first repos of an `-org` run are all `no_access` (the token can't see alerts);
- the run is interrupted (Ctrl-C).

The `.partial` files load the same way as complete ones. Rerun to get every repo.

If the repo listing itself fails, nothing is written, because no repo has been read.

### Access

Seeing a repo's alerts needs **Write, Maintain or Admin** on it, or the org's **Security
Manager** role. Org owners have Admin everywhere. That's more than `--properties` needs: a
read-only token sees every repo as `no_access`.

For `-org`, if the first 5 repos are all `no_access`, the run stops there and writes
nothing, rather than walking the whole org to learn the same thing.

| What GitHub returns | Status |
|---|---|
| `200`, with or without alerts | `enabled` |
| `403 Dependabot alerts are disabled for this repository.` | `disabled` |
| `403 You are not authorized to perform this operation.` | `no_access` |
| `403 Resource not accessible by personal access token` (a fine-grained token without "Dependabot alerts: Read") | `no_access` |
| `404` | `not_found` |
| anything else | `error` |

Two of these haven't been checked against a real account; they come from GitHub's
documentation:
- the fine-grained token message;
- the Security Manager role.

Everything else was measured with an org owner's classic token (`repo`, `read:org`).

### The alerts file

One object per open alert:

```json
{"repo_id": "github.com~acme~payments", "github_repo_id": 101, "org": "acme", "repo": "payments", "manifest_path": "web/package-lock.json", "package_ecosystem": "npm", "package_name": "cookie", "relationship": "transitive", "scope": "runtime", "version_requirement": "0.4.0", "current_version": "0.4.0", "vulnerable_range": "< 0.7.0", "patched_version": "0.7.0", "ghsa_id": "GHSA-pxg6-pf52-xh8x", "cve_id": "CVE-2024-47764", "summary": "cookie accepts cookie name, path, and domain with out of bounds characters", "severity": "low", "cwes": ["CWE-74"], "published_at": "2024-10-04T20:31:00Z", "cvss_v3_score": null, "cvss_v3_vector": null, "cvss_v4_score": null, "cvss_v4_vector": null, "epss_percentage": 0.00744, "epss_percentile": 0.54556, "alert_number": 60, "alert_url": "https://github.com/acme/payments/security/dependabot/60", "state": "open", "created_at": "2026-11-09T09:54:34Z", "updated_at": "2026-11-09T09:54:34Z", "fetched_at": "2026-11-09T10:08:51Z"}
```

| Field | |
|---|---|
| `repo_id`, `github_repo_id`, `org`, `repo` | The repo, as in the properties file |
| `manifest_path` | The file the alert is raised from, e.g. `requirements.txt`, `web/package-lock.json` |
| `package_ecosystem`, `package_name` | e.g. `pip` / `requests`, `npm` / `cookie` |
| `relationship` | `direct`, `transitive` or `unknown` |
| `scope` | `runtime` or `development` |
| `version_requirement` | As GitHub reports it: `= 2.19.0` for a pinned pip requirement, `0.4.0` for an npm lockfile, `""` for a ranged pip requirement |
| `current_version` | The installed version when the requirement names exactly one (`2.19.0`, `0.4.0`), otherwise `null` |
| `vulnerable_range` | The versions the advisory affects |
| `patched_version` | The first fixed version. **`null` when there is no fix**, so upgrading isn't an option |
| `ghsa_id`, `cve_id`, `summary`, `severity`, `cwes`, `published_at` | The advisory. `cve_id` can be `null`; `cwes` is a list of ids |
| `cvss_v3_score`, `cvss_v3_vector`, `cvss_v4_score`, `cvss_v4_vector` | `null` when the advisory has no score of that version. GitHub reports those as `0.0`, which would sort them as the least severe. |
| `epss_percentage`, `epss_percentile` | EPSS exploit-likelihood scores |
| `alert_number`, `alert_url`, `state`, `created_at`, `updated_at` | The alert. `alert_number` is per repo, so the key is (`repo_id`, `alert_number`) |
| `fetched_at` | When the run started (UTC), the same on every line of a run |

### The status file

One object per repo:

```json
{"repo_id": "github.com~acme~payments", "github_repo_id": 101, "org": "acme", "repo": "payments", "dependabot_status": "enabled", "open_alerts": 67, "error": null, "fetched_at": "2026-11-09T10:08:51Z"}
{"repo_id": "github.com~acme~docs", "github_repo_id": 102, "org": "acme", "repo": "docs", "dependabot_status": "disabled", "open_alerts": null, "error": null, "fetched_at": "2026-11-09T10:08:51Z"}
```

`open_alerts` is a count only for `enabled`. For any other status it's `null`, not `0`,
because the count isn't known. `error` holds the HTTP status and GitHub's message for
`no_access`, `not_found` and `error`.

### What it can't see

- **No alerts doesn't mean no vulnerable dependencies.** Dependabot can only match a
  requirement to an advisory when it knows the version.
  - A lockfile (`package-lock.json`) or an exact pin (`requests==2.19.0`) gives it one.
  - A ranged or unpinned pip requirement (`urllib3>=1.24,<1.25`) doesn't. GitHub records no
    version for it, so it gets no alerts unless the advisory affects every version.

  In testing, `urllib3>=1.24,<1.25` and `Jinja2>=2.10,<3.0` raised no alerts, although every
  version either range allows has known advisories (10 for urllib3 1.24.x). So a repo that
  doesn't pin can look cleaner than one that does. The command prints a note about this
  after every run.
- **Fixed and dismissed alerts aren't included**, only open ones.
- **Alerts can still be arriving after a push.** In testing, a new lockfile's alerts arrived
  over more than 10 minutes: 8 were there a few minutes after the push, and 16 later.
- **Whole repos can go missing**, as for `--properties`: the repo listing only includes the
  repos the token can see.

### Using the files

```bash
cat acme-alerts.jsonl acme-labs-alerts.jsonl > alerts.jsonl
cat acme-alerts-status.jsonl acme-labs-alerts-status.jsonl > status.jsonl

sqlite-utils insert dep.db alerts alerts.jsonl --nl --pk repo_id --pk alert_number --replace
sqlite-utils insert dep.db status status.jsonl --nl --pk repo_id --replace

# Repos by Dependabot status
sqlite-utils dep.db "select dependabot_status, count(*) from status group by 1" -t
```

Join to the properties file on `repo_id`:

```bash
sqlite-utils insert dep.db props all.jsonl --nl --pk repo_id --replace
sqlite-utils dep.db "select a.repo_id, a.package_name, a.severity, p.properties
                     from alerts a join props p using (repo_id)" -t
```
