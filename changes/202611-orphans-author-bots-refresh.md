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

## Decisions

- **Active is estate-wide**, whatever `-server` or `-target-list` scopes the
  repos to. The question is whether the person is still around, not whether
  they still work on this server.
- **Merges count** as activity and as having worked on a repo. Unlike
  key-person shares (#170), this is presence, not a measure of authorship,
  and the person merging a PR has looked at it.
- **Bots are detected with the same check as `krunner key-person` /
  `bus-factor`**, moved to `authorship.bot_checker()` so the three agree.
- **No identity resolution.** One person with two emails can look gone. The
  same is true across kospex until `email_map` is consumed.

## Files changed

- `src/kospex/authorship.py`: `orphan_row()` (the rule, pure), status
  constants, `ORPHAN_FIELDS`, `bot_checker()`.
- `src/kospex_query.py`: `active_author_emails()`, `repo_author_emails()`
  (chunked for SQLite's 999-variable limit), `repos_table()`.
- `src/kospex_cli.py`: `orphans` rewritten; adds `-stale-days`; `-target-list`
  takes repo_ids, skips blanks and `#` comments, and reports unparseable lines
  on stderr; CSV is spreadsheet-safe.
- `src/krunner.py`: `_is_bot()` uses `bot_checker()`.
- `src/kospex_utils.py`: unused `orphan_prettytable()` removed.
- `tests/test_orphans.py`, `docs/kospex.md`, `docs/index.md`.

## CSV columns

`repo_id, status, people, people_active, pct_here, bots, bots_active,
last_refresh, refresh_age_days, refresh_stale, window_days, active_days`.
Replaces `_repo_id, committers, active, Orphaned, % Here`.

## Not changed

kweb's `/orphans/` page (`KospexQuery.get_orphans()`) has its own copy of the
loop. It already uses author email, but still filters the window on committer
date, counts bots and shows no-commit repos as orphaned. Moving it onto
`orphan_row()` is a follow-up.
