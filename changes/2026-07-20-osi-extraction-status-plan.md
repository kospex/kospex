# `/osi/` Extraction Status — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show on `/osi/` whether kospex has extracted dependency details from each file (a per-file "Extracted?" column) plus a `classify()`-driven commentary grouping the file types with no extracted details by kind.

**Architecture:** Three small additive pieces — a scoped query returning the set of `(_repo_id, file_path)` that have extracted `dependency_data` rows; a pure `osi_extraction_view` helper that annotates each file with `extracted` and builds the commentary buckets (using the merged `classify()`); and the `osi()` route + `osi.html` wiring that renders them. No schema change.

**Tech Stack:** Python 3.12, FastAPI + Jinja2, `sqlite_utils`, `KospexData` query builder, `kospex.extractors.registry.classify`, pytest.

**Design:** `changes/2026-07-20-osi-extraction-status-design.md`.

## Global Constraints

- **Additive only.** Touch `src/kospex_query.py`, `src/kospex_web.py`, `src/kweb2.py`, `src/templates/osi.html`, and their tests. No schema change; no change to the dev-status table, the "N files found" line, or the Dependency File Summary table.
- **`kospex_web.py` is a module of functions** (imported as `import kospex_web as KospexWeb`, called `KospexWeb.func(...)`) — NOT a class. The new helper is a module-level function.
- **The helper is pure**: no DB, no request object, no I/O. It takes `files` and `extracted_keys` and returns `(files, commentary)`. This is what makes it unit-testable.
- **"Extracted?" is realized, per file**: `True` iff `(_repo_id, file_path)` has a `latest=1` row in `dependency_data`. `dependency_data.file_path` equals `file_metadata.Provider` for the same file.
- **`classify()` (kind + supported) is used ONLY to build the commentary** — never shown as its own column. `supported == bool(scanners)` is capability (a parser exists for the type), distinct from realized extraction.
- **`commentary` shape** (template-ready): `{"no_parser": {<kind_value>: {<basename>: <count>}}, "not_scanned": <int>}`. `no_parser` keys are `classify.kind.value` strings then file **basenames**; `not_scanned` counts supported-type files that simply weren't scanned.
- Python 3.12. Run tests from the worktree root: `PYTHONPATH=$PWD/src python -m pytest <path> -p no:cacheprovider`.
- **Execution:** on a fresh git worktree branched off current `main` (subagent-driven-development).

---

## Task 1: `KospexQuery.extracted_dependency_file_keys`

**Files:**
- Modify: `src/kospex_query.py` (add a method to `KospexQuery`, near `get_dependencies` ~line 262)
- Test: `tests/test_extracted_file_keys.py` (create)

**Interfaces:**
- Consumes: `KospexData` (already in the module), `KospexSchema.TBL_DEPENDENCY_DATA`.
- Produces: `KospexQuery.extracted_dependency_file_keys(request_id=None) -> set[tuple[str, str]]` — the set of `(_repo_id, file_path)` with `latest=1` rows in `dependency_data`, scoped like `get_dependencies`.

- [ ] **Step 1: Write the failing test.** Create `tests/test_extracted_file_keys.py`:

```python
"""Tests for KospexQuery.extracted_dependency_file_keys (sub-project B)."""
import sqlite_utils
import kospex_schema as KospexSchema
from kospex_query import KospexQuery


def _kq_with_rows(rows):
    db = sqlite_utils.Database(memory=True)
    db.execute(KospexSchema.SQL_CREATE_DEPENDENCY_DATA)
    for r in rows:
        db["dependency_data"].insert(r, pk=["_repo_id", "hash", "file_path",
                                            "package_type", "package_name",
                                            "package_version"], alter=True)
    return KospexQuery(kospex_db=db)


def test_returns_latest_repo_file_pairs():
    kq = _kq_with_rows([
        {"_repo_id": "s~o~r", "hash": "h1", "file_path": "requirements.txt",
         "package_type": "pypi", "package_name": "a", "package_version": "1", "latest": 1},
        {"_repo_id": "s~o~r", "hash": "h1", "file_path": "requirements.txt",
         "package_type": "pypi", "package_name": "b", "package_version": "2", "latest": 1},
        {"_repo_id": "s~o~r", "hash": "h2", "file_path": "package.json",
         "package_type": "npm", "package_name": "c", "package_version": "3", "latest": 1},
        {"_repo_id": "s~o~old", "hash": "h3", "file_path": "old.txt",
         "package_type": "pypi", "package_name": "d", "package_version": "4", "latest": 0},
    ])
    keys = kq.extracted_dependency_file_keys()
    assert keys == {("s~o~r", "requirements.txt"), ("s~o~r", "package.json")}
    assert isinstance(keys, set)


def test_scoped_by_repo_id():
    kq = _kq_with_rows([
        {"_repo_id": "s~o~r1", "hash": "h1", "file_path": "requirements.txt",
         "package_type": "pypi", "package_name": "a", "package_version": "1", "latest": 1},
        {"_repo_id": "s~o~r2", "hash": "h2", "file_path": "package.json",
         "package_type": "npm", "package_name": "c", "package_version": "3", "latest": 1},
    ])
    keys = kq.extracted_dependency_file_keys({"repo_id": "s~o~r1"})
    assert keys == {("s~o~r1", "requirements.txt")}
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_extracted_file_keys.py -p no:cacheprovider -q`
Expected: FAIL — `AttributeError: 'KospexQuery' object has no attribute 'extracted_dependency_file_keys'`.

- [ ] **Step 3: Implement.** Add to the `KospexQuery` class in `src/kospex_query.py` (immediately after `get_dependencies`):

```python
    def extracted_dependency_file_keys(self, request_id=None):
        """Return the set of (repo_id, file_path) that have extracted dependency
        rows (latest=1) in dependency_data, scoped like get_dependencies. Used by
        /osi/ to mark which discovered files kospex has actually parsed."""
        kd = KospexData(self.kospex_db)
        kd.from_table(KospexSchema.TBL_DEPENDENCY_DATA)
        kd.select("_repo_id", "file_path")
        kd.where("latest", "=", 1)

        if request_id:
            if repo_id := request_id.get("repo_id"):
                kd.where("_repo_id", "=", repo_id)
            elif org_key := request_id.get("org_key"):
                kd.where_org_key(org_key)
            elif server := request_id.get("server"):
                kd.where("_git_server", "=", server)

        return {(row["_repo_id"], row["file_path"]) for row in kd.execute()}
```

