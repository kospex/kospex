# kgit

The kgit command is a helper for some git commands and utilities on repos.

## clone

The clone function is a wrapper around the CLI git installed on your machine.
It will authenticate with your credentials as it's running in your shell.

```bash
kgit clone https://github.com/OWNER/REPO
```

By default, the clone command clones the repo to the `KOSPEX_CODE/SERVER/ORG/REPO`
file structure and syncs the entire git history to the kospex database.

You can confirm the sync by running the following kospex command:

```bash
kospex list-repos -db
```

### Cloning a list of repos in a file

To clone and sync in bulk you can use the `-filename FILENAME` switch:

```bash
kgit clone -filename FILENAME
```

For example, the contents of the file might look like:

```
https://github.com/kospex/kospex
# This is a comment
https://github.com/mergestat/mergestat-lite
```

## sync

`kgit sync --org` clones (or pulls) and syncs every repository in a GitHub
organisation. `kgit sync --user` does the same for the repositories a user owns.

Like every kospex pull, the pull of an existing clone is fast-forward only, whatever
your `pull.rebase` / `pull.ff` settings say. A clone that can't be fast-forwarded,
because the remote's history was rewritten or the clone has commits of its own, is
left as it was and recorded as `failed` ("clone or pull failed"), not synced.

```bash
kgit sync --org acme
kgit sync --user octocat
```

The owner can be given as `acme`, `github.com/acme`, `https://github.com/acme`
or `github.com~acme`. Giving an organisation to `--user`, or a user to `--org`,
is an error that tells you which flag to use. GitHub only, for now.

