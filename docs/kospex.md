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

## orphans

Orphans occur when a repo no longer has someone working in the organisation or team who's active.

*"Experimental feature — Work in Progress."*

All you need to do is run the following command:

```bash
kospex orphans
```

This will display a table with the following columns:

- `_repo_id`
- `committers`
- `active`
- `Orphaned`
- `% Here`

If you want to check for the orphan status of only a subset of repos, you can use the `-target-list` argument:

```bash
kospex orphans -target-list FILE_OF_CLONE_URLS
```

The format of this file is git clone URLs, one per line.

**Parameters**

- `-days` — committed in X days is considered active (default 90)
- `-window` — days to consider for orphaned repos (default 365)
- `-target-list` — a file containing repos to check

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
