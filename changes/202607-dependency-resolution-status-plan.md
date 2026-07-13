# Dependency Resolution Status — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Categorise *why* a deps.dev lookup failed (five categories + `resolved`) as a queryable `dependency_data.resolution` column + a disk log, normalise `versions_behind` to int-or-NULL, and show the category on `/dependencies/`.

**Architecture:** Fold classification into the shared `depsdev_record` seam (krunner osi + pypi/npm assess all call it). A status-aware deps.dev fetch (new `url_request_with_status`) lets us split 404 from transient errors, and a cached package-level fallback splits `package_not_found` from `version_yanked`. A pre-call `is_concrete_version` check catches specs before hitting the API.

**Tech Stack:** Python 3.12, sqlite_utils, requests, `packaging`, Click, Jinja2, pytest.

**Spec:** `changes/202607-dependency-resolution-status.md`. **Depends on PR #105** (the `/dependencies/` template guard) — the badge task (Task 6) produces the final template regardless, but land #105 first and rebase to avoid a line-90 conflict. Run tests with `PYTHONPATH=$PWD/src python -m pytest ... -p no:cacheprovider` from the worktree.

---

## File structure

- `src/kospex/db/migrations/0005_dependency_data_resolution.sql` — new: `resolution` column.
- `src/kospex_query.py` — new `url_request_with_status()` (status-aware fetch).
- `src/kospex_dependencies.py` — new `is_concrete_version()`, `deps_dev_status()`, `_classify_lookup_miss()`; classification folded into `depsdev_record()`.
- `src/krunner.py` — osi loop reads `resolution`/`versions_behind` from the record.
- `src/templates/dependencies.html` — resolution badge.
- Tests: `tests/test_resolution_status.py` (classifier + helpers), extend `tests/test_db_migrator.py`, `tests/test_dependencies_template.py`.

Reference decision tree (spec): empty→`no_version`; not concrete→`unresolved_spec`; deps.dev 200→`resolved`(+int); 404 & package exists→`version_yanked`; 404 & package missing→`package_not_found`; else→`lookup_error`.

---

## Task 1: Migration 0005 — `dependency_data.resolution`

**Files:** Create `src/kospex/db/migrations/0005_dependency_data_resolution.sql`; Test: append to `tests/test_db_migrator.py`.

- [ ] **Step 1: Failing test.** Append to `tests/test_db_migrator.py`:
```python
def test_shipped_0005_adds_dependency_data_resolution(tmp_path):
    import sqlite_utils
    import kospex_schema as KospexSchema
    from kospex.db.migrator import Migrator
    db = sqlite_utils.Database(tmp_path / "kospex.db")
    db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
    db.execute(
        "CREATE TABLE schema_migrations ("
        "id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, checksum TEXT NOT NULL, "
        "applied_at TEXT NOT NULL, duration_ms INTEGER, has_python INTEGER NOT NULL)"
    )
    Migrator(db).apply_pending()
    cols = {r[1] for r in db.execute("PRAGMA table_info(dependency_data)")}
    assert "resolution" in cols
```
- [ ] **Step 2: Run, verify FAIL.** `PYTHONPATH=$PWD/src python -m pytest tests/test_db_migrator.py::test_shipped_0005_adds_dependency_data_resolution -v -p no:cacheprovider`
- [ ] **Step 3: Create migration** `src/kospex/db/migrations/0005_dependency_data_resolution.sql`:
```sql
-- 0005_dependency_data_resolution.sql
--
-- Category for why a deps.dev version lookup resolved or failed: resolved,
-- no_version, unresolved_spec, version_yanked, package_not_found, lookup_error.
-- NULL means a legacy row not yet classified. Pairs with versions_behind, which
-- is an integer when resolved and NULL otherwise.

ALTER TABLE dependency_data ADD COLUMN resolution TEXT;
```
(Keep the comment free of `;` — the runner splits on `;`.)
- [ ] **Step 4: Run, verify PASS.** Same command. Also `PYTHONPATH=$PWD/src python -m pytest tests/test_db_migrator.py -q -p no:cacheprovider`.
- [ ] **Step 5: Commit.** `git add src/kospex/db/migrations/0005_dependency_data_resolution.sql tests/test_db_migrator.py && git commit -m "feat(db): 0005 add dependency_data.resolution"`

