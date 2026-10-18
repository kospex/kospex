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

The deps command queries the deps.dev API to get information about libraries in a dependency file.

```bash
kospex deps -file requirements.txt
```

The output will be a table with the following columns:

- `package_name`
- `package_version`
- `days_ago` — how many days ago this package was published
- `published_at` — date this package was published
- `advisories` — number of known security advisories
- `default` — is this the default version which is installed as of today
- `versions_behind` — how many versions behind is this version
- `source_repo` — the git URL for this package if known

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
- `versions_behind` — how many versions behind is this version
- `days_ago` — whole days since this version was published. For a range such as
  `>=2.25.0` this is the lowest version it allows (`2.25.0`)
- `published_at` — date this version was published
- `default` — is this the default version which is installed as of today
- `advisories` — number of known security advisories
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

The scopes are `direct`, `dev`, `peer`, `optional`, `transitive`, `unknown` (the file
did not say) and `all`. `-scope` only changes what is shown. Every package is still
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
- no tracked file in the repo has been modified or staged
- the file itself is committed

Untracked files elsewhere in the repo are fine.

```bash
kospex sync-directory .
kospex sca -save requirements.txt
```

**Parameters**

- `-scope` — comma-separated scopes to show (default `direct`)
- `-json` — print the results as JSON on stdout
- `-out` — write the table to a CSV file
- `-save` — save the results to the kospex database
- `-malware` — deprecated and ignored

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
