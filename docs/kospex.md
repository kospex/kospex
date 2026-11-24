# kospex

This is the main command to run analysis, sync directories, find orphaned repos, check dependencies, manage groups, health checks and more!

## developers

List all the developers in the kospex directory, by default, only those with active commits in the last 90 days.

```bash
kospex developers
```

If you want developers for "all time", use the `-all` flag:

```bash
kospex developers -all
```

If you need to restrict the search to only in a given domain name / server:

```bash
kospex developers -server [DOMAIN_NAME]
```

## key-person

Identifies the key people for a repo by number of commits. It lists the top committers of
all time, plus any of the top active committers (last 90 days) not already in that list.
This is the starting point for key person risk and off-boarding knowledge transfer.

Give it either a repo directory or a `repo_id`. Run on its own, it prints its help.

**By directory**

```bash
kospex key-person .
```

The directory is only used to work out the `repo_id`, from its git remote. The figures come
from the kospex database, as of that repo's last sync, and the command ends with a note
giving the sync date. Commits made since then are not counted until you sync it again with
[`kospex sync-directory`](#sync-directory).

**By repo_id**

```bash
kospex key-person -repo_id github.com~kospex~kospex
```

This needs no clone on disk, only a previous sync. If the database has never seen the
`repo_id`, or has no commits for it, the command says so and exits with an error instead
of printing an empty table.

The table has these columns:

- `author` — the commit author's email
- `commits` — authored commits, excluding merges
- `merges` — merge commits, counted separately because a merge is not authorship. Compare
  them within a repo only: a project that squash-merges has none.
- `% commits` — share of the repo's authored commits
- `last_commit`, `first_commit`
- `active_commits`, `% active` — the same, for the last 90 days
- `tenure` — years between first and last commit

**Parameters**

- `-repo_id` — the repo to analyse, in the format `SERVER~ORG~REPO`
- `-top` — the number of top authors to assess (default 4)

## sync-directory

The sync-directory command finds every git repo in the given directory (including the
directory itself, if it's a repo) takes all the commit history and adds it to the kospex
database.

```bash
kospex sync-directory [DIRECTORY]
```

Most likely, you'll cd to a repo and then run the following command:

```bash
kospex sync-directory .
```

Or sync everything you've cloned in one go:

```bash
kospex sync-directory ~/code
```

**Parameters**

- `-force` — sync repos already synced from another directory, repointing them.

To clone and sync in a single step, use [`kgit clone`](kgit). To refresh clones kospex
already knows about, use `kgit pull`.

## deps

The deps command finds dependency files in a directory or repo.

```bash
kospex deps -repo PATH/TO/REPO
kospex deps -directory PATH
```

It doesn't contact deps.dev. To assess a single dependency file, use
[`kospex sca FILE`](#sca).

## sca

The sca command runs a lightweight software composition analysis (SCA) of a single
dependency file, using the deps.dev API. It is meant for looking at a repo: it shows
results and, unless you pass `-save`, does not write anything to the kospex database.

```bash
kospex sca requirements.txt
```

The output is the file name, one tab-separated line per package (name, declared
version, release date), then a table:

```
requirements.txt
requests	>=2.25.0	2020-11-11T20:05:15Z
click	==8.1.7	2023-08-17T17:29:10Z
pyyaml	-	-
```

The table has the following columns. A missing value is shown as `-`.

- `package_name`
- `package_version` — the version as declared in the file, e.g. `>=2.25.0`
- `checked at` (`resolved_version` in `-json` and `-out`) — the version looked
  up on deps.dev, which the columns after it describe. For a range it is the
  oldest published version the range allows, the worst case it permits, not
  what is installed: `>=2.25.0` is checked at `2.25.0`, `^1` at the first
  published 1.x, `==8.*` at `8.0.0`. Spelled as deps.dev spells it (`>=6` is
  checked at `6.0.0`)
- `versions_behind` — how many releases newer than this one deps.dev lists. Pre-releases (canaries, nightlies, release candidates, Go pseudo-versions) are not counted, and a version that is itself a pre-release shows `-` (unknown)
- `days_ago` — whole days since this version was published
- `published_at` — date this version was published
- `default` — is this the default version which is installed as of today
- `advisories` — number of known security advisories
- `resolution` — whether the lookup succeeded, and if not why: `resolved`,
  `no_satisfying_version` (no published version is in the range),
  `range_not_evaluated` (a Maven or NuGet interval kospex can't find a
  lowest version for, such as `(1.0,2.0)`), `version_yanked`, `package_not_found`, `unresolved_spec`, `no_version` or
  `lookup_error`. A row's numbers are blank when it is not `resolved`
- `source_repo` — the git URL for this package if known

Below the table, a line says which scopes were shown and what else is in the file:

```
sca run with scope [direct]: 1 of 4 packages shown. Other scopes in this file: dev (2), peer (1). Use -scope dev,peer or -scope all.
```

**Choosing what to show**

By default only direct dependencies are shown. Use `-scope` with a comma-separated
list to choose others:

```bash
kospex sca -scope direct,dev package.json
kospex sca -scope all pnpm-lock.yaml
```

The scopes are `direct`, `dev`, `peer`, `optional`, `transitive`, `bom` (a Maven
bill of materials), `version-catalog` (an entry in a Gradle `libs.versions.toml`),
`unknown` (the file did not say) and `all`. `-scope` only changes what is shown. Every package is still
looked up, and all of them are saved with `-save`.

**JSON output**

`-json` prints the table rows as JSON and nothing else on stdout, so it can be piped
or redirected. Messages and the scope line go to stderr.

```bash
kospex sca -json requirements.txt > sca.json
```

**Saving to the kospex database**

`-save` writes the results to the kospex database, the same way `krunner osi` does.
Because the saved rows are recorded against the current commit, sca refuses to save,
before looking anything up, unless:

- the file is in a git repo
- that repo, and the commit you are on, have been synced (`kospex sync-directory`)
- the repo is the checkout kospex synced, not another clone or copy of it
- the file resolves inside that checkout (a committed symlink to a file elsewhere
  is refused)
- no tracked file in the repo has been modified or staged
- the file itself is committed

Untracked files elsewhere in the repo are fine. Without `-save`, sca reads any
file you give it.

```bash
kospex sync-directory .
kospex sca -save requirements.txt
```

**Parameters**

- `-scope` — comma-separated scopes to show (default `direct`)
- `-json` — print the results as JSON on stdout
- `-out` — write the table to a CSV file
- `-save` — save the results to the kospex database
- `-no-enrich` — parse the file without looking anything up (see below)
- `-enrich` — look packages up even when `KOSPEX_DEPS_ENRICH=false`
- `-malware` — deprecated and ignored

**Parsing without lookups.** With `-no-enrich`, or `KOSPEX_DEPS_ENRICH=false`
in the environment or `~/kospex/kospex.env`, sca lists the file's packages
and contacts nothing: no deps.dev, no PyPI. The lookup columns are empty and
the resolution is `not_checked`; with `-save` the rows are stored that way,
except that a row already looked up at the repo's current commit keeps that
lookup.
`-package` is a lookup and nothing else, so it refuses unless you add
`-enrich`.

```bash
kospex sca -no-enrich package-lock.json
```

**Private packages.** Package names matching `KOSPEX_DEPS_PRIVATE` (or, for
Go, `GOPRIVATE`) are never looked up, even with `-enrich`. Their resolution
is `private_skipped`, and sca says how many there were. `-package` refuses a
private name. For example:

```
KOSPEX_DEPS_PRIVATE=maven:com.example,go:github.com/example
```

**Looking up one package.** `-package TYPE:NAME` looks up a single package
instead of reading a file, and shows its release history from deps.dev:

```bash
kospex sca -package pypi:kospex
```

```
       Releases: pypi kospex
  Release   Version   Published    Age
  First     0.0.1     2024-07-13   2y 4m
  Latest    0.4.0     2026-11-16   <1d
44 versions, 0 pre-releases, 0 deprecated. 11 releases in the last 12 months.
```

- **First** is the oldest release, and **Latest** is the registry's default
  version (the highest release if that default is a pre-release). Pre-releases
  (alphas, betas, release candidates, nightlies) are excluded from both.
- **First (any)** and **Latest (any)** include pre-releases. They appear only
  when the answer is different, e.g. a package whose first versions were
  pre-releases, or a release candidate newer than the latest release.
- **Deprecated** versions are counted, and a deprecated latest release is
  flagged with the reason deps.dev gives.
- **Undated** versions (deps.dev lists some with no publish date) are counted,
  because any of them may be older than the first date shown.

`-package` always asks deps.dev directly rather than using the cache, so the
latest release is current. TYPE is one of `cargo`, `go`, `maven`, `npm`,
`nuget`, `pypi` or `rubygems`.

**Looking up one version.** Add `@VERSION` to place an exact version among
the releases:

```bash
kospex sca -package pypi:requests@2.25.0
kospex sca -package npm:@babel/traverse@7.0.0
```

```
           Releases: pypi requests
  Release     Version   Published    Age
  First       0.2.0     2011-02-14   15y 9m
  Requested   2.25.0    2020-11-11   6y
  Latest      2.34.2    2026-05-14   6m

21 releases were published between 2.25.0 and the latest (2.34.2), including any fixes to older lines.
Upgrade path to 2.34.2: 9 minor and 2 patch releases.
4 known advisories for 2.25.0 (deps.dev lists 8 IDs; aliases of the same
vulnerability are shown once):
```

followed by a table of the advisories: ID (with any alias IDs), CVE, CVSS 3
score and title.

- The version gets a **Requested** row in publish order. If it is the first
  or latest release, that row is marked `(requested)` instead.
- **Releases published between** is `versions_behind` from the file
  results: releases in publish order, so it includes fixes to older lines
  released in that window. A pre-release has no count.
- **Upgrade path** counts only the releases an upgrade passes through, by
  major, minor and patch: the same numbers as the `behind` column. When the
  latest is a major away, a further line gives the newest release on your
  current major.
- **Advisories** are deps.dev's, for that exact version. deps.dev lists one
  vulnerability under several IDs (a GHSA and a PYSEC, for example), so IDs
  that are aliases of each other are shown as one row.
- A CVSS 3 score of `-` means deps.dev has none. The advisory may be scored
  under CVSS 4 only, so check its severity on osv.dev.
- The version must be exact, not a range. deps.dev's spelling is used, and
  the command says when it differs (pypi `2.25` is `2.25.0`). A version
  deps.dev doesn't have exits with an error.
- The version follows the last `@`, so npm scopes work as written.

**Adding OSV.** `-osv` follows deps.dev's output with the advisories
[OSV](https://osv.dev) lists for the same version. It needs `@VERSION`:

```bash
kospex sca -package pypi:requests@2.25.0 -osv
```

```
OSV: 4 advisories for pypi requests 2.25.0.
  Advisory             Severity               Fixed in   Published    Summary
  GHSA-9hjg-9r4m-...   MEDIUM 5.3 (CVSS 3.1)  2.32.4     2025-06-09   Requests vulnerable to ...
Compared with deps.dev: 4 in both, 0 only in OSV, 0 only in deps.dev.
```

- **Fixed in** is the version that fixes it, which deps.dev's table doesn't
  give. A `*` means OSV lists a fix for each release line and doesn't say
  which applies to yours. OSV doesn't list a Go package's versions, so a Go
  advisory with several release lines always shows it.
- **Severity** is the worst the advisory carries, with its source. CVSS 3
  vectors are scored by kospex. CVSS 4 is not scored, so an advisory rated
  only under CVSS 4 shows the database's own label (such as `MEDIUM (GitHub)`),
  or `UNSCORED` when there is none.
- OSV files one vulnerability under several IDs (a GHSA and a PYSEC record), so
  records that share an ID or alias are shown once. Withdrawn advisories are
  shown, marked, and not counted.
- The last line compares the two sources and names any advisory only one of
  them lists.
- If OSV doesn't answer, deps.dev's output and the exit code are unchanged.
- Only the package name, ecosystem and version are sent, and never for a
  name that matches `KOSPEX_DEPS_PRIVATE`.

`maven:` entries cover Gradle files too. See
[krunner osi, Private packages](krunner.md#private-packages) for how
prefixes match.

## orphans

A repo is orphaned when no one who authored its code is still here: none of
its authors has committed to *any* synced repo in the last year.

```bash
kospex orphans                     # every synced repo
kospex orphans -server github.com  # one git server
```

Each repo gets a status line, then a table:

```
repo_id: github.com~acme~api, Orphaned, Present: 0, Active: 0, Total: 3 authors.
```

| Column | Meaning |
|---|---|
| `Status` | `Orphaned`, `Knowledge`, `No authors` or `Not synced` (below) |
| `Authors` | everyone who has authored a commit to the repo, all time |
| `Present` | how many of them committed in the last `-here-days` (365): still here |
| `% Here` | `Present` as a percentage of `Authors` |
| `Active` | how many of them committed in the last `-days` (90): recently active |
| `Bots active` | bots that committed to this repo in the last `-days` |
| `Last commit` | the repo's latest commit |
| `Last sync` | days since kospex last fetched or synced the repo |

- **Orphaned**: no author is still here.
- **Knowledge**: at least one author is still here.
- **No authors**: no person has authored a commit. Bots may have; the
  `Bots active` column says so.
- **Not synced**: listed in `-target-list` but not in the kospex database.

A repo nobody has touched for years still gets a verdict: if its authors are
still here, someone can pick it up. `Last commit` shows how long it has been.

### Who counts as an author

Authors are identified by author email and dated by author date. A merge made
in GitHub's web UI is committed by `noreply@github.com`, and a rebase gives an
old commit a new committer date, so committer fields are not used. Merge
commits don't count as authoring, because a merge's author merged the code
rather than wrote it. Bots never count as people: an Orphaned repo with bots
active is one whose dependencies are still being updated with no one left who
wrote it.

Each author counts once, whether they wrote half the code or one line, so
`% Here` is a head count, not a share of the code.

`-window DAYS` limits authors to those who committed to the repo in the last
DAYS, to ask "of the people who worked on it recently, is anyone still here?"
A repo with no commits in that window is then `No authors`.

### Who counts as still here

Two windows, because they answer different questions:

- **Present** (`-here-days`, default 365): still here. Orphaned is decided on
  this.
- **Active** (`-days`, default 90): recently active, so their knowledge of the
  code is fresh. Present but not Active is someone still around who may need
  time to get back into it.

A year is the threshold that research testing it chose for "has left":
Avelino et al. (ESEM 2019) found a 3-month rule wrongly marked 62% of core
developers as gone at least once, and Calefato et al. (EMSE 2022) rejected 3
and 6 months for the same reason. Both studied open-source projects, where
people pause; in a company a 90-day gap is more often a real departure, so set
`-here-days 90` if that is the rule you want.

Any commit counts as being here, merges included: merging is still being
here. Someone who moved
to another project is still here, so their old repo is not orphaned; someone
who has left the organisation stops committing everywhere, and that is what
kospex sees. Someone who stops committing but stays, for example by moving
into management, looks as if they have left.

"Working for us" is whatever you have synced. For a company, sync its git
servers or orgs and it means the company. It is less direct for open source,
but still useful: sync an org such as `github.com~apache` and a developer who
left one Apache project but still commits to several others counts as here,
so the project they left shows the knowledge is still in the org.

When your database holds more than one organisation, `-active-in` narrows
where a person counts as still here:

```bash
kospex orphans -active-in org      # only repos in the same kospex org
kospex orphans -active-in server   # only repos on the same git server
```

The org is the kospex org key, `server~owner`, so it works the same for
GitHub, GitLab, Bitbucket or a self-hosted server. A GitLab nested group is
its whole path: `gitlab.com~group~~sub-a` and `gitlab.com~group~~sub-b` are
different orgs. The default, `estate`, is every synced repo.

### Speed

Telling merge commits apart needs a small index on them, added by migration
0015. Without it, `kospex orphans` and `/orphans/` read every commit: about 10
seconds on a 1.4-million-commit database, against about 3 with it. If kospex shows the "database schema is out
of date" banner, run `kospex upgrade-db -apply`.

### Out-of-date data

Because still here is measured across the synced repos, the result is only as
current as the sync. A `Last sync` shown in red is older than `-stale-days`,
and the summary says how many repos that is and gives the command that syncs
them: `kgit pull --all --stale 30`, or `--server SERVER` when you passed
`-server`. `kgit pull` needs a scope, so use `--org ORG_KEY` for one org.

### A subset of repos

Pass a file with one git URL or `repo_id` per line (blank lines and lines
starting with `#` are skipped):

```bash
kospex orphans -target-list FILE
```

### In kweb

The page `/orphans/` (and `/orphans/ORG_KEY`, `/orphans/SERVER`) shows the
same results, with a menu for the `-active-in` scope and an "explain column
headings and table" link that describes each column.

**Parameters**

- `-here-days` — an author who committed anywhere in the last X days is still here; decides Orphaned (default 365)
- `-days` — an author who committed anywhere in the last X days is recently active (default 90)
- `-window` — only count authors who committed to the repo in the last X days (default: all time)
- `-stale-days` — flag repos whose last sync is older than X days (default 30)
- `-active-in` — where a person counts as still here: `estate` (any synced repo, the default), `server` or `org`
- `-server` — only repos on this git server
- `-target-list` — a file of repos to check
- `-csv FILE` — also write the results to a CSV file; values are escaped for spreadsheets

## rebuild

Recompute data that kospex derives from your synced commits, without re-syncing
the repos. Each subcommand rebuilds one kind of data:

```bash
kospex rebuild list                                     # tasks, what they write, whether they need a clone
kospex rebuild developer-stats -all                     # every repo
kospex rebuild developer-stats -org github.com~kospex   # every repo in an org
kospex rebuild developer-stats github.com~kospex~kospex # one repo
```

`developer-stats` rebuilds the per-developer statistics behind the web summary
page and `kospex stats`. It reads only the database, so it needs no clones,
network access or credentials, and takes seconds for hundreds of repos. Run it
once after upgrading to a release whose upgrade notes ask for it.

**Parameters**

- `REPO_ID`, `-all`, `-org ORG_KEY` or `-server SERVER`: the repos to rebuild.
  Exactly one is required.
- `-dry-run`: list the repos in scope without changing anything.

Each repo is rebuilt on its own. If one fails, the error is shown, the rest
carry on, and the command exits with status 1.

Every run is logged to `kospex.log`: a start line with the scope, one line per
repo with its status and time, and a summary. A failed repo is logged with its
full traceback. For `developer-stats`, each repo's line also gives
`developers`, `authored` (commits that aren't merges) and `merges`, which add up
to the repo's total commits:

```
rebuild developer-stats github.com~kospex~kospex ok 0.27s developers=4 authored=975 merges=145
```

## list-repos

This command either lists all the known repos that have been synced to the database, or
lists the repos found by searching a directory path.

**By directory**

```bash
kospex list-repos [DIRECTORY]
```

**From the database**

```bash
kospex list-repos -db
```

If you want to restrict the search to a specific server or domain name, use the `-server` flag:

```bash
kospex list-repos -db -server [DOMAIN_NAME]
```

For example:

```bash
kospex list-repos -db -server bitbucket.org
```

**Including the Repo ID**

Both `-db` and the filepath option support the `-repo_id` flag, which returns the repo_id.
The repo_id is used as a key in the format `GIT_SERVER~OWNER~REPO`.
So for the repository `https://github.com/kospex/kospex` the repo_id would be `github.com~kospex~kospex`.
