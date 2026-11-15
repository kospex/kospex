# kweb `/syncs/`, `kgit pull --stale DAYS`, and a record of each `kgit sync` command

Design doc, not yet built. Builds on `kgit sync --org -active` and the listing
history ([202611-kgit-sync-active.md](202611-kgit-sync-active.md)), on the same
branch, because it adds to the unshipped migration `0013`.

## Why

When you sit down at a site, or come back to your own 20–50 repositories after a
week, the first questions are: which repos in the kospex DB are out of date, and
what do I run to bring them up to date? And for org syncs: what has been run,
with which options and which token, and how far did it get?

Today the answers are spread across `kgit pull --check`, `kgit sync -status` and
`-history`, one org at a time, and the options and token a sync used aren't
recorded anywhere.

kweb is single-player: one consultant or developer, on their own machine, with
no authentication or roles. `/syncs/` is built for that person. It shows; it
never runs anything. You copy a command and run it yourself.

It will be tried at a few sites before it's linked from the menus or announced.

## Goals

1. A `/syncs/` page with two tabs, **Repos** and **Org syncs**, filterable by
   server / org.
2. The Repos tab lists repos not fetched within a chosen window (1 day, 1 week,
   30 days, or all), with a copy-ready command that refreshes **exactly** the
   repos the table shows.
3. `kgit pull --stale DAYS`, so that command exists.
4. Each `kgit sync --org/--user` command that does work is recorded: when,
   the options as typed, what it did, and which token (login, kind, source
   variable, expiry).
5. The Org syncs tab shows each run, expandable to its commands and its listing
   history, with the command to continue an unfinished run.

## Non-goals

- Running, scheduling or cancelling anything from the page.
- Users, roles, authentication. Multi-user use is out of scope for kospex.
- A token label (`KOSPEX_GITHUB_TOKEN_LABEL` was considered and dropped: GitHub
  doesn't expose a token's name, and a label is one more thing to keep in step).
- Recording `kgit pull` invocations. Its effect is already on each repo
  (`last_fetch`, `last_sync`).
- Org syncs for GitLab or Bitbucket; `kgit sync --org` is GitHub-only today.
- Linking `/syncs/` from the nav, the help pages, the docs site or its sitemap.

## Anti-goals (would make this a failure even if it works)

- **The command and the table disagree.** If the page says "194 stale" and its
  command pulls 403, the page is lying. The page and `kgit pull --stale` must use
  one shared staleness rule (below), not two implementations.
- **Storing token material.** No token, no hash of a token, no fragment of one.
- **A copied command that does something other than what it shows.** Every value
  interpolated into a command (org keys, owners, repo ids from the DB) is
  shell-quoted with `shlex.quote`. Repo and group names on hosts other than
  GitHub are not restricted the way GitHub's are.
- **The page writes to the DB**, or crashes on a DB that is behind. kweb never
  migrates an existing DB; a DB without `0013` must render the page with a note
  to run `kospex upgrade-db`, not a 500.

## Staleness rule (shared)

A repo's **last refresh** is `last_fetch`, or `last_sync` when `last_fetch` is
empty. `last_fetch` only exists since migration `0004` and is only written by
`kgit pull` / `clone` / `sync`, so on the dev DB 258 of 403 repos have none and
fall back to `last_sync`. A repo with neither is always stale.

A repo is **stale for DAYS** when its last refresh is more than DAYS × 24 hours
before now. Timestamps in `repos` carry mixed offsets (`+11:00`, `Z`), so compare
parsed datetimes, not strings.

One function implements this, used by both `kgit pull --stale` and the page.
`kgit.py` has `_age_days()` / `_staleness_rows()` for `--check`; extend or move
those rather than writing a second version. Where it lives is the builder's
call, but neither `kweb2.py` nor the template may compute it on its own.
`KospexUtils.days_between_datetimes` rounds and takes `abs()`, so it can't be
the comparison.

**This changes plain `kgit pull --check` too**: `fetch_age` becomes the
age of the last refresh, and rows sort by it, stalest first. The `last_fetch`
column still says "never" when empty, so you can see which repos fell back. Today
never-fetched repos sort first regardless of when they were synced, which for
258 of 403 dev-DB repos buries the ones that are actually stale.

## `kgit pull --stale DAYS`

- Narrows any scope (`--all`, `--org`, `--server`, one `REPO_ID`) to the repos
  stale for DAYS. DAYS is a whole number, 1 or more; anything else is a usage
  error.