---

## Task 2: `KospexQuery.url_request_with_status`

Status-aware fetch: returns `(content, status)`. Caches 200 bodies (like `url_request`); returns the HTTP status on failure so callers can categorise.

**Files:** Modify `src/kospex_query.py` (add near `url_request`); Test: create `tests/test_resolution_status.py`.

- [ ] **Step 1: Failing test.** Create `tests/test_resolution_status.py`:
```python
"""Tests for dependency resolution-status classification."""
import sqlite_utils
import kospex_schema as KospexSchema
from kospex_query import KospexQuery


def _kq():
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_URL_CACHE)
    return KospexQuery(kospex_db=db)


def test_url_request_with_status_200_returns_body_and_caches(monkeypatch):
    kq = _kq()

    class _Resp:
        status_code = 200
        text = '{"ok": true}'
        def raise_for_status(self): pass

    monkeypatch.setattr("kospex_query.requests.get", lambda *a, **k: _Resp())
    content, status = kq.url_request_with_status("http://x/pkg")
    assert status == 200 and content == '{"ok": true}'
    # cached: a second call with requests.get patched to blow up still returns it
    monkeypatch.setattr("kospex_query.requests.get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should be cached")))
    content2, status2 = kq.url_request_with_status("http://x/pkg")
    assert status2 == 200 and content2 == '{"ok": true}'


def test_url_request_with_status_404(monkeypatch):
    import requests
    kq = _kq()

    class _Resp:
        status_code = 404
        text = "not found"
        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    monkeypatch.setattr("kospex_query.requests.get", lambda *a, **k: _Resp())
    content, status = kq.url_request_with_status("http://x/missing")
    assert content is None and status == 404


def test_url_request_with_status_network_error(monkeypatch):
    import requests
    kq = _kq()
    def _boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr("kospex_query.requests.get", _boom)
    content, status = kq.url_request_with_status("http://x/err")
    assert content is None and status is None
```
- [ ] **Step 2: Run, verify FAIL** (AttributeError, no `url_request_with_status`). `PYTHONPATH=$PWD/src python -m pytest tests/test_resolution_status.py -k url_request_with_status -v -p no:cacheprovider`. Confirm `requests` is imported in `kospex_query.py` (add `import requests` if missing) and `SQL_CREATE_URL_CACHE` exists in kospex_schema.
- [ ] **Step 3: Implement.** Add to `src/kospex_query.py`:
```python
    def url_request_with_status(self, url, cache=3600, timeout=10, headers=None):
        """Like url_request, but returns (content, status). status is 200 on a
        cache hit or fresh success, the HTTP status on an HTTP error, or None on
        a network/timeout error. 200 responses are cached; failures are not."""
        cache_sql = f"SELECT content, timestamp FROM {KospexSchema.TBL_URL_CACHE} WHERE url = ?"
        result = next(self.kospex_db.query(cache_sql, (url,)), None)
        if result and (time.time() - result["timestamp"]) < cache:
            return result["content"], 200
        try:
            response = requests.get(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            content = response.text
            self.kospex_db.table(KospexSchema.TBL_URL_CACHE).upsert(
                {"url": url, "content": content, "timestamp": int(time.time())}, pk=["url"]
            )
            return content, 200
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            return None, status
        except requests.RequestException:
            return None, None
```
- [ ] **Step 4: Run, verify PASS.** Same `-k url_request_with_status` command.
- [ ] **Step 5: Commit.** `git add src/kospex_query.py tests/test_resolution_status.py && git commit -m "feat(query): url_request_with_status for status-aware deps.dev fetch"`

---

## Task 3: `is_concrete_version`

**Files:** Modify `src/kospex_dependencies.py`; Test: append to `tests/test_resolution_status.py`.

