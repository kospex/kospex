# advisory-history Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the disabled `kospex advisory-history` stub with a command that shows recent stable releases of one package with their OSV advisories, a percentage summary (overall and per major), and the next release with no known advisories.

**Architecture:** deps.dev supplies the release list (`KospexDependencies.release_list()`); one OSV `querybatch` call per 1000 versions returns advisory IDs per version; `OsvClient.vuln()` (now wired to the url_cache) fetches each distinct record once so withdrawn and severity are known. All row, summary and next-clean logic is pure functions in `integrations/osv/queries.py`; the CLI only orchestrates and prints.

**Tech Stack:** Python 3.12, Click, Rich, requests, pytest. SQL is not touched.

**Spec:** `changes/202611-advisory-history.md`

## Global Constraints

- Work in a worktree; run tests with `PYTHONPATH=$PWD/src pytest` (editable install points at the main checkout).
- Third-party text (advisory titles, versions, package names from OSV/deps.dev) is printed with `markup=False` and through `KospexUtils.terminal_safe()` (CWE-150).
- No DB writes, no new dependency, no migration.
- SQLite is not involved; nothing newer than 3.26 to worry about.
- User-facing text says "no known advisories", never "safe" or "clean". The JSON field is `next_clean`.
- "No known advisories" = OSV lists no non-withdrawn advisory for that release, any severity.
- Unknown is never zero: `?` in the table, `null` in JSON.
- Order by version number (`KospexDependencies._version_order_key`), never publish date.
- Name no private repo, commercial product or customer in any code, test, doc or commit message (the CLAUDE.md rules).
- Do not touch `extract_purl` or close #244. The PR uses `Refs #282`, not `Fixes`: #282's purl task is deliberately deferred, so the issue stays open.
- Commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; no session URLs.
- CHANGELOG entry goes in the feature PR under `## Unreleased`.

## Review Focus

Each line gets a test in the task named in brackets.

1. A package deps.dev has no record of: exit 1 with one line, no OSV call. [Task 3]
2. `@VERSION` that is not in deps.dev's list: usage error naming it, not a silent window from the latest. [Task 3]
3. Scoped npm (`@babel/core`) and Maven (`group:artifact`) names reach OSV intact and print without crashing; this is what killed the old command. [Task 3]
4. A major where every release is affected: `100%` for that major, and `next_clean` is the first later release without advisories, or "no release newer than X has no known advisories". [Task 2]
5. A package whose releases are all pre-releases: summary says there are no stable releases, no division by zero. [Task 2]
6. One `vuln()` record fails to fetch: only releases citing that ID become unknown; the rest keep their counts. [Task 3]

---

### Task 1: `OsvClient.querybatch`

**Files:**
- Modify: `src/kospex/integrations/osv/client.py` (add method after `query`)
- Test: `tests/test_osv_client.py` (add `class TestQuerybatch`, reusing `make`, `FakeResponse`)

**Interfaces:**
- Produces: `OsvClient.querybatch(self, ecosystem: str, name: str, versions: list[str]) -> dict[str, list[str] | None]`. Keys are the versions passed in. Value is the advisory IDs OSV returned for that version, `[]` when none, `None` when a follow-up page for that version failed. Raises `OsvError` when a batch request itself fails.
- Constant `BATCH_SIZE = 1000` in `client.py`.

- [ ] **Step 1: Write failing tests** in `TestQuerybatch`:
  - `test_posts_one_query_per_version`: two versions, one 200 response `{"results": [{"vulns": [{"id": "GHSA-a", "modified": "x"}]}, {}]}`; assert one request to `/querybatch`, body `{"queries": [{"package": {"name": N, "ecosystem": E}, "version": "1.0"}, ...]}` in input order, and result `{"1.0": ["GHSA-a"], "2.0": []}`.
  - `test_chunks_at_batch_size`: monkeypatch `client.BATCH_SIZE` to 2, pass 5 versions, assert 3 requests of sizes 2, 2, 1 and all 5 keys returned.
  - `test_follows_next_page_token_per_query`: first result carries `"next_page_token": "t"`; assert a second `/querybatch` request holding only that query with `page_token: "t"`, and its IDs appended.
  - `test_failed_followup_page_gives_none_for_that_version_only`: the follow-up returns 500 three times (use the existing retry fakes) after the first page succeeded; that version is `None`, the other keeps its IDs.
  - `test_whole_batch_failure_raises`: a 500 on the first request raises `OsvError`.
  - `test_empty_versions_makes_no_request`.
