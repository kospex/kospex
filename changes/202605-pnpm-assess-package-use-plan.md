# pnpm Asymmetry Fix + package_use Vocabulary — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `pnpm-lock.yaml` into `assess()` and `find_dependency_files()`, and stamp `package_use` on npm records, so `kospex sca` and `kospex deps` support pnpm lock files with correct dep-type classification.

**Architecture:** Add named string constants for `package_use` values to `kospex_schema.py` (no DB migration). Update `find_dependency_files()` with one new regex pattern. Stamp `package_use` in `npm_assess()` for records it already produces. Add a pnpm branch in `assess()` that calls `extract_pnpm_lock()`, maps `requirements_type → package_use`, and enriches via `depsdev_record()`. The existing save block in `assess()` handles DB writes unchanged.

**Tech Stack:** Python 3.12, pytest, `unittest.mock.patch`, existing `kospex.extractors.pnpm.extract_pnpm_lock`, `kospex_dependencies.KospexDependencies`, `kospex_schema`.

**Spec:** `changes/202605-pnpm-assess-package-use.md`

---

## File map

| File | Change |
|---|---|
| `src/kospex_schema.py` | Add `PACKAGE_USE_*` constants after line 31 |
| `src/kospex_dependencies.py` | (1) `find_dependency_files()` — add pnpm pattern; (2) `npm_assess()` — stamp `package_use`; (3) `assess()` — add pnpm branch |
| `tests/test_kospex_schema.py` | Add tests for new constants |
| `tests/test_kospex.py` | Add `find_dependency_files` pnpm test, `npm_assess` package_use test, `assess` pnpm test |

No new files. No DB migration.

---

## Task 1: PACKAGE_USE_* constants in kospex_schema.py

**Files:**
- Modify: `src/kospex_schema.py:31`
- Test: `tests/test_kospex_schema.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_kospex_schema.py`:

```python
"""
Tests for kospex schema
"""
import kospex_schema as KospexSchema


class TestPackageUseConstants:
    def test_all_constants_are_strings(self):
        for name in ("PACKAGE_USE_DIRECT", "PACKAGE_USE_DEV", "PACKAGE_USE_PEER",
                     "PACKAGE_USE_OPTIONAL", "PACKAGE_USE_TRANSITIVE"):
            assert isinstance(getattr(KospexSchema, name), str), f"{name} must be a str"

    def test_package_use_values_is_frozenset(self):
        assert isinstance(KospexSchema.PACKAGE_USE_VALUES, frozenset)

    def test_all_constants_in_values_set(self):
        for const in (KospexSchema.PACKAGE_USE_DIRECT, KospexSchema.PACKAGE_USE_DEV,
                      KospexSchema.PACKAGE_USE_PEER, KospexSchema.PACKAGE_USE_OPTIONAL,
                      KospexSchema.PACKAGE_USE_TRANSITIVE):
            assert const in KospexSchema.PACKAGE_USE_VALUES

    def test_values_set_has_five_members(self):
        assert len(KospexSchema.PACKAGE_USE_VALUES) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_kospex_schema.py -v
```

Expected: `AttributeError: module 'kospex_schema' has no attribute 'PACKAGE_USE_DIRECT'`

- [ ] **Step 3: Add constants to kospex_schema.py**

Open `src/kospex_schema.py`. After line 31 (`TBL_MAILMAP = "mailmaps"`), insert:

```python

# package_use vocabulary — free-text DB column, enforced in code only
PACKAGE_USE_DIRECT     = "direct"
PACKAGE_USE_DEV        = "dev"
PACKAGE_USE_PEER       = "peer"
PACKAGE_USE_OPTIONAL   = "optional"
PACKAGE_USE_TRANSITIVE = "transitive"

PACKAGE_USE_VALUES = frozenset({
    PACKAGE_USE_DIRECT, PACKAGE_USE_DEV,
    PACKAGE_USE_PEER, PACKAGE_USE_OPTIONAL,
    PACKAGE_USE_TRANSITIVE,
})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_kospex_schema.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex_schema.py tests/test_kospex_schema.py
git commit -m "feat: add PACKAGE_USE_* constants to kospex_schema"
```

---

## Task 2: Add pnpm-lock.yaml to find_dependency_files()

**Files:**
- Modify: `src/kospex_dependencies.py:1054` (inside `find_dependency_files` `package_files` dict)
- Test: `tests/test_kospex.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_kospex.py`:

```python
import os
from kospex_dependencies import KospexDependencies


def test_find_dependency_files_includes_pnpm(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    kdeps = KospexDependencies()
    found = kdeps.find_dependency_files(str(tmp_path))
    assert any("pnpm-lock.yaml" in f for f in found), \
        f"pnpm-lock.yaml not found in {found}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_kospex.py::test_find_dependency_files_includes_pnpm -v
```

Expected: FAIL — `AssertionError: pnpm-lock.yaml not found`.

- [ ] **Step 3: Add pnpm pattern to find_dependency_files()**

Open `src/kospex_dependencies.py`. In `find_dependency_files()` at line ~1054 (after `"Podfile.lock": "CocoaPods"`), add one entry to the `package_files` dict:

```python
            "pnpm-lock\\.yaml$": "pnpm",
```

The full dict tail should look like:

```python
            "Podfile": "CocoaPods",
            "Podfile.lock": "CocoaPods",
            "pnpm-lock\\.yaml$": "pnpm",
        }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_kospex.py::test_find_dependency_files_includes_pnpm -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex_dependencies.py tests/test_kospex.py
git commit -m "feat: add pnpm-lock.yaml to find_dependency_files()"
```

---

## Task 3: Stamp package_use in npm_assess()

**Files:**
- Modify: `src/kospex_dependencies.py:564-582` (inside `npm_assess`)
- Test: `tests/test_kospex.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_kospex.py`:

```python
import json
from unittest.mock import patch
from kospex_dependencies import KospexDependencies
import kospex_schema as KospexSchema


def test_npm_assess_stamps_package_use(tmp_path):
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({
        "dependencies": {"lodash": "4.17.21"},
        "devDependencies": {"jest": "29.0.0"},
    }))

    kdeps = KospexDependencies()

    def fake_depsdev(pkg_type, name, version):
        return {"package_name": name, "package_version": version, "package_type": pkg_type}

    with patch.object(kdeps, "depsdev_record", side_effect=fake_depsdev):
        results = kdeps.npm_assess(str(pkg_json), dev_deps=True)

    by_name = {r["package_name"]: r for r in results}
    assert by_name["lodash"]["package_use"] == KospexSchema.PACKAGE_USE_DIRECT
    assert by_name["jest"]["package_use"] == KospexSchema.PACKAGE_USE_DEV
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_kospex.py::test_npm_assess_stamps_package_use -v
```

Expected: FAIL — `KeyError: 'package_use'`.

- [ ] **Step 3: Add package_use stamps in npm_assess()**

Open `src/kospex_dependencies.py`. In `npm_assess()`, after the `details = self.get_npm_dependency_dict(item, data)` call for `dependencies` (around line 566), add the stamp. And after `details = self.get_npm_dependency_dict(item, data, dependency_key="devDependencies")` (around line 575), add the dev stamp.

The updated `dependencies` block:

```python
        if "dependencies" in data:
            for item in data.get("dependencies"):
                details = self.get_npm_dependency_dict(item, data)
                details["package_use"] = KospexSchema.PACKAGE_USE_DIRECT
                # ... existing code (results.append, print, table_rows.append) unchanged
```

The updated `devDependencies` block (inside the `if dev_deps:` branch):

```python
        if "devDependencies" in data:
            for item in data["devDependencies"]:
                if dev_deps:
                    details = self.get_npm_dependency_dict(
                        item, data, dependency_key="devDependencies"
                    )
                    details["package_use"] = KospexSchema.PACKAGE_USE_DEV
                    # ... existing code (results.append, print, table_rows.append) unchanged
```

`KospexSchema` is already imported at the top of the file as `import kospex_schema as KospexSchema`.

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_kospex.py::test_npm_assess_stamps_package_use -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex_dependencies.py tests/test_kospex.py
git commit -m "feat: stamp package_use on npm_assess() records (direct/dev)"
```

---

## Task 4: pnpm branch in assess()

**Files:**
- Modify: `src/kospex_dependencies.py:358` (before the `else: return None` in `assess`)
- Test: `tests/test_kospex.py`

Uses fixture: `tests/fixtures/pnpm/v9-simple.yaml` (lodash=direct, jest=dev, two scoped=transitive).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_kospex.py`:

```python
from pathlib import Path
from unittest.mock import patch
from kospex_dependencies import KospexDependencies
import kospex_schema as KospexSchema

PNPM_V9_FIXTURE = str(
    Path(__file__).parent / "fixtures" / "pnpm" / "v9-simple.yaml"
)


def test_assess_pnpm_returns_records_with_package_use():
    kdeps = KospexDependencies()

    def fake_depsdev(pkg_type, name, version):
        return {"package_name": name, "package_version": version, "package_type": pkg_type}

    with patch.object(kdeps, "depsdev_record", side_effect=fake_depsdev):
        results = kdeps.assess(PNPM_V9_FIXTURE)

    assert results is not None, "assess() returned None for pnpm-lock.yaml"
    assert len(results) > 0

    by_name = {r["package_name"]: r for r in results}

    assert by_name["lodash"]["package_use"] == KospexSchema.PACKAGE_USE_DIRECT
    assert by_name["jest"]["package_use"] == KospexSchema.PACKAGE_USE_DEV

    transitive = [r for r in results if r["package_use"] == KospexSchema.PACKAGE_USE_TRANSITIVE]
    assert len(transitive) >= 1, "Expected at least one transitive package"


def test_assess_pnpm_not_none():
    """assess() must not return None for a pnpm-lock.yaml (would print error and exit in CLI)."""
    kdeps = KospexDependencies()
    with patch.object(kdeps, "depsdev_record", return_value={}):
        result = kdeps.assess(PNPM_V9_FIXTURE)
    assert result is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_kospex.py::test_assess_pnpm_returns_records_with_package_use \
       tests/test_kospex.py::test_assess_pnpm_not_none -v
```

Expected: FAIL — `assess()` returns `None` (hits the `else: return None` branch).

- [ ] **Step 3: Add pnpm branch to assess()**

Open `src/kospex_dependencies.py`. In `assess()`, find the block that ends with:

```python
        elif self.is_pip_requirements_file(basefile):
            print(f"Found pip requirements file: {basefile}")
            package_type = "pypi"
            results = self.pypi_assess(
                filename,
                results_file=results_file,
                repo_info=repo_info,
                print_table=print_table,
            )

        else:
            print(f"Unknown or unsupported package manager file found {basefile}")
            return None
```

Insert a new `elif` branch before the `else`:

```python
        elif basefile == "pnpm-lock.yaml":
            from kospex.extractors.pnpm import extract_pnpm_lock
            package_type = "npm"
            _req_to_use = {
                "direct":   KospexSchema.PACKAGE_USE_DIRECT,
                "dev":      KospexSchema.PACKAGE_USE_DEV,
                "resolved": KospexSchema.PACKAGE_USE_TRANSITIVE,
            }
            packages = extract_pnpm_lock(filename)
            for pkg in packages:
                pkg["package_use"] = _req_to_use.get(pkg.get("requirements_type", ""), "")
                enrichment = self.depsdev_record("npm", pkg["package_name"], pkg["package_version"])
                pkg.update(enrichment)
            results = packages

        else:
            print(f"Unknown or unsupported package manager file found {basefile}")
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_kospex.py::test_assess_pnpm_returns_records_with_package_use \
       tests/test_kospex.py::test_assess_pnpm_not_none -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kospex_dependencies.py tests/test_kospex.py
git commit -m "feat: add pnpm-lock.yaml branch to assess() with package_use mapping"
```

---

## Task 5: Full test suite + final verification

- [ ] **Step 1: Run the full test suite**

```bash
pytest -v
```

Expected: all existing tests pass, new tests pass.

- [ ] **Step 2: Smoke test assess() against real pnpm-lock.yaml (optional but recommended)**

If you have a real pnpm project checked out:

```bash
kospex sca /path/to/real/pnpm-lock.yaml
```

Expected: package list printed, not "not a supported package manager".

```bash
kospex deps -directory /path/to/real/pnpm/project
```

Expected: `pnpm-lock.yaml` appears in the discovered dependency files table.

- [ ] **Step 3: Commit CHANGELOG entry**

Add under `## [Unreleased]` / `### Added` in `CHANGELOG.md`:

```markdown
- `kospex sca` and `kospex deps` now support `pnpm-lock.yaml` (lockfile versions 5, 6, 9)
- `package_use` field populated for pnpm packages (`direct`, `dev`, `transitive`) and npm `package.json` packages (`direct`, `dev`)
- `PACKAGE_USE_*` vocabulary constants added to `kospex_schema` for consistent cross-parser use
```

```bash
git add CHANGELOG.md
git commit -m "chore: update CHANGELOG for pnpm sca + package_use vocabulary"
```