- With `--check`, the offline report shows only those repos.
- When nothing is stale, say so ("No repos in scope were last refreshed more
  than 30 days ago.") and exit 0.
- Before pulling, print how many of how many it will pull ("Pulling 14 of 403
  repos not refreshed in 30 days.").
- Documented in `docs/kgit.md`, `CLAUDE.md` (the `kgit pull` lines) and
  CHANGELOG.

## Recording each `kgit sync` command

New table, created in `0013` (unshipped, so extended rather than adding `0014`;
its header comment gains a paragraph):

`org_sync_commands`

| column | meaning |
|---|---|
| `run_id` | the run it worked on (NULL if it failed before one existed) |
| `org_key` | e.g. `github.com~mergestat` |
| `started_at`, `finished_at` | UTC; `finished_at` NULL = still running, or stopped (Ctrl-C, crash) |
| `listing` | the listing this command saved: `first`, `refresh` (including the one `-active` makes on its own when the listing is older than `KOSPEX_ORG_SYNC_REFRESH_DAYS`), `restart`, or NULL when it saved none. Matches `org_sync_listings.kind`; `restart` is recorded there as `first`. |
| `synced_any` | 1 if the command cloned/synced items (not `-inventory`), else 0 |
| `options` | the options as typed, in the order kgit prints suggestions, e.g. `-active 90 -next 10 -no-auth` |
| `error` | why the command stopped early after starting (e.g. an incomplete listing); NULL otherwise |
| `command` | the full copyable command, e.g. `kgit sync --org mergestat -active 90 -no-auth` |
| `synced`, `failed`, `skipped` | items this command moved to each status |
| `new`, `gone` | from the listing this command saved, if it saved one |
| `auth_login` | GitHub login the token belongs to; NULL with `-no-auth` |
| `auth_kind` | `fine-grained` (`github_pat_`), `classic` (`ghp_`), `app` (`ghs_`), `oauth` (`gho_`), `none` (`-no-auth`), `unknown` |
| `auth_source` | the env var the token came from (`KospexGithub.token_source`) |
| `auth_expires_at` | from the `GitHub-Authentication-Token-Expiration` response header; NULL when the token has no expiry or the header is absent |

- Recorded for every `kgit sync --org/--user` that changes a run: listing,
  syncing or both, including when reached through `kgit github OWNER -sync`
  (recorded as the equivalent `kgit sync` command). **Not** for `-status` and
  `-history`, which only read.
- **When the row is inserted**: once the command is past every check
  that makes it do nothing — usage errors, the token check, `_check_owner_type`
  (no such owner, or `--org` for a user), and the run lock ("Nothing to do").
  Those change nothing, so they leave no row. From then on the row exists before
  any listing or syncing, so a stopped command still shows (`finished_at`
  NULL), and a failure after that point (e.g. an incomplete listing) is
  recorded in `error`. `run_id` is NULL only when the first listing of a new run
  failed.
- The auth fields come from the `GET /user` that `_sync_owner` already makes
  via `test_auth()`; return the login and the response's headers from it rather
  than making a second call. **Existing behaviour stays**: a failed
  token check still stops the command, as today. The expiry is read from the
  `GitHub-Authentication-Token-Expiration` header of that response. **Verify the
  header name against a real fine-grained token while building**; if it isn't
  there, record NULL and say so in the doc.
- The token kind is read from the token's prefix, locally.
- Runs from before this change have no command rows; the page says "not
  recorded".
- `docs/kgit.md` gains a short section on what's recorded (and that the token
  itself never is), with an SQL example. CHANGELOG gets a line.

## The page: `/syncs/`

A FastAPI route in `kweb2.py`, a new template, read-only queries through
`KospexQuery` (or a small query module it calls). Same look as other kweb pages:
Jinja, Tailwind classes, `_header.html`, DataTables via `_datatable_scripts.html`
(`repos.html` is a close model). Jinja autoescaping stays on; no `|safe` on DB
values.

**Query parameters** (so a view can be bookmarked):

- `tab` = `repos` (default) | `orgs`
- `org` = an org key (`github.com~jshttp`, `gitlab.example.com~acme~~platform`),
  validated with `KospexUtils.parse_org_key`.
- `server` = a git server (`github.com`, `go.googlesource.com`): every repo on
  it, owned or not. Matches `kgit pull --server` exactly.
- At most one of `org` / `server`; both, or an invalid value, is a 400. A valid
  value with no repos renders an empty table.
- `days` = positive integer, default 30; `all` shows every repo. Invalid is a 400.
  The buttons offer 1, 7, 30 and all.

**Server / org picker** (both tabs): grouped by server, type-to-filter, each
entry showing repo count and stale count, e.g. `github.com / jshttp — 14 repos ·
14 stale`. Each group starts with a "whole server" entry (`all of github.com`),
which sets `server=`; the org entries set `org=`. Owner-less repos (e.g.
`go.googlesource.com`) have no org entry: they're reached through their
server's entry, because no `kgit pull` scope selects "only the owner-less repos
on a server", and the command has to match the table. Nested GitLab groups
display with `/` (`gitlab.example.com / acme/platform`) and use the `~~` key in
the URL and commands. There is no GitLab data in the dev DB, so this is untested
until a GitLab site.

### Repos tab

- A summary line: "**194 of 403** repos not refreshed in 30 days", with a note
  when any rows fall back to `last_sync` ("258 have never been fetched by kgit;
  their last sync is used").
- A command box, matching the table exactly:
  - all: `kgit pull --all --stale 30`
  - an org: `kgit pull --org github.com~jshttp --stale 30`
  - a server: `kgit pull --server go.googlesource.com --stale 30`
  - `days=all`: the same without `--stale`
  - plus the offline version (`--check`), and a copy button on each.
- Table: repo id (linked to `/repo/{repo_id}`), last fetch ("never" when empty),
  last sync, last commit, age in days. Sorted stalest first; DataTables search
  and sort.

### Org syncs tab

- One row per run, latest first: org, state (finished / in progress), listed at,
  repos, synced, pending, skipped, failed, gone, archived, forks, finished at.
- Expanding a run shows:
  - its recorded commands: when, the command, synced, failed, how long, and the
    token (`login · fine-grained · GH_TOKEN · expires 20 May 2027`, or `no auth`);
  - its listings from `org_sync_listings` (new, gone, back, renamed, newly
    archived, unarchived), the same as `-history`.
- An unfinished run shows a copy-ready **continue** command: the last recorded
  command for the run with `-inventory`, `-refresh`, `-restart` and
  `-retry-failed` dropped, so it carries the same `-active`, `-next`,
  `-no-auth`, `-ssh`, `-no-prompt`, `-skip-archived` and fork flags. With no
  recorded command: `kgit sync --org OWNER`.

### One command builder, shared with kgit

kgit's own suggestions ("Continue with", "Resume with", and the
`-inventory -refresh` / `-restart` / `-next 10` hints) carry **every option the
user typed that still applies**, not just `-active`, `-no-auth` and `-next` as
today. One function builds the option string, in a fixed order, and is used for:
kgit's printed suggestions, the `options` and `command` columns, and the page's
continue command. So a copied suggestion repeats the command that was run, and
kgit and the page always print the same text. Values are `shlex.quote`d. The
existing suggestion tests change to match; `--help` does not.
- A token that has expired, or expires within 14 days, is highlighted on its
  command row.

## Removing `/recent/`

`/recent/` was added as a stub on 2025-08-14 (`d2c3af1`) and never built: the
handler has a `TODO` and always renders an empty table. It isn't in the nav.
Removed in this PR:

- the route in `kweb2.py` and `src/templates/recent-syncs.html`;
- its entries in `tests/test_web_endpoints.py` and `tests/manual/web_crawl.py`;
- in `docs/kweb/index.md`, its row in the page table, and the troubleshooting
  line that says `/recent/` shows what synced (reword it without naming
  `/syncs/`, which stays unannounced);
- a CHANGELOG line under Removed.

The URL then returns kweb's 404.

## Testing and checks

- Migration: `test_fresh_db_has_no_pending_migrations`, the `0013` backfill test
  and the migration-count tests stay green; the new table is in the `0013`
  schema test. SQL runs on SQLite 3.26 (the conftest guard enforces functions).
- Staleness rule: unit tests including mixed offsets, `last_fetch` empty, both
  empty, and exactly on the boundary.
- `kgit pull --stale`: scope × stale combinations, `--check` filtering, the
  nothing-stale message, usage errors; drive the CLI, as the existing pull tests do.
- Command recording: a row per working command with the right action, options,
  counts; none for `-status` / `-history`; a stopped command leaves
  `finished_at` NULL; `-no-auth` records `none`; token kind by prefix; expiry
  parsed from the header; a rejected token still stops the command, and leaves
  no row. Use the fake
  GitHub of `tests/test_kgit_sync_org_cli.py`.
- Page: call the handler directly (as `tests/test_generate_repo_id_endpoint.py`
  does), asserting the counts, that the command matches the rows, the 400s, the
  behind-DB note, and that a repo id containing shell metacharacters is quoted
  in the command and escaped in the HTML. Add `/syncs/` to
  `test_web_endpoints.py` and run kweb before committing.
- Live check against the dev DB (public repos only, `-no-auth` for orgs).

## Decisions made (2026-11-15)

- Layout: tabs, Repos first. Repos tab with a server / org picker.
- Stale = not fetched in N days, chosen on the page (1 day, 1 week, 30 days).
- Record options **per command**, not per run.
- Record token login, kind, source variable and expiry; no label.
- Add `kgit pull --stale DAYS`.
- kgit's own continue / resume suggestions carry every option, matching the page.
- Single user, read-only, not linked until tested at a few sites.
- `last_sync` stands in for a missing `last_fetch`.
- GitLab nested groups shown with `/`, keyed with `~~`. The picker offers whole
  servers (`--server`) and orgs (`--org`); owner-less repos only via their server.
- Plain `kgit pull --check` ages and sorts by the shared rule.
- A failed token check still stops `kgit sync`, as today; checks that make a
  command do nothing leave no row.
- `listing` + `synced_any` instead of a single `action`, since options combine
  (`-inventory -refresh`, `-restart -next 10`, `-active`'s own re-listing).
- Command table goes in `0013`, not `0014`.
- Filters live in the URL; bad values are 400s.
- `/recent/`, a never-built stub, is removed.
- `--stale` and command recording get CHANGELOG and `docs/kgit.md` entries;
  `/syncs/` gets only this doc until it's announced.
- Expiry warning threshold 14 days.

## Left to the builder

Module layout for the shared staleness rule and the page's queries; template
structure and JavaScript for tabs, picker and copy buttons; time display format;
exact wording of messages within the tone of existing kgit output.

## Implementation

Built on the `worktree-kgit-sync-active` branch, following
[202611-kweb-syncs-plan.md](202611-kweb-syncs-plan.md).

| File | What |
|---|---|
| `src/kospex/staleness.py` | the staleness rule; `kgit._staleness_rows` delegates to it |
| `src/kospex/org_sync_command.py` | `options()`, `command()`, `suggestion()`, `continuing()`, `parse()` |
| `src/kospex/syncs_view.py` | query-string rules, pull command, picker, Repos and Org syncs data |
| `src/kospex/org_sync_manifest.py` | `start_command`, `finish_command`, `commands`, `RESTART` |
| `src/kospex/db/migrations/0013_org_sync_listings.sql` | `org_sync_commands` table |
| `src/kospex_github.py` | `token_kind()`, `auth_identity()`; `test_auth()` uses it |
| `src/kgit.py` | `pull --stale`; command recording; suggestions via the builder |
| `src/kweb2.py`, `src/templates/syncs.html` | the `/syncs/` route and page; `/recent/` removed |

Where it differs from the plan, or settled what the plan left open:

- **Org keys are quoted in the page's commands** (`kgit pull --org
  'github.com~acme'`), unlike the examples above: zsh with `extendedglob` reads
  a `~` inside a word as a glob operator.
- **`suggestion(flag, owner, typed, **changes)`** was added to the command
  builder, so kgit can change one option (`-next 10`) or add listing flags
  without re-parsing a list. "Resume with" now keeps `-next` too.
- **An `-inventory` that lists without saving** (an unfinished run kept) is
  recorded, with `listing` NULL: it ran and used the token.
- **A command stopped with Ctrl-C** records 0 synced, failed and skipped: the
  sync loop doesn't return its counts when interrupted. The run's items still
  show the progress.
- **The picker is a native `<select>`** grouped by server. Browsers jump to an
  entry as you type its first letters; there's no search box.
- **The token expiry header name** (`GitHub-Authentication-Token-Expiration`)
  is unverified against a real fine-grained token: no token was available while
  building. If `auth_expires_at` stays NULL for a token that expires, check the
  header GitHub sends.

Checked live against public `mergestat` with `-no-auth` in a throwaway home:
three commands recorded with the right listing kinds and counts, the page's
continue command identical to kgit's, and on the dev DB
`kgit pull --check --all --stale 30` listing the same 331 of 403 repos the
page counts.
