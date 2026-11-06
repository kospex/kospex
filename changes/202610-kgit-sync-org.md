# `kgit sync --org` — resumable organisation sync

Design doc. No code yet.

```
$ kgit sync --org mergestat
Starting organization sync for: mergestat
Organization sync is not yet implemented
```

The `--org` branch of `kgit sync` (`src/kgit.py`, `sync()`) is a stub. The goal is
one command that checks auth, lists every repository in an org, then clones/pulls
and syncs each one, **and can be restarted after a failure without starting over
or losing track of what was planned.**

Related: [#80](https://github.com/kospex/kospex/issues/80) (`kgit sync-target`, keep an
org synced on a schedule) and [#12](https://github.com/kospex/kospex/issues/12) (flag
repos that no longer exist upstream). The design below is the base both need.

## What already exists

Most of the pieces are there. What's missing is the ability to resume.

| piece | where | notes |
|---|---|---|
| List an org's repos | `KospexGithub.get_repos()` | paginated, user vs org detection, `archived` / `fork` fields |
| Auth check | `KospexGithub.get_env_credentials()`, `test_auth()` | `TOKEN_ENV_VARS`: `GITHUB_AUTH_TOKEN` → `GH_TOKEN` → `GITHUB_TOKEN`, records `token_source` (#343) |
| Org clone + sync | `kgit github ORG -sync` | this is `sync --org`, but with no record of progress and no way to resume |
| Clone-or-pull | `KospexGit.clone_repo()` | pulls if the directory already exists |
| Path-conflict guard | `clone_path_conflict()` | already applied per repo in `kgit github -sync` |
| Incremental sync | `Kospex.sync_repo()` | uses `repos.last_sync_hash`, so re-syncing a done repo is cheap |
| Unattended git auth | `_pull_command(no_prompt=...)` | used by `kgit pull --no-prompt` |
| Run-ledger precedent | `osi_runs` (migration `0008`), `krunner osi -next` | `run_id` + per-repo row + lock |

You can already restart by hand today: `kgit github mergestat -out-repo-list x.txt`,
then `kgit clone -filename x.txt`, and re-run that clone command after a crash. It
works, but nothing records which repos failed or why, so a re-run pulls and
re-syncs every repo again.

### Prerequisite bug: a failed page silently truncates the list

`KospexGithub._paginate()` does `if data is None: break`. A timeout, 5xx or rate
limit on page 3 of 5 ends the loop, and the caller gets a **shorter list that looks
complete**. For a manifest this is the worst failure, because the repos that were
dropped never show up as failed. They simply aren't in the list. `_paginate` must
raise on a failed page (or return a completeness flag), and `sync --org` must
refuse to save a manifest from an incomplete listing. Fix this first, with a test.

## Where to keep the list: database, not a file

**Recommendation: two new tables in the kospex DB (migration `0012`).**

| | DB table | file (`~/kospex/runs/<org>-<ts>.jsonl`) |
|---|---|---|
| Atomic per-repo status update | yes, one small transaction per repo | needs rewrite-and-rename, or append-only with replay |
| Survives a crash mid-write | yes, SQLite journal | append-only JSONL: yes. Rewritten JSON: risk of a torn file |
| Concurrent runs / kweb reading | locking is already solved | ad hoc |
| Joins with `repos` (`last_fetch`, `last_sync`) | trivial | must be loaded and matched by hand |
| Visible in kweb / `system-status` later | natural | needs a parser |
| Human-editable to drop a repo | `UPDATE` / a CLI flag | edit the file |
| Needs a migration | yes | no |

The DB already has the information you'd join this against: `repos.last_fetch`,
`repos.last_sync` and `last_sync_hash`. `osi_runs` already set the pattern for
"a run id plus one row per repo", so this follows it rather than adding a second
convention. A file is still useful as an **export** (`-out-repo-list` already does
that), not as the source of truth.

### Schema sketch

```sql
-- One row per listing of an org.
CREATE TABLE IF NOT EXISTS [org_sync_runs] (
    [run_id] TEXT,            -- UTC timestamp, as osi_runs
    [org_key] TEXT,           -- e.g. github.com~mergestat
    [provider] TEXT,          -- github (bitbucket later)
    [owner_type] TEXT,        -- org | user
    [listed] INTEGER,         -- repos returned by the API
    [started_at] TEXT,
    [finished_at] TEXT,       -- NULL = unfinished, so resumable
    PRIMARY KEY(run_id)
    );

-- One row per repo in that listing, i.e. the manifest.
CREATE TABLE IF NOT EXISTS [org_sync_items] (
    [run_id] TEXT,
    [_repo_id] TEXT,          -- via generate_repo_id(), never hand-built
    [clone_url] TEXT,
    [provider_repo_id] TEXT,  -- e.g. github.com:10270250; survives renames/transfers
    [archived] INTEGER,
    [fork] INTEGER,
    [fork_parent] TEXT,       -- full_name of the immediate parent (forks only)
    [fork_source] TEXT,       -- full_name of the root of the fork network
    [private] INTEGER,
    [pushed_at] TEXT,         -- from the API; lets us skip unchanged repos
    [status] TEXT,            -- pending | in_progress | synced | skipped | failed
    [detail] TEXT,            -- why skipped / failed
    [started_at] TEXT,
    [finished_at] TEXT,
    [_git_server] TEXT,
    [_git_owner] TEXT,
    [_git_repo] TEXT,
    PRIMARY KEY(run_id, _repo_id)
    );
```

The run row and every item row are written **in one transaction, and only after
the listing is complete**. A failed listing leaves nothing behind, so there's no
half-saved manifest to resume from.

`0012` also adds the two lineage columns from #348, so that `sync --org` stores
GitHub ids from its first release (decided 2026-11-06):

```sql
ALTER TABLE repos ADD COLUMN provider_repo_id TEXT;    -- e.g. github.com:10270250
ALTER TABLE repos ADD COLUMN provider_checked_at TEXT; -- last lookup, found or not
```

Per CLAUDE.md: these go in the migration only, not in `kospex_schema.py`. Expect to
bump the migration counts in `test_db_health.py`, `test_kospex_utils.py` and
`test_kospex_schema.py`. Keep the SQL portable to SQLite 3.26.

## Flow

```
kgit sync --org mergestat
  1. Parse target       mergestat | github.com/mergestat | https://github.com/mergestat
                        bare name defaults to github.com -> org_key github.com~mergestat
  2. Auth               token present? test_auth() succeeds? (see Auth below)
  3. Unfinished run?    yes -> resume it (print run_id, done / pending / failed counts)
                        no  -> list repos (fail hard if incomplete) -> save manifest
  4. Take lock          one org sync per org at a time (as osi -next)
  5. For each item where status in (pending, in_progress, failed*):
       mark in_progress (commit)
       clone_path_conflict -> skipped
       clone_repo / pull   -> failed on error (detail = last stderr line)
       sync_repo           -> failed on exception, never abort the run
       set_repo_last_fetch
       mark synced (commit)
  6. Close run          finished_at set once nothing is pending / in_progress
  7. Summary            synced / skipped / failed, plus the resume command if any failed
  8. Drift report       repos in DB under org_key that are absent from the listing (#12)
```

\* `failed` is retried only with `-retry-failed`. Otherwise one permanently broken
repo would be retried on every resume.

### Why it's safe to resume

- `in_progress` is committed **before** the work starts. A crash, `kill -9` or
  power loss leaves the repo `in_progress`, and resume treats that as pending.
- Redoing a repo costs little. `clone_repo` pulls an existing clone, and `sync_repo`
  is incremental from `last_sync_hash`. Re-running the repo that was interrupted is
  the whole cost.
- Known gap: a `git clone` killed mid-transfer can leave a directory with a broken
  `.git`. On resume, if the path exists but `git rev-parse HEAD` fails, mark it
  `failed` with a clear detail. Don't delete it automatically.

## Auth

- No token in any of `KospexGithub.TOKEN_ENV_VARS` → stop with a message naming
  all three, unless `-no-auth` is passed (public repos only, 60 API req/hour).
  When a token is found, print `Using token from <VAR>`, as `kgit github -test-auth`
  does.
- Token present → `test_auth()` before listing, so a bad token fails in one second,
  not after a partial listing.
- **The API token is not the git credential.** Listing private repos with a token
  doesn't mean `git clone https://...` can fetch them, which depends on the user's
  credential helper or SSH. Support `-ssh` (use `ssh_url`) and `-no-prompt` (reuse
  `_pull_command`'s fail-fast env) so an unattended run fails each repo instead of
  hanging on a password prompt.

## CLI shape (proposed)

```
kgit sync --org TARGET  [-relist] [-retry-failed] [-skip-archived] [-include-forks | -skip-forks]
kgit sync --user TARGET           [-ssh] [-no-prompt] [-no-auth] [-next N]
kgit sync --org TARGET -inventory   # list the org's repos and SAVE them as a run; clone nothing
kgit sync --org TARGET -next 10     # sync the next 10 repos of the run, then stop
kgit sync --org TARGET -status      # show latest run's manifest, offline, no network
```

- `--org` and `--user` are mutually exclusive. The owner type is checked with
  `KospexGithub.get_account_type()` before listing, and a mismatch is an error,
  not a guess:
  ```
  $ kgit sync --org octocat
  Error: 'octocat' is a GitHub user, not an organisation. Use --user octocat
  $ kgit sync --user mergestat
  Error: 'mergestat' is a GitHub organisation, not a user. Use --org mergestat
  ```
  GitHub teams aren't repo owners (repos belong to the org), so there's no team mode.
- `org_sync_runs` gets an `owner_type` column (`org` / `user`). The key
  (`github.com~octocat`) has the same shape for both, so the rest of the manifest
  is unchanged.

- `-relist`: abandon the unfinished run (mark it finished, items left as they are)
  and take a fresh listing.
- **Forks are skipped by default, but always tracked.** Each fork is a manifest
  item with status `skipped`, reason `fork`, and its parent and source recorded.
  `KOSPEX_CLONE_FORKS_DEFAULT=true` (environment or `~/kospex/kospex.env`) makes
  syncing them the default for an installation; `-include-forks` / `-skip-forks`
  override it per run. `-skip-archived` works the same way for archived
  repos. Either way, the manifest records everything the org returned.
- The listing endpoint returns `id` and `fork` but not `parent` or `source`
  (checked against the live API). Those need one `GET /repos/{owner}/{repo}` per
  **fork**, and only for forks. `get_repos()` already makes that request and then
  drops the result.
- `-inventory`: take stock of the org and **save it**, without cloning or
  syncing anything. It runs steps 1–3 of the flow: list every repo, save the
  listing to the DB as a new run with every repo `pending`, and print a summary
  against what kospex already has. The saved run is what `-next N` and
  `-status` then work from.

  ```
  $ kgit sync --org acme -inventory
  Listed 1,042 repos for github.com~acme, saved as run 20261106T031500Z
    active        961     archived     58     forks      23 (skipped by default)
    in kospex     212     not yet     749     changed since last fetch   87
    in kospex but gone from the org: 4    (see -status)
  Nothing cloned. Run `kgit sync --org acme -next 10` to start syncing.
  ```

  "In kospex" means the repo is already in `repos`. "Changed since last fetch"
  compares the API's `pushed_at` with `repos.last_fetch`. "Gone from the org" is
  the #12 drift report. The listing needs only the API token, not git
  credentials, so this works before cloning is set up.

  **The one case where it doesn't save:** if the org already has an unfinished
  run, `-inventory` lists the org and prints the summary, but keeps the existing
  run and says so. A run partway through a sync isn't thrown away by a check.
  `-inventory -relist` closes the old run and saves the new listing.

  ```
  $ kgit sync --org acme -inventory
  Listed 1,047 repos for github.com~acme (not saved)
  Run 20261106T031500Z is unfinished: 310 synced, 2 failed, 730 pending.
    5 repos are new since that run, 0 are gone. Kept the unfinished run.
  To replace it with this listing: kgit sync --org acme -inventory -relist
  ```
- `-next N`: sync at most N items from the unfinished run, then stop with the run
  still open. Same name and meaning as `krunner osi -next`. With no unfinished
  run, it takes an inventory first (saved, as `-inventory`) and then syncs N. A
  1,000-repo org at `-next 10` per cron tick takes 100 ticks. Items are taken most recently pushed
  first, so active repos land first. Combine with `-no-prompt` for cron.
- `-pushed_at` shortcut (later): skip the fetch when `pushed_at <= repos.last_fetch`.

## Files

- `src/kospex_github.py`: `_paginate` raises on a failed page
- `src/kospex/db/migrations/0012_org_sync.sql`: two tables
- `src/kospex_query.py`: create run + items (atomic), next items, mark status, latest unfinished run
- `src/kgit.py`: replace the stub, add a target parser, keep the single-repo path as is
- `tests/`: paginate truncation, manifest atomicity, resume after `in_progress`, conflict → skipped, drift report

## Decisions (2026-11-04)

1. **`kgit github ORG -sync` becomes an alias** of `kgit sync --org` / `--user`
   (whichever the owner is), so there's one org-sync implementation. Listing
   (`kgit github ORG`, `-out-repo-list`, `-ssh-clone-url`) is unchanged. What the
   alias gains over today's `-sync`:

   | | `kgit github ORG -sync` today | `kgit sync --org` |
   |---|---|---|
   | Record of which repos the org returned | none, it's only in memory | manifest in the DB |
   | A failed page while listing | silently gives a shorter list that looks complete | listing must be complete, or nothing is saved |
   | A clone fails | prints `ERROR` and moves on, with no record | `failed` row with the reason |
   | `sync_repo` raises an exception | stops the whole run (it isn't wrapped in `try`) | that repo is marked `failed`, and the run continues |
   | Crash or Ctrl-C partway | start over; every repo is pulled and synced again | resume from where it stopped |
   | Two runs at once | nothing stops it | lock per org |
   | Repos gone from the org | not noticed | drift report (#12) |

2. **GitHub first.** The tables and resume logic are provider-neutral (the
   `provider` column), and Bitbucket follows in its own PR.
3. **`--user` is explicit**, with the mismatch error above.
4. **Forks: skip by default, always track** (2026-11-04). User accounts fork a
   lot, and a synced fork brings its parent's whole history, which inflates the
   org's developer stats. But knowing an org holds a fork matters, especially a
   patched one it depends on, so forks are recorded in the manifest and as
   `fork_of` lineage rows. `-include-forks` opts in. For closed-source work,
   where an org's forks are deliberate, `KOSPEX_CLONE_FORKS_DEFAULT=true` flips
   the default for the installation (2026-11-05). See
   `changes/202611-repo-lineage.md` (#348), which also covers private copies (which
   GitHub doesn't mark as forks) and moved repos.
5. **`-inventory` and `-next N`** (2026-11-06). `-inventory` lists the org's
   repos, saves them as a run, and reports them against the DB without cloning.
   Named `-inventory` rather than `-list-only`, which sounded like it saved
   nothing. `-next N` syncs a
   slice per run, for large orgs on a schedule. It replaces the earlier `-limit
   N` so the name matches `krunner osi -next`.
6. **`0012` includes `repos.provider_repo_id` and `provider_checked_at`**
   (2026-11-06), rather than waiting for the #348 migration.

## Delivery

Separate PRs, each mergeable on its own:

1. `_paginate` raises on a failed page, and `get_repos()` callers handle it. This
   is the prerequisite bug, and it fixes `kgit github` listing today.
2. Migration `0012` (the two manifest tables, plus `repos.provider_repo_id` and
   `provider_checked_at`) plus the query layer (create a run and its items atomically,
   next items, mark status, latest unfinished run).
3. `kgit sync --org` / `--user`: auth, owner-type check, listing → manifest, the
   per-repo loop, resume, lock, summary, `-status`, `-inventory` (with the drift
   count), `-next N`.
4. The `kgit github -sync` alias.

## Open questions

1. **Retention**: prune old `org_sync_runs` / `org_sync_items`, same open question
   as `osi_runs`.
2. **#80 `sync-target`**: is a "target" a saved org with its latest run, so that
   `sync-target` is just `sync --org` on a schedule? If so, this table is the target list.
