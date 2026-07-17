# Extractor Registry + `manifest_support()` Classifier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single source of truth that classifies a dependency-bearing filename into its *kind* (package/runtime/container/sca_config/lockfile/unknown) and current *support state*, as pure library code with no scanner/CLI/DB/UI behaviour change.

**Architecture:** One additive module, `src/kospex/extractors/registry.py`, holding a `Kind` enum, an `Extractor` descriptor dataclass, a `REGISTRY` catalog of the file types kospex recognises, a `Classification` result dataclass, and a `classify(filename)` function. Everything is pure (no DB, no CLI, no I/O). Later sub-projects (B `/osi/` display, C scanner parity, D new parsers) consume this; A only builds and tests it.

**Tech Stack:** Python 3.12, `dataclasses`, `enum`, `re`, `importlib`, pytest.

**Design:** `changes/2026-07-17-extractor-registry-classifier-design.md`.

## Global Constraints

- **Additive only.** Create `src/kospex/extractors/registry.py` and `tests/test_extractor_registry.py`. Do NOT modify any scanner, CLI, DB, template, or existing parser. No runtime behaviour changes.
- **Pure module.** `registry.py` does no DB access, no CLI concerns, and no I/O beyond nothing — it never opens a file; it classifies *filenames* (strings). Follows the conventions in `src/kospex/extractors/__init__.py`.
- **Python 3.12.** Use `from __future__ import annotations`; `tuple[str, ...]` / `Optional[...]` type hints.
- **`supported` is derived**, never stored: `supported == bool(extractor.scanners)`. Per the approved decision `/osi/` treats "supported" as scannable by *any* path, so `go.mod` (`scanners=("sca",)`) is `supported=True`.
- **Matchers are pure and total**: each takes a basename string and returns `bool`, never raising, for any string input (including `""`).
- **Matchers are mutually exclusive** over real filenames — at most one registry entry matches any given basename (keeps sub-project C's future dispatch deterministic).
- **`parse_ref`** is recorded but NOT invoked in A. Format: `"module:callable"` or `"module:Class.method"`. It must resolve to a callable (guards typos; wired for dispatch in C).
- **Run tests from the worktree root:** `PYTHONPATH=$PWD/src python -m pytest <path> -p no:cacheprovider`.
- **Execution:** on a fresh git worktree branched off current `main` (subagent-driven-development).

---

## Task 1: Registry module + classifier truth-table

**Files:**
- Create: `src/kospex/extractors/registry.py`
- Create (test): `tests/test_extractor_registry.py`

**Interfaces:**
- Consumes: nothing (leaf module). May reference (as strings only, in `parse_ref`, not imported here) existing callables: `kospex_dependencies:KospexDependencies.parse_pip_requirements_file`, `.parse_pyproject_file`, `.parse_package_json`, `.gomod_assess`, `.nuget_assess`, and `kospex.extractors.pnpm:extract_pnpm_lock`.
- Produces (relied on by Task 2, sub-projects B/C/D):
  - `Kind` (str Enum): `PACKAGE, RUNTIME, CONTAINER, SCA_CONFIG, LOCKFILE, UNKNOWN`.
  - `Extractor` frozen dataclass: `name: str`, `kind: Kind`, `matches: Callable[[str], bool]`, `scanners: tuple[str, ...]=()`, `package_type: Optional[str]=None`, `parse_ref: Optional[str]=None`.
  - `Classification` frozen dataclass: `kind: Kind`, `supported: bool`, `scanners: tuple[str, ...]`, `extractor: Optional[Extractor]`.
  - `REGISTRY: tuple[Extractor, ...]`.
  - `classify(filename: str) -> Classification`.

- [ ] **Step 1: Write the failing test.** Create `tests/test_extractor_registry.py`:

```python
"""Tests for the dependency-file extractor registry + classifier (sub-project A).

See changes/2026-07-17-extractor-registry-classifier-design.md.
"""
import importlib
import os

import pytest

from kospex.extractors.registry import Kind, REGISTRY, classify

# (filename, expected kind, expected supported) — drawn from the reference DB audit.
CASES = [
    ("requirements.txt", Kind.PACKAGE, True),
    ("requirements-dev.txt", Kind.PACKAGE, True),
    ("requirements.in", Kind.PACKAGE, True),
    ("requirements_merge_arrow_pr.txt", Kind.PACKAGE, True),
    ("pyproject.toml", Kind.PACKAGE, True),
    ("package.json", Kind.PACKAGE, True),
    ("pnpm-lock.yaml", Kind.PACKAGE, True),
    ("go.mod", Kind.PACKAGE, True),        # sca-only, but supported == any scanner
    ("Foo.csproj", Kind.PACKAGE, True),    # sca-only
    ("yarn.lock", Kind.PACKAGE, False),
    ("uv.lock", Kind.PACKAGE, False),
    ("package-lock.json", Kind.PACKAGE, False),
    ("build.gradle", Kind.PACKAGE, False),
    (".python-version", Kind.RUNTIME, False),
    (".nvmrc", Kind.RUNTIME, False),
    ("Dockerfile", Kind.CONTAINER, False),
    ("dependabot.yml", Kind.SCA_CONFIG, False),
    ("renovate.json", Kind.SCA_CONFIG, False),
    ("go.sum", Kind.LOCKFILE, False),
    ("mystery.xyz", Kind.UNKNOWN, False),
]


@pytest.mark.parametrize("fname,kind,supported", CASES)
def test_classify(fname, kind, supported):
    c = classify(fname)
    assert c.kind == kind
    assert c.supported is supported
    assert c.supported == bool(c.scanners)


def test_classify_uses_basename():
    assert classify("path/to/requirements.txt").kind == Kind.PACKAGE
    assert classify("/abs/dir/Dockerfile").kind == Kind.CONTAINER


def test_package_json_matcher_is_exact_not_lockfile():
    # The tightened package.json matcher must NOT swallow package-lock.json,
    # which is a separate (unsupported) registry row.
    assert classify("package.json").extractor.name == "npm-packagejson"
    assert classify("package-lock.json").extractor.name == "npm-lock"
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_extractor_registry.py -p no:cacheprovider -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kospex.extractors.registry'` (collection error).

- [ ] **Step 3: Write the module.** Create `src/kospex/extractors/registry.py`:

```python
"""Registry of the dependency-bearing file types kospex recognises.

Single source of truth for *which* manifest / lock / config files kospex can
classify, what *kind* of dependency each declares, and whether a scanner can
parse it today. Pure: no DB, no CLI, no I/O — it classifies filenames
(strings), it never opens a file. See
changes/2026-07-17-extractor-registry-classifier-design.md.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class Kind(str, Enum):
    """The kind of dependency a file declares."""
    PACKAGE = "package"        # named library deps (requirements.txt, package.json, ...)
    RUNTIME = "runtime"        # a language/toolchain version (.python-version, .nvmrc)
    CONTAINER = "container"    # base image + build-time installs (Dockerfile)
    SCA_CONFIG = "sca_config"  # scan config, declares no deps itself (dependabot.yml)
    LOCKFILE = "lockfile"      # integrity/checksum companion, not scanned alone (go.sum)
    UNKNOWN = "unknown"        # dependency-tagged file matching no registry entry


@dataclass(frozen=True)
class Extractor:
    """One recognised dependency-file type.

    `matches` is a pure basename predicate. `scanners` names the scan paths
    that handle this type TODAY (subset of "sca"/"osi"); support is derived
    from it. `package_type` is the DB package_type value, meaningful only for
    kind=PACKAGE. `parse_ref` is a "module:callable" reference to the in-place
    parser (documentation in A; the dispatch target in sub-project C), or None
    when no parser exists yet.
    """
    name: str
    kind: Kind
    matches: Callable[[str], bool]
    scanners: tuple[str, ...] = ()
    package_type: Optional[str] = None
    parse_ref: Optional[str] = None


@dataclass(frozen=True)
class Classification:
    """Result of classifying a filename."""
    kind: Kind
    supported: bool
    scanners: tuple[str, ...]
    extractor: Optional[Extractor]


def _matcher(pattern: str) -> Callable[[str], bool]:
    """Build a pure, total basename predicate from a regex (full, case-insensitive)."""
    rx = re.compile(pattern, re.IGNORECASE)

    def _match(basename: str) -> bool:
        return bool(rx.fullmatch(basename or ""))

    return _match


REGISTRY: tuple[Extractor, ...] = (
    # --- package: supported by both scan paths ---
    Extractor("pypi-requirements", Kind.PACKAGE,
              _matcher(r"requirements([-_][\w.]*)?\.(txt|in)"), ("sca", "osi"), "pypi",
              "kospex_dependencies:KospexDependencies.parse_pip_requirements_file"),
    Extractor("pyproject", Kind.PACKAGE, _matcher(r"pyproject\.toml"),
              ("sca", "osi"), "pypi",
              "kospex_dependencies:KospexDependencies.parse_pyproject_file"),
    Extractor("npm-packagejson", Kind.PACKAGE, _matcher(r"package\.json"),
              ("sca", "osi"), "npm",
              "kospex_dependencies:KospexDependencies.parse_package_json"),
    Extractor("pnpm-lock", Kind.PACKAGE, _matcher(r"pnpm-lock\.yaml"),
              ("sca", "osi"), "npm",
              "kospex.extractors.pnpm:extract_pnpm_lock"),
    # --- package: supported by kospex sca only (krunner osi gap -> sub-project C) ---
    Extractor("go-mod", Kind.PACKAGE, _matcher(r"go\.mod"), ("sca",), "go",
              "kospex_dependencies:KospexDependencies.gomod_assess"),
    Extractor("nuget-csproj", Kind.PACKAGE, _matcher(r".*\.csproj"), ("sca",), "nuget",
              "kospex_dependencies:KospexDependencies.nuget_assess"),
    # --- package: recognised but no parser yet (-> sub-project D) ---
    Extractor("yarn-lock", Kind.PACKAGE, _matcher(r"yarn\.lock"), (), "npm", None),
    Extractor("uv-lock", Kind.PACKAGE, _matcher(r"uv\.lock"), (), "pypi", None),
    Extractor("npm-lock", Kind.PACKAGE, _matcher(r"package-lock\.json"), (), "npm", None),
    Extractor("gradle-build", Kind.PACKAGE, _matcher(r"build\.gradle(\.kts)?"), (), "maven", None),
    # --- runtime ---
    Extractor("python-version", Kind.RUNTIME, _matcher(r"\.python-version"), (), None, None),
    Extractor("nvmrc", Kind.RUNTIME, _matcher(r"\.nvmrc"), (), None, None),
    # --- container ---
    Extractor("dockerfile", Kind.CONTAINER,
              _matcher(r"dockerfile(\..+)?|.+\.dockerfile"), (), None, None),
    # --- sca config (declares no deps itself) ---
    Extractor("dependabot", Kind.SCA_CONFIG, _matcher(r"dependabot\.ya?ml"), (), None, None),
    Extractor("renovate", Kind.SCA_CONFIG,
              _matcher(r"renovate\.json|\.renovaterc(\.json)?"), (), None, None),
    # --- lockfile (integrity only) ---
    Extractor("go-sum", Kind.LOCKFILE, _matcher(r"go\.sum"), (), None, None),
)


def classify(filename: str) -> Classification:
    """Classify a dependency-file name into its kind and support state.

    Assumes the file was tagged `|dependencies|` by panopticas (callers such as
    /osi/ pre-filter on that tag); a file matching no registry entry returns
    kind=UNKNOWN. Matching is on the basename. Returns the first matching
    REGISTRY entry's (kind, bool(scanners), scanners, entry), else
    (UNKNOWN, False, (), None).
    """
    basename = os.path.basename(filename or "")
    for entry in REGISTRY:
        if entry.matches(basename):
            return Classification(entry.kind, bool(entry.scanners), entry.scanners, entry)
    return Classification(Kind.UNKNOWN, False, (), None)
```

- [ ] **Step 4: Run test to verify it passes.**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_extractor_registry.py -p no:cacheprovider -q`
Expected: PASS — all 22 parametrized `test_classify` cases plus `test_classify_uses_basename` and `test_package_json_matcher_is_exact_not_lockfile` (24 passing).

- [ ] **Step 5: Commit.**

```bash
git add src/kospex/extractors/registry.py tests/test_extractor_registry.py
git commit -m "feat(extractors): registry + classify() for dependency-file kinds"
```

---

## Task 2: Registry-invariant guard tests

**Files:**
- Modify (append): `tests/test_extractor_registry.py`

**Interfaces:**
- Consumes: `Kind`, `REGISTRY`, `classify`, and the `CASES` table from Task 1 (same module/file). Uses stdlib `importlib`, `os`.
- Produces: nothing new — guard tests only.

These tests lock the catalog's invariants and tie it to real code. They should PASS against Task 1's module as written; if any fails, it has found a real defect in Task 1 (fix `registry.py`, do not weaken the test).

- [ ] **Step 1: Write the tests.** Append to `tests/test_extractor_registry.py`:

```python
def test_matchers_are_total():
    # Every matcher returns a bool and never raises, for any string input.
    odd = ["", "   ", "wiérd", "a/b/c", "UPPER.TXT", "noext", ".hidden", "pyproject"]
    for e in REGISTRY:
        for s in odd:
            assert e.matches(s) in (True, False)


def test_matchers_mutually_exclusive():
    # At most one registry entry matches any real basename (deterministic dispatch).
    for fname, kind, _supported in CASES:
        if kind == Kind.UNKNOWN:
            continue
        base = os.path.basename(fname)
        hits = [e.name for e in REGISTRY if e.matches(base)]
        assert len(hits) == 1, f"{fname!r} matched {hits}"


def test_coverage_matrix_records_the_krunner_gap():
    scanners = {e.name: e.scanners for e in REGISTRY}
    # sca-only today — sub-project C makes krunner osi handle these too.
    assert scanners["go-mod"] == ("sca",)
    assert scanners["nuget-csproj"] == ("sca",)
    # shared by both scan paths.
    for name in ("pypi-requirements", "pyproject", "npm-packagejson", "pnpm-lock"):
        assert scanners[name] == ("sca", "osi")


def test_parse_ref_resolves_to_a_callable():
    # Guards typos and ties the registry to real code (not invoked in A).
    for e in REGISTRY:
        if e.parse_ref is None:
            continue
        module_name, qualname = e.parse_ref.split(":")
        obj = importlib.import_module(module_name)
        for part in qualname.split("."):
            obj = getattr(obj, part)
        assert callable(obj), e.parse_ref


def test_unsupported_entries_have_no_parse_ref():
    # If nothing scans it, there is no parser to point at (and vice versa).
    for e in REGISTRY:
        assert bool(e.scanners) == (e.parse_ref is not None), e.name
```

- [ ] **Step 2: Run the tests to verify they pass.**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_extractor_registry.py -p no:cacheprovider -q`
Expected: PASS — the full file (Task 1's 24 + these 5 guard tests). If `test_parse_ref_resolves_to_a_callable` fails, a `parse_ref` in `registry.py` names a function that does not exist — fix the reference in `registry.py`. If `test_matchers_mutually_exclusive` fails, two matchers overlap — tighten the offending pattern in `registry.py`.

- [ ] **Step 3: Run the whole suite to confirm nothing else moved.**

Run: `PYTHONPATH=$PWD/src python -m pytest -p no:cacheprovider -q`
Expected: PASS — the pre-existing suite plus the new file, zero failures (A is additive).

- [ ] **Step 4: Commit.**

```bash
git add tests/test_extractor_registry.py
git commit -m "test(extractors): registry invariants (totality, exclusivity, coverage matrix, parse_ref)"
```

---

## Self-review (author checklist — completed)

- **Spec coverage:** `Kind` enum ✓ (T1), `Extractor`/`Classification` dataclasses ✓ (T1), full `REGISTRY` catalog of 16 rows ✓ (T1), `classify()` returning `(kind, supported, scanners, extractor)` ✓ (T1). Tests: truth-table ✓ (T1), matcher totality ✓ (T2), mutual exclusivity ✓ (T2), coverage matrix ✓ (T2), `parse_ref` resolvability ✓ (T2). Deferred N1–N6 require no code in A (documented in the design). No spec requirement is left without a task.
- **Placeholder scan:** none — every step has runnable code/commands and expected output.
- **Type consistency:** `Kind`, `Extractor` (`name/kind/matches/scanners/package_type/parse_ref`), `Classification` (`kind/supported/scanners/extractor`), `REGISTRY`, and `classify()` are named identically across Task 1, Task 2, and the Interfaces blocks. `supported == bool(scanners)` is asserted in both the truth-table (T1) and the invariant tests (T2, via `test_unsupported_entries_have_no_parse_ref` + the matrix).
