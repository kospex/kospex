# /developers/ Paging and Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/developers/` opens on active developers or an overview, with path scopes, server-side paging and sorting, and partial email search.

**Architecture:** One set of SQL in `KospexQuery` over a per-(repo, email) source that is `developer_stats`, or `commits` when stats have gaps or for the committers and `?days=` views. Pure parameter, URL and paging helpers go in a new `kospex/developers_view.py`. The route renders a plain server-side table.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLite, pytest.

**Spec:** `changes/202611-developers-page-paging-search.md`

## Global Constraints

- Work in a worktree. Run tests with `src` of the worktree on `sys.path` (see CLAUDE.md), and web endpoint tests against a branch kweb on a free port.
- SQL must run on SQLite 3.26. No database migration.
- `LIST_THRESHOLD = 250`, `PAGE_SIZE = 50`, `BUSIEST_LIMIT = 10`.
- Status bands come from `KospexUtils.DEV_STATUS_ACTIVE_DAYS` (90), `_AGING_DAYS` (180), `_STALE_DAYS` (365), on days since last commit rounded to 2 places, as `development_status()` does.
- CSV columns are unchanged: authors `author_email, commits, repos, first_commit, last_commit, last_seen, is_bot, bot_type`; committers the same with `committer_email`.
- Bots stay in every count.
- No customer names; use `acme` / `example.com` in tests.

## Review Focus

1. A search containing `%`, `_`, `\`, quotes or `<script>`: matched literally and escaped in the page. (Tasks 1, 3)
2. `page=0`, `page=abc`, `page=99999999999999999999`: a 400 or an empty page, never a 500. (Tasks 2, 3)
3. One email in different case across repos: one developer, repos counted once each. (Task 1)
4. Many developers with the same `last_commit`: no row repeated or lost across pages. (Task 1)
5. An empty author email: shown as `(no email)` with no links. (Task 3)

---

### Task 1: Queries

**Files:**
- Modify: `src/kospex_query.py` (beside `developer_statuses()`)
- Test: `tests/test_developers_page_queries.py` (new)

**Interfaces:**
- Consumes: `_stats_scope(org_key=, repo_id=, server=)`, `developer_stats_coverage()`.
- Produces (`scope` is `{}` or one of `repo_id` / `org_key` / `server`):

```python
developer_status_counts(scope, use_stats=True, now=None) -> dict
    # {"active": n, "aging": n, "stale": n, "unmaintained": n, "all": n}
developer_list(scope, use_stats=True, column="author_email", days=None,
               status=None, search=None, sort="last_seen", direction="asc",
               limit=None, offset=0, now=None) -> tuple[list[dict], int]
    # rows: {column, commits, repos, first_commit, last_commit, last_seen, status}
    # int: matching rows before limit/offset
busiest_scopes(scope, by="org", use_stats=True, limit=10, now=None) -> list[dict]
    # by "org" or "repo"; {"id": org_key or repo_id, "name": str, "active": int}
committer_count(scope, days=None) -> int
```

- [ ] **Step 1: Write the failing tests.** Use a DB fixture like `tests/test_summary_statuses.py` (real migrated DB, `update_developer_stats()` per repo), with a nested org, and pass a fixed `now` so ages are exact. Tests and assertions:
  - `test_counts_match_developers`: for estate, server, org, nested org and repo scopes, each band equals the count of that status in `developers(**scope)`; `all == len(developers(**scope))`.
  - `test_band_edges`: ages 90, 91, 180, 181, 365, 366 days land in active, aging, aging, stale, stale, unmaintained.
  - `test_stats_and_commits_sources_agree`: for each scope, `developer_list(scope, use_stats=True)` equals `use_stats=False`.
  - `test_mixed_case_is_one_developer`: `Dev@Example.com` and `dev@example.com` in two repos give one row, `repos == 2`, commits summed.
  - `test_status_filter`: `status="aging"` returns only aging rows and its total equals the aging count.
  - `test_search_is_a_case_insensitive_contains`: `search="PAUL"` finds `paul@example.com` and `mrpaul@example.com`.
  - `test_search_wildcards_are_literal`: `a_b` does not match `axb`; `%` matches only emails containing `%`; `\` and `'` do not raise.
  - `test_exact_match_is_first`: `search="paul@example.com"` lists it first, ahead of a more recently seen `mrpaul@example.com`.
  - `test_each_sort_and_direction`: first and last email for `last_seen`, `commits`, `repos`, `email`, both directions. `last_seen asc` is most recent first.
  - `test_pages_are_stable_with_ties`: 7 developers with identical dates and commits, `limit=3`: the three pages hold 7 distinct emails.
  - `test_committers_column`: `column="committer_email"` groups committers, rows keyed `committer_email`.
  - `test_days_window_counts_only_the_window`: `days=90` excludes older commits from `commits`; a commit an hour inside the window with a `-10:00` offset is included.
  - `test_busiest_orgs_and_repos`: ordered by active count, ties by id, zero-active entries left out, `limit` honoured, nested org id uses `~~`.
  - `test_unknown_column_or_sort_raises`: `ValueError`.