- [ ] **Step 2: Run** `PYTHONPATH=$PWD/src pytest tests/test_osv_client.py -k Querybatch -v`. Expected: FAIL, `no attribute 'querybatch'`.
- [ ] **Step 3: Implement** `querybatch` using `self._request("POST", "/querybatch", json=...)`. Per-query paging: after each chunk, re-send only queries that returned `next_page_token`, bounded by `MAX_PAGES`; an `OsvError` on a follow-up marks only those versions `None`.
- [ ] **Step 4: Run** the same command. Expected: all `TestQuerybatch` pass; then `pytest tests/test_osv_client.py` fully passes.
- [ ] **Step 5: Commit** `git add src/kospex/integrations/osv/client.py tests/test_osv_client.py` — "osv: add querybatch to OsvClient".

---

### Task 2: Pure history logic

**Files:**
- Modify: `src/kospex/integrations/osv/queries.py` (append)
- Test: `tests/test_osv_history.py` (new)

**Interfaces:**
- Consumes: release dicts as returned by `release_list()` (`version`, `published_at`, `is_default`, `is_prerelease`, `is_deprecated`); `advisory_row()`, `severity()` and `_RANK` already in `queries.py`; an `order_key(version) -> sortable` callable passed in by the caller (in production `lambda v: KospexDependencies._version_order_key(system, v)`).
- Produces (all in `queries.py`):
  - `osv_version(ecosystem: str, version: str) -> str`: Go versions lose a leading `v`; everything else unchanged.
  - `select_window(releases, start_version, limit, include_pre, order_key) -> dict` returning `{"rows": [release, ...], "skipped_prereleases": int, "start": release}`. `rows` start at `start_version` and go back `limit` releases by `order_key` descending. Excludes pre-releases unless `include_pre`, except `start` itself is always kept. Raises `ValueError(version)` when `start_version` is not in `releases`.
  - `release_rows(releases, ids_by_version, records, ecosystem, name, current_version) -> list[dict]`. One dict per release with keys `version, published_at, advisory_count, advisory_ids, worst_severity, is_current, is_deprecated, is_prerelease`. `ids_by_version[v]` is `None` -> count and ids `None` (unknown). `records` is `{id: record | None}`; a record that is `None` (fetch failed) makes every release citing it unknown; a withdrawn record is dropped from count and ids; unknown wins over a known count. `worst_severity` is the highest-ranked label among counted advisories, `""` when none.
  - `summarise(rows, include_pre) -> dict`: `{"releases": n, "affected": n, "unknown": n, "percent": float | None, "by_major": [{"major": "3", "releases": n, "affected": n}, ...]}`. Counts stable releases only unless `include_pre`; unknown rows are counted in `unknown` and excluded from `releases`/`affected`/`percent`; `percent` is `None` when `releases == 0`. Major is the version's text before the first `.` after stripping a leading `v`; `by_major` is sorted by major numerically where it parses, then as text.
  - `next_clean(rows, start_version, order_key) -> dict`: `{"status": "start_clean" | "found" | "none", "release": row | None, "skipped_unknown": [versions]}`. Considers stable, non-deprecated rows with `order_key` greater than the start's. `found` is the lowest such row with `advisory_count == 0`; unknown rows are skipped and listed in `skipped_unknown`, never returned. `start_clean` when the start row itself has `advisory_count == 0`.

- [ ] **Step 1: Write failing tests** (`tests/test_osv_history.py`, plain dict fixtures, no network):
  - `test_osv_version_strips_v_for_go_only`: `("Go","v1.8.0") -> "1.8.0"`, `("npm","v1.0.0")` unchanged.
  - `test_window_goes_back_by_version_number_not_publish_date`: releases 2.18.0 (published 2021-01), 2.17.1 (published 2022-01, a backport), 2.17.0; start 2.18.0, limit 2 -> versions `["2.18.0", "2.17.1"]`.
  - `test_window_skips_prereleases_and_counts_them`; `test_window_keeps_prerelease_start`; `test_window_include_pre`; `test_window_unknown_start_raises_valueerror`.
  - `test_rows_drop_withdrawn_and_mark_unknown`: a release citing only a withdrawn record gets count 0; one whose id has `None` record gets `None`/`None`; one with `ids_by_version[v] is None` gets `None`.
  - `test_rows_worst_severity` using records with `database_specific.severity` as the existing `severity()` reads them (copy a fixture shape from `tests/test_osv_queries.py`).
  - `test_summary_percent_and_by_major`: 4 stable releases in 3.x (2 affected) and 2 in 2.x (2 affected) -> `percent == 4/6*100`, `by_major == [{"major":"2","releases":2,"affected":2},{"major":"3","releases":4,"affected":2}]`; **a major where every release is affected is 100%** (Review Focus 4).
  - `test_summary_no_stable_releases`: all pre-release -> `releases == 0`, `percent is None`, no exception (Review Focus 5).
  - `test_summary_unknown_excluded_from_percent`.
  - `test_next_clean_found_skips_unknown_and_deprecated`, `test_next_clean_none_when_every_later_release_affected`, `test_next_clean_start_clean`, `test_next_clean_ignores_older_releases`.
