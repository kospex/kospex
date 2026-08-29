# Separate merge commits from authorship in key-person analysis

Closes #170.

## Overview

`commit_stats()` counted every row with `COUNT(*)`, so a merge added one to
whoever merged. On a 109-repo estate 12.3% of commits are merges, and the share
reaches 96.3% for some authors — meaning key-person analysis was partly
measuring who pressed the merge button.

Merges are now counted **separately** rather than discarded, because merging is
evidence of knowledge in its own right.

## Why not just exclude them

A merge is not authorship: it combines work already credited to the branch
commits, so counting it double-counts. But the merger read and accepted those
changes into the subsystem, which is a real signal about who knows the code.

Reporting both makes three patterns legible that a single `commits` number
cannot express:

| pattern | reading |
|---|---|
| high commits + high merges | deep owner — writes it and integrates it |
| high commits + zero merges | prolific contributor without integration authority |
| **low commits + high merges** | **gatekeeper — knows the code, writes little** |

The third is the one worth having, and it is invisible either way round
otherwise: today those merges inflate the commit count and look like authorship,
and under a naive exclusion the person simply drops down the table and vanishes.

Real example, `pallets/click`:

```
author                        commits  merges
armin.ronacher@active-4.com       465     128
davidism@gmail.com                326     550     <- more merges than commits
kevin@deldycke.com                133       4
markus@unterwaditzer.net           64      87
```

`davidism@gmail.com` previously showed 876 commits and ranked first, which read
as pure authorship. The split shows a maintainer who is primarily the
integrator — and `kevin@deldycke.com`, with a third of the commits and almost no
merges, is a different kind of contributor entirely.

## The rule

```sql
(parents <= 1 OR _files > 0)
```

Defined once as `AUTHORSHIP_COMMIT` in `kospex_query.py`.

A commit is authorship unless it is a **clean** merge. A merge carrying content
of its own — a conflict resolution, a file added while merging, captured since
#164 — exists in no parent and is real work by the merger, so it counts.

| commit kind | `parents` | `_files` | counted |
|---|---|---|---|
| ordinary | 1 | > 0 | authorship |
| clean merge | > 1 | 0 | merge |
| merge with own content | > 1 | > 0 | authorship |
| octopus (clean) | > 1 | 0 | merge |
| root commit | 0 | > 0 | authorship |

### It needs no follow-up after a re-sync

`parents` is NULL for commits synced before #164. `NULL <= 1` is NULL, which
falls to the `ELSE` of a `CASE`, so the `_files` clause carries those rows — the
`_files = 0` heuristic validated at 100% recall against 1,444,492 commits
carrying parent data.

Once `parents` is populated the first clause takes over. The only behaviour that
changes is that `git commit --allow-empty` commits (`parents = 1`, `_files = 0`)
stop being mistaken for merges — the 51 false positives in 1.34M from that
validation. A re-sync silently improves the numbers; no code change is needed to
match, and no ordering is required between this and any backfill.

Because it is evaluated per row, a mixed estate — some repos re-synced, some not
— gives each row its best available answer.

## The `merges` column is workflow-dependent

**Comparable within a repo, never across repos.** Measured on this estate:

- **18 of 88** repos with more than 200 commits have a merge share below 1%
- the highest are 30–35% (`pallets/click` 35.4%, `pallets/flask` 31.2%)

A project using squash-and-merge or rebase produces no merge commits at all, so
**zero merges does not mean "does not integrate"** — it means the project
squashes. Roughly a fifth of this estate is in that state.

Consequences, carried in the footnote and worth respecting in any future work:

- do not rank or score on merge count across repos
- an org- or estate-level roll-up of merges is meaningless without normalising
  by workflow
- bots merge too; whatever bot filtering key-person analysis grows must cover
  this column, or the top "gatekeeper" in some repos will be a robot

## Surfaces

All three read `key_person()` → `commit_stats()`, so the query change reaches
every one:

- CLI `kospex key-person` — `src/kospex_cli.py`
- Web `/key_person/{repo_id}` — `src/kweb2.py`, `src/templates/key_person.html`
- `krunner` batch key-person — `src/krunner.py`

A `merges` column sits beside `commits` in the CLI table and the web table, so a
maintainer's lower commit count is explained in place rather than taken on
trust. Both carry a footnote stating that merges are counted separately and that
the count is repo-relative.

## Expect the percentages to move in both directions

`% commits` is computed on the authorship total, so the denominator shrinks.
Someone who never merges can see their percentage **rise** while their own count
is unchanged. That is arithmetic, not a bug, and is the support question this
will generate.

## Files changed

- `src/kospex_query.py` — `AUTHORSHIP_COMMIT`, `commit_stats()`
- `src/kospex_utils.py` — `merges` column in `key_person_prettytable()`
- `src/kospex_cli.py` — footnote
- `src/templates/key_person.html` — column + footnote
- `tests/test_merge_authorship.py` — 11 tests
