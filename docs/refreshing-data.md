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
| Dependencies | `dependency_data` | `krunner osi`, `kospex sca -save` |
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
kospex deps -repo PATH        # list the dependency files in one repo (no lookups, no save)
kospex sca FILE               # one manifest, with advisory enrichment (display only)
kospex sca -save FILE         # ...and write it, from a clean, synced checkout
```

`krunner osi` walks the dependency files recorded in `file_metadata`, parses each
with the matching parser, enriches via deps.dev, and writes `dependency_data`.

Because it reads that file list from the database rather than the disk, a
manifest that panopticas doesn't recognise — or that was added since the last
metadata refresh — will not appear. If a manifest you expect is missing from
`/dependencies/`, refresh file metadata first and re-run.

> `kospex sca` takes a **file path argument**, not a repo. Its `-repo` option is
> declared but unimplemented — passing it prints `NOT implemented` and exits 1.
> `sca` writes to the database only with `-save`, and refuses unless the
> repository and its HEAD commit are synced and the worktree has no modified or
> staged files — run `kospex sync-directory` on the repository first.

## Order of operations

After upgrading panopticas, or when a new manifest type becomes supported:

```bash
kospex upgrade-db -apply    # only if a release added a migration
krunner file-metadata       # re-tag files; new manifest types become visible
krunner osi -all            # re-parse dependencies with the current parser
```

After pulling new commits into your clones:

```bash
kgit pull [SEE OPTIONS]     # update the clones, syncs and updates file metadata
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

## Normalising repo_id case

`repo_id` is now lowercased. Git providers treat an owner and repository name
as **case-insensitive for uniqueness but case-preserving for display**, so
`github.com/Kospex/Kospex` and `github.com/kospex/kospex` are one repository —
but kospex used to mint two ids for them, splitting the commit history and
double-counting the org.

There is **no migration for this**. It is a one-off data change with no schema
component, and whether two ids that collapse to one should be merged depends on
which row you want to keep — a judgement a migration cannot make. The SQL below
is deliberately run by hand.

Nothing self-heals: a repo keeps its old mixed-case id until you either
normalise it or re-sync it.

### Do you have any?

```sql
SELECT _repo_id FROM repos WHERE _repo_id <> lower(_repo_id);
```

Empty result means there is nothing to do — likely if every repo came from an
all-lowercase URL.

### Check for collisions first

This is the part that needs a decision. Two ids that differ only by case
collapse into one when lowercased, and the rows have to be merged rather than
updated:

```sql
SELECT lower(_repo_id) AS target, group_concat(_repo_id, ' + ') AS sources
FROM (SELECT DISTINCT _repo_id FROM repos)
GROUP BY lower(_repo_id) HAVING COUNT(*) > 1;
```

**If this returns rows, stop.** Each pair is the same repository recorded
twice. Decide which to keep — usually the one whose `repos.file_path` points at
a clone that still exists — then clear the other with
`kreaper delete-repo -repo_id <the-other-one> -yes` before running the update
below. Re-sync afterwards.

**If it returns nothing, the update is safe** — every id maps to a distinct
lowercase id, so no rows merge.

### Normalise

Back up first; there is no undo.

```bash
cp ~/kospex/kospex.db ~/kospex/kospex.db.bak
```

Thirteen tables carry `_repo_id`, and `repos`, `commits`, `commit_files` and
`file_metadata` also carry `_git_owner` / `_git_repo`, which must be lowercased
with it. They are **not** derived from the id — they are written from the parsed
clone URL, and the org-scoped queries bind `_git_owner` from a split `org_key`.
If the two disagree on case, every org lookup that starts from a `repo_id`
silently returns no rows.

```sql
BEGIN;

UPDATE branch_history    SET _repo_id = lower(_repo_id);
UPDATE branches          SET _repo_id = lower(_repo_id);
UPDATE commit_files      SET _repo_id = lower(_repo_id);
UPDATE commit_metadata   SET _repo_id = lower(_repo_id);
UPDATE commits           SET _repo_id = lower(_repo_id);
UPDATE dependency_data   SET _repo_id = lower(_repo_id);
UPDATE developer_stats   SET _repo_id = lower(_repo_id);
UPDATE file_metadata     SET _repo_id = lower(_repo_id);
UPDATE kospex_groups     SET _repo_id = lower(_repo_id);
UPDATE krunner           SET _repo_id = lower(_repo_id);
UPDATE observations      SET _repo_id = lower(_repo_id);
UPDATE repo_hotspots     SET _repo_id = lower(_repo_id);
UPDATE repos             SET _repo_id = lower(_repo_id);

UPDATE repos         SET _git_owner = lower(_git_owner), _git_repo = lower(_git_repo);
UPDATE commits       SET _git_owner = lower(_git_owner), _git_repo = lower(_git_repo);
UPDATE commit_files  SET _git_owner = lower(_git_owner), _git_repo = lower(_git_repo);
UPDATE file_metadata SET _git_owner = lower(_git_owner), _git_repo = lower(_git_repo);

COMMIT;
```

