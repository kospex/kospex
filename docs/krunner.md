# krunner

The krunner command is used to run a command on all the git repos in a directory.
Most of the commands won't use the kospex database, with the notable exception of `git-pull`.

## git-pull

The format for this command is:

```bash
krunner git-pull DIRECTORY
```

By default, this will find all repos in DIRECTORY, pull each one as your user,
and sync it to the kospex database.

The pull is fast-forward only (`git pull --ff-only --no-rebase`), whatever your
`pull.rebase` / `pull.ff` settings say. If a repo can't be fast-forwarded,
because its remote history was rewritten or the clone has commits of its own, it
is left as it was, not synced, and listed as `GIT_ERROR` in the summary at the end.

**NOTE** — this will NOT download new repositories, it will only sync existing ones that are already on disk.

## osi

Run an open source inventory across all synced repos, extracting dependency
names and versions and enriching them via deps.dev. Lookups can be turned off;
see [Parsing without lookups](#parsing-without-lookups).

```bash
krunner osi -all
```

This is what refreshes `dependency_data` — syncing a repo does not. Note it reads
the file list from `file_metadata` rather than scanning disk, so a manifest
kospex has never tagged stays invisible however often you re-run it. See
[Refreshing data](refreshing-data) for the ordering rule.

You can also scope it to a server, org or repo:

```bash
krunner osi GIT_SERVER~ORG~REPO
```

If a repo runs off a non-default branch (e.g. `development` rather than `main`),
see [Scanning a non-default branch](branch-aware-sync) for how to get an
accurate inventory.

### Parsing without lookups

`-no-enrich` extracts and saves the dependencies without contacting anything:
no deps.dev, no PyPI. It works with `-all`, a scope, and `-next`.

```bash
krunner osi -no-enrich -all
```

The rows show on `/osi/` and `/dependencies/` as **not checked**, with no
advisories, versions behind or release dates. A later `krunner osi` with
lookups on replaces them.

To make that the default on a machine, set `KOSPEX_DEPS_ENRICH=false` in the
environment or in `~/kospex/kospex.env`. `-enrich` then turns lookups back on
for one run. The flag beats the environment variable, which beats
`kospex.env`; the default is to look packages up.

**Rows that were already looked up.** A row is saved per commit. When a run
with lookups off parses a file at a commit that was already looked up, those
rows keep their earlier lookup and its date. Rows for a new commit are saved
not checked, because the file may have changed. Before such a run starts,
`krunner osi` reports how many current rows in its scope were looked up
before:

```
No current dependency rows in this scope have been looked up.
```

or

```
Warning: 412 current dependency rows in this scope were looked up before (most recently 2026-11-03 09:12:44 UTC). ...
```

On a machine set up with `KOSPEX_DEPS_ENRICH=false` from the start, it should
always be the first.

**In the web UI.** `/dependencies/` shows when its rows were last checked,
and turns that orange when the oldest check is over two days old or some rows
have no check date. Rows parsed with lookups off have no check date, so they
are listed under **No check date recorded** as *Parsed with lookups off*.
Rows that kept an earlier lookup show its date and go orange as it ages, as
they do when `krunner osi` hasn't run for a while.

## key-person

The `kospex key-person` table for every repo in scope: the top N authors by
commits (default 4), plus any of the top N active authors (last 90 days) not
already listed. Give exactly one scope:

```bash
krunner key-person -all
krunner key-person -org github.com~kospex
krunner key-person -server github.com
krunner key-person github.com~kospex~kospex
```

Merge commits are counted in their own `merges` column, not as commits.
`-days N` changes the active window, `-top N` changes N, and `-all-authors`
lists every author.

## bus-factor

The bus factor of each repo in scope: the fewest authors whose commits make up
at least 50% of the repo's commits. It is calculated for all time and for the
last 90 days.

```bash
krunner bus-factor -all
krunner bus-factor -org github.com~kospex -threshold 50,80 -days 180
```

- `-threshold` takes up to three whole-number percentages.
- Merge commits are not counted.
- A repo with no commits in the window has no active bus factor, which is shown
  as `-` and left blank in the CSV.

The table shows the bus factor **with bots excluded**, next to the bots' share
of commits (`bot %`). The CSV has both variants for each threshold:
`bus_factor_50` counts every author, and `human_bus_factor_50` leaves bots out.

Authors are counted by email address. One person who commits under two
addresses counts as two authors, which raises the bus factor.

### CSV output

`key-person` and `bus-factor` both show a table by default and write no file.

- `-out FILENAME` also writes the CSV. An existing file is kept unless you add
  `-overwrite`.
- `-stdout` writes **only** the CSV, to stdout, so another process can read it.
  Progress and the summary go to stderr.

```bash
krunner bus-factor -all -threshold 50,80 -stdout > bus-factor.csv
krunner key-person -org github.com~kospex -out key-people.csv
```

`repo_id` is the first column. Values are raw: numbers without `%`, and dates in
ISO-8601 UTC. Every row carries the window (`active_days`) and a `generated_at`
time.

## find-docker

This command finds files with the naming convention of `Dockerfile` and `docker-*.yml`.

```bash
krunner find-docker DIRECTORY
```

Useful additional switches:

- `-images` — will attempt to extract the image names from any of the files and provide a summary of the container registry as well.

So, if you want to find all the docker files and images, run the following command:

```bash
krunner find-docker -images DIRECTORY
```