- [ ] **Step 1: Failing test.** Append:
```python
import pytest
from kospex_dependencies import KospexDependencies


@pytest.mark.parametrize("v,expected", [
    ("1.2.3", True), ("0.0.16", True), ("2.0.0rc1", True), ("1.2.3-beta.1", True),
    ("", False), (None, False), ("^4.4.0", False), (">=1.0,<2.0", False),
    ("~1.2", False), ("1.x", False), ("latest", False), ("*", False),
    ("workspace:*", False), ("https://github.com/o/r", False), ("git+https://x", False),
])
def test_is_concrete_version(v, expected):
    assert KospexDependencies().is_concrete_version(v) is expected
```
- [ ] **Step 2: Run, verify FAIL.** `PYTHONPATH=$PWD/src python -m pytest tests/test_resolution_status.py -k is_concrete_version -v -p no:cacheprovider`
- [ ] **Step 3: Implement.** Add to `KospexDependencies` in `src/kospex_dependencies.py`:
```python
    # Markers that make a version string a range/spec/reference, not a concrete version.
    _SPEC_MARKERS = ("^", "~", ">", "<", "=", "*", "|", " - ", "://", "workspace:",
                     "file:", "link:", "git+", "portal:")

    def is_concrete_version(self, version):
        """True if `version` is a single concrete version (e.g. 1.2.3), not a
        range/spec/URL/keyword. Used to skip deps.dev calls that would 404 on a
        spec and to categorise them as unresolved_spec."""
        if not version or not isinstance(version, str):
            return False
        v = version.strip()
        if not v or v.lower() in ("latest", "next", "*", "x"):
            return False
        if any(m in v for m in self._SPEC_MARKERS):
            return False
        from packaging.version import Version, InvalidVersion
        try:
            Version(v)
            return True
        except InvalidVersion:
            # Accept npm-ish concrete versions packaging can't parse (e.g. 1.2.3-beta.1)
            return bool(re.match(r"^\d+(\.\d+){0,3}([-+][0-9A-Za-z.-]+)?$", v))
```
Confirm `import re` is present at the top of `kospex_dependencies.py` (it is; verify).
- [ ] **Step 4: Run, verify PASS.** Same `-k is_concrete_version` command (all params pass).
- [ ] **Step 5: Commit.** `git add src/kospex_dependencies.py tests/test_resolution_status.py && git commit -m "feat(deps): is_concrete_version helper"`

---

## Task 4: `deps_dev_status` + classification in `depsdev_record`

The core. `deps_dev_status` does the status-aware exact-version lookup; the classification lives in `depsdev_record` so both krunner osi and assess get it.

**Files:** Modify `src/kospex_dependencies.py`; Test: append to `tests/test_resolution_status.py`.

