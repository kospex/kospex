# Refreshing data

Kospex stores what it found *at the time it looked*. Nothing re-derives itself,
so after upgrading kospex, upgrading panopticas, or pulling new commits, the
database keeps reporting the old answer until you re-run the right command.

Two things catch people out:

- **There is no `kospex sync` command.** Syncing a repo happens through `kgit`
  (`kgit clone`, `kgit sync`) or `kospex sync-directory`. A bare `kospex sync`
  exists only as commented-out code; restoring it for on-disk repos without
  kgit/auth is tracked in
  [issue #123](https://github.com/kospex/kospex/issues/123).
- **Syncing a repo does not refresh its dependencies.** `dependency_data` is
  populated by a separate pass — `krunner osi` — and nothing in the sync path
  writes to it.

## What refreshes what

| Data | Table | Refreshed by |
|---|---|---|
| Commits, authors, developer stats | `commits`, `developer_stats` | `kgit clone`, `kgit sync`, `kospex sync-directory` |
| File metadata + panopticas tags | `file_metadata` | the above, plus `kospex sync-metadata` and `krunner file-metadata` |
| Dependencies | `dependency_data` | `krunner osi`, `kospex deps`, `kospex sca` |
| Branch counts | `observations` | `krunner branches -save` |
| Repo sizes | `observations` | `krunner repo-size -save` |

The three commit-sync commands all call the same internal `sync_repo`, which
ingests commits and then refreshes that repo's `file_metadata` — so a repo sync
does update panopticas tags, subject to the rebuild guard described below.

**`krunner osi` does not re-scan files.** It reads the file list out of
`file_metadata`, so a dependency file kospex has never tagged is invisible to it,
however many times you re-run it. That gives the ordering rule: **file metadata
first, dependencies second.**

## Syncing repos

```bash
kgit clone REPO_URL              # clone into KOSPEX_CODE and sync (-sync is on by default)
kgit sync https://host/org/repo  # sync a single repo from a URL
kgit sync --org https://host/org # sync every repo in an org
kgit pull DIRECTORY              # git pull the clones kospex already knows about
kospex sync-directory DIR        # sync every repo found under DIR
```

`kgit clone` syncs an existing clone rather than failing, so it doubles as a
refresh for a single repo.

If a repo is already recorded against a *different* path that still exists, the
sync is refused rather than silently repointing it — pass `-force` to override.
A recorded path that no longer exists is treated as a move and repoints with a
warning.

## Refreshing file metadata

File metadata is where panopticas tags live — the `tech_type` column, encoded as
`|tag1|tag2|`, which everything else filters on. To refresh it *without* a full
repo sync:

```bash
krunner file-metadata                 # every repo in scope
krunner file-metadata github.com~org  # one server, org or repo_id
kospex sync-metadata -repo PATH       # one repo on disk
kospex sync-metadata -directory DIR   # a directory of repos
```

### You usually don't need `-force`

`Kospex.file_metadata()` consults `needs_metadata_rebuild()`, which rebuilds a
repo when **any** of these is true:

- no prior sync was recorded for it
- HEAD has moved since the last sync
- the installed **panopticas** version differs from the recorded one
- the installed **scc** version differs from the recorded one

Tool versions are compared as opaque strings — kospex only cares whether the tag
changed, never how the versions order — so pre-release suffixes and unusual
version schemes need no special handling.

The practical consequence: **after upgrading panopticas, a plain
`krunner file-metadata` re-tags everything.** The recorded
`last_panopticas_version` no longer matches the installed one, so every repo is
marked for rebuild. No `-force`, no re-clone, no need to touch HEAD.

Reach for `-force` only when you suspect the stored metadata is wrong for a
reason kospex cannot detect — a partial run, a hand-edited database, or a
panopticas change that shipped under an unchanged version number.

> **Note:** `krunner file-metadata -force` deletes the existing rows for the
> current hash before rescanning. It does not pass `force` down to
> `Kospex.file_metadata()`, so it is a row-clearing hammer rather than a way to
> override the rebuild guard — which has usually already decided to rebuild.

## Refreshing dependencies

```bash
krunner osi -all              # every repo — regenerates OSI-all.csv
krunner osi <repo_id>         # one repo, org or server
kospex deps -repo PATH        # find/assess dependency files in one repo
kospex deps -file FILE        # assess a single manifest
kospex sca FILE               # one manifest, with advisory enrichment
kospex sca -malware FILE      # ...and maliciouspackages.com lookup (needs API_MAT)
```

`krunner osi` walks the dependency files recorded in `file_metadata`, parses each
with the matching parser, enriches via deps.dev, and writes `dependency_data`.

Because it reads that file list from the database rather than the disk, a
manifest that panopticas doesn't recognise — or that was added since the last
metadata refresh — will not appear. If a manifest you expect is missing from
`/dependencies/`, refresh file metadata first and re-run.

> `kospex sca` takes a **file path argument**, not a repo. Its `-repo` option is
> declared but unimplemented — passing it prints `NOT implemented` and exits 1.
> `-save` defaults to on, so `sca` writes to the database unless told otherwise.

## Order of operations

After upgrading panopticas, or when a new manifest type becomes supported:

```bash
kospex upgrade-db -apply    # only if a release added a migration
krunner file-metadata       # re-tag files; new manifest types become visible
krunner osi -all            # re-parse dependencies with the current parser
```

After pulling new commits into your clones:

```bash
kgit pull DIRECTORY         # update the clones
kospex sync-directory DIR   # commits + file metadata
krunner osi -all            # dependencies, if manifests may have changed
```

## Why the numbers can get worse after a refresh

A refresh reports what is *actually there now*, which is not always an
improvement on what was reported before. Two mechanisms cause a
legitimate-looking drop:

**Parser fixes surface things that were previously dropped.** If a parser was
silently discarding declarations it couldn't handle, fixing it makes those
dependencies appear — typically as rows with no resolvable version, which then
fail freshness checks. Dependency counts rise and health figures fall. Nothing
regressed; the earlier figure was flattering because part of the input was
missing.

**Tag changes invalidate saved queries rather than erroring.** `tech_type` is
matched with `LIKE '%|tag|%'`. If panopticas renames a tag, a query filtering on
the old name returns **zero rows instead of failing**, so anything built on the
old vocabulary quietly reports nothing. Check release notes for tag renames, and
re-check saved queries and dashboards after a panopticas upgrade.

Both are reasons to refresh deliberately rather than on a schedule nobody
watches — and to refresh *before* presenting numbers to anyone.

## Checking what a repo was last built from

Provenance is recorded per repo, so you can tell whether a refresh is needed
without guessing:

```sql
SELECT _repo_id, last_sync_hash, last_panopticas_version, last_scc_version
FROM repos;
```

A `last_panopticas_version` behind the installed one means that repo's
`file_metadata` will rebuild on its next metadata run. These columns are
point-in-time and keep no history — they record only the most recent successful
sync.

> Requires migration `0003`. If the columns are absent, run
> `kospex upgrade-db -apply`.

## See also

- [Commands](commands) — the full command list
- [kgit](kgit) — cloning and syncing repos
- [krunner](krunner) — bulk operations across repos
- [Data schemas](data-schemas) — table structures
- [Troubleshooting](troubleshooting)