Run it with `sqlite3 ~/kospex/kospex.db < normalise.sql`, or paste it into an
interactive `sqlite3` session. The first query above should then return nothing.

### What is not affected

**Existing clone directories keep their original casing, and nothing needs
moving.** `repos.file_path` records the real path, and `kgit pull`, `kgit clone`
and sync all read it, so an existing clone keeps working wherever it is.

**New clones are lowercased**, because `kgit clone` derives the directory from
the same parsed URL as the `repo_id`. So a disk that predates this change ends
up mixed — `~/code/github.com/Textualize/rich` beside
`~/code/github.com/textualize/rich` for a repo cloned later. That is cosmetic:
each is recorded in `repos.file_path` and found through it. If you would rather
have one convention, move the directory and update the row:

```sql
UPDATE repos SET file_path = ? WHERE _repo_id = ?;
```

On a case-insensitive filesystem (macOS by default) the two names are the same
directory and there is nothing to do.

**Display casing is lost.** `_git_owner` held the provider's canonical casing
(`Textualize`, `NousResearch`), and after this it does not. That is a deliberate
trade: the columns have to agree with the id, and the id has to be
case-insensitive for the identity to be correct. If canonical display names are
wanted later they should come from the provider API, which is authoritative,
rather than from whatever casing a clone URL happened to carry.

## Upgrading to 0.1.0: re-syncing after the ingest fixes

