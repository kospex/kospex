# Developer Technology by Language Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The developer view lists technology by language, as the repo view does, instead of by file extension.

**Architecture:** A new `KospexQuery.author_languages()` reads an author's file changes and groups them in Python by the language panopticas gives each path. The two kweb handlers that render `developer_view.html` use it through one shared helper.

**Tech Stack:** Python 3.12, SQLite, panopticas, FastAPI, Jinja2, pytest

**Spec:** `changes/202611-developer-technology-by-language.md`

## Global Constraints

- SQL must run on SQLite 3.26 (`CAST(strftime('%s', x) AS INTEGER)`, never `unixepoch()`).
- Language is `Panopticas.get_language(file_path, skip_shebang=True)`; `Unknown` stays in the table and chart.
- Join `commit_files` to `commits` on both `_repo_id` and `hash`; filter on exact `author_email`.
- Do not change `author_tech()`, `developer_tech()`, the `/api` routes or the CLI.
- No schema change or migration. The `author_email` index belongs to #492.
- Work on a branch in a worktree. Run tests as `PYTHONPATH=$PWD/src pytest`.

## Review Focus

- A path panopticas cannot name, or a NULL `file_path`: counted as `Unknown`, no error (Task 1).
- An author with no file changes (unknown email, or only merges): the page renders with an empty table (Task 2).
- NULL `author_when`: the page still renders (Task 1).
- A `+` in a noreply email arriving as a space: still finds the author (Task 2).
- One file changed by many commits: one file, many commits (Task 1).

---

### Task 1: `KospexQuery.author_languages()`

**Files:**
- Modify: `src/kospex_query.py` (add after `author_tech`)
- Create: `tests/test_author_languages.py`

**Interfaces:**
- Produces: `author_languages(self, author_email: str) -> list[dict]`. One dict per language with `language: str`, `commits: int`, `files: int`, `repos: int`, `first_commit` / `last_commit: str | None` (`YYYY-MM-DDTHH:MM:SSZ`), `first_seen` / `last_seen: float` (from `KospexUtils.days_ago`), `days_active: int`, `years_active: str` (3 decimals). Sorted by `commits` descending, then `language`.

- [ ] **Step 1: Write the failing tests**

Fixture: an in-memory `sqlite_utils.Database` with `SQL_CREATE_COMMITS`, `SQL_CREATE_COMMIT_FILES` and `SQL_CREATE_REPOS`; a helper `_change(db, repo, hash, path, when, email="dev@example.com")` that inserts the commit (once per repo and hash) and the file row. Copy the env-preserving autouse fixture from `tests/test_landscape_scope.py`. Every test calls `KospexQuery(kospex_db=db).author_languages(...)`.

| Test | Data | Asserts |
|---|---|---|
| `test_commits_are_counted_once_per_language` | one commit: `a.py`, `b.py`, `c.html` | Python `commits == 1`, `files == 2`; HTML `commits == 1` |
| `test_one_file_in_many_commits` | `a.py` in three commits | `commits == 3`, `files == 1` |
| `test_shared_hash_in_two_repos_is_not_multiplied` | hash `h1` with `README.md` in `s~o~a` and `s~o~b` | Markdown `commits == 2`, `files == 2`, `repos == 2` |
| `test_path_no_longer_present_is_still_counted` | `old/gone.go`, no `file_metadata` table | one `Go` row |
| `test_unnameable_and_null_paths_are_unknown` | paths `noext`, `x.zzz`, `None` in separate commits | one `Unknown` row, `commits == 3` |
| `test_dates_compare_as_instants` | `a.py` at `2026-04-16T18:34:03+01:00` and `2026-04-16T16:59:54-04:00` | `first_commit == "2026-04-16T17:34:03Z"`, `last_commit == "2026-04-16T20:59:54Z"` |
| `test_null_author_when` | `a.py`, `when=None` | `last_commit is None`, `days_active == 0` |
| `test_only_the_exact_author` | a second author's `x.go` | no `Go` row; `author_languages("nobody@example.com") == []` |
| `test_ordered_by_commits` | two Go commits, one Python | languages `["Go", "Python"]` |

NULL `file_path` is only insertable if the primary key allows it; SQLite does for these tables.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_author_languages.py -v`
Expected: FAIL, `KospexQuery` has no attribute `author_languages`

- [ ] **Step 3: Implement `author_languages`**

One query selecting `_repo_id`, `hash`, `file_path` and epoch `author_when`, iterated without building a row list. Per language keep sets of `(_repo_id, hash)`, `(_repo_id, file_path)` and `_repo_id`, plus min and max epoch ignoring NULL. Cache `get_language` by path. A falsy `file_path` is `Unknown`. `days_active` is `int(first_seen) - int(last_seen)`, as in `author_tech`.

- [ ] **Step 4: Run to verify they pass**

Run: `PYTHONPATH=$PWD/src pytest tests/test_author_languages.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit** — `feat: author_languages query, an author's history by language`

---

### Task 2: Use it on the developer pages

**Files:**
- Modify: `src/kweb2.py` (`developers()` author branch; `developer_view()`)
- Modify: `src/templates/developer_view.html` (Technology View table)
- Modify: `CHANGELOG.md`
- Modify: `changes/202611-developer-technology-by-language.md` (status line: built)
- Test: `tests/test_author_languages.py`

**Interfaces:**
- Consumes: `KospexQuery.author_languages(author_email)` from Task 1.
- Produces: `_developer_technology(author_email: str) -> tuple[list[dict], list[str], list[int]]` in `kweb2.py`: rows, radar labels, radar datapoints (`commits`), the chart taking the first 11 rows as today.

- [ ] **Step 1: Write the failing handler tests**

Monkeypatch `kweb2.KospexQuery` to return `KospexQuery(kospex_db=db)`, build a `starlette.requests.Request` as `tests/test_landscape_scope.py` does, and call both `kweb2.developers` and `kweb2.developer_view` (parametrise).

- `test_page_shows_languages_and_file_count`: status 200; body contains `Python` and `# Files`; the Technology table has no `.py` cell.
- `test_author_with_no_changes_renders`: `author_email=nobody@example.com`, status 200.
- `test_plus_in_email_survives`: author `1+dev@users.noreply.github.com`, query string with an unencoded `+`; body contains `Python`.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=$PWD/src pytest tests/test_author_languages.py -v`
Expected: the language test FAILS for both handlers

- [ ] **Step 3: Implement**

Add `_developer_technology` and call it from both handlers in place of `author_tech` and the label loop. In the template show `row['language']` and add a right-aligned `# Files` column (`row['files']`) after `# Commits`. The DataTable default sort stays on column 1.

CHANGELOG, under a new `## Unreleased` heading, `### Changed`: the developer view lists technology by language rather than file extension, "# Commits" now counts commits rather than file changes, and "# Files" is new. The `/api` routes and `kospex author-tech` are unchanged.

- [ ] **Step 4: Full suite with a live kweb**

Start this branch's kweb on a free port (check with `lsof`; port 8000 serves the main checkout) and point the web tests' `BASE_URL` at it.
Run: `PYTHONPATH=$PWD/src pytest -rs`
Expected: no failures, and `test_web_endpoints.py` not skipped. Quote the skip count.

- [ ] **Step 5: Check the real page**

On that kweb, open `/developers/?author_email=…` and `/developer/?author_email=…` for a developer in the local DB. Expected: language names matching the repo view, the radar chart drawn, load time much as before.

- [ ] **Step 6: Commit** — `feat: developer view lists technology by language`