- [ ] **Step 2: Run** `pytest tests/test_developers_page_queries.py`. Expected: fail with `AttributeError`.
- [ ] **Step 3: Implement.**
  - Source subquery, one per (repo, email): from `developer_stats` (`stat_type = 'ALL_TIME'`, `total_commits`), or from `commits` grouped by `_repo_id` and `column` with first/last as UTC `...Z` text, as `update_developer_stats()` writes them. `use_stats` is ignored (commits) when `days` is given or `column` is `committer_email`.
  - Outer query groups by `LOWER(email)`: `SUM(commits)`, `COUNT(DISTINCT repo)`, `MIN(first_commit)`, `MAX(last_commit)`. A NULL `last_commit` counts in `all` only.
  - `days`: filter with `julianday(committer_when) > julianday(?)`, with a text prefilter two days wider so the index still narrows the scan.
  - Search: `LIKE ? ESCAPE '\'` with `\`, `%`, `_` escaped. `column`, `sort` and `direction` are whitelisted, never interpolated from input. Every ordering ends with the email.
- [ ] **Step 4: Run** the tests. Expected: pass.
- [ ] **Step 5: Commit** `kospex: queries for a paged, searchable developer list`.

### Task 2: Parameters, URLs and paging

**Files:**
- Create: `src/kospex/developers_view.py`
- Test: `tests/test_developers_view.py` (new)

**Interfaces:**
- Produces:

```python
LIST_THRESHOLD = 250
PAGE_SIZE = 50
BUSIEST_LIMIT = 10
SORT_DEFAULT_DIRECTION = {"last_seen": "asc", "commits": "desc", "repos": "desc", "email": "asc"}

parse_list_params(query_params) -> dict
    # {"status": str|None, "q": str|None, "sort": str, "dir": str, "page": int}
    # raises ValueError with a message fit to show