- [ ] **Step 2: Run** `PYTHONPATH=$PWD/src pytest tests/test_osv_history.py -v`. Expected: FAIL on import.
- [ ] **Step 3: Implement** the five functions above in `queries.py`. Reuse `severity()`, `_RANK`; do not duplicate withdrawn detection (`record.get("withdrawn")`).
- [ ] **Step 4: Run** the same command plus `pytest tests/test_osv_queries.py`. Expected: all pass.
- [ ] **Step 5: Commit** `git add src/kospex/integrations/osv/queries.py tests/test_osv_history.py` — "osv: pure functions for advisory history".

---

### Task 3: The command

**Files:**
- Modify: `src/kospex_cli.py` — replace `ADVISORY_HISTORY_NOTICE` and the `advisory_history` stub (around line 2043); add helpers beside `_print_osv_section`.
- Delete: `tests/test_advisory_history_stub.py`
- Test: `tests/test_advisory_history.py` (new; reuse the `run` fixture pattern, `FakeOsv` and `entry()` style from `tests/test_sca_package_osv.py`, with `FakeOsv` gaining `querybatch(ecosystem, name, versions)` and `vuln(id)`)

**Interfaces:**
- Consumes: Task 1 `querybatch`, `OsvClient.vuln`; Task 2 functions; `_split_package_version`, `package_types()`, `kospex.dependencies.lookup_permitted(type, name)`, `kospex.dependencies.release_list(type, name)`, `KospexDependencies._latest_release(type, releases)`, `osv_queries.ecosystem_for(type)`.
- Produces: `kospex advisory-history [-ecosystem E] -package SPEC [-limit N=20] [-pre] [-json]`. `SPEC` is `TYPE:NAME[@VERSION]`, or `NAME[@VERSION]` when `-ecosystem` is given. Helper `_advisory_history_data(package_type, package_name, start_version, limit, include_pre) -> dict` returns the JSON document below; the printer and `-json` both consume it.
- JSON document (stdout only for `-json`): `{"package": {"type", "name"}, "start": version, "limit": N, "include_prereleases": bool, "skipped_prereleases": n, "summary": <summarise()>, "next_clean": {"status", "version", "published_at", "skipped_unknown"}, "versions": [<release_rows row>...window only], "sources": {"deps_dev": "<package URL>", "osv": "https://api.osv.dev/v1/querybatch"}}`.

Flow of `_advisory_history_data`: ecosystem via `ecosystem_for` (None -> exit 1 "OSV has no ecosystem for X"); `lookup_permitted` (refuse as `sca` does); `release_list` (None -> `_explain_no_package_record`, exit 1, no OSV call); start = `@VERSION` or `_latest_release()`; `select_window`; query **all** considered releases (stable, plus pre if `-pre`, plus the start) with one `querybatch` using `osv_version()` and map back; fetch each distinct ID once through a `vuln()` client whose `cached_get` is `kospex.kospex_query.url_request_with_status` (so records are url_cached; this wiring does not exist yet); `OsvError` from `querybatch` -> print one line to stderr, exit 1; an `OsvError` from `vuln()` stores `None` for that ID.

