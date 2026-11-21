# kospex orphans: author identity, bots and refresh age

## Overview

`kospex orphans` was labelled "Experimental - Work in Progress", and measured
against a 403-repo dev DB it was wrong for about half of the repos it called
Orphaned. Three problems, all fixed here, and the label removed:

1. **Committer vs author.** "Still active" was built from `author_email`, but
   each repo's people came from `committer_email`. A merge in GitHub's web UI
   is committed by `noreply@github.com` (in 227 of the 403 repos in the last
   year), which is never an author, so it always counted as someone who had
   left. Gerrit, Android automerger and other build identities did the same.
   The window also filtered on `committer_when`, so a rebase or cherry-pick of
   an old commit pulled its author into the window. Both now use author email
   and author date.
2. **No commits read as Orphaned.** A repo with nobody in the window ("Total:
   0") was reported Orphaned, and so was a `-target-list` repo that was never
   synced. These are now `Dormant` and `Not synced`.
3. **Bots counted as people.** A repo whose only recent committer was
   renovate or dependabot got "Working knowledge exists". Bots are no longer
   people; they get their own `Bots active` column (bots that committed to
   *this* repo in the last `-days`). The status is people only.

The table is rich, the author table that `active_developers()` printed first
is gone, and each repo's status is one line.

| On the 403-repo dev DB | Orphaned | Knowledge | No one in window |
|---|---|---|---|
| Before | 139 | 136 | 128 (shown as Orphaned) |
| After | 69 | 202 | 132 Dormant |

## Refresh age, not a "Stale sync" status

A repo that has not been refreshed can look quieter than it is. The first idea
was a `Stale sync` status, but 356 of the 403 repos were last refreshed more
than 30 days ago, so it would have replaced nearly every status. Instead the
status stays people-based and a `Refreshed` column shows the age, in red past
`-stale-days` (default 30), with a summary line and the `kgit pull --stale`
command. The age is `kospex.staleness`: `last_fetch`, else `last_sync`, the
same rule as `kgit pull --check` and kweb `/syncs/`.

The limit it can't show: "still active" is estate-wide, so a person whose
recent work is in an unrefreshed repo looks gone, and the repos they used to
work on can be wrongly Orphaned. The summary line covers this by saying how
many repos in scope are stale.

## Where "still working for us" is looked for: `-active-in`

The default is the whole estate, which is right for a company that syncs its
own servers. A database holding several organisations (open source, or more
than one customer business unit) can narrow it per repo:

- `estate`: any synced repo (default).
- `server`: repos on the repo's own git server.
- `org`: repos in the repo's own kospex org, `KospexGit.generate_org_key(server,
  owner)`, not a GitHub concept, so GitLab, Bitbucket and self-hosted servers
  work the same. A GitLab nested group's owner is its whole path, so sibling
  subgroups are different orgs; rolling a subgroup up to its parent group is
  not done.

On the 403-repo dev DB, `org` moves 42 repos from Knowledge to Orphaned (111
vs 69): its 235 orgs mostly hold one or two repos. `server` matches `estate`,
because 392 of the repos are on github.com.

## kweb /orphans/

`KospexQuery.get_orphans()` had its own copy of the loop: author email (good)
but committer date for both windows, bots as people, and no-commit repos as
orphaned. It now returns `kospex.orphans.report()` rows, so the page and the
CLI agree (`test_get_orphans_matches_the_cli_rows`). The page shows Status,
People, Present, % Here, Bots active and Refreshed (red past 30 days), and a
menu for `?active_in=estate|server|org`; anything else is a 400. Scope ids
(`/orphans/ORG_KEY`, `/orphans/SERVER`, `/orphans/REPO_ID`) select repos from
the repos table, so a synced repo with no commits now appears (as Dormant).

## Decisions

- **Orphaned means "no longer working for us"**, so active is estate-wide,
  whatever `-server` or `-target-list` scopes the repos to. A person who left
  a project for another one is still here; one who left the organisation stops
  committing everywhere. "Us" is whatever is synced: a company's servers, or
  for open source an org such as `github.com~apache`, where a developer who
  left one project but commits to others still counts.
- **Merges count** as activity and as having worked on a repo. Unlike
  key-person shares (#170), this is presence, not a measure of authorship,
  and the person merging a PR has looked at it.
- **Bots are detected with the same check as `krunner key-person` /
  `bus-factor`**, moved to `authorship.bot_checker()` so the three agree.
- **No identity resolution.** One person with two emails can look gone. The
  same is true across kospex until `email_map` is consumed.

## Files changed

- `src/kospex/orphans.py`: the rule (`orphan_row()`, pure), `report()` used by
  the CLI and kweb, `scope_key()`, `counts()`, `summary()`, `FIELDS`.
- `src/kospex/authorship.py`: `bot_checker()`.
- `src/kospex_query.py`: `active_author_emails()`,
  `active_author_emails_by_scope()`, `repo_author_emails()` (chunked for
  SQLite's 999-variable limit), `repos_table()`; `get_orphans()` uses
  `report()`.
- `src/kweb2.py`, `src/templates/orphans.html`: the page.
- `src/kospex_cli.py`: `orphans` rewritten; adds `-stale-days`; `-target-list`
  takes repo_ids, skips blanks and `#` comments, and reports unparseable lines
  on stderr; CSV is spreadsheet-safe.
- `src/krunner.py`: `_is_bot()` uses `bot_checker()`.
- `src/kospex_utils.py`: unused `orphan_prettytable()` removed.
- `tests/test_orphans.py`, `docs/kospex.md`, `docs/index.md`.

## CSV columns

`repo_id, status, people, people_active, pct_here, bots, bots_active,
last_refresh, refresh_age_days, refresh_stale, window_days, active_days,
active_in`.
Replaces `_repo_id, committers, active, Orphaned, % Here`.

## Open question: Dormant repos

A Dormant repo has no person in the window, so nobody to check. Its last
people could still be here (it is finished, and someone could pick it up) or
all gone (finished and orphaned). Looking at the people in the year before its
last commit would tell those apart; not done yet.
