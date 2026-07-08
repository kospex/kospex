# `kgit pull` — refresh known local clones + offline staleness check

## Overview

kospex analyses local git clones under `KOSPEX_CODE`. Those clones only advance
when something pulls them, and today nothing does reliably: `kgit clone` clones
+ syncs a *new* repo, `kgit sync URL` clones/syncs from a *remote URL* (its
`--org` mode is an unimplemented stub), and `kospex-agent` is a stub that isn't
run. There is no command to refresh the repos kospex **already knows about**, so
clones drift out of date (in practice last pulled ~2 months ago) and every view
— `/osi/`, tech-landscape, developer activity — reflects a stale snapshot. There
is also no record of *when* a clone was last refreshed.

This adds a `kgit pull` command that refreshes known local clones on demand, and
an offline `--check` mode that shows how stale each clone is.

## Goal / scope

- **In:** on-demand refresh (git pull + kospex sync) of repos already in the DB,
  selectable by single repo / org / server / all; an offline staleness report;
  tracking when each clone was last refreshed.
- **Out:** discovering/cloning *new* repos from an org (that is `kgit sync --org`
  / clone territory, separate and currently stubbed); a live "commits behind
  upstream" comparison; any improved bulk-authentication story (out of scope for
  kospex OSS); multi-branch (kospex syncs the default branch only — see the
  file_metadata single-row design doc).

## Command surface

```
kgit pull REPO_ID              # one known repo, e.g. github.com~kospex~kospex
kgit pull --all                # every repo in the DB
kgit pull --org  ORG_KEY       # e.g. github.com~kospex
kgit pull --server SERVER      # e.g. github.com
kgit pull --check [scope]      # offline staleness view, no pull/network
kgit pull --no-prompt [scope]  # non-interactive auth (fail-fast), for unattended runs
```

- A scope is **required** — `REPO_ID` or one of `--all` / `--org` / `--server`.
  A bare `kgit pull` errors (no accidental "pull everything").
- `--check` and `--no-prompt` compose with any scope.
- The verb is `pull` (not overloading `kgit sync`, which is remote-URL
  discovery/clone) — it reads as "update local clones" and mirrors `git pull`.
- The commented-out single-directory `kgit pull` stub (kgit.py:257) is replaced
  by this.

## Behaviour — refresh

Resolve the in-scope repos from the DB (reuse the existing
`KospexQuery` repos-by-scope pattern, same shape as other commands). Then,
**sequentially** (fine per design; sync is sequential anyway), for each repo:

1. If the clone is missing on disk → skip, record `not cloned`.
2. `git pull --ff-only` in the clone via `subprocess` (capture output, check exit
   code — not `os.system`). Clones are pure mirrors of the remote with no local
   commits, so a pull fast-forwards naturally; `--ff-only` is a safety net that
   turns any unexpected divergence into a clean skip rather than a merge commit.
3. On success: stamp `repos.last_fetch = now`, then `kospex.sync_repo(path)`. New
   commits get ingested and file_metadata rebuilt; if nothing moved, the
   file_metadata version-aware guard skips the expensive scan, so unchanged repos
   are cheap.
4. On failure (non-fast-forward, auth failure, network error): skip, record the
   reason. The run never dies on one repo.

**Report** at the end: per-repo outcome — `updated (N commits)` / `up to date` /
`skipped (<reason>)` / `failed (<error>)` — plus a tally.

## Behaviour — `--check` (offline)

From the DB only, no network, no auth prompts. For each in-scope repo, show:
`last_fetch`, `last_sync`, last commit date (the repos `last_seen`, = newest
`committer_when`), and an age (e.g. "63d"). Sorted stalest-first so overdue
refreshes are obvious. This is the cheap "what needs pulling" view.

## Authentication handling

git shells out and uses the configured credential helper. When credentials are
cached the pull is silent; otherwise the helper prompts and the git process
blocks waiting for it (core git has **no** timeout on a credential prompt).

- **Default: interactive** — the helper works as normal, so private repos pull on
  a developer machine. A bulk run may occasionally prompt; acceptable when
  attended.
- **`--no-prompt`** — sets `GIT_TERMINAL_PROMPT=0` and `credential.interactive=false`
  so git fails fast instead of prompting; auth-needing repos are skipped +
  reported. For cron / unattended runs so they never hang.

A better bulk-auth experience is out of scope for kospex OSS.

## Schema

Migration `0004`: add `repos.last_fetch TEXT` (ISO timestamp, nullable). NULL =
"never recorded" (shown as such in `--check`). Stamped:

- on `kgit clone` (initial clone), and
- on every successful `kgit pull`.

## Error handling summary

| Situation | Outcome |
|---|---|
| clone dir missing | skip — `not cloned` |
| pull can't fast-forward | skip — `not fast-forwardable` (unexpected for a mirror) |
| auth / network failure | skip — `fetch failed: <detail>` |
| repo not in scope / DB empty | clear message, exit 0 |

## Testing

- **Unit:** the `--check` row-building (repo rows → formatted staleness incl. age
  and NULL `last_fetch`); the scope resolver (`REPO_ID` / `--all` / `--org` /
  `--server` → correct repo set); the git-pull argument construction
  (`--no-prompt` sets the non-interactive env).
- **Integration:** a temp "upstream" repo + a clone one commit behind →
  `kgit pull REPO_ID` fast-forwards it, stamps `last_fetch`, re-syncs, and reports
  `updated`; a second `kgit pull` reports `up to date`; `--check` prints ages
  without touching the network. scc-dependent assertions gated on `which("scc")`
  (optional binary, absent on CI).

## Files anticipated

- `src/kgit.py` — new `pull` command (scope resolution, per-repo pull loop,
  report, `--check`, `--no-prompt`); replace the commented-out `pull` stub.
- `src/kgit.py` / `src/kospex_git.py` — stamp `last_fetch` on `clone` and `pull`
  (small helper).
- `src/kospex/db/migrations/0004_repos_last_fetch.sql` — add `last_fetch`.
- `src/kospex_query.py` — a repos-with-staleness query for `--check` if the
  existing `get_repos` shape isn't sufficient.
- Tests as above.
