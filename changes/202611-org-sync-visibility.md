# Org sync: record each repo's visibility (public, private, internal)

Design doc, not yet built. Follows
[202611-kweb-sync-run.md](202611-kweb-sync-run.md) (#460).

## Why

`kgit sync --org` records one flag per repo, `private`, set when GitHub's
`visibility` is `"private"`. GitHub has a third value, **`internal`**: readable
by every member of the enterprise, across all its orgs, but not by the public.
Only orgs owned by an enterprise account (GitHub Enterprise Cloud or Server)
can have internal repos. A Team-plan org has only public and private repos.

On an enterprise org, every repo is internal. GitHub returned
`visibility: "internal"` and `private: true` for all. kospex stored
`private = 0` for all of them, so the run page shows no private repos and
nothing says they aren't public. kospex stores no other trace: kgit reads
`visibility` from the listing, turns it into the 0/1 flag, and drops the value.

Calling internal repos "private" would be wrong too. To someone who runs the
org's GitHub, "private" means named people and teams; internal means the whole
enterprise. The difference is an access-control fact, and kospex should report
it as GitHub states it.

## Goals

1. Store each listed repo's visibility exactly as GitHub reports it: `public`,
   `private` or `internal`.
2. Count internal repos in each listing, next to the existing private count.
3. Show visibility in kgit (`-inventory`, `-history`) and on the kweb run page,
   with internal and private told apart.
4. Fill it in for runs already saved, through a re-listing that clones nothing.

## Non-goals

- A patch release with internal counted as private. Considered and dropped:
  it would give the misleading label this change exists to avoid.
- Bitbucket or GitLab visibility. `kgit sync --org` is GitHub-only.
- Tracking visibility changes between listings (internal → public) in
  `org_sync_listings`, as `newly_archived` does for archiving. Possible later;
  the per-item column makes it a query.
- Linking the run page from nav, help or docs. It stays silent, like `/syncs/`.

## Anti-goals

- **Rewriting what `private` means.** It keeps meaning "GitHub says `private`".
  Existing rows stay correct: an internal repo stored as `private = 0` is right,
  because it isn't private. What's missing is `internal`, which is added beside it.
- **Guessing a visibility.** A repo listed before this change has `NULL`, shown
  as "not recorded", until a re-listing fills it. It is never inferred from
  `private = 0`, which could be public or internal.
- **Losing a value GitHub sends.** An unknown future visibility is stored as
  sent, not dropped.

## Data: migration `0014`

```sql
ALTER TABLE [org_sync_items] ADD COLUMN [visibility] TEXT;   -- public | private | internal; NULL = not recorded
ALTER TABLE [org_sync_listings] ADD COLUMN [internal] INTEGER; -- count in this listing; NULL = not recorded
UPDATE [org_sync_items] SET [visibility] = 'private' WHERE [private] = 1;
```

- The backfill sets only what is known: `private = 1` means GitHub said
  `private`. Everything else stays `NULL`.
- `org_sync_listings.internal` stays `NULL` for listings saved before 0014, so
  `-history` can show "-" rather than a false 0.
- Columns go in the migration only, never in `kospex_schema.py`'s baseline
  (see `CLAUDE.md`, *Adding a Database Migration*). Expect to bump the migration
  counts in `test_db_health.py`, `test_kospex_utils.py` and
  `test_kospex_schema.py`.

## Listing

- `kospex_github.get_repos()` passes GitHub's `private` boolean through with
  `visibility` (today it passes only `visibility`).
- `org_sync` items get `visibility` = GitHub's value, lowercased. When GitHub
  omits it (older GitHub Enterprise Server), fall back to `private` if
  `private` is true, else `public`. `private` stays `visibility == "private"`.
- `visibility` joins `LISTING_FIELDS`, so `-refresh` overwrites it like
  `archived`.
- `-no-auth` lists public repos only, so they're all `public`.

## kgit

- `-inventory` gains a line: `public N   private N   internal N`, plus
  `not recorded N` when any are `NULL`.
- `-history` gains an `internal` column after `private`.
- `org_sync_manifest.inventory()` returns `public`, `internal` and
  `visibility_unknown` beside `private`, all over the current listing
  (excluding `gone`), as `private` is now.

## kweb run page (`/syncs/run/{run_id}`)

- The `Private` column becomes **Visibility**, showing the stored value or
  "not recorded".
- Row markers: `private` (purple, as now) and `internal` (a second colour).
  Nothing on public rows.
- Banner, when any are non-public:
  "This run includes N internal and M private repos out of Y", leaving out
  a zero part.
- When any are `NULL`: "Visibility not recorded for K repos. To fill it in
  without cloning: `kgit sync --org OWNER -inventory -refresh`", with a copy
  button. The command is built by `org_sync_command`, like the continue command.
- Rebuild `tailwind.css` if the internal marker needs a class the committed CSS
  lacks (see #462).

## Rollout at a site

1. Upgrade kospex, back up `~/kospex/kospex.db`, then `kospex upgrade-db -apply`.
2. `kgit sync --org OWNER -inventory -refresh`: lists the org again into the
   unfinished run, keeping progress. For a finished run, the next `-active` or
   `-inventory` run lists it afresh.
3. The run page then shows visibility for every listed repo.

## Testing

- Mapping: `internal`, `private` and `public`; a missing `visibility` with
  `private` true and false; an unexpected value is kept; `-no-auth` is `public`.
- Migration: applies on a fresh DB (`test_fresh_db_has_no_pending_migrations`),
  backfills `private = 1` rows only, and the count tests are bumped exactly.
- `refresh_listing` overwrites `visibility`; `inventory()` counts each value and
  `NULL`, excluding `gone`.
- kgit `-inventory` and `-history` output, using the fake GitHub in
  `tests/test_kgit_sync_org_cli.py`.
- Run page: markers, banner wording with zero parts left out, the not-recorded
  note and its command, and a DB behind 0014 (the page says to run
  `kospex upgrade-db`, not a 500).
- SQL under the SQLite 3.26 guard; full suite with a live kweb run from the
  branch (a worktree's tests otherwise hit the main checkout's kweb on :8000).

## Docs

- `docs/kgit.md`: `-inventory` and `-history`, and what internal means.
- `CHANGELOG`: migration 0014 and the kgit output. The run page stays unannounced.
- `docs/data-schemas.md` has no `org_sync_*` section, so nothing to update there.

## Decisions (2026-11-21)

- Store GitHub's visibility; don't count internal as private.
- No patch release; this ships as one change with the migration.
- `private` keeps its meaning; `internal` is added beside it.
- Unknown visibility is `NULL` and shown as "not recorded", never guessed.

## Implementation

| File | What |
|---|---|
| `src/kospex/db/migrations/0014_org_sync_visibility.sql` | the two columns and the `private = 1` backfill |
| `src/kospex_github.py` | passes GitHub's `private` boolean through with `visibility` |
| `src/kospex/org_sync.py` | `visibility(repo)`; items carry it, `private` derived from it |
| `src/kospex/org_sync_manifest.py` | `PUBLIC` / `PRIVATE` / `INTERNAL`; `visibility` in `LISTING_FIELDS`; listings count `internal`; `inventory()` counts each |
| `src/kospex/org_sync_command.py` | `relisting(opts)`, for the page's re-listing command |
| `src/kgit.py` | `-inventory` / `-status` visibility line, `-history` `internal` column |
| `src/kospex/syncs_view.py`, `src/templates/syncs_run.html` | Visibility column, markers, banner, not-recorded note |

Where it differs from the plan, or settled what the plan left open:

- **No `visibility` and no `private`** from GitHub gives `NULL`, not `public`:
  the plan's fallback would have guessed.
- **An unexpected value** (one GitHub adds later) is counted as
  `visibility_other` and shown as "other non-public" in the banner, with its
  own value as the row's marker.
- **`-history` shows an empty cell**, not "-", for a listing from before 0014,
  as it does for every other NULL count.
- **The not-recorded note on a finished run** omits `-refresh`: with no
  unfinished run, `-inventory -refresh` saves a new run, so the note says that
  instead of promising to fill in this one.
- **The re-listing command** keeps the last command's options except `-active`
  and `-next` (which `-inventory` refuses) and the listing flags.
- **The run page needs 0014**: on a DB behind it, the page shows the
  `kospex upgrade-db` note. The Org syncs tab doesn't read visibility, so it
  still works there.
- **The internal marker is blue** (`bg-blue-100 text-blue-800`, already in the
  committed CSS), private stays purple. No CSS rebuild.
- **Also on the run page: the server / org breadcrumb** (`_breadcrumb.html`, as
  on `/osi/` and the org's other pages), under the "← Syncs" link. It is left
  out until the org has repos in kospex, because its crumbs would 404 before then.