> Two separate things changed in 0.1.0 and both need attention on an existing
> database. This section covers **re-syncing** for the commit-ingest fixes.
> [Normalising repo_id case](#normalising-repo_id-case) above covers the
> **one-off SQL** for repo ids — do that first, since re-syncing a repo whose
> id is still mixed-case records it twice.

0.1.0 changed how commits are read from git. The fixes apply to **newly-synced
commits only**, and commit sync is incremental — it walks from the last recorded
commit — so a routine sync never revisits existing rows and nothing self-heals.

Three things differ between data ingested before and after the upgrade:

- merge commits are now identifiable (`commits.parents` is populated)
- content a merge introduced that exists in no parent is now recorded in
  `commit_files`
- non-ASCII file paths are stored unquoted

Until a repo is re-synced it keeps the old values, so an estate part-way through
will hold a mix. Reported numbers stay self-consistent either way — the queries
read whichever evidence is present — but merge-aware figures are only complete
for re-synced repos.

### Which repos are stale

```sql
SELECT _repo_id, COUNT(*) AS commits
FROM commits WHERE parents IS NULL
GROUP BY _repo_id ORDER BY commits DESC;
```

Every repo synced before 0.1.0 appears here, so on first upgrade this is your
whole estate. It stays useful afterwards as a general staleness check: a repo
listed here predates the current ingest.

### Re-syncing a repo

A re-sync has to start from an empty slate, because `sync_repo` derives its
window from the newest commit already recorded. Clearing the repo removes its
`repos` row along with everything else, which resets the sync provenance so the
next sync walks the full history.

```bash
# See what would be removed - read-only, deletes nothing
kreaper delete-repo -repo_id github.com~owner~repo -dry-run

# Clear every table carrying this _repo_id, including the repos row
kreaper delete-repo -repo_id github.com~owner~repo -yes

# Re-sync from the local clone (offline; finds the repo at or below the path)
kospex sync-directory ~/code/github.com/owner/repo
```

`kospex sync-directory` walks a directory and syncs every git repo at or below
it, so pointing it at one repo syncs just that one, and pointing it at
`~/code` re-syncs everything.

To pull and re-sync in one step for clones kospex already knows about, use
[`kgit pull`](kgit) instead.

### One thing a re-sync will not fix

Non-ASCII paths stored in their old quoted form — `"caf\303\251.py"` — do not
get replaced. `file_path` is part of the primary key of `commit_files`, so
unquoting produces a *different* key: a re-sync adds the correct row and leaves
the quoted one behind as a duplicate.

Check for them first:

```sql
SELECT _repo_id, COUNT(*) FROM commit_files
WHERE file_path LIKE '"%' GROUP BY _repo_id;
```

If any exist, delete them — before or after the re-sync, but do not skip it:

```sql
DELETE FROM commit_files WHERE file_path LIKE '"%';
```

These rows are orphans that nothing joins to; the correct rows are recreated by
the re-sync.

### Back up first

`kreaper` deletes. Take a copy of `~/kospex/kospex.db` before starting, and run
`kospex upgrade-db -apply` if the database is behind — writes against a
behind-schema database can record incomplete data.

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

## Clearing unknown advisory counts

`dependency_data.advisories` and `versions_behind` are `INTEGER` columns, and
every record template used to seed them with `''`. SQLite sorts TEXT above
every INTEGER regardless of content, so `'' > 0` is **true**: a package that
was parsed from a manifest but never enriched matched `WHERE advisories > 0` —
the predicate meaning "has a security advisory" — while being in none of
`> 0`, `= 0` or `IS NULL`.

The web UI disagreed with SQL about the same row, because
`item.get("advisories") or 0` coalesces the falsy `''` to 0 and renders it as
clean. Neither answer was right; the honest one is unknown, which is NULL.

The writer is fixed, so nothing new accumulates. Existing rows do **not**
self-heal — `''` stays until you clear it or re-enrich the row.

There is **no migration for this**. It is a data-only change with no schema
component, and a `.py` migration requires a matching non-empty `.sql`, so one
would mean fabricating SQL to wrap the backfill. Run it by hand.

### Do you have any?

```sql
SELECT 'advisories' AS column_name, count(*) AS rows
FROM dependency_data WHERE typeof(advisories) = 'text'
UNION ALL
SELECT 'versions_behind', count(*)
FROM dependency_data WHERE typeof(versions_behind) = 'text';
```

Empty, or two zeroes, means there is nothing to do.

### What it is costing you

**Check `latest` separately — it changes the answer by three orders of
magnitude.** A re-save supersedes a row rather than deleting it, so an
un-enriched row that was later re-enriched keeps its `''` at `latest = 0`,
where no reporting query looks:

```sql
SELECT latest, count(*) AS unknown_rows
FROM dependency_data WHERE typeof(advisories) = 'text'
GROUP BY latest;
```

Then measure only what readers actually see:

```sql
SELECT count(*) AS reported_vulnerable,
       sum(typeof(advisories) = 'text') AS of_which_unknown
FROM dependency_data WHERE advisories > 0 AND latest = 1;
```

On a several-hundred-repository estate, 1,968 rows carried `''` but **1,962 of
them sat at `latest = 0`** — leaving 6 of 3,537 reported-vulnerable current
rows wrong, not 35%. Drop the `latest = 1` and the same estate reports 5,574
with 1,968 unknown, which is where a 35% figure comes from: it is counting
superseded history. Both numbers are real; only the first describes what anyone
is being shown.

The rows at `latest = 0` are still worth clearing — they are what `latest` is
not filtered on in an ad-hoc query, and the next person to write one has no
reason to expect a text value in an INTEGER column.

A currency banding query written as `CASE WHEN versions_behind <= 5 … ELSE
'100+'` has the same problem in reverse: `'' <= 5` is false, so every unknown
row lands in the worst band.

### Check for a non-empty value first

```sql
SELECT DISTINCT quote(advisories) FROM dependency_data
WHERE typeof(advisories) = 'text' AND advisories <> '';
```

Usually empty. On one estate it returned a single `'unknown'`, on a row with
`latest = 0` and no `source`, written by a path that no longer exists — NULL is
the right answer for that too, which is why the update below keys on `typeof()`
rather than on `= ''`.

**If it returns something that looks like a number stored as text, stop.** That
is a different defect — find the writer before nulling the evidence.

### Clear them

Back up first; there is no undo.

```bash
cp ~/kospex/kospex.db ~/kospex/kospex.db.bak
```

```sql
BEGIN;

UPDATE dependency_data SET advisories = NULL
 WHERE typeof(advisories) = 'text';

UPDATE dependency_data SET versions_behind = NULL
 WHERE typeof(versions_behind) = 'text';

COMMIT;
```

This replaces a wrong answer with an honest absence; it does not recover the
real counts. To get those, re-enrich the affected repositories — identify them
first:

```sql
SELECT DISTINCT _repo_id FROM dependency_data
WHERE advisories IS NULL AND latest = 1;
```

then run `krunner osi -repo_id <id>` for each, or let the scheduled
`krunner osi -next` reach them on its own. Re-enrichment calls deps.dev, so it
is bounded by rate limits and the response cache TTL
(`KOSPEX_URL_CACHE_SECONDS`) rather than by local work.

## See also

- [Commands](commands) — the full command list
- [kgit](kgit) — cloning and syncing repos
- [krunner](krunner) — bulk operations across repos
- [Data schemas](data-schemas) — table structures
- [Troubleshooting](troubleshooting)
