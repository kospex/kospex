# DB Migration System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace kospex's auto-`ALTER TABLE` `upgrade-db` command with a script-driven migration system under `src/kospex/db/`, and refactor `KOSPEX_TABLES` / `REPO_TABLES` into runtime introspection helpers so future migrations adding tables don't risk orphan rows in `kreaper`.

**Architecture:** New `src/kospex/db/` namespace contains the migrator (`migrator.py`), introspection helpers (`introspect.py`), and SQL/Python migration scripts (`migrations/`). Each migration is identified by a 4-digit prefix and runs as one transactional unit. Applied migrations are tracked per-row in a new `schema_migrations` table; the existing `KOSPEX_DB_VERSION_KEY` integer in `kospex_config` is updated as a cache to keep `KospexQuery.get_kospex_db_version()` working.

**Tech Stack:** Python 3.12+, Click, sqlite-utils, sqlite3, pytest. Spec at `changes/202605-db-migration-system.md` (commits `89eccdd` + `d77f355`).

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `src/kospex/__init__.py` | Empty marker — establishes the namespace |
| `src/kospex/db/__init__.py` | Re-exports `Migrator` for convenient import |
| `src/kospex/db/migrator.py` | The migration runner — discovery, validation, apply, status |
| `src/kospex/db/introspect.py` | `get_kospex_tables`, `get_repo_tables`, `invalidate_cache` |
| `src/kospex/db/migrations/__init__.py` | Empty marker (helps test discovery) |
| `src/kospex/db/migrations/.gitkeep` | Keep directory present even with no migrations yet |
| `src/kospex/db/migrations/README.md` | How to add a new migration |
| `tests/test_db_introspect.py` | Tests for introspection helpers |
| `tests/test_db_migrator.py` | Tests for migrator behaviour |

### Modified files

| Path | Change |
|---|---|
| `pyproject.toml` | Add `kospex` namespace to packaging; include `*.sql` data files |
| `src/kospex_schema.py` | Remove `KOSPEX_TABLES`, `REPO_TABLES`, `generate_alter_table`, `validate_square_brackets*`; add `TBL_SCHEMA_MIGRATIONS` + `SQL_CREATE_SCHEMA_MIGRATIONS`; update `drop_table` |
| `src/kospex_query.py` | 3 validation spots switch to `get_kospex_tables(self.kospex_db)`; rewrite 2 error strings; delete 1 dead comment; fix `tech_commits()` to pass `self.kospex_db` |
| `src/kospex_core.py` | 2 spots switch to `get_kospex_tables`; remove `apply_alter_table_commands` |
| `src/kreaper.py` | 5 spots switch to `get_kospex_tables` / `get_repo_tables` |
| `src/kospex_cli.py` | `upgrade-db` body replaced with ~10 lines that delegate to `Migrator` |
| `tests/test_kospex_schema.py` | Delete `test_generate_alter_table` (tests removed function) |
| `CLAUDE.md` | Brief mention of the migration system + where migrations live |

---

## Task 1: Namespace skeleton + packaging

**Files:**
- Create: `src/kospex/__init__.py`
- Create: `src/kospex/db/__init__.py`
- Create: `src/kospex/db/migrations/__init__.py`
- Create: `src/kospex/db/migrations/.gitkeep`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create the namespace directories with marker files**

```bash
mkdir -p src/kospex/db/migrations
```

Then create these four files (all empty except where noted):

`src/kospex/__init__.py`:
```python
"""kospex package — namespace beachhead. See changes/202605-db-migration-system.md."""
```

`src/kospex/db/__init__.py`:
```python
"""kospex database helpers — migrator and introspection."""
```

`src/kospex/db/migrations/__init__.py`:
```python
"""Migration scripts. Add new files as 0003_<slug>.sql (+ optional 0003_<slug>.py)."""
```

`src/kospex/db/migrations/.gitkeep`: (empty file)

- [ ] **Step 2: Update `pyproject.toml` for the new namespace**

The current `[tool.setuptools]` block has no explicit `packages.find` — top-level `src/*.py` modules ship via setuptools auto-discovery. Adding the `kospex` package needs explicit config so it (and its sub-packages) is included, plus `package-data` so `.sql` files ship in the wheel.

Add this block immediately after the existing `[tool.setuptools.package-data]` block:

```toml
[tool.setuptools.packages.find]
where = ["src"]
include = ["kospex*"]
```

And extend `[tool.setuptools.package-data]` to include the SQL files:

```toml
[tool.setuptools.package-data]
"*" = [
    "templates/*.html",
    "templates/**/*.html",
    "static/**/*"
]
"kospex.db.migrations" = ["*.sql", "*.py", "README.md"]
```

- [ ] **Step 3: Verify the wheel builds and includes everything**

```bash
pip install build
python -m build --wheel
unzip -l dist/kospex-0.0.37-py3-none-any.whl | grep -E "(kospex/db|kospex_cli)"
```

Expected: output lists `kospex/db/__init__.py`, `kospex/db/migrations/__init__.py`, AND the existing flat modules like `kospex_cli.py`. If flat modules are missing, the `packages.find` change broke auto-discovery — see step 4.

- [ ] **Step 4: If flat modules missing, add explicit py-modules**

Only needed if step 3 showed flat modules disappeared. Add this immediately after the `[tool.setuptools.packages.find]` block:

```toml
[tool.setuptools]
include-package-data = true
py-modules = [
    "api_routes", "kgit", "kospex_agent", "kospex_bitbucket", "kospex_cli",
    "kospex_core", "kospex_dependencies", "kospex_email", "kospex_git",
    "kospex_github", "kospex_logging", "kospex_mergestat", "kospex_observation",
    "kospex_query", "kospex_request_cache", "kospex_schema", "kospex_stats",
    "kospex_utils", "kospex_web", "kreaper", "krunner", "krunner_utils",
    "kweb2", "kweb_graph_service", "kweb_help_service", "kweb_security",
    "repo_sync", "yearly", "yearly_repos",
]
package-dir = {"" = "src"}
```

(List drawn from `src/kospex.egg-info/top_level.txt`.) Rebuild and re-verify with the unzip command.

- [ ] **Step 5: Verify the new namespace imports**

```bash
pip install -e .
python -c "import kospex; import kospex.db; print('ok')"
```

Expected: `ok`. If import fails with `ModuleNotFoundError`, packaging is wrong — revisit step 4.

- [ ] **Step 6: Verify existing CLI entry points still work**

```bash
kospex --help
kreaper --help
```

Expected: usage output, no `ImportError`. If anything broke, revert pyproject.toml changes and reinvestigate.

- [ ] **Step 7: Commit**

```bash
git add src/kospex pyproject.toml
git commit -m "Add src/kospex namespace skeleton for db helpers"
```

---

## Task 2: `introspect.get_kospex_tables`

**Files:**
- Create: `src/kospex/db/introspect.py`
- Create: `tests/test_db_introspect.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db_introspect.py`:
```python
"""Tests for kospex.db.introspect."""
from sqlite_utils import Database


def _make_db(tmp_path):
    db = Database(tmp_path / "test.db")
    db.execute("CREATE TABLE [repos] (_repo_id TEXT, name TEXT)")
    db.execute("CREATE TABLE [commits] (_repo_id TEXT, hash TEXT)")
    db.execute("CREATE TABLE [kospex_config] (key TEXT, value TEXT)")
    return db


def test_get_kospex_tables_lists_user_tables(tmp_path):
    from kospex.db.introspect import get_kospex_tables
    db = _make_db(tmp_path)

    tables = get_kospex_tables(db)

    assert tables == {"repos", "commits", "kospex_config"}


def test_get_kospex_tables_excludes_sqlite_internals(tmp_path):
    from kospex.db.introspect import get_kospex_tables
    db = _make_db(tmp_path)
    # sqlite auto-creates sqlite_sequence when AUTOINCREMENT is used; force one
    db.execute("CREATE TABLE [auto] (id INTEGER PRIMARY KEY AUTOINCREMENT)")

    tables = get_kospex_tables(db)

    assert not any(t.startswith("sqlite_") for t in tables)
    assert "auto" in tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db_introspect.py -v`
