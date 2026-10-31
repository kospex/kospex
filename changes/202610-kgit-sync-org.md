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
| Auth check | `KospexGithub.get_env_credentials()`, `test_auth()` | reads `GITHUB_AUTH_TOKEN`, then `GH_TOKEN` |
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
    [archived] INTEGER,
    [fork] INTEGER,
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

- No `GITHUB_AUTH_TOKEN` / `GH_TOKEN` → stop with a message naming both variables,
  unless `-no-auth` is passed (public repos only, 60 API req/hour).
- Token present → `test_auth()` before listing, so a bad token fails in one second,
  not after a partial listing.
- **The API token is not the git credential.** Listing private repos with a token
  doesn't mean `git clone https://...` can fetch them, which depends on the user's
  credential helper or SSH. Support `-ssh` (use `ssh_url`) and `-no-prompt` (reuse
  `_pull_command`'s fail-fast env) so an unattended run fails each repo instead of
  hanging on a password prompt.
- Docs drift to fix alongside: CLAUDE.md names `GITHUB_TOKEN`, but the code reads
  `GITHUB_AUTH_TOKEN` / `GH_TOKEN`.

## CLI shape (proposed)

```
kgit sync --org TARGET [-relist] [-retry-failed] [-skip-archived] [-skip-forks]
                       [-ssh] [-no-prompt] [-no-auth] [-limit N]
kgit sync --org TARGET -status      # show latest run's manifest, offline, no network
```

- `-relist`: abandon the unfinished run (mark it finished, items left as they are)
  and take a fresh listing.
- `-skip-archived` / `-skip-forks`: those items are still written to the manifest,
  as `skipped` with a reason. The manifest records everything the org returned.
- `-limit N`: process at most N items, for cron-sized batches (as `osi -next`).
- `-pushed_at` shortcut (later): skip the fetch when `pushed_at <= repos.last_fetch`.

## Files

- `src/kospex_github.py`: `_paginate` raises on a failed page
- `src/kospex/db/migrations/0012_org_sync.sql`: two tables
- `src/kospex_query.py`: create run + items (atomic), next items, mark status, latest unfinished run
- `src/kgit.py`: replace the stub, add a target parser, keep the single-repo path as is
- `tests/`: paginate truncation, manifest atomicity, resume after `in_progress`, conflict → skipped, drift report

## Open questions

1. **`kgit github ORG -sync`**: make it an alias of `sync --org`, or deprecate it?
   Two org-sync paths with different safety properties shouldn't both exist.
2. **Bitbucket**: `kgit bitbucket -workspace` already lists repos. Do it in the
   same PR behind a small provider interface, or GitHub first?
3. **Retention**: prune old `org_sync_runs` / `org_sync_items`, same open question
   as `osi_runs`.
4. **#80 `sync-target`**: is a "target" a saved org with its latest run, so that
   `sync-target` is just `sync --org` on a schedule? If so, this table is the target list.
5. **User accounts** (`kgit sync --org someuser`): `get_repos` already handles users.
   Allow it, or require `--user`?
