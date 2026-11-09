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
repository: `pending`, `in_progress`, `synced`, `skipped` or `failed`. If a sync is
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
  prints the comparison but keeps that run; add `-relist` to replace it.
- **`-next N`** syncs the next N repositories, most recently pushed first, so a
  large organisation can be synced from a schedule. A 1,000-repo organisation at
  `-next 10` takes 100 runs. Once every repository is done, the next run lists the
  organisation again.
- **`-status`** shows the latest run: counts, the failures with their reasons, and
  repositories kospex has that the listing didn't include. No network.
- **`-relist`** closes the unfinished run and starts from a fresh listing.
- **`-retry-failed`** retries repositories that failed earlier in the unfinished
  run. Without it, a failure is retried on the next listing.

Only one sync of an organisation runs at a time. A second one finds the lock,
says so, and exits 0, so overlapping scheduled runs are harmless. A sync is
never interrupted for taking a long time. If a sync crashes, the next one
notices its process has gone and takes over the lock. The lock file is
`org-sync-<org>.lock` in `~/kospex`.

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
```

The pull is fast-forward only: a clone that can't be fast-forwarded is left as
it was, reported, and not synced.

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