Expected: `ImportError` / `ModuleNotFoundError` for `kospex.db.introspect`.

- [ ] **Step 3: Write minimal implementation**

`src/kospex/db/introspect.py`:
```python
"""Runtime introspection helpers for the kospex database.

Replaces the hand-maintained KOSPEX_TABLES / REPO_TABLES list constants
that used to live in kospex_schema.py. Reads sqlite_master and PRAGMA
table_info directly, so migrations that add new tables are picked up
automatically.
"""

_TABLE_CACHE: dict[str, set[str]] = {}


def get_kospex_tables(db) -> set[str]:
    """Return the set of user tables in the kospex database."""
    key = str(db.path)
    if key not in _TABLE_CACHE:
        rows = db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        _TABLE_CACHE[key] = {r[0] for r in rows}
    return _TABLE_CACHE[key]


def invalidate_cache(db=None) -> None:
    """Clear cached table lists. Call after applying migrations."""
    if db is None:
        _TABLE_CACHE.clear()
    else:
        _TABLE_CACHE.pop(str(db.path), None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db_introspect.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/introspect.py tests/test_db_introspect.py
git commit -m "Add get_kospex_tables introspection helper"
```

---

## Task 3: `introspect.get_repo_tables`

**Files:**
- Modify: `src/kospex/db/introspect.py`
- Modify: `tests/test_db_introspect.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_db_introspect.py`:
```python
def test_get_repo_tables_filters_by_repo_id_column(tmp_path):
    from kospex.db.introspect import get_repo_tables
    db = Database(tmp_path / "test.db")
    db.execute("CREATE TABLE [repos] (_repo_id TEXT, name TEXT)")
    db.execute("CREATE TABLE [commits] (_repo_id TEXT, hash TEXT)")
    db.execute("CREATE TABLE [kospex_config] (key TEXT, value TEXT)")  # no _repo_id

    tables = get_repo_tables(db)

    assert tables == {"repos", "commits"}
    assert "kospex_config" not in tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db_introspect.py::test_get_repo_tables_filters_by_repo_id_column -v`
Expected: `ImportError: cannot import name 'get_repo_tables'`.

- [ ] **Step 3: Implement `get_repo_tables`**

Add to `src/kospex/db/introspect.py`:
```python
_REPO_TABLE_CACHE: dict[str, set[str]] = {}


def get_repo_tables(db) -> set[str]:
    """Return tables that have a _repo_id column (auto-detected via PRAGMA)."""
    key = str(db.path)
    if key not in _REPO_TABLE_CACHE:
        out = set()
        for t in get_kospex_tables(db):
            cols = [c[1] for c in db.execute(f"PRAGMA table_info([{t}])").fetchall()]
            if "_repo_id" in cols:
                out.add(t)
        _REPO_TABLE_CACHE[key] = out
    return _REPO_TABLE_CACHE[key]
```

Update `invalidate_cache` to clear both caches:
```python
def invalidate_cache(db=None) -> None:
    """Clear cached table lists. Call after applying migrations."""
    if db is None:
        _TABLE_CACHE.clear()
        _REPO_TABLE_CACHE.clear()
    else:
        key = str(db.path)
        _TABLE_CACHE.pop(key, None)
        _REPO_TABLE_CACHE.pop(key, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db_introspect.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/introspect.py tests/test_db_introspect.py
git commit -m "Add get_repo_tables introspection helper"
```

---

## Task 4: Cache invalidation behaviour

**Files:**
- Modify: `tests/test_db_introspect.py`

- [ ] **Step 1: Add failing test for cache behaviour**

Append to `tests/test_db_introspect.py`:
```python
def test_invalidate_cache_picks_up_new_table(tmp_path):
    from kospex.db.introspect import get_kospex_tables, invalidate_cache
    db = Database(tmp_path / "test.db")
    db.execute("CREATE TABLE [repos] (_repo_id TEXT)")

    before = get_kospex_tables(db)
    assert before == {"repos"}

    db.execute("CREATE TABLE [widgets] (id INTEGER)")
    # Without invalidation, cache still says only "repos"
    cached = get_kospex_tables(db)
    assert cached == {"repos"}

    invalidate_cache(db)
    after = get_kospex_tables(db)
    assert after == {"repos", "widgets"}


def test_invalidate_cache_no_db_clears_all(tmp_path):
    from kospex.db.introspect import get_kospex_tables, invalidate_cache
    db1 = Database(tmp_path / "a.db")
    db1.execute("CREATE TABLE [a] (x TEXT)")
    db2 = Database(tmp_path / "b.db")
    db2.execute("CREATE TABLE [b] (y TEXT)")

    get_kospex_tables(db1)
    get_kospex_tables(db2)

    invalidate_cache()  # no arg → clear all

    from kospex.db.introspect import _TABLE_CACHE
    assert _TABLE_CACHE == {}
```

- [ ] **Step 2: Run tests — both should pass already**

Run: `pytest tests/test_db_introspect.py -v`
Expected: all 5 tests PASS. The implementation from Task 3 already supports this. If a test fails, fix the implementation, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_db_introspect.py
git commit -m "Test cache invalidation in introspect helpers"
```

---

## Task 5: Switch `kospex_query.py` to introspection

**Files:**
- Modify: `src/kospex_query.py:2227-2228, 2300-2301, 2314-2315, 1516`

- [ ] **Step 1: Add the import**

Near the existing `import kospex_schema as KospexSchema` (line 14), add:
```python
from kospex.db.introspect import get_kospex_tables
```

- [ ] **Step 2: Update `where_join` (line 2225-2228)**

Replace:
```python
        # Check the table are valid in our schema
        for t in (table, join_table):
            if t not in KospexSchema.KOSPEX_TABLES:
                raise ValueError(f"Table '{t}' not in KospexSchema.KOSPEX_TABLES")
```

With:
```python
        # Check the tables exist in the live schema
        valid = get_kospex_tables(self.kospex_db)
        for t in (table, join_table):
            if t not in valid:
                raise ValueError(f"Table '{t}' is not a known Kospex table")
```

- [ ] **Step 3: Update `from_table` (line 2297-2303)**

Replace:
```python
    def from_table(self, *tables):
        """Add a table to the query"""
        for table in tables:
            if table not in KospexSchema.KOSPEX_TABLES:
                raise ValueError(f"Table '{table}' not in KospexSchema.KOSPEX_TABLES")
            else:
                self.from_tables.append(table)
```

With:
```python
    def from_table(self, *tables):
        """Add a table to the query"""
        valid = get_kospex_tables(self.kospex_db)
        for table in tables:
            if table not in valid:
                raise ValueError(f"Table '{table}' is not a known Kospex table")
            self.from_tables.append(table)
```

- [ ] **Step 4: Update `valid_table_prefix_select` (line 2305-2326)**

Replace the block starting at line 2314:
```python
            if parts[0] not in KospexSchema.KOSPEX_TABLES:
                # raise ValueError(f"Table '{parts[0]}' not in KospexSchema.KOSPEX_TABLES")
                return False
```

With:
```python
            if parts[0] not in get_kospex_tables(self.kospex_db):
                return False
```

(Both the live check and the dead commented-out reference are replaced.)

- [ ] **Step 5: Fix `tech_commits()` (line 1516) — pre-existing inconsistency**

Today, line 1516 creates `KospexData()` with no db, which would crash once `from_table` requires a db for validation. Replace:

```python
        kd = KospexData()
```

With:
```python
        kd = KospexData(kospex_db=self.kospex_db)
```

- [ ] **Step 6: Verify nothing else in `kospex_query.py` references `KOSPEX_TABLES`**

Run:
```bash
grep -n "KOSPEX_TABLES\|REPO_TABLES" src/kospex_query.py
```

Expected: no output.

- [ ] **Step 7: Run the existing test suite — nothing should break**

Run: `pytest -v`
Expected: all existing tests still pass. (Constants are still defined in `kospex_schema.py` at this point, so old code paths still work.)

- [ ] **Step 8: Commit**

```bash
git add src/kospex_query.py
git commit -m "Switch KospexData to runtime table introspection"
```

---

## Task 6: Switch `kospex_core.py` to introspection

**Files:**
- Modify: `src/kospex_core.py:1243, 1274`

- [ ] **Step 1: Add the import**

Near the existing `import kospex_schema as KospexSchema`, add:
```python
from kospex.db.introspect import get_kospex_tables
```

- [ ] **Step 2: Update the row-count iterator (line 1243)**

Replace:
```python
        for db_table in KospexSchema.KOSPEX_TABLES:
```

With:
```python
        for db_table in get_kospex_tables(self.kospex_db):
```

- [ ] **Step 3: Update `delete_repo_id_from_table` (line 1274)**

Replace:
```python
        if table not in KospexSchema.KOSPEX_TABLES:
            raise ValueError(f"table: {table} is not a Kospex table")
```

With:
```python
        if table not in get_kospex_tables(self.kospex_db):
            raise ValueError(f"table: {table} is not a Kospex table")
```

(Error message is already generic — no rewrite needed.)

- [ ] **Step 4: Verify no remaining `KOSPEX_TABLES` references**

```bash
grep -n "KOSPEX_TABLES\|REPO_TABLES" src/kospex_core.py
```

Expected: no output.

- [ ] **Step 5: Run tests**

Run: `pytest -v`
Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/kospex_core.py
git commit -m "Switch kospex_core to runtime table introspection"
```

---

## Task 7: Switch `kreaper.py` to introspection

**Files:**
- Modify: `src/kreaper.py:36, 51, 60, 76, 86`

- [ ] **Step 1: Add the import**

Near the existing imports (after `import kospex_schema as KospexSchema`), add:
```python
from kospex.db.introspect import get_kospex_tables, get_repo_tables
```

- [ ] **Step 2: Update `delete-repo -table` validation (line 36)**

Replace:
```python
        if table in KospexSchema.KOSPEX_TABLES:
```

With:
```python
        if table in get_kospex_tables(kospex.kospex_db):
```

- [ ] **Step 3: Update "list of valid tables" output in `delete-repo` (line 51)**

Replace:
```python
            for table in KospexSchema.KOSPEX_TABLES:
                print(table)
```

With:
```python
            for table in sorted(get_kospex_tables(kospex.kospex_db)):
                print(table)
```

(Sort for stable output.)

- [ ] **Step 4: Update `delete-repo -repo_id` iteration (line 60)**

Replace:
```python
        for table in KospexSchema.REPO_TABLES:
```

With:
```python
        for table in sorted(get_repo_tables(kospex.kospex_db)):
```

- [ ] **Step 5: Update `drop-table` validation (line 76)**

Replace:
```python
    if table and table in KospexSchema.KOSPEX_TABLES:
```

With:
```python
    if table and table in get_kospex_tables(kospex.kospex_db):
```

- [ ] **Step 6: Update "list of valid tables" output in `drop-table` (line 86)**

Replace:
```python
        for table in KospexSchema.KOSPEX_TABLES:
            print(table)
```

With:
```python
        for table in sorted(get_kospex_tables(kospex.kospex_db)):
            print(table)
```

- [ ] **Step 7: Verify no remaining references**

```bash
grep -n "KOSPEX_TABLES\|REPO_TABLES" src/kreaper.py
```

Expected: no output.

- [ ] **Step 8: Manual smoke test**

```bash
kreaper repos
kreaper delete-repo -table nonsense
kreaper drop-table
```

Expected:
- `kreaper repos` lists current repo ids.
- `delete-repo -table nonsense` reports "table nonsense is NOT a valid table name." and lists the live table set.
- `drop-table` with no args prints the live table set.

- [ ] **Step 9: Commit**

```bash
git add src/kreaper.py
git commit -m "Switch kreaper to runtime table introspection"
```

---

## Task 8: Remove `KOSPEX_TABLES` / `REPO_TABLES` and update `drop_table`

**Files:**
- Modify: `src/kospex_schema.py:31-39, 472-479`

- [ ] **Step 1: Add the introspect import at the top of `kospex_schema.py`**

Near the existing imports, add:
```python
from kospex.db.introspect import get_kospex_tables
```

Note: this creates a one-directional dep — `kospex_schema` depends on `kospex.db.introspect`. That's fine; `introspect` only imports stdlib and sqlite_utils. There is no risk of a circular import.

- [ ] **Step 2: Delete `KOSPEX_TABLES` and `REPO_TABLES` constants (lines 31-39)**

Remove these blocks entirely:
```python
KOSPEX_TABLES = [ TBL_COMMITS, TBL_COMMIT_FILES, TBL_COMMIT_METADATA, TBL_FILE_METADATA,
                TBL_REPO_HOTSPOTS, TBL_DEPENDENCY_DATA, TBL_URL_CACHE, TBL_KRUNNER,
                TBL_OBSERVATIONS, TBL_REPOS, TBL_KOSPEX_META, TBL_GROUPS, TBL_KOSPEX_CONFIG,
                TBL_EMAIL_MAP, TBL_DEVELOPER_STATS]

# The following are tables with a repo_id
REPO_TABLES = [ TBL_COMMITS, TBL_COMMIT_FILES, TBL_COMMIT_METADATA, TBL_FILE_METADATA,
                TBL_REPO_HOTSPOTS, TBL_DEPENDENCY_DATA, TBL_KRUNNER, TBL_OBSERVATIONS, TBL_REPOS,
                TBL_GROUPS, TBL_BRANCHES, TBL_BRANCH_HISTORY, TBL_DEVELOPER_STATS ]
```

- [ ] **Step 3: Update `drop_table()` (line 472-479)**

Replace:
```python
def drop_table(table):
    """ Drop a table from the DB """
    db = connect_or_create_kospex_db()
    if table in KOSPEX_TABLES:
        db.execute(f"DROP TABLE IF EXISTS [{table}]")
        print(f"Dropped table '{table}', if it existed")
    else:
        print(f"Invalid table '{table}', was not in KOSPEX_TABLES")
```

With:
```python
def drop_table(table):
    """ Drop a table from the DB """
    db = connect_or_create_kospex_db()
    if table in get_kospex_tables(db):
        db.execute(f"DROP TABLE IF EXISTS [{table}]")
        print(f"Dropped table '{table}', if it existed")
    else:
        print(f"Invalid table '{table}', not a known Kospex table")
```

- [ ] **Step 4: Verify nothing in the codebase still references the removed constants**

```bash
grep -rn "KOSPEX_TABLES\|REPO_TABLES" src/ tests/
```

Expected: no output. If anything appears, it's either:
- A user-facing string from the spec's "cleanup pass" note (reword it), or
- A consumer we missed (apply the same introspection swap).

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass except for any in `test_kospex_schema.py` that reference removed code (we handle those in Task 22).

- [ ] **Step 6: Commit**

```bash
git add src/kospex_schema.py
git commit -m "Remove KOSPEX_TABLES/REPO_TABLES constants in favour of introspection"
```

---

## Task 9: Add `schema_migrations` table to baseline

**Files:**
- Modify: `src/kospex_schema.py`

- [ ] **Step 1: Add the table name constant and CREATE statement**

Near the existing `TBL_*` constants (around line 27), add:
```python
TBL_SCHEMA_MIGRATIONS = "schema_migrations"
```

Below the other `SQL_CREATE_*` definitions (e.g., after `SQL_CREATE_DEVELOPER_STATS`, ~line 387), add:
```python
SQL_CREATE_SCHEMA_MIGRATIONS = f'''CREATE TABLE IF NOT EXISTS [{TBL_SCHEMA_MIGRATIONS}] (
    [id] TEXT PRIMARY KEY,         -- e.g. '0003_add_repos_size_bytes'
    [sequence] INTEGER NOT NULL,   -- 3 (extracted from prefix)
    [checksum] TEXT NOT NULL,      -- sha256(sql)[:sha256(py)]
    [applied_at] TEXT NOT NULL,    -- ISO 8601 UTC
    [duration_ms] INTEGER,
    [has_python] INTEGER NOT NULL  -- 0 or 1
    )'''
```

- [ ] **Step 2: Add it to `DB_CREATE_STATEMENTS`**

