# Scanning a non-default branch (branch-aware sync)

By default kospex is not branch-aware. `kospex sync` records the files and
dependencies of whatever is **currently checked out on disk**, and it does not
track every branch or every commit. If a client (or an environment) runs off a
`development` branch rather than `main`, you can still get an accurate open
source inventory with a small manual workflow.

This is a documented workaround until full branch awareness and all-commit
tracking land.

## The short version

For each repo, check out the branch you care about, then re-sync, then run the
inventory:

```
# per repo
git checkout development
kospex sync .

# once every repo is on development and synced
krunner osi -all
```

After this, both halves of the inventory agree: the list of files scanned and
the dependency versions reported are both taken from the `development` branch.

## Why the re-sync is required

`krunner osi` gets its results from two places:

- **Which dependency files to scan** comes from the kospex database
  (`file_metadata`), i.e. from your last `kospex sync`.
- **The version numbers** are parsed live from the files on disk (the checked
  out working tree).

If you only switch branches without re-syncing, you get `development` versions
for the files that already existed on `main`, but you **miss** any dependency
files that only exist on `development`, and you may hit errors on files that
`development` removed. Re-syncing rebuilds the file list from the branch you
have checked out, so the two halves line up.

Good to know:

- `kospex sync` discovers files by walking the checked out working tree, so it
  always reflects the current branch.
- The rebuild is keyed on `HEAD`. Switching branches moves `HEAD`, so the
  re-sync is guaranteed to rebuild `file_metadata` rather than skip it as
  "up to date".
- New dependency files that only exist on `development` are picked up by the
  re-sync, so they are included in the inventory.

## Caveats

- **Your database now reports off `development`.** Until you switch the repos
  back and re-sync, kospex reports (and the web UI) reflect the `development`
  branch, not `main`.
- **Do it consistently across the fleet.** Mixed state (some repos on `main`,
  some on `development`) produces a mixed inventory. Check out and sync every
  repo in scope before running `krunner osi -all`.
- **Commit-date metadata may lag.** Per-file "last commit" dates come from the
  commit history already synced to the database. If `development` has commits
  you have not ingested, those dates can be stale. This does not affect which
  files are scanned or the dependency versions reported.

## Related

- [krunner](krunner) — running commands across all repos in a directory
- [kospex](kospex) — the `sync` command
