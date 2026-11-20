# kweb `/syncs/run/{run_id}`: one org sync run, repo by repo

Design doc. Part of [#412](https://github.com/kospex/kospex/issues/412), which
[202611-kweb-syncs.md](202611-kweb-syncs.md) (#441) partly delivered. This covers
the per-run page and drift; the link from an org's page to its latest run stays
open on #412.

## Why

The Org syncs tab on `/syncs/` counts a run's items: 3 failed, 12 skipped,
2 private. It doesn't say which repos, or why. Those answers are already stored
per item in `org_sync_items` (`status`, `detail`, `fork_parent`, `fork_source`,
`archived`, `private`, `pushed_at`), and `org_sync_manifest.inventory()` already
works out drift: repos kospex has under the org that the listing doesn't
include. Neither is shown anywhere in kweb.

## Goals

1. `GET /syncs/run/{run_id}`: one page per run.
2. A header: org, owner type, state, started, finished, listed (count and when),
   the item counts per status, and the continue command for an unfinished run
   (the same one the Org syncs tab shows).
3. An item table: repo, status, detail, fork parent (and source when it differs),
   archived, private, pushed date, in kospex. A client-side status filter, since
   a large org has hundreds of rows. `gone` items are shown, marked as not in the
   current listing.
4. A separate table, **In kospex, not in this listing**: `inventory()["gone"]`.
5. Private repos: a marker on each private row, and a banner when any exist:
   "This run includes N private repos out of Y."
6. Each run row on the Org syncs tab links to its page.

## Non-goals

- A link from an org's page (`/org/...`) to its latest run. Still on #412.
- Nav, help, docs-site or CHANGELOG entries. Like `/syncs/`, this stays
  unlinked until it has been tried at a few sites.
- Writing anything: no migration, no kgit changes, no buttons that run commands.

## Anti-goals

- **A second drift query.** Drift comes from `inventory()`, which is what
  `kgit sync -inventory` / `-status` print. If the page and kgit disagree on
  drift, one of them is wrong.
- **A 500 on a DB from before migration 0013.** Same message as `/syncs/`:
  back up and run `kospex upgrade-db -apply`.
- **A 500 for an unknown run.** Unknown `run_id` is a 404, raised as an
  `HTTPException` and re-raised past the route's generic 500 handler.
- **Unescaped DB values.** Repo ids, `detail` and fork names come from GitHub
  or the DB; autoescaping stays on, no `|safe`. The status filter reads only
  the fixed status names, never text from the page.

## Data

- **Run header and counts**: the `org_sync_runs` row, `run_counts()`,
  `listed_at()`.
- **Items**: a new read-only `org_sync_manifest.items(db, run_id)`: the run's
  items `LEFT JOIN repos`, with `in_kospex` per row, ordered by repo id. It is
  the per-row form of the join `inventory()` aggregates, kept beside it.
- **Drift and the private banner**: `inventory()`. Its `private` and `listed`
  exclude `gone` items, so the banner counts the current listing, and so does Y.
- `run_id` comes from the URL and is only ever a bound parameter.

## Page

- Template `syncs_run.html`, same look as `syncs.html`; a "← Syncs" link back
  to the Org syncs tab filtered to the run's org.
- Item table via DataTables (search, sort, paging). The status filter is a row
  of buttons, one per status present, with counts, plus "all". A button sets an
  exact-match search on the status column. Default: all.
- `gone` rows are greyed, with "not in current listing" under the status.
- Private rows carry a `private` pill next to the repo id.
- "In kospex" links to `/repo/{repo_id}`; "no" otherwise.
- Pushed date is the date part of `pushed_at`.

## Testing

- View (`tests/test_syncs_view.py`): `run_view()` builds the header, counts,
  items with `in_kospex`, drift and private count; unknown run raises
  `KeyError`; behind-0013 is not available.
- Route (`tests/test_syncs_page.py`, handler called directly): 200 with items,
  drift and banner; 404 for an unknown run; the upgrade message on a DB behind
  0013; a hostile repo id and `detail` are escaped; no banner without private
  repos; the Org syncs tab links each run; nothing in `_header.html`.
- SQL runs under the SQLite 3.26 guard in `tests/conftest.py`.
- Full suite with a live kweb, skip count reported in the PR.

## Implementation

| File | What |
|---|---|
| `src/kospex/org_sync_manifest.py` | `items(db, run_id)`, beside `inventory()` |
| `src/kospex/syncs_view.py` | `run_view()`, `FILTER_STATUSES`; `_available()` shared with `org_runs()` |
| `src/kweb2.py` | `GET /syncs/run/{run_id}` |
| `src/templates/syncs_run.html` | the page |
| `src/templates/syncs.html` | each run id links to its page |

Notes:

- The view returns the items as `rows`, not `items`: in Jinja `page.items` is
  the dict's `items` method.
- The status filter uses DataTables' exact-match regex on the status cell's
  `data-search` value, so a `gone` row's "not in current listing" note doesn't
  stop it matching.
- `src/static/css/tailwind.css` is committed and wasn't rebuilt after
  `syncs.html` was added: `bg-yellow-50`, `border-yellow-300` and
  `text-yellow-700` there have no CSS. This page only uses classes the
  committed CSS has, and leaves the rebuild to its own change.
- Checked against the dev DB: both `mergestat` runs render (37 items each, no
  drift, no private repos), and an unknown run id returns 404.