It needs a GitHub token, from the same variables as [`kgit github`](#github), and
prints which variable it used. `-no-auth` lists public repositories without one,
within GitHub's 60 requests an hour.

### Resuming, and syncing a few at a time

The listing is saved in the kospex database as a **run**, with a status for each
repository: `pending`, `in_progress`, `synced`, `skipped`, `failed` or `gone`. If a sync is
interrupted, or one repository fails, run the same command again and it carries on
from the saved run rather than listing and syncing everything again.

```bash
kgit sync --org acme -inventory   # list and save the repos; clone nothing
kgit sync --org acme -next 10     # sync the next 10, then stop
kgit sync --org acme -status      # progress so far, from the database only
```

- **`-inventory`** saves the listing and prints what the organisation has, compared
  with what kospex already holds: active, archived, forks, already in kospex,
  changed since kospex last fetched, and in kospex but no longer listed. It needs
  only the token, not git credentials. If a run is already partway through, it
  prints the comparison but keeps that run: add `-refresh` to merge the new
  listing into it, or `-restart` to replace it.
- **`-next N`** syncs the next N repositories, most recently pushed first, so a
  large organisation can be synced from a schedule. A 1,000-repo organisation at
  `-next 10` takes 100 runs. Once every repository is done, the next run lists the
  organisation again.
- **`-status`** shows the latest run: counts, the failures with their reasons, and
  repositories kospex has that the listing didn't include. No network.
- **`-refresh`** lists the organisation again and merges it into the unfinished
  run, keeping each repository's progress. New repositories are added as
  `pending`; ones no longer listed become `gone` (deleted, renamed, transferred,
  or no longer visible to the token), with what they were in the detail; and
  everything else gets its new pushed date and flags. A repository archived since
  keeps its status. Without `-refresh`, resuming a run never lists again.
- **`-restart`** closes the unfinished run and starts from a fresh listing, with
  every repository pending again.
- **`-retry-failed`** retries repositories that failed earlier in the unfinished
  run. Without it, a failure is retried on the next listing.

A run's times are in `org_sync_runs`:

- `started_at`: when the run was created (its first listing), not when syncing began.
- `listed_at`: when the run was last listed. `-refresh` updates it, and so does
  `-active` when it lists again.
- `finished_at`: when every repository was done, or the run was replaced by
  `-restart`. It's empty while the run is unfinished.

Each repository in `org_sync_items` has its own `started_at` and `finished_at`
for its clone and sync.

Only one sync of an organisation runs at a time. A second one finds the lock,
says so, and exits 0, so overlapping scheduled runs are harmless. A sync is
never interrupted for taking a long time. If a sync crashes, the next one
notices its process has gone and takes over the lock. The lock file is
`org-sync-<org>.lock` in `~/kospex`.

### Only active repositories: `-active`

A large organisation is mostly dormant. `-active` syncs only the repositories
pushed to in the last 90 days, or `-active N` for the last N days:

```bash
kgit sync --org acme -inventory              # list and save every repo
kgit sync --org acme -status -active 365     # how many were pushed to in a year
kgit sync --org acme -active 365             # sync those
kgit sync --org acme -active 365 -next 50    # or 50 at a time, from a schedule
```

- **Put the organisation before `-active`.** The number is optional, so in
  `kgit sync --org -active acme` the `acme` is read as the number of days.
- **The rest stay `pending`**, not skipped, so a later run with a wider window,
  or without `-active`, syncs them. The run stays unfinished until then.
- **Inside the window, a repository is cloned if it's new to kospex and pulled
  if it isn't.** Outside it, the run leaves repositories alone, including ones
  cloned earlier: keep those up to date with `kgit pull --org acme`.
- **The window counts back from now**, using the pushed dates from the listing.
  If the run's listing is more than 2 days old, `-active` lists the organisation
  again and merges it in, as `-refresh` does, so a scheduled run picks up new
  repositories and ones that have come back to life. Set
  `KOSPEX_ORG_SYNC_REFRESH_DAYS` to change the 2 days; `0` lists every time.
- **The pushed date counts a push to any branch or tag**, not only commits to
  the default branch. Bots that push branches, such as Dependabot and Renovate,
  make some dormant repositories look active. No active repository is left out.

### What changed between listings: `-history`

Every saved listing is recorded: the first one of each run, and each refresh,
including the ones `-active` makes by itself. A weekly scheduled run therefore
builds a history of the organisation. `-history` shows it, from the database
only:

```bash
kgit sync --org acme -history
```

Each row counts the changes since the listing before: repositories that are
`new` (in no earlier listing), `gone`, `back` (listed before, missing from the
previous listing), `renamed`, newly archived (`archived+`) and `unarchived`, with
the totals for archived, forks, private and internal repositories. The counts
are kept in `org_sync_listings`. A listing saved before kospex recorded
internal repositories has an empty `internal`.

To see which repositories changed, use three columns of `org_sync_items`.
They're recorded for every repository in the listing, synced or not:

- `first_listed_at`: the first listing of the organisation that had it. It
  carries across runs, and across a rename.
- `archived_seen_at`: the first listing that found it archived after one that
  didn't. It's empty if it isn't archived, or if it was already archived when
  first listed, because kospex can't know when that happened. It's as precise
  as your listings: weekly listings date an archive to within a week.
- `created_at`: when GitHub says the repository was created. A repository
  first listed recently but created long ago was transferred in, or has only
  just become visible to the token.

For example, run against `~/kospex/kospex.db` with `sqlite3`, the repositories
added in the last 30 days:

```sql
SELECT _repo_id, clone_url, provider_repo_id, created_at, first_listed_at
  FROM org_sync_items
 WHERE run_id = (SELECT MAX(run_id) FROM org_sync_runs WHERE org_key = 'github.com~acme')
   AND status != 'gone'
   AND first_listed_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-30 days')
   AND first_listed_at > (SELECT MIN(listed_at) FROM org_sync_listings
                           WHERE org_key = 'github.com~acme')
 ORDER BY first_listed_at;
```

The last condition leaves out the organisation's first listing, where every
repository is new.

For the repositories archived in the last 30 days, use `archived_seen_at` in
place of `first_listed_at` in the first condition, and drop the last one. For
the repositories that have disappeared since the run began, use
`status = 'gone'`. Those rows' `finished_at` says when a refresh first found
them missing, and `detail` says what they were before, such as `was synced`. A
renamed repository's old name is one of them, with its new name in the detail:
`was synced, renamed to github.com~acme~new`.

A repository missing from the listing after a `-restart` has no row in the
new run. `-history` still counts it as gone.

### Public, private and internal repositories

Each listed repository's visibility is recorded as GitHub reports it, in
`org_sync_items.visibility`:

- `public`: anyone can read it.
- `private`: only the people and teams given access to it.
- `internal`: every member of the enterprise can read it, across all of its
  organisations, but not the public. Only organisations owned by a GitHub
  Enterprise account (Cloud or Server) have internal repositories.

`-inventory` and `-status` count each one:

```
  public       120     private       14     internal 1,178
```

`private` (`1` or `0`) still means GitHub said `private`, so an internal
repository has `private = 0`. Repositories listed before kospex recorded
visibility have an empty `visibility`, counted as `not recorded`. To fill it
in for an unfinished run, list the organisation again into the run. This
clones nothing:

```bash
kgit sync --org acme -inventory -refresh
```

A finished run can't be listed again: the next listing of the organisation
starts a new run and records it.

### What each command records

A run is often worked through over several commands with different options:
`-inventory`, then `-next 10` a few times, then `-active 90`. Each
`kgit sync --org` / `--user` command that lists or syncs gets a row in
`org_sync_commands`:

- the command and its options, as typed, ready to copy;
- the listing it saved (`first`, `refresh` or `restart`) and what it changed;
- how many repositories it synced, skipped and failed;
- the GitHub token it used: the login it belongs to, its kind (`fine-grained`,
  `classic`, `app` or `oauth`, from its prefix), the environment variable it
  came from, and its expiry as GitHub reports it. `-no-auth` is recorded as
  `none`. **The token itself is never stored.**

`finished_at` is empty for a command that is still running or was stopped,
for example with Ctrl-C, and `error` says why a command stopped early. Reads
(`-status`, `-history`) aren't recorded, and nor is a command refused before it
did anything: a rejected token, an owner that doesn't exist, or a run already
in progress.

The token matters because a fine-grained token limited to some of an
organisation's repositories lists fewer of them: after a `-refresh` with a
different token, the repositories it can't see are `gone`. To see which token
listed each time:

```sql
SELECT started_at, command, listing, [new], gone, auth_login, auth_kind,
       auth_source, auth_expires_at
  FROM org_sync_commands
 WHERE org_key = 'github.com~acme'
 ORDER BY started_at;
```

Suggestions kgit prints, such as `Continue with: ...` and `Resume with: ...`,
repeat every option the command was given (`-active`, `-next`, `-ssh`,
`-no-prompt`, `-skip-archived`, the fork options and `-no-auth`), so a copied
suggestion does what was asked. `-retry-failed`, `-inventory`, `-refresh` and
`-restart` aren't carried over.

### Forks, archived repositories and other options

- **Forks are skipped, but recorded** with their parent and the root of their fork
  network. `-include-forks` syncs them. To sync forks by default, for example
  when kospex is installed for a company's own repositories, set
  `KOSPEX_CLONE_FORKS_DEFAULT=true` in the environment or `~/kospex/kospex.env`.
  `-skip-forks` overrides that for one run.
- **Archived repositories are synced** unless `-skip-archived`.
- **`--user`** leaves out repositories owned by other accounts. GitHub lists the
  ones a user collaborates on or is a member of alongside their own.
- **`-ssh`** clones with SSH URLs instead of HTTPS.
- **`-no-prompt`** makes git fail instead of asking for credentials, for
  unattended runs. The failure is recorded and the run moves on.

For example, from cron, with a token set in cron's environment:

```
*/30 * * * * kgit sync --org acme -next 10 -no-prompt
```

## bitbucket

All bitbucket commands require Atlassian credentials in the environment. The following
variables are required:

- `BITBUCKET_API_TOKEN` — an Atlassian API token
- `BITBUCKET_EMAIL` — your Atlassian account email (or `BITBUCKET_USERNAME`, but not both)

See [Troubleshooting → Bitbucket authentication](troubleshooting#bitbucket-authentication)
for the token types, required scopes and worked examples.

> *Historical:* the old `BITBUCKET_USERNAME` + `BITBUCKET_APP_PASSWORD` method stopped
> working on 2026-06-09, when Atlassian permanently disabled all app passwords. Use an
> API token as above.

```bash
kgit bitbucket -test-auth
```

This flag will check if the credentials work, display the status and exit.

```bash
kgit bitbucket -workspace WORKSPACE
```

If you want to write all the clone URLs to a file you can use the `-out-repo-list FILENAME` switch:

```bash
kgit bitbucket -workspace WORKSPACE -out-repo-list FILENAME
```

This can be used with the `kgit clone -filename` switch as shown above.

## github

The github function can interact with both authenticated and public orgs and users.

For authenticated requests, set a GitHub Personal Access Token (PAT) in one of
these environment variables. kgit checks them in this order and uses the first
one that is set and not empty:

1. `GITHUB_AUTH_TOKEN`: kospex's own name, so a token set for kospex wins
2. `GH_TOKEN`: the gh CLI's name
3. `GITHUB_TOKEN`: also read by the gh CLI, and what GitHub Actions provides

Surrounding whitespace is ignored. For read-only use, a token with no scopes
(classic) or "Public repositories (read-only)" (fine-grained) is enough.

```bash
kgit github -test-auth
```

This `-test-auth` flag checks the token works, prints which variable it came
from (never the token itself), and exits.

To list an owner's repos, give the organisation or user name:

```bash
kgit github acme                     # an organisation's repos
kgit github octocat                  # the repos a user owns
kgit github octocat -include-member  # also the ones they don't own
```

For a user, kgit lists only the repos the user owns by default, forks included,
as `kgit sync --user` does. GitHub's listing for a user also returns repos they
collaborate on or reach through an organisation: for `octocat`, 8 owned and 203
in all. `-include-member` lists those too. For your own account, that includes
your organisations' repos. An organisation's listing is always its own repos.

`-out-repo-list FILENAME` writes the clone URLs of the listed repos to a file,
for `kgit clone -filename`; add `-ssh-clone-url` for SSH URLs.

`-sync` clones and syncs the owner's repos. It runs
[`kgit sync --org` or `--user`](#sync), whichever the owner is, so it saves the
listing as a run, resumes after an interruption, and skips forks unless told
otherwise:

```bash
kgit github acme -sync      # the same as: kgit sync --org acme
```

For that command's other options (`-next`, `-include-forks`, `-ssh` and so on),
use `kgit sync` directly. `-sync` can't be combined with `-include-member`,
`-out-repo-list` or `-ssh-clone-url`, which only apply to the listing.

## pull

`kgit pull` updates clones kospex already has, then syncs them. It never clones
anything new. It needs a scope:

```bash
kgit pull github.com~acme~app    # one repo, by repo id
kgit pull --org acme             # every repo of an org in kospex
kgit pull --server gitlab.com    # every repo on a git server
kgit pull --all                  # every repo
kgit pull --org acme --check     # how stale each clone is; no network
kgit pull --all --stale 30       # only repos not refreshed in 30 days
```

The pull is fast-forward only: a clone that can't be fast-forwarded is left as
it was, reported, and not synced.

### Only stale repos: `--stale DAYS`

`--stale DAYS` narrows any scope to the repos whose last refresh was more than
DAYS days ago. The last refresh is `last_fetch`, the time kgit last pulled or
cloned the repo. A repo that kgit never fetched (one synced before kospex
recorded fetches, or with `kospex sync-directory`) uses `last_sync` instead,
and a repo with neither always counts as stale.

```bash
kgit pull --check --all --stale 30   # which ones, offline
kgit pull --all --stale 30           # Pulling 14 of 403 repos not refreshed in 30 days.
```

With `--check`, the report shows only those repos. When none are stale, kgit
says so and exits successfully, so `--stale` is safe in a schedule. The
`fetch_age` column in `--check` uses the same rule: a repo's `last_fetch` says
`never` when it fell back to `last_sync`.

`--org` accepts the same forms as `kgit sync --org`: `acme`, `github.com/acme`,
`https://github.com/acme` or the org key `github.com~acme`. Unlike `kgit sync`,
which asks GitHub, `kgit pull` matches the value against the orgs already in
kospex, on any git server, ignoring case:

- **One match** is used, and kgit prints the org key it chose, for example
  `Using org github.com~acme`.
- **More than one match**, such as `acme` on both github.com and gitlab.com, is
  an error listing both keys. Give the key, or the host and owner
  (`gitlab.com/acme`).
- **A repository** (`github.com/acme/app`) is an error giving the repo id to
  use instead.
- **A git server** (`gitlab.com`, or a host whose repos have no owner, such as
  `go.googlesource.com`) is an error suggesting `--server`.
- **An org kospex doesn't have** is an error suggesting `kgit sync --org` first.

GitLab nested groups work as `gitlab.com/group/subgroup` or by their org key,
`gitlab.com~group~~subgroup`. A name without `--org` is always read as a repo id.