- [ ] **Step 1: Write failing tests** (`tests/test_advisory_history.py`):
  - `test_requires_package`: no `-package` -> usage error, exit 2.
  - `test_package_spec_forms`: `-package pypi:requests@2.25.0` and `-ecosystem pypi -package requests@2.25.0` give the same start; an unknown type is a usage error listing `package_types()`.
  - `test_private_package_refused_without_network` (same message as `sca`).
  - `test_no_deps_dev_record_exits_1_without_osv_call` (Review Focus 1).
  - `test_unknown_start_version_is_usage_error_naming_it` (Review Focus 2).
  - `test_scoped_npm_and_maven_names_reach_osv_intact` parametrised over `npm:@babel/core` and `maven:org.apache.logging.log4j:log4j-core`; assert the `(ecosystem, name)` passed to `querybatch` equals the name unchanged, exit 0, table printed (Review Focus 3).
  - `test_go_versions_sent_without_v_and_mapped_back`.
  - `test_table_columns_and_unknown_marker`: output contains the version, date, count, `?` for an unknown release, notes `current`, `deprecated`.
  - `test_summary_and_next_clean_lines`: output contains `of` / `%` summary text, per-major lines, and `next release with no known advisories`; contains no word `safe` or `clean`.
  - `test_json_is_only_stdout_and_has_documented_keys`: `json.loads(result.stdout)` succeeds, top-level keys exactly as above, per-version keys exactly `version, published_at, advisory_count, advisory_ids, worst_severity, is_current, is_deprecated, is_prerelease`, unknown release has `advisory_count: null`.
  - `test_one_failed_vuln_fetch_marks_only_citing_releases_unknown` (Review Focus 6).
  - `test_querybatch_failure_exits_1_with_one_line_on_stderr`.
  - `test_third_party_text_is_not_markup`: a version/name containing `[red]x[/red]` prints literally.
  - `test_help_no_longer_says_redesigned`.
- [ ] **Step 2: Run** `PYTHONPATH=$PWD/src pytest tests/test_advisory_history.py -v`. Expected: FAIL (stub exits 1 with notice).
- [ ] **Step 3: Implement** the command and `_advisory_history_data` as described; delete the stub test and `ADVISORY_HISTORY_NOTICE`. Option `-limit` is an `IntRange(min=1)`; `-pre` and `-json` are flags with the same `-` single-dash style as neighbouring options. Table via Rich with `markup=False` and `KospexUtils.terminal_safe()` on every third-party string.
- [ ] **Step 4: Run** `PYTHONPATH=$PWD/src pytest tests/test_advisory_history.py tests/test_sca_package_osv.py tests/test_osv_client.py tests/test_osv_queries.py tests/test_osv_history.py -v`. Expected: all pass. Then run the full suite once: `PYTHONPATH=$PWD/src pytest -q` and report the pass/skip counts.
- [ ] **Step 5: Try it for real** (needs network): `kospex advisory-history -package maven:org.apache.logging.log4j:log4j-core`, `-package npm:@babel/core -limit 5`, `-package pypi:requests@2.25.0 -json | jq .next_clean`. Expect a table, a summary with a per-major line, and a next-release line; log4j-core should show most 2.x releases affected. Record what you saw in the PR description.
- [ ] **Step 6: Commit** `git add src/kospex_cli.py tests/test_advisory_history.py` and `git rm tests/test_advisory_history_stub.py` — "advisory-history: advisories per release from OSV, with next release without known advisories".

---

### Task 4: Docs and changelog

**Files:**
- Modify: `docs/kospex.md` (new `## advisory-history` section beside `sca`), `docs/commands.md` (link), `CHANGELOG.md` (`## Unreleased`, replacing nothing already shipped: the 0.2.0 "disabled" note stays as history), `changes/202611-osv-integration.md` (the "separate command" line and Decision 1: add "see `changes/202611-advisory-history.md`"), `CLAUDE.md` (Common Commands: one bullet for `kospex advisory-history`).
- Test: none; run `PYTHONPATH=$PWD/src pytest tests/test_docs*.py -q` if such tests exist (`ls tests | grep -i doc`).

- [ ] **Step 1: Write the docs section**: usage with the three examples from the spec, the options, an example table, the `-json` fields, "unknown is not zero", and the definition of "no known advisories" in the spec's words. State the limit: Maven ordering does not rank qualifiers (`_version_order_key` docstring), so `next_clean` among Maven releases with the same numbers is by string.
- [ ] **Step 2: CHANGELOG** entry: command is back; what it shows; `-package TYPE:NAME[@VERSION]`; the old `-ecosystem/-package NAME` form still works; fixes #282 crash on scoped npm/Maven (describe the mechanism, not a tracker). Do not say #244 is fixed.
- [ ] **Step 3: Verify** that nothing in the diff names a private repo, commercial product or customer: read `git diff main` for them.
- [ ] **Step 4: Commit** the doc paths only — "docs: advisory-history".

**PR description** (when asked to raise it): the three try-it commands from Task 3 Step 5, the pass/skip counts, `Refs #282` (purl input is a follow-up, so the issue stays open), and a note that #244 is unaffected.