Append to the `DB_CREATE_STATEMENTS` dict (around line 417):
```python
    TBL_SCHEMA_MIGRATIONS: SQL_CREATE_SCHEMA_MIGRATIONS,
```

- [ ] **Step 3: Add the `execute` call in `connect_or_create_kospex_db`**

After the existing `kospex_db.execute(SQL_CREATE_DEVELOPER_STATS)` (~line 459), add:
```python
    kospex_db.execute(SQL_CREATE_SCHEMA_MIGRATIONS)
```

- [ ] **Step 4: Smoke test fresh DB creation**

```bash
python -c "
import tempfile, os
from pathlib import Path
tmp = tempfile.mkdtemp()
os.environ['KOSPEX_HOME'] = tmp
import kospex_schema as KS
db = KS.connect_or_create_kospex_db()
print(sorted(t[0] for t in db.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()))
"
```

Expected: list of tables including `schema_migrations`.

- [ ] **Step 5: Commit**

```bash
git add src/kospex_schema.py
git commit -m "Add schema_migrations table to kospex baseline"
```

---

## Task 10: `Migration` dataclass + `compute_checksum`

**Files:**
- Create: `src/kospex/db/migrator.py`
- Create: `tests/test_db_migrator.py`

- [ ] **Step 1: Write failing tests for `Migration` and checksum**

`tests/test_db_migrator.py`:
```python
"""Tests for kospex.db.migrator."""
import hashlib
from pathlib import Path


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_migration_from_sql_only(tmp_path):
    from kospex.db.migrator import Migration
    sql = _write(tmp_path / "0003_add_widgets.sql", "CREATE TABLE widgets (id INTEGER);")

    m = Migration.from_paths(sql_path=sql, py_path=None)

    assert m.id == "0003_add_widgets"
    assert m.sequence == 3
    assert m.slug == "add_widgets"
    assert m.sql_path == sql
    assert m.py_path is None


def test_migration_from_sql_and_python(tmp_path):
    from kospex.db.migrator import Migration
    sql = _write(tmp_path / "0004_backfill.sql", "ALTER TABLE widgets ADD COLUMN x INTEGER;")
    py = _write(tmp_path / "0004_backfill.py", "def up(db): pass\n")

    m = Migration.from_paths(sql_path=sql, py_path=py)

    assert m.id == "0004_backfill"
    assert m.sequence == 4
    assert m.py_path == py


def test_checksum_combines_sql_and_python(tmp_path):
    from kospex.db.migrator import Migration
    sql = _write(tmp_path / "0003_x.sql", "CREATE TABLE x (id INTEGER);")
    py = _write(tmp_path / "0003_x.py", "def up(db): pass\n")
    m = Migration.from_paths(sql_path=sql, py_path=py)

    expected = (
        hashlib.sha256(sql.read_bytes()).hexdigest()
        + ":"
        + hashlib.sha256(py.read_bytes()).hexdigest()
    )

    assert m.checksum() == expected


def test_checksum_sql_only_has_trailing_colon(tmp_path):
    from kospex.db.migrator import Migration
    sql = _write(tmp_path / "0003_x.sql", "CREATE TABLE x (id INTEGER);")
    m = Migration.from_paths(sql_path=sql, py_path=None)

    assert m.checksum() == hashlib.sha256(sql.read_bytes()).hexdigest() + ":"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_db_migrator.py -v`
Expected: `ModuleNotFoundError: No module named 'kospex.db.migrator'`.

- [ ] **Step 3: Implement `Migration`**

`src/kospex/db/migrator.py`:
```python
"""Script-driven SQLite migration runner for kospex.

Replaces the old auto-ALTER `kospex upgrade-db` flow. See
changes/202605-db-migration-system.md for the design.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


FILE_RE = re.compile(r"^(\d{4})_(.+)\.(sql|py)$")


@dataclass(frozen=True)
class Migration:
    id: str             # e.g. '0003_add_widgets'
    sequence: int       # 3
    slug: str           # 'add_widgets'
    sql_path: Path
    py_path: Optional[Path]

    @classmethod
    def from_paths(cls, sql_path: Path, py_path: Optional[Path]) -> "Migration":
        m = FILE_RE.match(sql_path.name)
        if not m:
            raise ValueError(f"Not a migration filename: {sql_path.name}")
        seq, slug, _ext = m.groups()
        return cls(
            id=f"{seq}_{slug}",
            sequence=int(seq),
            slug=slug,
            sql_path=sql_path,
            py_path=py_path,
        )

    def checksum(self) -> str:
        sql_hash = hashlib.sha256(self.sql_path.read_bytes()).hexdigest()
        py_hash = (
            hashlib.sha256(self.py_path.read_bytes()).hexdigest()
            if self.py_path
            else ""
        )
        return f"{sql_hash}:{py_hash}"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_db_migrator.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/migrator.py tests/test_db_migrator.py
git commit -m "Add Migration dataclass with checksum"
```

---

## Task 11: Discovery — `discover_migrations`

**Files:**
- Modify: `src/kospex/db/migrator.py`
- Modify: `tests/test_db_migrator.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db_migrator.py`:
```python
def test_discover_empty_dir(tmp_path):
    from kospex.db.migrator import discover_migrations
    assert discover_migrations(tmp_path) == []


def test_discover_returns_sorted_by_sequence(tmp_path):
    from kospex.db.migrator import discover_migrations
    _write(tmp_path / "0005_e.sql", "SELECT 1;")
    _write(tmp_path / "0003_c.sql", "SELECT 1;")
    _write(tmp_path / "0004_d.sql", "SELECT 1;")

    migrations = discover_migrations(tmp_path)

    assert [m.id for m in migrations] == ["0003_c", "0004_d", "0005_e"]


def test_discover_pairs_sql_with_matching_python(tmp_path):
    from kospex.db.migrator import discover_migrations
    _write(tmp_path / "0003_x.sql", "SELECT 1;")
    _write(tmp_path / "0003_x.py", "def up(db): pass\n")
    _write(tmp_path / "0004_y.sql", "SELECT 1;")

    migrations = discover_migrations(tmp_path)

    by_id = {m.id: m for m in migrations}
    assert by_id["0003_x"].py_path is not None
    assert by_id["0004_y"].py_path is None


def test_discover_ignores_unrelated_files(tmp_path):
    from kospex.db.migrator import discover_migrations
    _write(tmp_path / "0003_x.sql", "SELECT 1;")
    _write(tmp_path / "README.md", "docs")
    _write(tmp_path / "__init__.py", "")
    _write(tmp_path / "not_a_migration.sql", "SELECT 1;")

    migrations = discover_migrations(tmp_path)

    assert [m.id for m in migrations] == ["0003_x"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_db_migrator.py -v -k discover`
Expected: `ImportError: cannot import name 'discover_migrations'`.

- [ ] **Step 3: Implement `discover_migrations`**

Add to `src/kospex/db/migrator.py`:
```python
def discover_migrations(migrations_dir: Path) -> list[Migration]:
    """Scan a directory for migration files and return them sorted by sequence."""
    if not migrations_dir.exists():
        return []

    sql_files: dict[str, Path] = {}   # id -> path
    py_files: dict[str, Path] = {}    # id -> path

    for path in sorted(migrations_dir.iterdir()):
        m = FILE_RE.match(path.name)
        if not m:
            continue
        seq, slug, ext = m.groups()
        migration_id = f"{seq}_{slug}"
        if ext == "sql":
            sql_files[migration_id] = path
        elif ext == "py":
            py_files[migration_id] = path

    migrations = []
    for migration_id, sql_path in sql_files.items():
        py_path = py_files.get(migration_id)
        migrations.append(Migration.from_paths(sql_path=sql_path, py_path=py_path))

    migrations.sort(key=lambda m: m.sequence)
    return migrations
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_db_migrator.py -v`
Expected: all tests PASS (7 total now).

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/migrator.py tests/test_db_migrator.py
git commit -m "Add discover_migrations directory scanner"
```

---

## Task 12: Validation — `validate_migrations`

**Files:**
- Modify: `src/kospex/db/migrator.py`
- Modify: `tests/test_db_migrator.py`

- [ ] **Step 1: Write failing tests for the fatal validation cases**

Append to `tests/test_db_migrator.py`:
```python
import pytest