developers_url(scope, **params) -> str
page_window(total, page) -> dict   # {"limit": int|None, "offset": int, "page": int, "pages": int}
```

- [ ] **Step 1: Write the failing tests.**
  - Defaults: `{"status": None, "q": None, "sort": "last_seen", "dir": "asc", "page": 1}`; `sort=commits` alone gives `dir="desc"`.
  - `status=Active` gives `"active"`; `q="  paul "` gives `"paul"`; `q="   "` gives `None`.
  - `ValueError` for `status=bogus`, `sort=bogus`, `dir=up`, `page` in `0`, `-1`, `abc`, `1.5`, `²`, `99999999999999999999`.
  - `developers_url({})` is `/developers/`; `{"server": "github.com"}` is `/developers/github.com`; `{"org_key": "gitlab.com~beta~~sub"}` is `/developers/gitlab.com~beta~~sub`; `{"repo_id": "github.com~acme~api"}` with `status="active", page=2` is `/developers/github.com~acme~api?status=active&page=2`; `None` and empty params are left out; `q="a&b c"` is URL-encoded.
  - `page_window(250, 1)` is `{"limit": None, "offset": 0, "page": 1, "pages": 1}`; `page_window(251, 2)` is `{"limit": 50, "offset": 50, "page": 2, "pages": 6}`; a page past the end keeps its offset.
- [ ] **Step 2: Run** `pytest tests/test_developers_view.py`. Expected: fail on import.
- [ ] **Step 3: Implement** the module. Keep it free of imports from `kweb2`. Cap `page` at 1,000,000.
- [ ] **Step 4: Run** the tests. Expected: pass.
- [ ] **Step 5: Commit** `kweb: parameter and URL helpers for /developers/`.

### Task 3: Route, scopes and the list

**Files:**
- Modify: `src/kweb2.py` (`_developer_rows`, `developers`)
- Modify: `src/templates/developers.html`
- Modify: `tests/test_landscape_scope.py` (developers classes)
- Test: `tests/test_developers_page.py` (new)

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: `_developers_scope(id, query_params) -> dict`; template context `people`, `counts`, `status_links`, `data` (`authors`, `committers`), `mode` (`"list"` here, `"overview"` in Task 4), `pager`, `search_action`, `q`, `status`, plus the existing `view`, `days`, `collab_url`, `row_scope` and breadcrumb keys.

**Rules:**
- The `?author_email=` view is unchanged and handled first.
- Scope: a path ID wins over `?repo_id=` > `?org_key=` > `?server=`. A path ID with `~` is an org or repo. One without is a server if `scope_exists({"server": id})`; else if it is base64 of something containing `@`, a 404 whose detail names `/developers/?author_email=`; else the usual unknown-server 404. Query scopes go through `_require_known_scope` too.
- `use_stats = not any(developer_stats_coverage().values())`.
- Authors view without `days`: effective status is `status`, else `all` when searching, else `active`.
- Committers view and `?days=`: no status filter, no search box, `status` and `q` ignored; `days` keeps its current validation.
- Cards: authors is `counts["all"]` (the windowed total when `days`); committers is `committer_count(scope, days)`. Counts and committer count go through `cached_query`. `summary()` is no longer called.
- Bot detection runs only on rows returned. CSV passes no limit and drops `status`.
- Template: plain table, no DataTables. Column headings link to sort (toggle direction), pager shows "Page N of M" with previous/next, a past-the-end page says so and links to page 1. A Status column appears only when searching. Replace the "Show active developers" link with the status counts; keep "Show all time" for `?days=`.

- [ ] **Step 1: Write the failing tests** against a real DB fixture, calling `kweb2.developers` as `test_landscape_scope.py` does:
  - Default shows active developers only; `?status=all` shows everyone; counts for all five links are rendered with `href` in path form.
  - `/developers/github.com`, `/developers/github.com~acme`, `/developers/github.com~acme~api` each list only that scope's developers; `?server=`, `?org_key=`, `?repo_id=` do the same.
  - A server named `git1` resolves as a server (assert `is_base64("git1")` first so the test cannot pass vacuously).
  - Base64 email path: 404 mentioning `?author_email=`. Unknown server, org, repo, by path and by query: 404.
  - With 251 matching developers, 50 rows and "Page 1 of 6"; `page=6` has 1; `page=99` is 200 with no rows; with 250, all rows and no pager.
  - `status=bogus`, `page=abc`, `sort=bogus`: 400.
  - `q=<script>`: 200 and the raw tag is absent from the HTML.
  - An empty author email renders `(no email)` with no `author_email=` link.
  - CSV with `status=all&q=` and a 251-row scope: every matching row, header exactly as in Global Constraints.
  - Monkeypatch `summary`, `authors`, `committers` to raise: the page still renders.
  - With a `developer_stats` gap, the same emails and commits are listed.
- [ ] **Step 2: Run** them. Expected: fail.
- [ ] **Step 3: Implement** route and template. Remove `_developer_rows`' calls to `authors()` / `committers()`; leave those query methods for their other callers.
- [ ] **Step 4: Update `tests/test_landscape_scope.py`.** Fakes gain the Task 1 methods in place of `authors` / `committers` / `summary`; scoped link assertions move to path form; the two "active view" tests assert the Active count link instead of `?days=90`. Keep every assertion about scope reaching the query, breadcrumbs, bot badges, CSV headers and 400s.
- [ ] **Step 5: Run** the whole suite. Expected: pass.
- [ ] **Step 6: Commit** `kweb: path scopes, paging and email search on /developers/`.

### Task 4: Overview, docs and verification

**Files:**
- Modify: `src/kweb2.py`, `src/templates/developers.html`
- Modify: `CHANGELOG.md`, `changes/202611-developers-page-paging-search.md` (status line)
- Test: `tests/test_developers_page.py`

**Interfaces:**
- Consumes: `busiest_scopes()`, `BUSIEST_LIMIT`, `developers_url()`.
- Produces: `mode == "overview"` with `busiest` (each with `url` = that scope with `status="active"`) and `busiest_label` (`"orgs"` or `"repos"`).

- [ ] **Step 1: Write the failing tests.** Overview only when authors view, no `days`, `status` or `q`, and active > 250: no developer rows, up to 10 busiest orgs linked, "List all active" and "Download CSV" links. Active == 250 gives the list. Org scope lists busiest repos; repo scope lists none. The overview's "Download CSV" is the active slice (`status=active&download=true`). Zero active: 200, a message, counts still linked.
- [ ] **Step 2: Run** them. Expected: fail.
- [ ] **Step 3: Implement** the overview.
- [ ] **Step 4: CHANGELOG** under `## Unreleased`: Changed (overview and active default, path scopes, search, paging; the Active list shows all-time commits where the old active link counted the window), Fixed (server scope was ignored).
- [ ] **Step 5: Run** the whole suite with a branch kweb. Expected: pass, web endpoint tests not skipped.
- [ ] **Step 6: Verify on the real DB** (spec goals): `/developers/` cold in about a second and well under 1 MB; status counts equal `/summary/` for estate, one server and one org; `/summary2/` status links filter.
- [ ] **Step 7: Commit** `kweb: overview for large scopes on /developers/`.
