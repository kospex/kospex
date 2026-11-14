# `kgit sync --org -active [DAYS]` — sync only recently active repos

Design doc. No code yet. Builds on `kgit sync --org` / `--user`
([202610-kgit-sync-org.md](202610-kgit-sync-org.md)).

## Why

A large org has thousands of repositories, and most are dormant: in an org the
size of apache, perhaps 15% have had a commit in the last 90 days. A company
with ten orgs may only want data on what was worked on in the last year. Today
an org sync clones everything in the listing (bar forks and, optionally,
archived repos), so the only way to limit it is `-next N`, which limits how
many, not which.

The intended workflow:

```
kgit sync --org acme -inventory              # per org: list and save, clone nothing
kgit sync --org acme -status -active 365     # how many would be synced
kgit sync --org acme -active 365             # clone/pull and sync the active ones
kgit sync --org acme -active 365 -next 50    # or in batches, on a schedule
```

## Decisions (2026-11-15)

1. **"Active" means `pushed_at` from the listing**, within the last N days.
   It's free: the listing already returns it and `org_sync_items.pushed_at`
   already stores it. It's a loose measure, see [Limits](#limits).
2. **`-active` alone means 90 days**; `-active 365` sets the window. The window
   counts back from now, not from when the inventory was taken.
3. **The window decides everything for that run.** Repos inside it are cloned
   if new, pulled if already on disk (the same `clone_repo` path as today), and
   synced. Repos outside it are left alone by the run, including ones cloned
   earlier. Those are kept up to date separately, with `kgit pull --org acme`,
   or by a later run without `-active`.
4. **`-active` refreshes a stale inventory in place.** If the run's listing is
   older than `KOSPEX_ORG_SYNC_REFRESH_DAYS` (default **2**), `-active` lists
   the org again and merges the result into the same run before choosing what
   to sync. A run started straight after `-inventory` doesn't list twice.
5. **`-refresh` updates the run, `-restart` replaces it.** `-refresh` lists the
   org again and merges into the unfinished run, whatever its age. `-restart`
   is today's `-relist`: close the run and start a new one. `-relist` is
   removed outright, with no alias: it was never announced, so nobody else is
   using it. The CHANGELOG notes the rename.
6. **A plain resume doesn't change.** Without `-active` or `-refresh`, resuming
   a run never lists again, as today. The staleness check applies only to
   `-active`.