- [ ] **Step 1: Failing test.** Append:
```python
class _StubQuery:
    """Stub KospexQuery: url_request_with_status returns queued (content, status)."""
    def __init__(self, version_result, package_result=None):
        self._version = version_result      # (content, status) for the exact version
        self._package = package_result      # (content, status) for the package-level call
    def url_request_with_status(self, url, **kw):
        return self._package if "/versions/" not in url else self._version


def _deps(version_result, package_result=None):
    kd = KospexDependencies(kospex_query=_StubQuery(version_result, package_result))
    return kd


def test_depsdev_record_no_version():
    rec = _deps((None, None)).depsdev_record("pypi", "foo", "")
    assert rec["resolution"] == "no_version" and rec["versions_behind"] is None


def test_depsdev_record_unresolved_spec():
    rec = _deps((None, None)).depsdev_record("npm", "chartjs", "^4.4.0")
    assert rec["resolution"] == "unresolved_spec" and rec["versions_behind"] is None


def test_depsdev_record_package_not_found():
    # exact version 404, package-level also 404
    rec = _deps(version_result=(None, 404), package_result=(None, 404)).depsdev_record(
        "pypi", "nope-typo", "1.0.0")
    assert rec["resolution"] == "package_not_found" and rec["versions_behind"] is None


def test_depsdev_record_version_yanked():
    # exact version 404, but package exists
    rec = _deps(version_result=(None, 404), package_result=('{"versions": []}', 200)).depsdev_record(
        "pypi", "realpkg", "9.9.9")
    assert rec["resolution"] == "version_yanked" and rec["versions_behind"] is None


def test_depsdev_record_lookup_error():
    rec = _deps(version_result=(None, None)).depsdev_record("pypi", "x", "1.0.0")
    assert rec["resolution"] == "lookup_error" and rec["versions_behind"] is None
```
- [ ] **Step 2: Run, verify FAIL.** `PYTHONPATH=$PWD/src python -m pytest tests/test_resolution_status.py -k depsdev_record -v -p no:cacheprovider` (KeyError `resolution` / wrong values).
- [ ] **Step 3: Implement.** In `src/kospex_dependencies.py`, add the two helpers and rework `depsdev_record`'s lookup + failure path. Add:
```python
    def deps_dev_status(self, package_type, package_name, version):
        """Exact-version deps.dev lookup returning (data|None, status)."""
        encoded = urllib.parse.quote(package_name, safe="")
        url = (f"https://api.deps.dev/v3alpha/systems/{package_type}"
               f"/packages/{encoded}/versions/{version}")
        content, status = self.kospex_query.url_request_with_status(url)
        data = json.loads(content) if content else None
        return data, status

    def _package_exists(self, package_type, package_name):
        """True if the package (any version) is known to deps.dev."""
        encoded = urllib.parse.quote(package_name, safe="")
        url = f"https://api.deps.dev/v3alpha/systems/{package_type}/packages/{encoded}"
        content, status = self.kospex_query.url_request_with_status(url)
        return bool(content)

    def _classify_lookup_miss(self, package_type, package_name, status):
        """Category for a failed exact-version lookup given its HTTP status."""
        if status == 404:
            return "version_yanked" if self._package_exists(package_type, package_name) \
                else "package_not_found"
        return "lookup_error"
```
Then modify `depsdev_record` — replace the current `deps_info = self.deps_dev(...)` + early-return block with the classified version, and set `resolution`/`versions_behind` throughout:
```python
    def depsdev_record(self, package_type, package_name, package_version):
        """Convert a deps.dev package info record into a dictionary with other
        metadata, including a `resolution` category and int-or-None versions_behind."""
        details = {"package_name": package_name, "package_version": package_version,
                   "package_type": package_type, "versions_behind": None}
        today = datetime.datetime.now(datetime.timezone.utc)

        if not package_version:
            details["resolution"] = "no_version"
            return details
        if not self.is_concrete_version(package_version):
            details["resolution"] = "unresolved_spec"
            return details

        deps_info, status = self.deps_dev_status(package_type, package_name, package_version)
        if not deps_info:
            details["resolution"] = self._classify_lookup_miss(package_type, package_name, status)
            return details

        # resolved
        details["resolution"] = "resolved"
        pub_date = deps_info.get("publishedAt")
        if pub_date:
            details["days_ago"] = (today - dateutil.parser.isoparse(pub_date)).days
        details["published_at"] = pub_date
        details["default"] = deps_info.get("isDefault")
        details["source_repo"] = KospexUtils.extract_git_url(self.get_source_repo_info(deps_info))
        details["advisories"] = self.get_advisories_count(deps_info)
        days_info = self.get_versions_behind(package_type, package_name, package_version)
        details["versions_behind"] = days_info.get("versions_behind")
        details["authors"] = None
        if details["source_repo"]:
            details["authors"] = self.get_repo_authors(details["source_repo"])
        return details
```
Note: the old `deps_dev()` method stays (other callers may use it); `depsdev_record` now uses `deps_dev_status`. `get_versions_behind` still runs on the resolved path (it does its own package-level fetch to count — unchanged).
- [ ] **Step 4: Run, verify PASS.** `PYTHONPATH=$PWD/src python -m pytest tests/test_resolution_status.py -p no:cacheprovider` (all classifier tests).
- [ ] **Step 5: Commit.** `git add src/kospex_dependencies.py tests/test_resolution_status.py && git commit -m "feat(deps): classify version lookups in depsdev_record (resolution + int/None versions_behind)"`

---

## Task 5: Carry `resolution` to the DB (krunner osi + save)

**Files:** Modify `src/krunner.py` (osi enrichment loop) and confirm `src/kospex_dependencies.py` `save_dependencies`/assess persist `resolution`; Test: extend `tests/test_dependencies_save.py`.