- [ ] **Step 4: Run test to verify it passes.** Same command as Step 2. Expected: PASS (2 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/kospex_query.py tests/test_extracted_file_keys.py
git commit -m "feat(query): extracted_dependency_file_keys for /osi/ extraction status"
```

---

## Task 2: `KospexWeb.osi_extraction_view` (pure helper)

**Files:**
- Modify: `src/kospex_web.py` (add a module-level function; add the `classify` import)
- Test: `tests/test_osi_extraction_view.py` (create)

**Interfaces:**
- Consumes: `kospex.extractors.registry.classify` (pure, merged in sub-project A). `classify(name)` returns an object with `.supported: bool` and `.kind` (a `Kind` enum whose `.value` is the string).
- Produces: `kospex_web.osi_extraction_view(files, extracted_keys) -> tuple[list, dict]`. Mutates each file dict in `files` to add `file["extracted"] = bool` and returns `(files, commentary)` with `commentary = {"no_parser": {kind_value: {basename: count}}, "not_scanned": int}`.

- [ ] **Step 1: Write the failing test.** Create `tests/test_osi_extraction_view.py`:

```python
"""Tests for kospex_web.osi_extraction_view (sub-project B)."""
import kospex_web as KospexWeb


def _f(repo, provider, filename=None):
    return {"_repo_id": repo, "Provider": provider,
            "Filename": filename or provider.rsplit("/", 1)[-1]}


def test_marks_extracted_and_buckets_the_rest():
    files = [
        _f("s~o~r", "requirements.txt"),              # extracted (supported)
        _f("s~o~r", "package.json"),                  # NOT extracted, supported -> not_scanned
        _f("s~o~r", "yarn.lock"),                     # NOT extracted, no parser -> no_parser[package]
        _f("s~o~r", "sub/uv.lock", "uv.lock"),        # NOT extracted, no parser -> no_parser[package]
        _f("s~o~r", ".github/Dockerfile", "Dockerfile"),  # NOT extracted -> no_parser[container]
    ]
    extracted = {("s~o~r", "requirements.txt")}

    out_files, commentary = KospexWeb.osi_extraction_view(files, extracted)

    by_provider = {f["Provider"]: f["extracted"] for f in out_files}
    assert by_provider["requirements.txt"] is True
    assert by_provider["package.json"] is False
    assert by_provider["yarn.lock"] is False

    assert commentary["no_parser"]["package"] == {"yarn.lock": 1, "uv.lock": 1}
    assert commentary["no_parser"]["container"] == {"Dockerfile": 1}
    assert commentary["not_scanned"] == 1  # the package.json


def test_all_extracted_gives_empty_commentary():
    files = [_f("s~o~r", "requirements.txt"), _f("s~o~r", "package.json")]
    extracted = {("s~o~r", "requirements.txt"), ("s~o~r", "package.json")}
    out_files, commentary = KospexWeb.osi_extraction_view(files, extracted)
    assert all(f["extracted"] for f in out_files)
    assert commentary == {"no_parser": {}, "not_scanned": 0}
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_osi_extraction_view.py -p no:cacheprovider -q`
Expected: FAIL — `AttributeError: module 'kospex_web' has no attribute 'osi_extraction_view'`.

- [ ] **Step 3: Implement.** At the top of `src/kospex_web.py`, add the import (alongside the existing imports):

```python
import os
from kospex.extractors.registry import classify
```

(If `import os` is already present, do not duplicate it.) Then add this module-level function:

```python
def osi_extraction_view(files, extracted_keys):
    """Annotate /osi/ dependency files with realized extraction status and build
    the commentary buckets.

    files: list of file_metadata dicts (each with _repo_id and Provider).
    extracted_keys: set of (repo_id, file_path) that have extracted dependency rows.

    Sets file["extracted"] = True/False on each file. Returns (files, commentary)
    where commentary = {"no_parser": {kind_value: {basename: count}}, "not_scanned": int}:
      - no_parser: not-extracted files whose type has no parser (classify.supported False),
        grouped by kind then basename.
      - not_scanned: count of not-extracted files whose type IS supported (just not scanned).
    """
    no_parser = {}
    not_scanned = 0

    for f in files:
        key = (f.get("_repo_id"), f.get("Provider"))
        extracted = key in extracted_keys
        f["extracted"] = extracted
        if extracted:
            continue

        result = classify(f.get("Provider") or "")
        if result.supported:
            not_scanned += 1
        else:
            basename = os.path.basename(f.get("Provider") or "")
            kind = result.kind.value
            no_parser.setdefault(kind, {})
            no_parser[kind][basename] = no_parser[kind].get(basename, 0) + 1

    return files, {"no_parser": no_parser, "not_scanned": not_scanned}
```

- [ ] **Step 4: Run test to verify it passes.** Same command as Step 2. Expected: PASS (2 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/kospex_web.py tests/test_osi_extraction_view.py
git commit -m "feat(web): osi_extraction_view helper (extracted flag + commentary buckets)"
```

---

## Task 3: `osi()` route wiring + `osi.html` + render test

**Files:**
- Modify: `src/kweb2.py` (the `osi()` route, ~line 478-502)
- Modify: `src/templates/osi.html` (commentary callout + "Extracted?" column + caption)
- Test: `tests/test_osi_template.py` (create)

**Interfaces:**
- Consumes: `KospexQuery.extracted_dependency_file_keys` (Task 1) and `KospexWeb.osi_extraction_view` (Task 2). `KospexWeb` is already imported in `kweb2.py` as `import kospex_web as KospexWeb`; `KospexQuery` is already used in the route.
- Produces: the rendered `/osi/` page with the new column and callout. Template context gains `commentary`; each `data` row gains `extracted`.

- [ ] **Step 1: Write the failing test.** Create `tests/test_osi_template.py`:

```python
"""Render tests for the /osi/ page (sub-project B)."""
import kweb2


def _render(context):
    tmpl = kweb2.templates.get_template("osi.html")
    base = {"request": None, "data": [], "file_number": 0,
            "dep_files": [], "status": {}, "commentary": {"no_parser": {}, "not_scanned": 0}}
    base.update(context)
    return tmpl.render(base)


def test_extracted_badges_and_commentary():
    rows = [
        {"_repo_id": "s~o~r", "_git_repo": "r", "Provider": "requirements.txt",
         "committer_when": "2025-01-01", "status": "Active", "days_ago": 1, "extracted": True},
        {"_repo_id": "s~o~r", "_git_repo": "r", "Provider": "yarn.lock",
         "committer_when": "2025-01-01", "status": "Active", "days_ago": 1, "extracted": False},
    ]
    commentary = {"no_parser": {"package": {"yarn.lock": 49}, "container": {"Dockerfile": 3}},
                  "not_scanned": 5}
    html = _render({"data": rows, "file_number": 2, "commentary": commentary})
    assert "Extracted?" in html          # new column header
    assert "Yes" in html and "No" in html
    assert "yarn.lock" in html and "49" in html   # commentary names the type + count
    assert "not yet scanned" in html.lower()      # the not_scanned line


def test_all_extracted_message():
    html = _render({"commentary": {"no_parser": {}, "not_scanned": 0}})
    assert "all discovered dependency files have extracted details" in html.lower()
```

- [ ] **Step 2: Run test to verify it fails.**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_osi_template.py -p no:cacheprovider -q`
Expected: FAIL — `assert "Extracted?" in html` fails (column/callout not in the template yet).

- [ ] **Step 3a: Add the commentary callout to `src/templates/osi.html`.** Insert this block immediately after `{% include '_header.html' %}` block's containing `<div class="container mx-auto px-4 mt-12">` opens and BEFORE the `<!-- Page Header -->` div (i.e. as the first card inside the container). Place it right after line `<div class="container mx-auto px-4 mt-12">`:

```html
            <!-- Dependency Extraction Coverage -->
            <div class="bg-white border border-gray-200 rounded-lg shadow-sm mb-8">
                <div class="p-6">
                    <h2 class="text-2xl font-bold text-gray-900 mb-4">
                        Dependency extraction coverage
                    </h2>
                    {% if commentary and (commentary['no_parser'] or commentary['not_scanned']) %}
                        {% if commentary['no_parser'] %}
                        <p class="text-gray-700 mb-2">Not extracted — no parser yet:</p>
                        <ul class="list-disc list-inside text-gray-700 mb-3">
                            {% for kind, types in commentary['no_parser'].items() %}
                            <li>
                                <span class="font-semibold">{{ kind }}</span>:
                                {% for name, count in types.items() %}{{ name }} ({{ count }}){% if not loop.last %}, {% endif %}{% endfor %}
                            </li>
                            {% endfor %}
                        </ul>
                        {% endif %}
                        {% if commentary['not_scanned'] %}
                        <p class="text-gray-700">
                            {{ commentary['not_scanned'] }} supported file{{ 's' if commentary['not_scanned'] != 1 else '' }} not yet scanned — run <code>kospex sca</code> / <code>krunner osi</code> to extract them.
                        </p>
                        {% endif %}
                    {% else %}
                        <p class="text-gray-700">All discovered dependency files have extracted details.</p>
                    {% endif %}
                </div>
            </div>
```

- [ ] **Step 3b: Add the "Extracted?" column to the Individual Files table.** In the `#myTable` `<thead>`, after the "Days Ago" `<th>` (the last header, ~line 227), add:

```html
                                    <th
                                        class="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider"
                                    >
                                        Extracted?
                                    </th>
```

In the `<tbody>` loop, after the "Days Ago" `<td>` (`{{ row.get('days_ago',"-") }}`, ~line 261), add:

```html
                                    <td
                                        class="px-6 py-4 whitespace-nowrap text-sm text-center"
                                    >
                                        {% if row.get('extracted') %}
                                        <span class="inline-flex px-2 py-1 text-xs font-semibold rounded-full bg-green-100 text-green-800">Yes</span>
                                        {% else %}
                                        <span class="inline-flex px-2 py-1 text-xs font-semibold rounded-full bg-gray-100 text-gray-700">No</span>
                                        {% endif %}
                                    </td>
```

(Appending as the final column keeps "Days Ago" at column index 4, so the DataTable `order: [[4, "asc"]]` on `#myTable` is unchanged.) Add a caption line right after the `</table>`'s closing `</div>` inside the Individual Files card, before that card's closing `</div>`:

```html
                    <p class="text-sm text-gray-500 mt-3">
                        "Extracted?" = kospex has parsed and enriched dependency details from this file.
                    </p>
```

- [ ] **Step 3c: Wire the route.** In `src/kweb2.py`, replace the body of `osi()` between `deps = KospexQuery().get_dependency_files(request_id=params)` and the `return templates.TemplateResponse(...)` so it computes the extraction view. The updated section:

```python
        params = KospexWeb.get_id_params(id)
        deps = KospexQuery().get_dependency_files(request_id=params)

        for file in deps:
            file["days_ago"] = KospexUtils.days_ago(file.get("committer_when"))
            file["status"] = KospexUtils.development_status(file.get("days_ago"))

        extracted_keys = KospexQuery().extracted_dependency_file_keys(request_id=params)
        deps, commentary = KospexWeb.osi_extraction_view(deps, extracted_keys)

        file_number = len(deps)
        status = KospexUtils.repo_stats(deps, "committer_when")
        filenames = KospexUtils.filenames_by_repo_id(deps)

        return templates.TemplateResponse(
            request, "osi.html",
            {
                "data": deps,
                "file_number": file_number,
                "dep_files": filenames,
                "status": status,
                "commentary": commentary,
            },
        )
```

- [ ] **Step 4: Run the render test to verify it passes.**

Run: `PYTHONPATH=$PWD/src python -m pytest tests/test_osi_template.py -p no:cacheprovider -q`
Expected: PASS (2 tests). If `_header.html`/`_datatable_scripts.html` includes raise on the `None` request, mirror how `tests/test_dependencies_template.py` handles context — but they render fine there with `request=None`, so no extra context should be needed.

- [ ] **Step 5: Run the full suite.**

Run: `PYTHONPATH=$PWD/src python -m pytest -p no:cacheprovider -q`
Expected: PASS — pre-existing suite plus the three new test files, zero failures. (The 2 pre-existing `/osi/`/web live-server tests that need a running server are skipped without one — unchanged by this task.)

- [ ] **Step 6: Commit.**

```bash
git add src/kweb2.py src/templates/osi.html tests/test_osi_template.py
git commit -m "feat(web): show per-file Extracted? + extraction-coverage commentary on /osi/"
```

---

## Self-review (author checklist — completed)

- **Spec coverage:** "Extracted?" column ✓ (T3 template + T2 flag + T1 query). Commentary callout grouped by kind ✓ (T2 buckets + T3 template). Pure helper ✓ (T2). Scoped query ✓ (T1). All three testable without a live server ✓ (unit + in-process render). Deferrals (staleness, N4–N6, Summary/dev-status tables) require no code — untouched. No spec requirement is left without a task.
- **Placeholder scan:** none — every step has runnable code/commands and expected output.
- **Type consistency:** `extracted_dependency_file_keys(request_id) -> set[(repo_id, file_path)]` (T1) is consumed as `extracted_keys` by `osi_extraction_view(files, extracted_keys) -> (files, commentary)` (T2); the route (T3) passes T1's output to T2 and T2's `commentary` to the template. `commentary` shape `{"no_parser": {kind: {basename: count}}, "not_scanned": int}` is identical across T2's implementation, T2's test, and T3's template/test. `file["extracted"]` (bool) is set in T2 and read in T3's template.
