# pnpm asymmetry fix: wire pnpm into assess() + package_use vocabulary

## Overview

`krunner osi` gained pnpm-lock.yaml support (via `kospex.extractors.pnpm`) in an earlier
session. `kospex sca` and `kospex deps` do not yet support pnpm-lock.yaml — they call
`assess()`, which has no pnpm branch, and `find_dependency_files()`, which has no pnpm
pattern. This doc covers the minimal change to close that gap while also establishing a
concrete `package_use` vocabulary in the database.

Related doc: `changes/202605-osi-dependencies-pipeline.md` (broader consolidation context).

---

## Design decisions

### package_use vocabulary — code constants, free-text DB column

The `package_use` column in `TBL_DEPENDENCY_DATA` exists in the schema but is never
populated. We define named constants in code (not a DB enum / CHECK constraint) and
populate them in the parsers that already have the information. The column stays `TEXT`
so new values can be added without a migration.

| Value | Meaning | Sources |
|---|---|---|
| `direct` | Runtime dep explicitly declared in manifest | pyproject `[dependencies]`, requirements.txt (whole file), package.json `dependencies`, go.mod `require` |
| `dev` | Dev/test-only, not shipped | `devDependencies`, pyproject dev extras |
| `peer` | Expected by the package, not bundled | package.json `peerDependencies` (npm concept) |
| `optional` | Optional extension, not required | `optionalDependencies`, pyproject extras |
| `transitive` | In the resolved closure but not directly declared | pnpm-lock.yaml non-direct entries, yarn.lock, package-lock.json |

Unknown/unmapped values write through with a log warning — data is not dropped.

`requirements.txt` is treated as `direct` throughout (no lockfile means no way to
distinguish direct from transitive at parse time).

### Extractor stays unchanged

`kospex/extractors/pnpm.py` returns `requirements_type` with values `direct`, `dev`,
`resolved`. `assess()` is the translation layer: it maps `resolved → transitive` when
writing to `TBL_DEPENDENCY_DATA`. `krunner osi` continues using `requirements_type` in
its CSV output without disruption.

---

## Files changed

### 1. `src/kospex_schema.py` — add PACKAGE_USE_* constants

```python
PACKAGE_USE_DIRECT      = "direct"
PACKAGE_USE_DEV         = "dev"
PACKAGE_USE_PEER        = "peer"
PACKAGE_USE_OPTIONAL    = "optional"
PACKAGE_USE_TRANSITIVE  = "transitive"

PACKAGE_USE_VALUES = frozenset({
    PACKAGE_USE_DIRECT, PACKAGE_USE_DEV,
    PACKAGE_USE_PEER, PACKAGE_USE_OPTIONAL,
    PACKAGE_USE_TRANSITIVE,
})
```

No DB migration required — `package_use` is already a `TEXT` column.

### 2. `src/kospex_dependencies.py` — `find_dependency_files()`

Add one pattern to the existing regex list:

```python
r"pnpm-lock\.yaml$"
```

This enables `kospex deps -directory` and `kospex deps -repo` to discover pnpm lock files
alongside `package.json`, `requirements.txt`, etc.

### 3. `src/kospex_dependencies.py` — `npm_assess()`

`npm_assess()` iterates `dependencies` then `devDependencies` separately. Stamp
`package_use` on each record as it is built — no parsing change:

| Source block | `package_use` |
|---|---|
| `dependencies` | `PACKAGE_USE_DIRECT` |
| `devDependencies` | `PACKAGE_USE_DEV` |

peer/optional are not handled here (that is Approach 3 / a future session).

### 4. `src/kospex_dependencies.py` — `assess()` pnpm dispatch

Add a new branch in the filename dispatch after the existing `package.json` branch:

```
elif basename == "pnpm-lock.yaml":
    from kospex.extractors.pnpm import extract_pnpm_lock
    packages = extract_pnpm_lock(path)
    _MAP = {"direct": PACKAGE_USE_DIRECT,
            "dev":    PACKAGE_USE_DEV,
            "resolved": PACKAGE_USE_TRANSITIVE}
    for pkg in packages:
        pkg["package_use"] = _MAP.get(pkg.get("requirements_type", ""), "")
        enrichment = self.depsdev_record(pkg["package_name"], pkg["package_version"],
                                         ecosystem="npm")
        if enrichment:
            pkg.update(enrichment)
    results = packages
```

The existing save block at the bottom of `assess()` handles the DB write — pnpm records
flow through it unchanged.

### 5. `src/kospex/extractors/pnpm.py` — no changes

The extractor is stable. `assess()` is the adapter between extractor vocabulary and DB
schema.

---

## What this fixes

| Command | Before | After |
|---|---|---|
| `kospex sca pnpm-lock.yaml` | "not a supported package manager" | enriched results + DB write |
| `kospex deps -file pnpm-lock.yaml` | "not a supported package manager" | enriched results, no DB write |
| `kospex deps -directory /path` | pnpm-lock.yaml silently skipped | pnpm-lock.yaml discovered and assessed |
| `kospex deps -repo /path` | pnpm-lock.yaml silently skipped | pnpm-lock.yaml discovered and assessed |
| `kospex sca -repo` | NOT IMPLEMENTED (pre-existing) | unchanged |

---

## Explicit non-goals (this change)

- Switching `npm_assess` → `parse_package_json` (peer/optional support) — future session
- Populating `package_use` for go.mod, nuget, pyproject parsers — follow-on
- Fixing the `latest` flag demotion issue — separate session
- Making `krunner osi` write to DB — separate session
- pnpm peer/optional classification — the extractor doesn't distinguish them from direct

---

## Testing

- `tests/test_kospex_dependencies.py`: add `assess()` test with a pnpm-lock.yaml v9
  fixture; assert records contain correct `package_use` values (direct, dev, transitive)
- `tests/test_pnpm_extractor.py`: no changes expected (extractor already tested)
- `find_dependency_files()` test: assert pnpm-lock.yaml is returned for a fixture directory