- [ ] **Step 1: Read the osi enrichment loop** in `src/krunner.py` (the block that sets `d["versions_behind"] = deps_rec.get("versions_behind", "Unknown")` and `d["advisories"] = ...`). Replace the versions_behind default and add resolution:
```python
        d["versions_behind"] = deps_rec.get("versions_behind")   # int or None, no "Unknown"
        d["advisories"] = deps_rec.get("advisories")
        d["resolution"] = deps_rec.get("resolution")
```
(Leave `published_at`/`package_type`/`package_use` handling as-is.)
- [ ] **Step 1b: Audit the assess() ecosystem paths.** `assess()` saves the records its per-ecosystem assessors build. Confirm each routes version enrichment through `depsdev_record` (which now sets `resolution` — Task 4), so `resolution` is present on the saved record:
  - `npm_assess` → `get_npm_dependency_dict` → `depsdev_record` ✓ (already routes through it).
  - pyproject/pip path → `pypi_assess2` → `depsdev_record` ✓.
  - `gomod_assess`, `nuget_assess`, pnpm path — grep each for where it sets `versions_behind` / calls deps.dev. If it routes through `depsdev_record`, no change. If it sets `versions_behind = ""`/`"Unknown"` itself (e.g. via `get_package_template`) without `depsdev_record`, either point that enrichment at `depsdev_record`, or — if its path is materially different — leave it as-is and add a one-line note here that it's a follow-up (its rows keep `resolution` NULL and render via the legacy branch). Do NOT half-wire it.
- [ ] **Step 2: Confirm the DB writer keeps `resolution`.** `save_dependencies` (`src/kospex_dependencies.py`) upserts the row dicts; `resolution` is a plain column added by 0005, so it flows through. Verify `_NON_SCHEMA_FIELDS` (the strip-list) does NOT include `resolution` (it must be persisted). Add a test — append to `tests/test_dependencies_save.py`:
```python
def test_save_dependencies_persists_resolution():
    import sqlite_utils, kospex_schema as KospexSchema
    from kospex_dependencies import KospexDependencies
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
    db.execute("ALTER TABLE dependency_data ADD COLUMN resolution TEXT")
    kd = KospexDependencies(kospex_db=db)
    kd.save_dependencies([{
        "_repo_id": "s~o~r", "hash": "h", "file_path": "req.txt", "package_type": "pypi",
        "package_name": "foo", "package_version": "^1.0",
        "versions_behind": None, "resolution": "unresolved_spec",
    }], source="krunner osi")
    row = next(db.query("SELECT resolution, versions_behind FROM dependency_data"))
    assert row["resolution"] == "unresolved_spec" and row["versions_behind"] is None
```
- [ ] **Step 3: Run, verify PASS** (or fix if `resolution` is stripped). `PYTHONPATH=$PWD/src python -m pytest tests/test_dependencies_save.py -p no:cacheprovider -q`
- [ ] **Step 4: Log failures.** In `src/kospex_dependencies.py`, at the end of `depsdev_record` before each failure `return`, log via the module/instance logger (add `log = KospexUtils.get_kospex_logger("kospex")` at module top if absent, or reuse an existing logger). Minimal: one INFO line per non-resolved outcome:
```python
        # (in each failure branch, before return)
        log.info(f"deps.dev unresolved [{details['resolution']}] {package_type} "
                 f"{package_name} {package_version!r}")
```
Verify no test asserts silence; run `tests/test_resolution_status.py` again — still green.
- [ ] **Step 5: Commit.** `git add src/krunner.py src/kospex_dependencies.py tests/test_dependencies_save.py && git commit -m "feat(deps): persist + log resolution; drop 'Unknown' versions_behind"`

---

## Task 6: `/dependencies/` resolution badge

**Files:** Modify `src/templates/dependencies.html` (the versions-behind cell); Test: extend `tests/test_dependencies_template.py`.

