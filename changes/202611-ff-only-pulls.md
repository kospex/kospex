# Every kospex pull is fast-forward only

## Overview

A kospex clone is a mirror of its remote. kospex reads it, and never commits
to it. So the only correct update is a fast-forward. If that isn't possible,
the remote's history was rewritten, or the clone has commits of its own, and
either way someone should look before kospex records anything.

`kgit pull` already ran `git pull --ff-only`. The other places that update a
clone ran a plain `git pull`, which follows the user's `pull.rebase` /
`pull.ff` config:

| Where | Used by |
|---|---|
| `KospexGit.clone_repo` | `kgit clone`, `kgit sync URL`, `kgit sync --org` / `--user`, `kgit github -sync`, and anything else that calls it |
| `krunner git-pull` | |
| `kospex_agent._update_repositories` | `kospex-agent start` |

`krunner git-pull` and the agent also synced a repo whose pull had failed.

## What a plain pull did

Tested with real repositories on git 2.54, with a clone of a remote with
commits `a`, `b`. "Rewritten" means the remote replaced `b` with a new commit.
"Recreated" means a repo was deleted and recreated at the same URL with
unrelated history:

| config | rewritten | recreated |
|---|---|---|
| neither set | refused | refused |
| `pull.rebase=false` | merge commit added | refused (unrelated histories) |
| `pull.rebase=true` | rebased: `b` dropped from the branch | rebased onto the new history |

The sync after a rebase or merge records commits that exist only in that
clone, and the old commits are no longer reachable from `HEAD`. They stay in
the kospex DB, because a sync never deletes rows.

## The change

- `KospexGit.pull_command(path, no_prompt=False)` builds the pull. It returns
  `(argv, env_overrides)`. It is moved from `kgit._pull_command`, which now
  delegates to it, and adds `--no-rebase`:
  `git -C PATH pull --ff-only --no-rebase`.
  - `--ff-only` makes git refuse anything but a fast-forward.
  - `--no-rebase` overrides `pull.rebase` on the command line, rather than
    relying on `--ff-only` taking precedence over it.
- `clone_repo` uses it. A failed pull still returns `None`, which every
  caller already treats as "don't sync". The message now says the clone was
  left as it was.
- `krunner git-pull` uses it, and on failure records `GIT_ERROR` and skips the
  sync.
- `kospex_agent` uses it with `no_prompt=True`, because nobody is watching a
  background agent. On failure it logs a warning and skips the sync. It also
  no longer `chdir`s the process around the pull. An exception in a sync left
  the agent in the repo's directory.

## Checked

- **Every pull config, two git versions.** On git 2.54 (macOS) and git 2.43
  (Rocky Linux 8), `--ff-only` refused rewritten, recreated and local-commit
  cases, and fast-forwarded the normal case, under:
  - no pull config
  - `pull.rebase=true`
  - `pull.rebase=merges`
  - `pull.rebase=false`
  - `pull.ff=false`

  Adding `--no-rebase` changed nothing on either version. Earlier RHEL 8
  point releases shipped older git, which wasn't tested; `--no-rebase` is
  there for them.
- **Tests:** `tests/test_ff_only_pulls.py` builds real repositories under an
  isolated `GIT_CONFIG_GLOBAL`, and checks each clone's history after the
  pull, not just the command line:
  - `clone_repo` under all five configs and four cases
  - `krunner git-pull` (one stuck and one moving clone: only the moving one
    is synced)
  - the agent, the same way, plus its non-interactive pull
- **Deliberate breaks:** 8 breaks, all caught. They were a plain pull
  everywhere, dropping `--no-rebase`, a plain pull in `clone_repo`, in
  krunner, and in `kgit pull`'s own command, krunner and the agent syncing
  after a failed pull, and the agent able to prompt.

## Not in this change

- **Commits missed after a merge.** The `--since` gap is separate: an
  incremental sync reads `git log --since=<latest committer date>`, so commits
  from a merged branch with older committer dates are missed. A fast-forward
  only update makes the eventual fix simpler, because the commits to read are
  then exactly `<previous HEAD>..HEAD`.
- **Recovering a clone that can't be fast-forwarded.** kospex reports it and
  leaves it as it was. Deciding what to do with it (re-clone, or keep the old
  history as a separate lineage) is part of the repo lineage design in
  `changes/202611-repo-lineage.md`.
