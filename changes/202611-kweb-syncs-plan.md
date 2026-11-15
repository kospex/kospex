# kweb `/syncs/` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only kweb `/syncs/` page (stale repos, org sync runs and their commands), `kgit pull --stale DAYS`, a record of every `kgit sync --org/--user` command with its options and token details, one command builder shared by kgit and the page, and removal of the `/recent/` stub.

**Architecture:** Pure functions in small `src/kospex/` modules hold every rule (staleness, command text, page data); `kgit.py` and `kweb2.py` only call them, so the CLI and the page cannot disagree. The new `org_sync_commands` table goes in the unshipped migration `0013`. The page is one FastAPI route and one Jinja template.

**Tech Stack:** Python 3.12, Click, sqlite_utils, FastAPI + Jinja2, Tailwind classes, DataTables, pytest.

**Spec:** `changes/202611-kweb-syncs.md` (read it first; this plan argues from it).

## Global Constraints

- SQL must run on SQLite 3.26 (`tests/conftest.py` refuses newer functions; avoid `RETURNING`, `->>`, `DROP COLUMN`).
- Schema changes go in `src/kospex/db/migrations/0013_org_sync_listings.sql` only, never `kospex_schema.py`. No `;` inside SQL comments.
- Never store a token, a hash of one, or any part of one.
- Every value interpolated into a printed or displayed command is `shlex.quote`d.
- Jinja autoescape stays on; no `|safe` on DB values.
- `/syncs/` is not linked from the nav, help pages, docs site or sitemap, and has no button that runs anything.
- No customer names, no session URLs, no mention of private repos in code, docs or commits.
- Lines ≤ 100 characters. Match the surrounding code's comment density.
- Run tests in this worktree in-process: `/Users/peterfreiberg/dev/github.com/kospex/kospex/.venv/bin/python -I /private/tmp/claude-501/-Users-peterfreiberg-dev-github-com-kospex-kospex/70a772af-8cba-491c-bddd-99597f322eb5/scratchpad/run_active_tests.py -q -p no:cacheprovider <tests>` (it puts the worktree's `src` first). Plain `pytest` tests the main checkout.
- Before each commit that touches kweb, run the full suite with kweb up (`curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/` → `200`) and quote the skip count.

## Review Focus

1. **A repo with neither `last_fetch` nor `last_sync`** must count as stale for every DAYS, in `--stale`, `--check` and the page alike. Test in Task 1.
2. **Timestamps without an offset** (`2026-07-10 23:02:15`) alongside `+11:00` and `Z` ones: treat naive as UTC, never raise. Test in Task 1.
3. **A DB without `0013`** (no `org_sync_commands` / `org_sync_listings`): `/syncs/` renders with an "upgrade-db" note, not a 500; `kgit pull --stale` still works. Test in Tasks 7 and 8.
4. **An org key or repo id with shell or HTML metacharacters** (`gitlab.example.com~a;rm -rf~~b`, `<script>`): quoted in the command, escaped in the page. Test in Tasks 7 and 8.
5. **A command stopped with Ctrl-C** leaves `finished_at` NULL and the page shows it as "stopped or running" rather than failing on the NULLs. Test in Tasks 6 and 7.

---

### Task 1: Shared staleness rule

**Files:**
- Create: `src/kospex/staleness.py`
- Modify: `src/kgit.py:108-138` (`_staleness_rows` delegates)
- Test: `tests/test_staleness.py`, adjust `tests/test_kgit_pull.py` where it asserts `fetch_age` order

**Interfaces:**
- Produces:
  - `parse_time(value: str | None) -> datetime | None` — aware UTC datetime; naive values are UTC; unparseable → `None`.
  - `last_refreshed(repo: dict) -> str | None` — `repo["last_fetch"]` or else `repo["last_sync"]`.
  - `age_days(repo: dict, now: datetime) -> float | None` — exact (no rounding, no `abs`).
  - `is_stale(repo: dict, days: int, now: datetime) -> bool` — `True` when no refresh time, or age > `days`.
  - `staleness_rows(repos: list[dict], now: datetime | None = None) -> list[dict]` — keys `repo_id, last_fetch ("never" if empty), last_sync, last_commit, fetch_age ("{int}d" or "never"), age_days (float | None), fell_back (bool)`; sorted by age, never-refreshed first.

- [ ] **Step 1: Write the failing tests** in `tests/test_staleness.py`:
  - `test_last_fetch_wins_over_last_sync`, `test_falls_back_to_last_sync`, `test_neither_is_always_stale` (`is_stale(..., 1, now)` and `..., 10_000, now)` both `True`).
  - `test_mixed_offsets_compare_as_instants`: `+11:00` vs `Z` for the same instant give equal ages.
  - `test_naive_time_is_utc`, `test_unparseable_is_none`.
  - `test_boundary`: age exactly 30 days → `is_stale(r, 30, now) is False`; 30 days + 1 second → `True`.
  - `test_rows_sort_by_fallback_age`: a never-fetched repo synced yesterday sorts after one fetched 40 days ago; its `last_fetch` shows `"never"` and `fell_back` is `True`.
- [ ] **Step 2: Run them; expect `ModuleNotFoundError: kospex.staleness`.**
- [ ] **Step 3: Implement `src/kospex/staleness.py`;** make `kgit._staleness_rows(repos, now=None)` call `staleness_rows` (keep its name and ISO-string `now` for existing callers by parsing it with `parse_time`). Do not use `KospexUtils.days_between_datetimes`.
- [ ] **Step 4: Run `tests/test_staleness.py tests/test_kgit_pull.py`; update only assertions that encoded the old never-first order, and say which in the commit.**
- [ ] **Step 5: Commit** `feat(kgit): one staleness rule: last_fetch, else last_sync`.

### Task 2: `kgit pull --stale DAYS`

**Files:**
- Modify: `src/kgit.py` (`pull` command, ~line 728 on main / its position on this branch)
- Test: `tests/test_kgit_pull_stale.py`

**Interfaces:**
- Consumes: `staleness.is_stale`, `staleness.staleness_rows`.
- Produces: option `--stale` (`type=click.INT`, `default=None`, `metavar="DAYS"`).

- [ ] **Step 1: Write the failing tests** (drive `CliRunner().invoke(cli, [...])` over a DB built like `tests/test_kgit_pull.py::_repos_db`, with `kgit._git_pull` and `kospex.sync_repo` monkeypatched to record calls):
  - `test_stale_pulls_only_repos_older_than_days`: 3 repos (fetched 1, 10, 40 days ago) + `--all --stale 7` → pulls exactly the 10- and 40-day ones; output contains `Pulling 2 of 3 repos not refreshed in 7 days.`
  - `test_stale_narrows_org_and_server_and_repo_id` (parametrised over `--org`, `--server`, a `REPO_ID`).
  - `test_stale_with_check_filters_the_report`: `--check --all --stale 7` lists 2 repo ids, not 3; no pulls.
  - `test_nothing_stale`: exit 0, `No repos in scope were last refreshed more than 7 days ago.`
  - `test_stale_must_be_positive` (`0`, `-3` → usage error mentioning `--stale`).
- [ ] **Step 2: Run; expect "No such option: --stale".**
- [ ] **Step 3: Implement:** validate `--stale >= 1` as `click.UsageError("--stale needs a whole number of days, 1 or more.")`; filter `repos` with `is_stale(r, stale, now)` after `_resolve_pull_repos`; print the two messages above (use `_days()` for "1 day"/"N days"). Update the `pull` docstring with an example.
- [ ] **Step 4: Run the new and existing pull tests; expect PASS.**
- [ ] **Step 5: Commit** `feat(kgit): pull --stale DAYS pulls only repos not refreshed in DAYS`.

### Task 3: One command builder for kgit sync

**Files:**
- Create: `src/kospex/org_sync_command.py`
- Modify: `src/kgit.py` — every suggestion in `_sync_owner_locked` (`-inventory -refresh`, `-inventory -restart`, `-next 10`, "Continue with" ×2, "Resume with", "See the failures") and `_print_status`'s suggestions
- Test: `tests/test_org_sync_command.py`; update `tests/test_kgit_sync_org_cli.py::test_suggested_commands_keep_no_auth` and any test asserting suggestion text

**Interfaces:**
- Produces:
  - `options(*, inventory=False, refresh=False, restart=False, active=None, next_=None, retry_failed=False, include_forks=False, skip_forks=False, skip_archived=False, ssh=False, no_prompt=False, no_auth=False) -> list[str]` — the options as typed, in this fixed order: `-inventory -refresh -restart -active N -next N -retry-failed -include-forks -skip-forks -skip-archived -ssh -no-prompt -no-auth`.
  - `continuing(opts: list[str]) -> list[str]` — drops `-inventory`, `-refresh`, `-restart`, `-retry-failed`.
  - `command(flag: str, owner: str, opts: list[str]) -> str` — `kgit sync {flag} {shlex.quote(owner)} …`, each value quoted.
  - `parse(options_text: str) -> list[str]` — `shlex.split`, for reading the stored column back.

- [ ] **Step 1: Write the failing tests:** order is fixed regardless of keyword order; `active=90, next_=10, no_auth=True` → `["-active", "90", "-next", "10", "-no-auth"]`; `continuing` drops exactly the four; `command("--org", "acme", [...])` == `"kgit sync --org acme -active 90 -next 10 -no-auth"`; an owner `"a b"` is quoted `'a b'`; `parse(" ".join(...))` round-trips.
- [ ] **Step 2: Run; expect import failure.**
- [ ] **Step 3: Implement the module; pass the user's flags from `_sync_owner` into `_sync_owner_locked` as one `opts: list[str]` (built once with `options(...)`), and build every suggestion from `command(flag, owner, continuing(opts) + extra)`** where `extra` is the suggestion's own flags (e.g. `["-inventory", "-refresh"]`, or `["-next", "10"]` when `opts` has no `-next`). Keep the surrounding sentences as they are.
- [ ] **Step 4: Add `test_suggestions_carry_every_option`** to `tests/test_kgit_sync_org_cli.py`: `--org acme -active 90 -next 1 -no-auth -ssh -skip-archived -no-prompt -include-forks` → output contains `Continue with: kgit sync --org acme -active 90 -next 1 -include-forks -skip-archived -ssh -no-prompt -no-auth`. Run the CLI suite; fix expected suggestion strings that changed order.
- [ ] **Step 5: Commit** `feat(kgit): sync --org suggestions repeat every option`.

### Task 4: `org_sync_commands` table and its manifest functions

**Files:**
- Modify: `src/kospex/db/migrations/0013_org_sync_listings.sql` (header paragraph + table + index), `src/kospex/org_sync_manifest.py`
- Test: `tests/test_org_sync_manifest.py` (schema check + new tests)

**Interfaces:**
- Produces (in `org_sync_manifest`):
  - `TBL_ORG_SYNC_COMMANDS = "org_sync_commands"`
  - `start_command(db, org_key: str, command: str, options: str, auth: dict, now: str | None = None) -> int` — inserts, returns `command_id`. `auth` keys: `login, kind, source, expires_at`.
  - `finish_command(db, command_id: int, *, run_id: str | None, listing: str | None, synced_any: bool, synced: int, failed: int, skipped: int, new: int | None, gone: int | None, error: str | None = None, finished: bool = True, now: str | None = None) -> None` — `finished=False` leaves `finished_at` NULL.
  - `commands(db, run_id: str) -> list[dict]` — oldest first.
- Table columns, exactly: `command_id INTEGER PRIMARY KEY, run_id, org_key, started_at, finished_at, listing, synced_any INTEGER, options, command, synced INTEGER, failed INTEGER, skipped INTEGER, [new] INTEGER, gone INTEGER, auth_login, auth_kind, auth_source, auth_expires_at, error` (TEXT unless marked). Index `idx_org_sync_commands_run ON org_sync_commands (run_id, started_at)`.

- [ ] **Step 1: Write the failing tests:** the baseline-schema test expects `org_sync_commands`; `test_command_start_and_finish` (row values round-trip, `finished_at` set); `test_unfinished_command_keeps_finished_at_null`; `test_commands_are_per_run_oldest_first`.
- [ ] **Step 2: Run; expect missing table.**
- [ ] **Step 3: Add the table to `0013`** (document each column in a `--` comment, no `;`), then the three functions. `listing` values are `"first"`, `"refresh"`, `"restart"` or `None`.
- [ ] **Step 4: Run `tests/test_org_sync_manifest.py tests/test_kospex_schema.py tests/test_db_health.py tests/test_kospex_utils.py`; expect PASS** (migration counts are unchanged: still one `0013`).
- [ ] **Step 5: Commit** `feat(kgit): org_sync_commands table in 0013`.

### Task 5: Token identity from GitHub

**Files:**
- Modify: `src/kospex_github.py`
- Test: `tests/test_github_auth_identity.py`

**Interfaces:**
- Produces:
  - `KospexGithub.token_kind(token: str | None) -> str` (staticmethod) — `github_pat_`→`fine-grained`, `ghp_`→`classic`, `ghs_`→`app`, `gho_`→`oauth`, empty/None→`none`, else `unknown`.
  - `KospexGithub.auth_identity(self) -> dict | None` — one `GET /user`; on success `{"login", "kind", "source", "expires_at"}`, `expires_at` parsed from the `GitHub-Authentication-Token-Expiration` header (`"2027-05-20 00:00:00 UTC"` → `"2027-05-20T00:00:00Z"`; absent or unparseable → `None`). On failure `None`, printing what `test_auth` prints today. `test_auth()` keeps its behaviour (it may call `auth_identity`).

- [ ] **Step 1: Write the failing tests** with `requests.request` monkeypatched to return a response object with `status_code`, `text`, `json()`, `headers`: each prefix → kind; header parsed; header absent → `None`; 401 → `None`; `test_auth()` still returns `True`/`False` and prints `Authenticated as: octocat`.
- [ ] **Step 2: Run; expect AttributeError.**
- [ ] **Step 3: Implement.** Read headers case-insensitively (`response.headers.get(...)` on `requests` is already case-insensitive).
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Manual check, by Peter** (needs a real fine-grained token; never print the token): `! python -c "from kospex_github import KospexGithub as G; g=G(); g.get_env_credentials(); print(g.auth_identity())"`. Expected: login, `fine-grained`, the variable name, an ISO expiry. If `expires_at` is `None` for a token that has an expiry, the header name is wrong: record what GitHub sends in `changes/202611-kweb-syncs.md`.
- [ ] **Step 6: Commit** `feat(github): auth_identity: login, token kind, source and expiry`.

### Task 6: Record each `kgit sync` command

**Files:**
- Modify: `src/kgit.py` (`_error`, `_sync_owner`, `_sync_owner_locked`, `_refresh_run`)
- Test: `tests/test_kgit_sync_org_cli.py` (FakeGitHub gains `auth_identity()` returning `{"login": "octocat", "kind": "fine-grained", "source": "GH_TOKEN", "expires_at": "2027-05-20T00:00:00Z"}` when `auth_ok`, else `None`)

**Interfaces:**
- Consumes: Task 3 `options/command`, Task 4 `start_command/finish_command`, Task 5 `auth_identity`.
- Produces: `class _Stopped(SystemExit)` with `.message`; `_error(message)` prints as today and raises `_Stopped(message)` (exit code 1, so existing tests keep passing).

- [ ] **Step 1: Write the failing tests:**
  - `test_each_working_command_is_recorded`: `-inventory`, then `-next 1`, then default → 3 rows for the run with `listing` `first`, `None`, `None`; `synced_any` 0, 1, 1; `synced` 0, 1, rest; `options`/`command` as typed; auth fields from the fake.
  - `test_reads_and_refusals_leave_no_row`: `-status`, `-history`, a rejected token, an owner that doesn't exist, a held lock → 0 rows.
  - `test_no_auth_is_recorded_as_none`: `auth_kind == "none"`, `auth_login is None`.
  - `test_refresh_and_restart_are_recorded`: `-inventory -refresh` → `listing == "refresh"`, `new`/`gone` from that listing; `-inventory -restart` → `"restart"`; `-active` re-listing a stale run → `"refresh"`.
  - `test_an_incomplete_listing_records_the_error`: on a refresh of an existing run, `error` contains the listing message and `finished_at` is set.
  - `test_interrupted_command_is_left_unfinished`: `sync_repo` raises `KeyboardInterrupt` → exit 130, row has `finished_at` NULL and `run_id` set.
- [ ] **Step 2: Run; expect failures (no rows).**
- [ ] **Step 3: Implement:** replace the `test_auth()` call in `_sync_owner` with `auth_identity()` (same stop-on-failure behaviour); with `-no-auth` the auth dict is `{"kind": "none", "source": None, "login": None, "expires_at": None}`. After `lock.acquire()` succeeds, `start_command(...)`; pass `command_id` into `_sync_owner_locked`, which reports `run_id`, the listing kind it saved and the counts (`done` from `sync_items`; `new`/`gone` from the listing it saved, via `OrgSyncManifest.listings`). Wrap the call: `_Stopped` → `finish_command(..., error=e.message)` and re-raise; `SystemExit(130)` → `finish_command(..., finished=False)` and re-raise; normal return → `finish_command(...)`.
- [ ] **Step 4: Run the CLI and manifest suites; expect PASS.** Check `kgit github OWNER -sync` reaches `_sync_owner` unchanged and its row's `command` reads `kgit sync --org OWNER …`.
- [ ] **Step 5: Commit** `feat(kgit): record each sync --org command, its options and token`.

### Task 7: Page data (`syncs_view`)

**Files:**
- Create: `src/kospex/syncs_view.py`
- Test: `tests/test_syncs_view.py`

**Interfaces:**
- Consumes: `staleness.*`, `org_sync_command.continuing/command/parse`, `OrgSyncManifest.commands/listings/run_counts`, `KospexUtils.parse_org_key`, `KospexGit.generate_org_key`.
- Produces:
  - `DAYS_CHOICES = (1, 7, 30)`; `DEFAULT_DAYS = 30`.
  - `parse_params(tab: str | None, org: str | None, server: str | None, days: str | None) -> dict` — returns `{"tab", "org", "server", "days" (int | None for "all")}`; raises `ValueError(message)` for an unknown tab, both org and server, an invalid org key, or days not a positive int / `"all"`.
  - `pull_command(*, org: str | None, server: str | None, days: int | None, check: bool = False) -> str` — `kgit pull --all|--org K|--server S [--check] [--stale N]`, quoted.
  - `picker(repos: list[dict], days: int | None, now: datetime) -> list[dict]` — one dict per server: `{"server", "repos", "stale", "orgs": [{"org_key", "label", "repos", "stale"}]}`; owner-less repos counted in the server only; nested owners labelled with `/`.
  - `repo_view(repos: list[dict], *, org: str | None, server: str | None, days: int | None, now: datetime) -> dict` — `{"rows" (staleness_rows of the stale ones), "total", "stale", "fell_back", "command", "check_command"}`.
  - `org_runs(db, *, org: str | None, server: str | None, now: datetime) -> dict` — `{"available": bool, "runs": [...]}`; `available` is `False` when `org_sync_commands` or `org_sync_listings` is missing. Each run: run row + `counts`, `archived`, `forks`, `commands` (each with `token_text` like `octocat · fine-grained · GH_TOKEN · expires 2027-05-20`, `expiring` bool for expired or ≤ 14 days, `state` `"finished" | "stopped or running"`), `listings`, `continue_command` (from the last command via `continuing`, else `kgit sync --org OWNER`; `None` when the run is finished).

- [ ] **Step 1: Write the failing tests** over an in-memory DB built with `connect_or_create`-style schema (copy the fixture approach in `tests/test_org_sync_manifest.py`):
  - params: each 400 case raises `ValueError`; `days="all"` → `None`.
  - `pull_command` for all / org / server / nested `gitlab.example.com~acme~~platform` / a key with `;` (quoted) / `check=True`.
  - `repo_view`: page counts equal what `kgit pull --stale` would pull (same `is_stale`), including a repo with neither time (Review Focus 1).
  - `picker`: owner-less `go.googlesource.com` appears as a server with no org entry; nested label `acme/platform`.
  - `org_runs`: continue command keeps `-ssh` and drops `-inventory`; finished run → `None`; command with NULL `finished_at` → `"stopped or running"` (Review Focus 5); expiry 10 days out → `expiring`; DB without `0013` tables → `available is False` (Review Focus 3).
- [ ] **Step 2: Run; expect import failure.**
- [ ] **Step 3: Implement.** Read-only queries; check tables with `db[name].exists()`.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat(kweb): data for /syncs/`.

### Task 8: `/syncs/` route and template

**Files:**
- Modify: `src/kweb2.py` (new route near `/repos/`)
- Create: `src/templates/syncs.html` (model on `src/templates/repos.html`: `_header.html`, `_footer_scripts.html`, `_datatable_scripts.html`)
- Modify: `tests/test_web_endpoints.py` (add `/syncs/`, `/syncs/?tab=orgs`)
- Test: `tests/test_syncs_page.py`

**Interfaces:**
- Consumes: Task 7.
- Produces: `syncs(request: Request)` handler for `GET /syncs/`.

- [ ] **Step 1: Write the failing tests** calling `kweb2.syncs(request)` with a minimal `starlette.requests.Request({"type": "http", "method": "GET", "path": "/syncs/", "query_string": b"...", "headers": []})` and `KospexQuery` pointed at a temp DB (via `KOSPEX_HOME` + `HabitatConfig.reset_instance()`):
  - 200 with the summary `N of M`, and the command text from `pull_command`.
  - each invalid param → `HTTPException` 400.
  - a repo id `github.com~acme~<script>` renders escaped (`&lt;script&gt;`) (Review Focus 4).
  - a DB without `0013` tables, `?tab=orgs` → 200 with `kospex upgrade-db` in the body (Review Focus 3).
- [ ] **Step 2: Run; expect AttributeError (no `syncs`).**
- [ ] **Step 3: Implement the route** (`ValueError` from `parse_params` → `HTTPException(400, detail=str(e))`; re-raise `HTTPException` before the generic 500 handler, as other routes do) **and the template:** tabs as links carrying the query string; days buttons 1 day / 1 week / 30 days / all; the picker as a `<select>` with `<optgroup>` per server (type-to-filter is the browser's); command boxes with a copy button (`navigator.clipboard.writeText` of the element's text); repos table with DataTables, repo ids linking to `/repo/{repo_id}`; org runs table with a `<details>` per run for commands and listings; expiring tokens highlighted. Not added to `_header.html`, help or sitemap.
- [ ] **Step 4: Run the page tests; start kweb from this worktree's `src` and open `/syncs/` and `/syncs/?tab=orgs` against the dev DB; run the full suite with kweb up.**
- [ ] **Step 5: Commit** `feat(kweb): /syncs/ page, unlinked`.

### Task 9: Remove `/recent/`

**Files:**
- Modify: `src/kweb2.py` (delete the `/recent/` route), `tests/test_web_endpoints.py:73`, `tests/manual/web_crawl.py:57`, `docs/kweb/index.md:106,126`, `CHANGELOG.md`
- Delete: `src/templates/recent-syncs.html`

- [ ] **Step 1: Add `test_recent_is_gone`** to `tests/test_syncs_page.py`: no route in `kweb2.app.routes` has path `/recent/`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Remove the route, template and list entries; delete the doc row; reword line 126 to point at `kgit pull --check` (not `/syncs/`); CHANGELOG "Removed: the `/recent/` kweb page, a stub that always showed an empty table."** `grep -rn "recent-syncs\|/recent/" src tests docs` returns nothing.
- [ ] **Step 4: Run the full suite with kweb up (restart kweb first).**
- [ ] **Step 5: Commit** `chore(kweb): remove the /recent/ stub`.

### Task 10: Docs and live check

**Files:**
- Modify: `docs/kgit.md` (`kgit pull --stale`; "What each command records" with the token fields, that the token is never stored, and an SQL example listing a run's commands), `CHANGELOG.md` (Added: `--stale`, recorded commands; Changed: suggestions carry every option, `--check` ages by last refresh), `CLAUDE.md` (`kgit pull` and `kgit sync` lines), `changes/202611-kweb-syncs.md` (add an "Implementation" section: files, deviations, the header name found in Task 5)

- [ ] **Step 1: Write the docs.** Don't mention `/syncs/` outside `changes/`.
- [ ] **Step 2: Live check against a copy of the dev DB** (public repos only, `-no-auth`): `kgit pull --check --all --stale 30` count equals the page's "N of M" for 30 days; `kgit sync --org mergestat -no-auth -inventory -refresh` then `-next 1 -ssh` record two rows, shown on `?tab=orgs` with a continue command keeping `-ssh -no-auth`.
- [ ] **Step 3: Full suite with kweb up; quote passed/skipped.**
- [ ] **Step 4: Commit** `docs(kgit): pull --stale and recorded sync commands`.