def test_validate_duplicate_prefix_raises(tmp_path):
    from kospex.db.migrator import discover_migrations, validate_migrations
    # Two .sql files sharing prefix 0003 but different slugs — discovery will
    # only keep one (dict overwrite), so we have to construct the list manually
    # to test the validator's duplicate detection.
    from kospex.db.migrator import Migration
    sql1 = _write(tmp_path / "0003_foo.sql", "SELECT 1;")
    sql2 = _write(tmp_path / "0003_bar.sql", "SELECT 1;")
    migrations = [
        Migration.from_paths(sql_path=sql1, py_path=None),
        Migration.from_paths(sql_path=sql2, py_path=None),
    ]

    with pytest.raises(ValueError, match="Duplicate migration sequence: 3"):
        validate_migrations(migrations)


def test_validate_orphan_python_raises(tmp_path):
    from kospex.db.migrator import discover_migrations, validate_migrations
    _write(tmp_path / "0003_foo.py", "def up(db): pass\n")  # no matching .sql

    # discover_migrations only indexes .sql; the orphan .py is invisible to it.
    # Validator runs against the directory itself for this check.
    with pytest.raises(ValueError, match="Orphan Python migration file"):
        validate_migrations(discover_migrations(tmp_path), migrations_dir=tmp_path)


def test_validate_empty_sql_raises(tmp_path):
    from kospex.db.migrator import discover_migrations, validate_migrations
    _write(tmp_path / "0003_empty.sql", "   -- only a comment\n\n")

    with pytest.raises(ValueError, match="empty"):
        validate_migrations(discover_migrations(tmp_path))


def test_validate_python_missing_up_raises(tmp_path):
    from kospex.db.migrator import discover_migrations, validate_migrations
    _write(tmp_path / "0003_no_up.sql", "SELECT 1;")
    _write(tmp_path / "0003_no_up.py", "x = 1\n")  # no up() function

    with pytest.raises(ValueError, match="missing 'up'"):
        validate_migrations(discover_migrations(tmp_path))


def test_validate_clean_set_passes(tmp_path):
    from kospex.db.migrator import discover_migrations, validate_migrations
    _write(tmp_path / "0003_a.sql", "CREATE TABLE a (id INTEGER);")
    _write(tmp_path / "0004_b.sql", "CREATE TABLE b (id INTEGER);")
    _write(tmp_path / "0004_b.py", "def up(db): pass\n")

    # Should not raise
    validate_migrations(discover_migrations(tmp_path), migrations_dir=tmp_path)
```

- [ ] **Step 2: Run to verify failures**

Run: `pytest tests/test_db_migrator.py -v -k validate`
Expected: `ImportError: cannot import name 'validate_migrations'`.

- [ ] **Step 3: Implement `validate_migrations`**

Add to `src/kospex/db/migrator.py`:
```python
def validate_migrations(
    migrations: list[Migration],
    migrations_dir: Optional[Path] = None,
) -> None:
    """Run pre-execution validation. Raises ValueError on any fatal issue.

    Fatal issues:
    - Duplicate sequence number
    - Slug mismatch between SQL and Python files (caught by discovery already,
      surfaced again here for defence in depth)
    - Orphan Python file (no matching SQL — requires migrations_dir to detect)
    - Empty SQL file
    - Python file missing `up(db)` function
    """
    # 1. Duplicate sequences
    seen: dict[int, str] = {}
    for m in migrations:
        if m.sequence in seen:
            raise ValueError(
                f"Duplicate migration sequence: {m.sequence} "
                f"('{seen[m.sequence]}' and '{m.id}')"
            )
        seen[m.sequence] = m.id

    # 2. Orphan .py files (need the directory to check)
    if migrations_dir is not None:
        sql_ids = {m.id for m in migrations}
        for path in migrations_dir.iterdir():
            fm = FILE_RE.match(path.name)
            if not fm:
                continue
            seq, slug, ext = fm.groups()
            if ext == "py":
                py_id = f"{seq}_{slug}"
                if py_id not in sql_ids:
                    raise ValueError(
                        f"Orphan Python migration file: {path.name} has no matching .sql"
                    )

    # 3. Empty SQL + 4. Python missing up()
    for m in migrations:
        stripped = "\n".join(
            line for line in m.sql_path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("--")
        )
        if not stripped.strip():
            raise ValueError(f"Migration {m.id} SQL file is empty")

        if m.py_path is not None:
            mod = _load_python_module(m.py_path, m.id)
            if not hasattr(mod, "up") or not callable(mod.up):
                raise ValueError(f"Migration {m.id} Python file is missing 'up' function")


def _load_python_module(py_path: Path, migration_id: str):
    """Load a migration's .py file as a module via importlib."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"_migration_{migration_id}", py_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load Python migration {migration_id} from {py_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/test_db_migrator.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/migrator.py tests/test_db_migrator.py
git commit -m "Add validate_migrations with fatal-case checks"
```

---

## Task 13: `Migrator.apply` — SQL only, transactional

**Files:**
- Modify: `src/kospex/db/migrator.py`
- Modify: `tests/test_db_migrator.py`

- [ ] **Step 1: Write a failing test**

Append to `tests/test_db_migrator.py`:
```python
import sqlite_utils


def _baseline_db(tmp_path):
    """A fresh kospex-shaped DB with the schema_migrations table only."""
    db = sqlite_utils.Database(tmp_path / "kospex.db")
    db.execute("""CREATE TABLE [schema_migrations] (
        [id] TEXT PRIMARY KEY,
        [sequence] INTEGER NOT NULL,
        [checksum] TEXT NOT NULL,
        [applied_at] TEXT NOT NULL,
        [duration_ms] INTEGER,
        [has_python] INTEGER NOT NULL
    )""")
    return db


def test_apply_sql_only_creates_table(tmp_path):
    from kospex.db.migrator import Migrator, discover_migrations
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_widgets.sql",
           "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrations = discover_migrations(migrations_dir)
    migrator.apply(migrations[0])

    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "widgets" in tables


def test_apply_records_in_schema_migrations(tmp_path):
    from kospex.db.migrator import Migrator, discover_migrations
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_widgets.sql", "CREATE TABLE widgets (id INTEGER);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrations = discover_migrations(migrations_dir)
    migrator.apply(migrations[0])

    rows = list(db.execute("SELECT id, sequence, has_python FROM schema_migrations").fetchall())
    assert rows == [("0003_widgets", 3, 0)]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_db_migrator.py -v -k apply`
Expected: `ImportError: cannot import name 'Migrator'`.

- [ ] **Step 3: Implement `Migrator` with `apply`**

Add to `src/kospex/db/migrator.py`:
```python
import time
from datetime import datetime, timezone


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Migrator:
    def __init__(self, db, migrations_dir: Optional[Path] = None):
        self.db = db
        self.migrations_dir = migrations_dir or _default_migrations_dir()

    def apply(self, migration: Migration) -> None:
        """Apply one migration in a single transaction. Raises on failure."""
        started = time.monotonic()
        with self.db.conn:  # sqlite3 transaction context
            sql = migration.sql_path.read_text()
            self.db.conn.executescript(sql)

            if migration.py_path is not None:
                mod = _load_python_module(migration.py_path, migration.id)
                mod.up(self.db)

            self.db["schema_migrations"].insert({
                "id": migration.id,
                "sequence": migration.sequence,
                "checksum": migration.checksum(),
                "applied_at": _utcnow_iso(),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "has_python": 1 if migration.py_path else 0,
            })


def _default_migrations_dir() -> Path:
    """Locate the migrations directory shipped with this package."""
    import importlib.resources
    return Path(str(importlib.resources.files("kospex.db") / "migrations"))
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_db_migrator.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/migrator.py tests/test_db_migrator.py
git commit -m "Add Migrator.apply for SQL-only migrations"
```

---

## Task 14: `Migrator.apply` — Python `up()` step

**Files:**
- Modify: `tests/test_db_migrator.py`

(`apply` already supports the Python step from Task 13. Add tests to lock it in.)

- [ ] **Step 1: Write failing tests for the Python step**

Append to `tests/test_db_migrator.py`:
```python
def test_apply_runs_python_up_after_sql(tmp_path):
    from kospex.db.migrator import Migrator, discover_migrations
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_widgets.sql",
           "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);")
    _write(migrations_dir / "0003_widgets.py", """