7. **`gone` is a new item status:** in the run, but missing from a later
   complete listing. Usually deleted, renamed (the new name arrives as a new
   item with the same `provider_repo_id`), transferred, or made private or
   no longer visible to the listing token ([#403](https://github.com/kospex/kospex/issues/403)). It isn't a progress state:
   `next_items` never picks it, and it doesn't keep a run open.

## Design

### Select, don't skip

Fork and archived skips are decided when the listing is saved
(`OrgSync.build_items`) and are fixed for the run; a resumed run ignores
`-include-forks` and `-skip-archived`. `-active` has to work the other way,
because the point is to run the same inventory with different windows.

So `-active` is a **filter on what's picked next**, in
`OrgSyncManifest.next_items`, not a status. Repos outside the window stay
`pending`. A later `-active 365`, or a run without `-active`, still finds them.

`next_items` already orders by `pushed_at` descending, so the filter is a
`WHERE` on the same expression, kept SQLite 3.26-safe:

```sql
AND CAST(strftime('%s', pushed_at) AS INTEGER) >= CAST(strftime('%s', 'now', ?) AS INTEGER)
-- ? = '-90 days'
```

Items with no `pushed_at` (an empty repository) are outside every window.

### The run never finishes under `-active`

`close_run_if_done` closes a run when nothing is pending or in progress. With
`-active`, repos outside the window stay pending, so the run stays open
indefinitely, and every later `kgit sync --org acme` resumes it **without
listing again**. That's the reason for decision 4: without a refresh, a
scheduled `-active 365 -next 50` would work from the same listing for months,
miss new repos, and never see a dormant repo come back to life.

This is also why a run is reported as "done for this window" rather than
finished: `-status -active 365` shows `0 to do in the window, 2,300 pending
outside it`.

### Refresh in place (new)

Today the only way to get a fresh listing is `-relist`, which closes the run
and starts a new one with everything pending again. The in-place refresh keeps
the run and its progress:

| Listed repo is… | In the run | Refresh does |
|---|---|---|
| new | absent | adds it, `pending` (or `skipped` for a fork/archived, as `build_items` decides) |
| still there | any status | updates `pushed_at`, `archived`, `private`, URLs; keeps the status |
| gone | present | sets status `gone`; keeps the row |

It needs to know when the run was last listed, which `started_at` stops being
after the first refresh. So a migration (the next free number, `0013` at the
time of writing) adds `org_sync_runs.listed_at`, set by `save_listing` and by
each refresh. `listed` is updated to the new count.

The refresh needs the network and a token, as the first listing did. If the
listing fails (`GitHubListingIncomplete`), the run is left untouched and the
sync stops: a partial listing must not mark repos gone.

### Option and setting

- `-active [DAYS]`, `type=click.INT, is_flag=False, flag_value=90`.
  Combines with `-next N` and `-status`.
- `KOSPEX_ORG_SYNC_REFRESH_DAYS`, default `2`. A non-integer or negative value
  is an error, not a guess, as with `KOSPEX_CLONE_FORKS_DEFAULT`. `0` refreshes
  every time.
- `-refresh` forces the refresh regardless of age, with or without `-active`.
- `-restart` replaces the run (today's `-relist`, which is removed).
- `-active` with `-inventory` is an error: the inventory is the whole listing.

**Argument order trap.** Click consumes the next token as the optional value,
so the target must come before `-active` (tested with Click 8.5.1):

| Command | Result |
|---|---|
| `kgit sync --org acme -active` | 90 days |
| `kgit sync --org acme -active 365` | 365 days |
| `kgit sync -active --org acme` | 90 days |
| `kgit sync --org -active acme` | error: `'acme' is not a valid integer` |

Help text and docs examples put the target first.

### Output

- On resume: `Resuming run … : 120 to do in the last 365 days, 2,180 pending
  outside it. Listed 3 days ago, refreshing.`
- `-status -active N` prints the same split without syncing, so the window can
  be tried before anything is cloned.

## Limits

- **`pushed_at` over-counts.** GitHub updates it on a push to any branch or
  tag, not just commits to the default branch. Dependabot and Renovate push
  branches to otherwise dormant repos, so some of those will look active. It
  never wrongly drops a repo with real commits, so it's safe as a first cut.
  The tighter measure, the last commit on the default branch, costs one REST
  call per repo (thousands for a large org, against 5,000 an hour) or a
  GraphQL query (`defaultBranchRef.target.committedDate`, ~100 repos a query),
  which kospex doesn't use yet. Left for later.
- **One org per command.** Running `-active` across every inventoried org at
  once is left until it's needed; a shell loop over the orgs does it today.

## Open questions

1. **A fork or archived flag that changes on refresh.** Proposed: the refresh
   updates the `archived` / `fork` flags and leaves the status alone, and the
   run doesn't record which flags it was started with. An archived repo can't
   be pushed to, so it ages out of any window by itself; one archived recently
   may still be synced once more.

   There's no archive date in the REST listing: `mergestat/blocks-legacy`
   returns `archived: true`, `pushed_at` 2022-12-09 and `updated_at`
   2023-09-22 (later than the archiving, so no stand-in). GraphQL has
   `archivedAt` (2022-12-18T00:07:55Z for the same repo). If kospex adds
   GraphQL for the default-branch commit date ([Limits](#limits)),
   `archivedAt` comes in the same query.

## Files (expected)

- `src/kgit.py`: `-active`, `-refresh`, `-restart` (replacing `-relist`), resume/status output.
- `src/kospex/org_sync_manifest.py`: window filter in `next_items`, window
  counts, `refresh_listing`, `listed_at`, the `gone` status.
- `src/kospex/org_sync.py`: refresh threshold setting.
- `src/kospex/db/migrations/0013_org_sync_listed_at.sql` (or next free number).
- Tests; `docs/kgit.md`; CHANGELOG; CLAUDE.md command line.
- Every `-relist` mention moves to `-restart`: the messages in `src/kgit.py`
  that suggest it, `org_sync_manifest.py` docstrings,
  `tests/test_kgit_sync_org_cli.py`, `tests/test_org_sync_manifest.py` and
  `docs/kgit.md`. `202610-kgit-sync-org.md` is left as written, as a record.
