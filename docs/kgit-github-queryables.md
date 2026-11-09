# kgit github: queryable exports

Commands that pull data from the GitHub API into a file another script can load. Each
section covers one kind of data: how to run it, what access it needs, and what the file
contains.

- [Custom properties](#custom-properties)

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

# One property for every repo
sqlite-utils props.db "select repo_id, json_extract(properties, '$.business_unit') as bu from props" -t

# Every property and value, with counts
sqlite-utils props.db "select j.key, j.value, count(*) as repos
                       from props, json_each(props.properties) j group by 1, 2" -t

# Repos without a business_unit (errors excluded)
sqlite-utils props.db "select repo_id from props
                       where error is null and json_extract(properties, '$.business_unit') is null" -t
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
