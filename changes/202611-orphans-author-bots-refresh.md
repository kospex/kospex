# kospex orphans: no one who authored the code is still here

## Overview

`kospex orphans` was labelled "Experimental - Work in Progress". It is no
longer, and it now measures one thing:

> A repo is **orphaned** when no one who authored its code is still here.

- **Authors**: everyone who authored a commit to the repo, all time, by author
  email. Merge commits and bots are excluded. `-window DAYS` limits authors to
  the last DAYS.
- **Still here (Present)**: committed in the last 365 days (`-here-days`),
  merges included, anywhere in the `-active-in` scope (default: every synced
  repo). Orphaned is decided on this.
- **Active**: committed in the last 90 days (`-days`). Shown beside Present,
  not used for the status.
- **Statuses**: `Orphaned` (no author here), `Knowledge` (at least one),
  `No authors` (no human authored a commit), `Not synced` (a `-target-list`
  repo not in the DB).

kweb's `/orphans/` page uses the same code (`kospex.orphans.report()`), so the
page and the CLI agree.

## What was wrong

Measured against a 403-repo dev DB.

1. **Committer vs author.** "Still active" was built from `author_email`, but
   each repo's people came from `committer_email`. A merge in GitHub's web UI
   is committed by `noreply@github.com` (in 227 of the 403 repos in the last
   year), which is never an author, so it always counted as someone who had
   left. Gerrit, Android automerger and other build identities did the same.
   The window also filtered on `committer_when`, so a rebase or cherry-pick of
   an old commit pulled its author into the window.
2. **Only the last 365 days counted.** Someone who wrote most of a repo two
   years ago and is still here didn't count, and a repo nobody touched in a
   year was reported Orphaned (CLI) whoever wrote it. The first fix made these
   `Dormant`, a repo with no verdict; counting all-time authors gives every
   repo a verdict instead, with a `Last commit` column to show it is quiet.
3. **Bots counted as people.** A repo whose only recent committer was renovate
   or dependabot got "Working knowledge exists". Bots now have a `Bots active`
   column: bots that committed to *this* repo in the last `-days`. An Orphaned
   repo with bots active is one whose dependencies are still being updated
   with no one left who wrote it.
