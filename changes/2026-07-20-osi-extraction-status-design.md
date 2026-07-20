# `/osi/` extraction status — design

**Date:** 2026-07-20
**Status:** Approved (design) — ready for implementation plan
**Sub-project:** B of the 4-part extractor program (A registry+classifier ✅ merged · **B `/osi/` display** · C scanner parity · D new extractors)

## Motivation

`/osi/` lists the dependency files kospex has discovered but says nothing about whether kospex has actually pulled the dependencies *out* of each one. A reader can't tell a `package.json` we parsed into 40 enriched dependencies from a `yarn.lock` we can't parse at all — both are just rows. Sub-project A shipped `kospex.extractors.registry.classify()`; this surfaces it on `/osi/`.

The signal a user wants is **realized, per file**: *have we extracted dependency details from this file?* — not the abstract "is this file type supported." Those are different axes (see below), and only the realized one goes on the page.

## Definitions (the two axes, kept distinct)

- **Extracted (realized, per file):** `dependency_data` holds `latest=1` rows for this exact `(_repo_id, file_path)`. Yes = kospex parsed this file into dependency records (and enriched the package ones via deps.dev). This is the **only** new per-file signal shown.
- **Supported (capability, per type):** a parser exists for this file *type* (`classify().supported == bool(scanners)`). Does **not** imply we ran it. A supported type can be un-extracted because the repo was never scanned. Used **only** behind the scenes to explain the No's in the commentary — never shown as its own column.

`classify()` assumes a `|dependencies|`-tagged filename; `/osi/`'s `get_dependency_files` already filters to exactly that, so every row is valid input.

## Scope

**In:**
- One new **"Extracted?"** column on the Individual Files table (Yes/No, realized).
- A **commentary callout above the tables** naming, grouped by kind, the file types with no extracted details — using `classify()` for the *why*.
- The route computation + a pure aggregation helper + one query method, all unit-testable.

**Out (unchanged / deferred):**
- The dev-status count table, the "N files found" line, and the Dependency File Summary table — untouched (YAGNI).
- **Staleness** — `/osi/` reflects whatever the last `krunner osi` / `kospex sca` captured; it does not trigger extraction. The "supported but not scanned" commentary bucket *surfaces* this; auto-refresh/trigger is a later concern (noted, not built).
- **N5 (umbrella with a separate actions interface)** and **N6 (kind-based drill-down to `/cicd/`)** from the A design — still deferred; the actions interface does not exist. Package-kind rows keep their existing `/dependencies/{repo_id}` link.
- No schema change.

## Data flow (route: `kweb2.py` `osi()`)

Today the route builds `deps` (file_metadata rows, each with `_repo_id` + `Provider`) and derives `status` / `dep_files`. It gains three steps before rendering:

1. **Realized-extracted set** — `extracted_keys = KospexQuery().extracted_dependency_file_keys(request_id)` → a `set` of `(_repo_id, file_path)` that have `latest=1` rows in `dependency_data`, scoped to the same `request_id`. (`dependency_data.file_path` equals `file_metadata.Provider` for the same file — this is the join already used to count 416 assessed files.)
2. **Annotate + aggregate** — `annotated, commentary = KospexWeb.osi_extraction_view(deps, extracted_keys)` (pure helper, below). Sets `file["extracted"] = True/False` on each row and builds the commentary buckets.
3. Pass `commentary` into the template context alongside the existing keys; `deps` now carries the `extracted` flag per row.

### Pure helper — `KospexWeb.osi_extraction_view(files, extracted_keys)`

Module-level/staticmethod in `kospex_web.py` (pure — no DB, no request), so it is unit-tested with synthetic inputs.

- Input: `files` (list of dicts with `_repo_id`, `Provider`), `extracted_keys` (set of `(_repo_id, file_path)`).
- For each file: `file["extracted"] = (file["_repo_id"], file["Provider"]) in extracted_keys`.
- For every **not-extracted** file, call `classify(file["Provider"])` and bucket:
  - **`no_parser`** — `classify.supported is False`: keyed by kind's **string value** (`classify.kind.value`, e.g. `"package"`, `"container"`), then by file **basename** (`os.path.basename(file["Provider"])`, e.g. `"yarn.lock"` — not the full path `pydantic-core/uv.lock`): `{ "package": {"yarn.lock": 49, "uv.lock": 22}, "container": {"Dockerfile": 12} }`.
  - **`not_scanned`** — `classify.supported is True`: a single integer count (supported types we simply haven't scanned).
- Returns `(files, commentary)` where `commentary = {"no_parser": {<kind>: {<basename>: <count>}}, "not_scanned": <int>}`. Both buckets are ordinary dicts/ints so the template can iterate them directly.

Return shape is deliberately template-ready: the callout iterates `no_parser` by kind and prints the `not_scanned` count.

## What `/osi/` shows

- **Commentary callout** (new card, placed above "Dependency File Status"): title *"Dependency extraction coverage."*
  - If `no_parser` is non-empty: *"Not extracted — no parser yet:"* then, grouped by kind, the file types with counts (`yarn.lock (49)`, `uv.lock (22)`; runtime: `.python-version`; container: `Dockerfile` …).
  - If `not_scanned > 0`: *"N supported files not yet scanned — run `kospex sca` / `krunner osi` to extract them."*
  - If both empty: a single line *"All discovered dependency files have extracted details."*
- **Individual Files table** gains an **"Extracted?"** column: a green **Yes** badge (the Repo cell already links to `/dependencies/{repo_id}` where those details live) or a gray **No** badge. Caption under the table: *"'Extracted?' = kospex has parsed and enriched dependency details from this file."*
- Everything else on the page is unchanged.

## Query — `KospexQuery.extracted_dependency_file_keys(request_id)`

Mirrors the scoping of `get_dependencies` (repo_id / org_key / server), returns a `set` of `(_repo_id, file_path)`:

```sql
SELECT DISTINCT _repo_id, file_path FROM dependency_data WHERE latest = 1 [ + scope ]
```

## Files

- `src/kweb2.py` — `osi()` route: two new calls + pass `commentary`.
- `src/kospex_web.py` — new pure `osi_extraction_view(files, extracted_keys)`.
- `src/kospex_query.py` — new `extracted_dependency_file_keys(request_id)`.
- `src/templates/osi.html` — commentary callout + "Extracted?" column + caption.

## Testing

- **`osi_extraction_view` unit tests** (pure, synthetic files — no DB/server): an extracted file → `extracted=True`, not in any bucket; a not-extracted supported file (`package.json`) → `extracted=False`, counted in `not_scanned`; an unsupported manifest (`yarn.lock`) → `no_parser["package"]["yarn.lock"] == 1`; a `Dockerfile` → `no_parser["container"]`; the all-extracted case → both buckets empty.
- **Template render** (in-process, the `test_dependencies_template.py` pattern): render `osi.html` with rows for each state + a sample commentary → the "Extracted?" Yes/No badges appear, the callout names `yarn.lock` and shows the `not_scanned` count, and the all-extracted case shows the "all extracted" line. No crash.
- **Query test** — against an in-memory DB with a `dependency_data` fixture: `extracted_dependency_file_keys` returns exactly the `latest=1` `(_repo_id, file_path)` pairs, honouring scope.

## Boundary

B stops at *surfacing* extraction status honestly. It does not add parsers (D), route scanners through the registry (C), trigger extraction, or build the actions/`cicd` surface (N4–N6). Package rows keep the existing `/dependencies/` drill-through.