def up(db):
    db["widgets"].insert({"id": 1, "name": "backfilled"})
""")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply(discover_migrations(migrations_dir)[0])

    rows = list(db.execute("SELECT id, name FROM widgets").fetchall())
    assert rows == [(1, "backfilled")]

    schema_rows = list(db.execute("SELECT has_python FROM schema_migrations").fetchall())
    assert schema_rows == [(1,)]
```

- [ ] **Step 2: Run — should pass on the existing implementation**

Run: `pytest tests/test_db_migrator.py::test_apply_runs_python_up_after_sql -v`
Expected: PASS. If it fails, the implementation from Task 13 has a bug — fix that, don't change the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_db_migrator.py
git commit -m "Test apply runs Python up() after SQL"
```

---

## Task 15: Transaction rollback on Python failure

**Files:**
- Modify: `tests/test_db_migrator.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_db_migrator.py`:
```python
def test_apply_rolls_back_when_python_raises(tmp_path):
    from kospex.db.migrator import Migrator, discover_migrations
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_widgets.sql",
           "CREATE TABLE widgets (id INTEGER PRIMARY KEY);")
    _write(migrations_dir / "0003_widgets.py", """
def up(db):
    raise RuntimeError("boom")
""")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migration = discover_migrations(migrations_dir)[0]

    with pytest.raises(RuntimeError, match="boom"):
        migrator.apply(migration)

    # SQL change should be rolled back
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "widgets" not in tables

    # No row recorded
    rows = list(db.execute("SELECT id FROM schema_migrations").fetchall())
    assert rows == []
```

- [ ] **Step 2: Run — expectation depends on sqlite_utils behaviour**

Run: `pytest tests/test_db_migrator.py::test_apply_rolls_back_when_python_raises -v`

Two possibilities:
- **PASS** — sqlite3 `with conn:` context properly rolls back DDL inside the transaction. Good.
- **FAIL** showing `widgets` table still exists — SQLite auto-commits DDL when using `executescript()`. In that case, switch the implementation to use `execute()` per-statement, splitting the script on `;`, OR use an explicit `BEGIN` / `COMMIT` / `ROLLBACK` wrapper.

- [ ] **Step 3: If the test fails, fix `apply` to use explicit transaction control**

Replace the `apply` body with:
```python
    def apply(self, migration: Migration) -> None:
        started = time.monotonic()
        conn = self.db.conn
        sql = migration.sql_path.read_text()
        try:
            conn.execute("BEGIN")
            # Split on `;` and execute each non-empty statement so DDL stays in tx
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                conn.execute(stmt)

            if migration.py_path is not None:
                mod = _load_python_module(migration.py_path, migration.id)
                mod.up(self.db)

            self.db["schema_migrations"].insert({
                "id": migration.id,
                "sequence": migration.sequence,
                "checksum": migration.checksum(),
                "applied_at": _utcnow_iso(),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "has_python": 1 if migration.py_path else 0,
            })
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
```

Re-run: `pytest tests/test_db_migrator.py -v` — all tests should PASS.

- [ ] **Step 4: Commit**

```bash
git add src/kospex/db/migrator.py tests/test_db_migrator.py
git commit -m "Roll back SQL when Python migration fails"
```

---

## Task 16: `applied()`, `pending()`, `apply_pending()`, version int update

**Files:**
- Modify: `src/kospex/db/migrator.py`
- Modify: `tests/test_db_migrator.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db_migrator.py`:
```python
def _add_config_table(db):
    db.execute("""CREATE TABLE [kospex_config] (
        [format] TEXT, [key] TEXT, [value] TEXT,
        [latest] INTEGER, [created_at] TEXT, [updated_at] TEXT,
        PRIMARY KEY(key, latest)
    )""")


def test_applied_returns_recorded_ids(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    db["schema_migrations"].insert({
        "id": "0003_a", "sequence": 3, "checksum": "x:",
        "applied_at": "2026-05-18T00:00:00Z", "duration_ms": 1, "has_python": 0,
    })

    assert Migrator(db, migrations_dir=tmp_path).applied() == ["0003_a"]


def test_pending_returns_discovered_minus_applied(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    db["schema_migrations"].insert({
        "id": "0003_a", "sequence": 3, "checksum": "x:",
        "applied_at": "2026-05-18T00:00:00Z", "duration_ms": 1, "has_python": 0,
    })
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_a.sql", "SELECT 1;")
    _write(migrations_dir / "0004_b.sql", "CREATE TABLE b (id INTEGER);")

    pending = Migrator(db, migrations_dir=migrations_dir).pending()
    assert [m.id for m in pending] == ["0004_b"]


def test_apply_pending_runs_all_pending_in_order(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_a.sql", "CREATE TABLE a (id INTEGER);")
    _write(migrations_dir / "0004_b.sql", "CREATE TABLE b (id INTEGER);")

    Migrator(db, migrations_dir=migrations_dir).apply_pending()

    applied = [r[0] for r in db.execute(
        "SELECT id FROM schema_migrations ORDER BY sequence"
    ).fetchall()]
    assert applied == ["0003_a", "0004_b"]


def test_apply_pending_stops_on_first_failure(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_good.sql", "CREATE TABLE good (id INTEGER);")
    _write(migrations_dir / "0004_broken.sql", "NOT VALID SQL;")
    _write(migrations_dir / "0005_later.sql", "CREATE TABLE later (id INTEGER);")

    with pytest.raises(Exception):
        Migrator(db, migrations_dir=migrations_dir).apply_pending()

    applied = [r[0] for r in db.execute(
        "SELECT id FROM schema_migrations ORDER BY sequence"
    ).fetchall()]
    assert applied == ["0003_good"]   # earlier success preserved
    # 0004 rolled back, 0005 never attempted


def test_apply_pending_updates_version_int(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    # Seed baseline version int
    db["kospex_config"].insert(
        {"key": "KOSPEX_DB_VERSION_KEY", "value": "2", "format": "INTEGER", "latest": 1},
        pk=["key", "latest"],
    )
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_a.sql", "CREATE TABLE a (id INTEGER);")
    _write(migrations_dir / "0004_b.sql", "CREATE TABLE b (id INTEGER);")

    Migrator(db, migrations_dir=migrations_dir).apply_pending()

    row = list(db.execute(
        "SELECT value FROM kospex_config WHERE key='KOSPEX_DB_VERSION_KEY' AND latest=1"
    ).fetchall())
    assert row == [("4",)]
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_db_migrator.py -v -k "applied or pending or version"`
Expected: failures (`AttributeError` for the new methods).

- [ ] **Step 3: Implement the new methods**

Add to `Migrator` in `src/kospex/db/migrator.py`:
```python
    def discover(self) -> list[Migration]:
        migrations = discover_migrations(self.migrations_dir)
        validate_migrations(migrations, migrations_dir=self.migrations_dir)
        return migrations

    def applied(self) -> list[str]:
        rows = self.db.execute(
            "SELECT id FROM schema_migrations ORDER BY sequence"
        ).fetchall()
        return [r[0] for r in rows]

    def pending(self) -> list[Migration]:
        applied = set(self.applied())
        return [m for m in self.discover() if m.id not in applied]

    def apply_pending(self) -> list[Migration]:
        """Apply all pending migrations in order. Stop and re-raise on first failure.

        Returns the list of migrations actually applied in this run.
        """
        from kospex.db.introspect import invalidate_cache

        ran: list[Migration] = []
        for migration in self.pending():
            self.apply(migration)
            ran.append(migration)
            invalidate_cache(self.db)

        if ran:
            self._update_version_int()
        return ran

    def _update_version_int(self) -> None:
        """Set KOSPEX_DB_VERSION_KEY in kospex_config to max(baseline, max(sequence))."""
        import kospex_schema as KospexSchema

        max_row = list(self.db.execute(
            "SELECT MAX(sequence) FROM schema_migrations"
        ).fetchall())
        max_seq = max_row[0][0] if max_row and max_row[0][0] is not None else 0
        version = max(KospexSchema.KOSPEX_DB_VERSION, max_seq)

        self.db["kospex_config"].upsert(
            {
                "key": KospexSchema.KOSPEX_DB_VERSION_KEY,
                "value": str(version),
                "format": "INTEGER",
                "latest": 1,
            },
            pk=["key", "latest"],
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_db_migrator.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/migrator.py tests/test_db_migrator.py
git commit -m "Add applied/pending/apply_pending with version int update"
```

---

## Task 17: `verify_checksums`

**Files:**
- Modify: `src/kospex/db/migrator.py`
- Modify: `tests/test_db_migrator.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db_migrator.py`:
```python
def test_verify_checksums_clean(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_a.sql", "CREATE TABLE a (id INTEGER);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply_pending()

    issues = migrator.verify_checksums()

    assert issues == []


def test_verify_checksums_detects_modified_file(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    sql = _write(migrations_dir / "0003_a.sql", "CREATE TABLE a (id INTEGER);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply_pending()

    sql.write_text("CREATE TABLE a (id INTEGER, extra TEXT);")
    issues = migrator.verify_checksums()

    assert len(issues) == 1
    assert issues[0]["id"] == "0003_a"
    assert issues[0]["reason"] == "checksum_mismatch"


def test_verify_checksums_detects_missing_file(tmp_path):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    sql = _write(migrations_dir / "0003_a.sql", "CREATE TABLE a (id INTEGER);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply_pending()

    sql.unlink()
    issues = migrator.verify_checksums()

    assert len(issues) == 1
    assert issues[0]["id"] == "0003_a"
    assert issues[0]["reason"] == "file_missing"
```

- [ ] **Step 2: Run to verify failures**

Run: `pytest tests/test_db_migrator.py -v -k verify`
Expected: `AttributeError: 'Migrator' object has no attribute 'verify_checksums'`.

- [ ] **Step 3: Implement `verify_checksums`**

Add to `Migrator`:
```python
    def verify_checksums(self) -> list[dict]:
        """Check applied migrations against on-disk files. Returns a list of issues.

        Each issue is {"id": str, "reason": "file_missing" | "checksum_mismatch"}.
        Warnings only — caller decides whether to print/log/abort.
        """
        issues: list[dict] = []
        on_disk = {m.id: m for m in discover_migrations(self.migrations_dir)}

        rows = self.db.execute(
            "SELECT id, checksum FROM schema_migrations"
        ).fetchall()
        for migration_id, stored_checksum in rows:
            if migration_id not in on_disk:
                issues.append({"id": migration_id, "reason": "file_missing"})
                continue
            actual = on_disk[migration_id].checksum()
            if actual != stored_checksum:
                issues.append({"id": migration_id, "reason": "checksum_mismatch"})

        return issues
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_db_migrator.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/migrator.py tests/test_db_migrator.py
git commit -m "Add verify_checksums for tamper/missing detection"
```

---

## Task 18: `print_status`

**Files:**
- Modify: `src/kospex/db/migrator.py`
- Modify: `tests/test_db_migrator.py`

- [ ] **Step 1: Write a failing test**

Append to `tests/test_db_migrator.py`:
```python
import io


def test_print_status_no_pending(tmp_path, capsys):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()

    Migrator(db, migrations_dir=migrations_dir).print_status()

    out = capsys.readouterr().out
    assert "no migrations" in out.lower() or "0 pending" in out.lower()


def test_print_status_lists_applied_and_pending(tmp_path, capsys):
    from kospex.db.migrator import Migrator
    db = _baseline_db(tmp_path)
    _add_config_table(db)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write(migrations_dir / "0003_done.sql", "CREATE TABLE done (id INTEGER);")
    _write(migrations_dir / "0004_pending.sql", "CREATE TABLE pending (id INTEGER);")

    migrator = Migrator(db, migrations_dir=migrations_dir)
    migrator.apply(migrator.discover()[0])  # apply just 0003

    migrator.print_status()

    out = capsys.readouterr().out
    assert "0003_done" in out
    assert "0004_pending" in out
    assert "Applied" in out or "applied" in out
    assert "Pending" in out or "pending" in out
```

- [ ] **Step 2: Run to verify failures**

Run: `pytest tests/test_db_migrator.py -v -k print_status`
Expected: `AttributeError: 'Migrator' object has no attribute 'print_status'`.

- [ ] **Step 3: Implement `print_status`**

Add to `Migrator`:
```python
    def print_status(self) -> None:
        """Write a human-readable status summary to stdout."""
        import kospex_schema as KospexSchema

        try:
            discovered = self.discover()
        except FileNotFoundError:
            discovered = []

        applied_ids = set(self.applied())
        applied = [m for m in discovered if m.id in applied_ids]
        pending = [m for m in discovered if m.id not in applied_ids]

        # Resolve current version int
        version_row = list(self.db.execute(
            "SELECT value FROM kospex_config WHERE key=? AND latest=1",
            [KospexSchema.KOSPEX_DB_VERSION_KEY],
        ).fetchall())
        version = version_row[0][0] if version_row else "unknown"

        print(f"Kospex DB version: {version}")
        print(f"Database: {self.db.path}")
        print()

        if not discovered:
            print("No migrations on disk.")
            return

        if applied:
            print(f"Applied migrations ({len(applied)}):")
            # Pull applied_at from the table for nicer output
            applied_meta = {
                r[0]: (r[1], r[2]) for r in self.db.execute(
                    "SELECT id, applied_at, duration_ms FROM schema_migrations"
                ).fetchall()
            }
            for m in applied:
                ts, dur = applied_meta.get(m.id, ("?", 0))
                print(f"  {m.id:40s} applied {ts}  ({dur}ms)")
            print()

        if pending:
            print(f"Pending migrations ({len(pending)}):")
            for m in pending:
                kind = "sql + python" if m.py_path else "sql"
                print(f"  {m.id:40s} {kind}")
            print()
            print("Run with -apply to execute pending migrations.")
            print("WARNING: backup your database before applying.")
        else:
            print(f"No pending migrations. DB is at version {version}.")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_db_migrator.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex/db/migrator.py tests/test_db_migrator.py
git commit -m "Add Migrator.print_status"
```

---

## Task 19: Export `Migrator` from `kospex.db`

**Files:**
- Modify: `src/kospex/db/__init__.py`

- [ ] **Step 1: Update `__init__.py` to re-export**

`src/kospex/db/__init__.py`:
```python
"""kospex database helpers — migrator and introspection."""
from kospex.db.migrator import Migrator

__all__ = ["Migrator"]
```

- [ ] **Step 2: Verify import works**

```bash
python -c "from kospex.db import Migrator; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add src/kospex/db/__init__.py
git commit -m "Re-export Migrator from kospex.db"
```

---

## Task 20: Replace `upgrade-db` CLI body

**Files:**
- Modify: `src/kospex_cli.py:1227-1332`

- [ ] **Step 1: Add the import**

Near the top of `kospex_cli.py`, alongside the other imports, add:
```python
from kospex.db import Migrator
```

- [ ] **Step 2: Replace the `upgrade_db` function**

Replace the entire `@cli.command("upgrade-db")` block (lines 1227-1332) with:

```python
@cli.command("upgrade-db")
@click.option(
    "-apply", is_flag=True, default=False,
    help="Apply pending migrations. Without this flag, runs in status/preview mode."
)
def upgrade_db(apply):
    """
    Show the kospex DB migration status, or apply pending migrations.

    Without options: prints current version, applied migrations, pending migrations.
    With -apply: runs each pending migration in sequence (one transaction each).
    """
    click.echo(f"Kospex CLI version {Kospex.VERSION}")
    click.echo("\nWARNING: backup your database before performing ANY upgrade.\n")

    migrator = Migrator(kospex.kospex_db)

    if apply:
        ran = migrator.apply_pending()
        if ran:
            click.echo(f"\nApplied {len(ran)} migration(s).")
        else:
            click.echo("\nNothing to apply.")
    else:
        migrator.print_status()
```

- [ ] **Step 3: Smoke test**

```bash
kospex upgrade-db
```

Expected: prints version, "No migrations on disk." (since `migrations/` is empty), no exceptions.

```bash
kospex upgrade-db -apply
```

Expected: same status, then "Nothing to apply." Exit code 0.

- [ ] **Step 4: Run the full test suite**

Run: `pytest -v`
Expected: all tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/kospex_cli.py
git commit -m "Replace upgrade-db CLI body with Migrator delegation"
```

---

## Task 21: Remove `apply_alter_table_commands` from `kospex_core.py`

**Files:**
- Modify: `src/kospex_core.py:1284-onwards`

- [ ] **Step 1: Confirm no remaining callers**

```bash
grep -rn "apply_alter_table_commands" src/ tests/
```

Expected: only `src/kospex_core.py` (the definition). If any callers appear, stop and investigate — they should have been the now-replaced `upgrade-db` code path.

- [ ] **Step 2: Delete the method**

Remove the entire `def apply_alter_table_commands(self, alter_commands):` method body from `src/kospex_core.py`. The method runs roughly from line 1284 to ~1313 (look for the next `def` to find the end).

- [ ] **Step 3: Run tests**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/kospex_core.py
git commit -m "Remove apply_alter_table_commands (replaced by Migrator)"
```

---

## Task 22: Remove `generate_alter_table` + `validate_square_brackets*` + their tests

**Files:**
- Modify: `src/kospex_schema.py:540-684`
- Modify: `tests/test_kospex_schema.py`

- [ ] **Step 1: Confirm no remaining callers in src/**

```bash
grep -rn "generate_alter_table\|validate_square_brackets" src/
```

Expected: only `src/kospex_schema.py` (definitions). If any other file shows up, that consumer must be removed first.

- [ ] **Step 2: Delete the three functions from `kospex_schema.py`**

Remove these blocks from `src/kospex_schema.py` (line numbers approximate):
- `def generate_alter_table(old_create_sql, new_create_sql, tbl_name):` (lines 540-594)
- `def validate_square_brackets2(create_sql):` (lines 596-651)
- `def validate_square_brackets(create_sql):` (lines 653-684)

Also remove any orphan comments above them (e.g., the "Claude.ai helped out with this one below" comment at line 536-538).

- [ ] **Step 3: Delete `test_generate_alter_table` from `tests/test_kospex_schema.py`**

Remove the entire `def test_generate_alter_table():` function from `tests/test_kospex_schema.py`. Verify nothing else in that file references `generate_alter_table` or `validate_square_brackets`.

- [ ] **Step 4: Run tests**

Run: `pytest -v`
Expected: all remaining tests pass; no `ImportError` or `AttributeError`.

- [ ] **Step 5: Final grep — confirm zero hits**

```bash
grep -rn "KOSPEX_TABLES\|REPO_TABLES\|generate_alter_table\|apply_alter_table_commands\|validate_square_brackets" src/ tests/
```

Expected: no output at all.

- [ ] **Step 6: Commit**

```bash
git add src/kospex_schema.py tests/test_kospex_schema.py
git commit -m "Remove dead auto-alter helpers (superseded by Migrator)"
```

---

## Task 23: Migrations directory README + CLAUDE.md note

**Files:**
- Create: `src/kospex/db/migrations/README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Create the migrations README**

`src/kospex/db/migrations/README.md`:
```markdown
# Kospex DB Migrations

Each migration is identified by a 4-digit prefix. The current baseline is
version 2 (frozen in `kospex_schema.py`), so the next migration starts at `0003`.

## Adding a new migration

1. Create `NNNN_<short-slug>.sql` (next sequential number). Example:
   `0003_add_repos_size_bytes.sql`.
2. (Optional) Create `NNNN_<short-slug>.py` with the same prefix and slug if
   you need a Python backfill. It must export `def up(db): ...` which runs
   *after* the SQL, in the same transaction.
3. Don't include `BEGIN` / `COMMIT` / `ROLLBACK` in your SQL — the runner
   manages the transaction.
4. Once a migration is committed and shipped, **do not edit it.** Write a
   new one.

## Running

- `kospex upgrade-db` — show status (dry run)
- `kospex upgrade-db -apply` — apply pending migrations

See `changes/202605-db-migration-system.md` for the full design.
```

- [ ] **Step 2: Add a brief mention to `CLAUDE.md`**

Find the "Database Operations" section in `CLAUDE.md` and replace the line:
```
- `kospex upgrade-db` - Apply database schema updates
```

With:
```
- `kospex upgrade-db` - Show DB migration status (dry run by default)
- `kospex upgrade-db -apply` - Apply pending DB migrations from `src/kospex/db/migrations/`
```

Also add a new subsection to "Architecture > Core Components" (next to the existing schema bullet):
```
- **kospex/db/migrator.py** - Script-driven DB migration runner
- **kospex/db/introspect.py** - Runtime table introspection helpers
- **kospex/db/migrations/** - Numbered SQL (+ optional Python) migration files
```

- [ ] **Step 3: Commit**

```bash
git add src/kospex/db/migrations/README.md CLAUDE.md
git commit -m "Document migrations directory and updated upgrade-db UX"
```

---

## Task 24: Final smoke test + verify pip install picks everything up

**Files:** (no edits — verification only)

- [ ] **Step 1: Reinstall to pick up `pyproject.toml` and packaging changes**

```bash
pip install -e .
```

Expected: clean install, no errors.

- [ ] **Step 2: Full test suite**

Run: `pytest -v`
Expected: all tests pass. Count the totals before/after the plan started to confirm no test was accidentally dropped.

- [ ] **Step 3: CLI smoke tests**

```bash
kospex --help
kospex upgrade-db
kospex upgrade-db -apply
kreaper drop-table
```

Expected:
- `kospex --help` shows the command list including `upgrade-db`.
- `kospex upgrade-db` shows status with version info and "No migrations on disk."
- `kospex upgrade-db -apply` reports "Nothing to apply."
- `kreaper drop-table` (no arg) lists the live table set including `schema_migrations`.

- [ ] **Step 4: Wheel sanity check**

```bash
python -m build --wheel
unzip -l dist/kospex-*.whl | grep -E "kospex/db"
```

Expected: lists `kospex/db/__init__.py`, `kospex/db/migrator.py`, `kospex/db/introspect.py`, `kospex/db/migrations/__init__.py`, `kospex/db/migrations/README.md`. (No `.sql` yet — none exist — but `.sql` would be included thanks to the `package-data` config from Task 1.)

- [ ] **Step 5: Final commit if anything changed**

If steps above turned up issues you fixed, commit them now. Otherwise no commit needed.

---

## Self-Review

Done after writing — checked against the spec.

**Spec coverage:**
- §Overview & Goals → Tasks 9-19 build the migrator; Tasks 5-8 do the introspection refactor.
- §Architecture & Layout → Task 1 (skeleton), Task 19 (re-export).
- §Migration File Format → Tasks 10-15 (Migration class, discovery, validation, apply, Python step).
- §Tracking → Task 9 (schema_migrations table), Task 16 (version int reconciliation).
- §Runner Behaviour → Tasks 13-18.
- §CLI Surface → Task 20.
- §Table Introspection → Tasks 2-4 (helpers), Tasks 5-8 (consumers).
- §Cleanup & Change List → Tasks 8, 21, 22.
- §Implementation Sequencing → followed (foundation → introspection → schema table → migrator → CLI → cleanup).

**Placeholder scan:** no "TBD" / "TODO" / "fill in" / "similar to". Every step has actual code or commands.

**Type consistency:** `Migration` shape stable across tasks. `Migrator(db, migrations_dir=...)` signature consistent. `validate_migrations(migrations, migrations_dir=None)` consistent. `verify_checksums` returns `list[dict]` with `{"id", "reason"}` shape.

**Edge case noted in spec:** `KospexData(kospex_db=None)` — handled in Task 5 step 5 by fixing `tech_commits()` to pass `self.kospex_db`.

**FK ordering note from spec:** not actioned in tasks (no FKs in current schema, deletion order doesn't matter). If a future migration introduces FKs, `get_repo_tables` would need ordering — out of scope for this PR.