4. **Merges counted as authorship.** A merge's author merged the code rather
   than wrote it. Excluded with `AUTHORSHIP_COMMIT`, as key-person and
   bus-factor do (#170). Merges still count as being here.
5. **The page had its own copy of the loop**, with committer dates, bots as
   people and no-commit repos as orphaned.
6. **The suggested sync command failed.** `kgit pull --stale 30` is refused:
   kgit pull needs exactly one scope. `orphans.sync_command()` names it
   (`--org`, `--server`, a repo_id, or `--all`).

The table is rich, the author table that `active_developers()` printed first
is gone, and each repo's status is one line:

```
repo_id: github.com~acme~api, Orphaned, Present: 0, Total: 3 authors.
```

| 403-repo dev DB, still here = estate | Orphaned | Knowledge | Other |
|---|---|---|---|
| Original command | 139 | 136 | 128 (no one in window, shown as Orphaned) |
| 365-day window, author email | 70 | 201 | 132 Dormant |
| **All-time authors, merges and bots out** | **136** | **267** | 0 |

With `-active-in server`: 145 / 258. With `-active-in org`: 210 / 193 (the DB
has 235 orgs, most holding one or two repos).

## Two windows: Present (365) and Active (90)

90 days was the first choice, as seat licensing counts. Research that tested
the threshold says it measures "recently active", not "has left": Avelino et
al. (ESEM 2019) compared 3, 6, 12, 18 and 24 months against core developers'
real commit gaps and found a 3-month rule wrongly marks 62% as gone at least
once (they chose 1 year); Calefato et al. (EMSE 2022) rejected 3 and 6 months
for 12. Both are open-source studies, where people pause more than in a
company. So both are shown: Present decides Orphaned, Active says whose
knowledge is fresh. `-here-days 90` gives the licensing rule.

On the 403-repo dev DB: 57 Orphaned at 365 days, against 136 at 90.

## Speed: migration 0015

Leaving merges out needs `parents` and `_files`, which no index held, so the
authors query read every commit row: 10.9s on 1.4M commits, and the page timed
out its web tests. `0015_commits_merge_index` is a partial index over merge
commits only (`WHERE NOT (parents <= 1 OR _files > 0)`, 4,025 rows). The
authors now come from two grouped queries: every commit from
`idx_commits_repo_author_when` (0011), and merges from the new index (0.02s);
an author whose every commit is a merge is dropped. On a copy of the dev DB
the index built in 3.5s.

The query's WHERE must match the index's exactly or SQLite won't use it;
`test_merge_query_uses_the_partial_index` checks the plan. Without the
migration applied, the results are the same, just slow.

One semantic difference from `AUTHORSHIP_COMMIT`: a commit with NULL
`parents` (250,000 of them in 109 repos of the dev DB, from older syncs)
counts as authorship, because a merge can't be told apart without parents.
`AUTHORSHIP_COMMIT` drops such a commit when it has no files, which also
affects key-person and bus-factor; that is a separate data issue.

## Where "still here" is looked for: `-active-in`

The default is the whole estate, which is right for a company that syncs its
own servers: a person who left a project for another one is still here, and
one who left the organisation stops committing everywhere. "Here" is whatever
is synced; for open source, sync an org such as `github.com~apache` and a
developer who left one project but commits to others still counts.

A database holding several organisations can narrow it per repo:

- `estate`: any synced repo (default).
- `server`: repos on the repo's own git server.
- `org`: repos in the repo's own kospex org,
  `KospexGit.generate_org_key(server, owner)`, so GitLab, Bitbucket and
  self-hosted servers work the same. A GitLab nested group's owner is its
  whole path, so sibling subgroups are different orgs; rolling a subgroup up
  to its parent group is not done.

The page has a menu for the same three (`?active_in=`; anything else is 400).

## Last sync, not a "Stale sync" status

A repo that has not been synced can look quieter than it is. A `Stale sync`
status was considered, but 356 of the 403 repos were last synced more than 30
days ago, so it would have replaced nearly every status. Instead the status
stays people-based and `Last sync` shows the age, in red past `-stale-days`
(default 30), with a summary line and the scoped `kgit pull` command. The age
is `kospex.staleness` (`last_fetch`, else `last_sync`), the same rule as
`kgit pull --check` and kweb `/syncs/`. The CSV keeps `last_refresh` /
`refresh_age_days`, because a `last_sync` column would be mistaken for the
repos table's.

The limit it can't show: still here is measured across the estate, so a
person whose recent work is only in an unsynced repo looks gone, and the repos
they wrote can be wrongly Orphaned. The summary says how many repos in scope
are stale.

## Decisions

- **Authors are all time by default.** "No one who authored this code is here"
  is about the code, not recent activity. `-window` is there for the recent
  question.
- **Merges are not authorship but are presence.**
- **Each author counts once**, so `% Here` is a head count: a 50% author and a
  1% author weigh the same. A knowledge-weighted measure is a separate design
  (see Not changed).
- **Bots are detected with the same check as `krunner key-person` /
  `bus-factor`**, moved to `authorship.bot_checker()` so the three agree.
- **A year without a commit means gone** (Present); 90 days is shown as
  Active. Someone who moves into management, or is on very long leave, still
  looks gone.
- **No identity resolution.** One person with two emails, or a new email after
  a move from one git server to another, can look gone. The same is true across
  kospex until `email_map` is consumed.

## Files changed

- `src/kospex/orphans.py`: the rule (`orphan_row()`, pure), `report()` used by
  the CLI and kweb, `scope_key()`, `counts()`, `summary()`, `sync_command()`,
  `FIELDS`.
- `src/kospex/authorship.py`: `bot_checker()`.
- `src/kospex_query.py`: `author_last_active()` (one query for both presence
  windows, grouped by scope when asked), `repo_author_emails()` (authors, all
  time or a window, merges dropped via the 0015 index; chunked for SQLite's
  999-variable limit), `MERGE_COMMIT`, `repos_table()`; `get_orphans()` uses
  `report()`.
- `src/kospex/db/migrations/0015_commits_merge_index.sql`: the partial index.
- `tests/test_db_health.py`, `tests/test_kospex_schema.py`,
  `tests/test_kospex_utils.py`: migration counts.
- `src/kweb2.py`, `src/templates/orphans.html`: the page, its scope menu and
  "explain column headings and table" toggle.
- `src/kospex_cli.py`: `orphans` rewritten; adds `-stale-days` and
  `-active-in`; `-window` defaults to all time; `-target-list` takes repo_ids,
  skips blanks and `#` comments, and reports unparseable lines on stderr; CSV
  is spreadsheet-safe.
- `src/krunner.py`: `_is_bot()` uses `bot_checker()`.
- `src/kospex_utils.py`: unused `orphan_prettytable()` removed.
- `tests/test_orphans.py`, `docs/kospex.md`, `docs/index.md`.

## CSV columns

`repo_id, status, authors, present, pct_here, active, bots, bots_active,
last_commit, last_refresh, refresh_age_days, refresh_stale, window_days,
here_days, active_days, active_in`. `window_days` is blank for all time. Replaces
`_repo_id, committers, active, Orphaned, % Here`.

## Not changed

- **Knowledge weighting.** Weighting authors by their share of the code, and
  how many of the bus-factor authors are still here, was discussed. It is
  methodology rather than measurement, so it is drafted separately and not
  part of this change.
- **A roster of people known to be here or gone**, overriding the 90-day rule
  (for managers, people on leave, or leavers the 90 days hasn't caught yet).
  A possible future option.