- [ ] **Step 1: Failing test.** Append to `tests/test_dependencies_template.py`:
```python
def test_dependencies_shows_resolution_category():
    rows = [
        {"package_name": "a", "package_type": "pypi", "package_version": "1", "resolution": "resolved", "versions_behind": 3},
        {"package_name": "b", "package_type": "npm", "package_version": "^1", "resolution": "unresolved_spec", "versions_behind": None},
        {"package_name": "c", "package_type": "pypi", "package_version": "9", "resolution": "package_not_found", "versions_behind": None},
        {"package_name": "d", "package_type": "pypi", "package_version": "0", "resolution": "resolved", "versions_behind": 0},
    ]
    html = _render(rows)   # helper already in this file
    assert "3" in html and "Up to date" in html
    assert "unresolved spec" in html.lower()
    assert "not found" in html.lower()
```
- [ ] **Step 2: Run, verify FAIL.** `PYTHONPATH=$PWD/src python -m pytest tests/test_dependencies_template.py -k resolution_category -v -p no:cacheprovider`
- [ ] **Step 3: Implement.** Replace the versions-behind cell block in `src/templates/dependencies.html` (the `{% if row['versions_behind'] ... %}` block, ~line 89-99) with a 3-way that prefers a failure `resolution`:
```jinja
                                    <td class="px-6 py-4 whitespace-nowrap text-sm text-right">
                                        {% set _res = row['resolution'] %}
                                        {% set _labels = {'unresolved_spec': 'unresolved spec', 'version_yanked': 'yanked', 'package_not_found': 'not found', 'no_version': 'no version', 'lookup_error': 'lookup error'} %}
                                        {% if _res in _labels %}
                                            <span class="inline-flex px-2 py-1 text-xs font-semibold rounded-full bg-gray-100 text-gray-700">
                                                {{ _labels[_res] }}
                                            </span>
                                        {% elif row['versions_behind'] is number and row['versions_behind'] > 0 %}
                                            <span class="inline-flex px-2 py-1 text-xs font-semibold rounded-full bg-orange-100 text-orange-800">
                                                {{ row['versions_behind'] }}
                                            </span>
                                        {% else %}
                                            <span class="inline-flex px-2 py-1 text-xs font-semibold rounded-full bg-green-100 text-green-800">
                                                Up to date
                                            </span>
                                        {% endif %}
                                    </td>
```
(This subsumes the PR #105 `is number` guard. Legacy rows — `resolution` NULL — fall to the numeric/`Up to date` branch, rendering as they do post-#105.)
- [ ] **Step 4: Run, verify PASS.** The new test AND the #105 tests: `PYTHONPATH=$PWD/src python -m pytest tests/test_dependencies_template.py -p no:cacheprovider -q`
- [ ] **Step 5: Commit.** `git add src/templates/dependencies.html tests/test_dependencies_template.py && git commit -m "feat(web): show dependency resolution category on /dependencies/"`

---

## Task 7: Docs + full suite

**Files:** Modify `changes/202607-dependency-resolution-status.md` (mark implemented); run full suite.

- [ ] **Step 1: Note completion** at the top of `changes/202607-dependency-resolution-status.md` (the `resolution` column + five categories + badge shipped; migration `0005`).
- [ ] **Step 2: Full suite.** `PYTHONPATH=$PWD/src python -m pytest -q -p no:cacheprovider`. Expect green except the 2 pre-existing `/dependencies/` **live-server** web-endpoint tests (they need a running server; the in-process template is now correct). scc-gated tests still pass without scc.
- [ ] **Step 3: Commit.** `git add changes/202607-dependency-resolution-status.md && git commit -m "docs: mark resolution-status implemented"`

---

## Manual verification (before PR)

```bash
PYTHONPATH=$PWD/src python -c "import sys; sys.argv=['kospex','upgrade-db','-apply']; import kospex_cli; kospex_cli.cli()"   # apply 0005
# re-run osi for a repo to classify its deps, then check the split:
PYTHONPATH=$PWD/src python -c "import sys; sys.argv=['krunner','osi','github.com~kospex~kospex']; import krunner; krunner.cli()"
PYTHONPATH=$PWD/src python -c "import kospex_utils as KU, sqlite3; db=sqlite3.connect(KU.get_kospex_db_path()); print(db.execute(\"SELECT resolution, COUNT(*) FROM dependency_data WHERE latest=1 GROUP BY resolution\").fetchall())"
```
Confirm the `resolution` counts split sensibly and `/dependencies/` returns 200 with category badges.
