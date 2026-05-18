# OSI / Dependencies pipeline — current state and hardening plan

## Overview

The `/osi/` and `/dependencies/` web endpoints in `kweb2.py` are partially implemented. They render correctly when data exists, but the pipeline that populates `TBL_DEPENDENCY_DATA` is fragmented and largely manual — there is no single command that refreshes dependency data for a synced scope, and the most complete enrichment path (`krunner osi`) writes to CSV instead of the database.

This doc captures the current data flow, the gaps, and a prioritised plan to make these endpoints reliable.

## Endpoint definitions

| Route | File | Reads from | Query method |
|---|---|---|---|
| `/osi/`, `/osi/{id}` | `src/kweb2.py:476-505` | `TBL_FILE_METADATA` (file rows tagged `tech_type LIKE '%\|dependencies\|%'`) | `KospexQuery.get_dependency_files()` — `src/kospex_query.py:237-259` |
| `/dependencies/`, `/dependencies/{id}` | `src/kweb2.py:1224-1237` | `TBL_DEPENDENCY_DATA` (per-package rows: name, version, `versions_behind`, advisories) | `KospexQuery.get_dependencies()` — `src/kospex_query.py:261-282` |

The two endpoints read two **different** tables:

- **OSI** lists *dependency files* discovered during repo sync (package.json, requirements.txt, pyproject.toml, etc.). These are populated as a side effect of `kospex sync`, which walks the tree and tags file types in `TBL_FILE_METADATA`.
- **Dependencies** lists the *parsed contents* of those files — package name/version, deps.dev enrichment (versions_behind, advisories, published_at).

## How `TBL_DEPENDENCY_DATA` gets populated — the gap

`KospexDependencies.assess()` (`src/kospex_dependencies.py:282-389`) is the only writer. It only writes when called with `save=True` (line 365), upserting on key `(_repo_id, hash, file_path, package_type, package_name, package_version)`.

Searching the codebase, `save=True` is never set explicitly. The only path that writes is `kospex sca FILE_PATH` (`src/kospex_cli.py:712-737`), where `-save` defaults to True and `params = locals()` forwards it.

Caller summary:

| Caller | Writes to `TBL_DEPENDENCY_DATA`? |
|---|---|
| `kospex sca FILE` | yes (via `-save` default) |
| `kospex deps -file FILE` (`src/kospex_cli.py:679`) | no — no `save` kwarg |
| `krunner osi [-all\|request_id]` (`src/krunner.py:562-692`) | no — writes `OSI-*.csv` only |
| `kospex sync REPO` | no — only file metadata (feeds `/osi/`, not `/dependencies/`) |
| `/upload` handler (`src/kweb2.py:1299`) | no — `assess(temp_path)` with no save |

Result: `/dependencies/` only has data if a user manually ran `kospex sca` per file. That's why `dependencies.html:42-59` carries a "Work in Progress — data may not be current" banner.

## Other concrete gaps

- **`get_dependencies()` has no `latest=1` filter.** `src/kospex_query.py:268` is commented out, so old versions of a package will be returned alongside current ones (double-counting).
- **~~`sync-dependencies -repo` is unimplemented.~~** Resolved 2026-05-18 — the entire `sync-dependencies` command (obsolete CLI-era clone-and-sync-source-repos flow; `-repo` was a `NOT IMPLEMENTED!` stub) was removed. The repo-walk capability it was meant to provide now belongs in `deps -repo` (see consolidation note below).
- **krunner osi parser coverage is narrower than file detection.** `find_dependency_files` detects `go.mod`, `.csproj`, etc., but krunner's loop at `src/krunner.py:611-651` only handles `requirements*.txt`, `pyproject.toml`, `package*.json` — everything else logs `Unsupported`. `assess()` itself does handle go.mod and nuget; krunner just doesn't call it.
- **Two parallel data flows that don't reconcile.** krunner enriches via deps.dev → CSV; `assess(save=True)` enriches via deps.dev → DB. The CSV path is more complete (handles malformed JSON, has `-all` scope) but throws results away.
- **`latest` flag is never demoted on prior rows.** `assess()` sets `latest=1` on every insert (line 377) but never marks superseded rows `latest=0`, so re-enabling the filter at `kospex_query.py:268` without fixing this would still be wrong.

## CLI surface & consolidation

Verified 2026-05-18 by reading the code (not just the handlers). This section underpins Hardening steps 2, 3 and 5.

### `assess()` callers (now 3)

`KospexDependencies.assess()` is the single parse+enrich entry point. After `sync-dependencies` was removed, exactly three wrappers call it:

| Caller | `save=True`? | Reaches `/dependencies/`? |
|---|---|---|
| `kospex sca FILE` (`src/kospex_cli.py:737`) | yes (default) | **yes — the only writer** |
| `kospex deps -file` (`src/kospex_cli.py:679`) | no | no |
| `/upload` handler (`src/kweb2.py:1365`) | no | no |

`krunner osi` deliberately **bypasses `assess()`** — it calls the leaf parsers + `depsdev_record()` itself. That bypass is the core duplication.

### `deps` vs `sca` overlap

| Capability | `kospex deps` | `kospex sca` |
|---|---|---|
| Single-file deps.dev assessment | ✓ (`-file`) | ✓ (positional `FILE`) |
| Persists to `TBL_DEPENDENCY_DATA` | ✗ | ✓ (default) |
| Malware check (maliciouspackages.com) | ✗ | ✓ (`-malware`) |
| Repo-level scan | discovery only (`-repo` lists files) | `-repo` is a `NOT implemented` stub |
| Directory recursive scan + summary tables | ✓ (`-directory`) | ✗ |
| Dev-deps flag | ✓ | ✓ |

They are complementary, not duplicative: `deps` is find-and-summarise, `sca` is parse-and-persist. The only true overlap is the single-file path, where both just call `assess()` with different defaults/output.

### Parser comparison — `krunner osi` vs `assess()`

| File type | `krunner osi` (`src/krunner.py:611-651`) | `assess()` (`src/kospex_dependencies.py:323-360`) | Verdict |
|---|---|---|---|
| `requirements.txt` | `parse_pip_requirements_file` → `parse_pypi_package_declaration` | `pypi_assess` (re-reads file inline; also calls `parse_pypi_package_declaration`) | shared leaf, divergent wrappers — `pypi_assess` adds GitHub-URL deps, multi-version skip, `repo_info` copy |
| `pyproject.toml` | `parse_pyproject_file` → `depsdev_record` | `parse_pyproject_file` → `pypi_assess2` → `depsdev_record` | effectively identical |
| `package.json` | `parse_package_json` | `npm_assess` → `get_npm_dependency_dict` | **two genuinely different extractors** |
| `go.mod`, nuget/`.csproj` | unsupported (logs `Unsupported`) | `gomod_assess` / `nuget_assess` | krunner missing coverage |

So pyproject is a free win, requirements needs the `pypi_assess` extras folded in, npm needs one parser chosen as canonical.

### `assess()` rework needed before it can be the single save path

Beyond the `latest`-demotion gap (see "Other concrete gaps"):

- **Two save blocks, one used.** `assess()` upserts in its own block (line 381); `pypi_assess(store=True)` also upserts (line 951) with the same PK — but `assess()` calls `pypi_assess()` *without* `store=True`, and `pypi_assess2` never saves. Save logic must be consolidated into exactly one place.
- **`source` column unused.** Schema has `[source] TEXT -- what tool was used` (`kospex_schema.py:235`) but `assess()` never sets it. This is precisely the discriminator needed for `sca` and `krunner osi` to coexist in the table without clobbering each other — populate it.
- **No file-less entry point.** `assess()` requires a real file on disk + `find_git_base()`. For `krunner osi` to reuse it (step 5), the save logic should be extracted into a reusable method taking pre-parsed records + git context (krunner already has `_repo_id` + relative `file_path` from `file_metadata`).

### Consolidation recommendation

Fold `sca` into `deps` and deprecate `sca`:

1. Add `--save` (default `True` for `-file`/`-repo`) and `--malware` to `deps`.
2. `deps -file` becomes the single-file canonical path (absorbs `sca` entirely).
3. Implement the `deps -repo` full walk (step 3) — the repo-level capability the removed `sync-dependencies -repo` stub promised.
4. `krunner osi` stays the **scope/batch** path but calls the extracted `assess(save=True)` save method (step 5), so `/dependencies/` populates as a byproduct of the batch tool people already run.
5. Leave `sca` as a thin "use `deps`" shim for one release, then remove.

**Tradeoff:** `sca` is a snappy, industry-standard name (Software Composition Analysis); retiring it loses recognisability. The opposite fold (make `sca` canonical) was considered but rejected — `deps`'s `-directory`/`-repo`/`-file` mode dispatch is the better skeleton, and `sca`'s name doesn't fit the discovery-only modes.

## Hardening plan

In rough order of effort/payoff:

1. **Wire `krunner osi` to write to the DB.** Parsing + deps.dev enrichment is already done; add a call to `kospex_db.table(TBL_DEPENDENCY_DATA).upsert_all(...)` after the enrichment loop near `src/krunner.py:670`. This makes `krunner osi -all` the canonical refresh command for `/dependencies/`.
2. **Default `kospex deps -file` to `save=True`.** It already calls `assess()`; just pass the kwarg. (`sync-dependencies -file` was removed 2026-05-18, so `deps -file` and `sca` are now the only single-file paths.)
3. **Implement a full `deps -repo` walk.** Walk the repo tree, reuse `find_dependency_files`, call `assess(save=True)` on each match. This is the repo-level capability the removed `sync-dependencies -repo` stub was meant to provide.
4. **Fix `latest` semantics.** Before upserting a new `(repo_id, file_path, package_name)`, set `latest=0` on prior rows for the same key. Then re-enable the filter at `kospex_query.py:268`.
5. **Refactor krunner osi to call `assess()` directly** instead of duplicating parser dispatch. Closes the go.mod / nuget gap and removes drift between the two enrichment paths.
6. **Background refresh via `kospex_agent`.** On each repo sync, run dependency parse+save for changed dep files. Once this is reliable, the WIP banner in `dependencies.html` can be removed.

## Recommended starting point

Step 1 is the smallest change with the largest UI payoff and aligns with the in-flight 0.0.37 bundle (krunner osi `-all` fix is already there). Recommend doing 1 + 4 together so re-running `krunner osi` doesn't accumulate stale rows in the table.

## Open questions

Decisions worth making before implementation starts:

- **CLI consolidation direction** is now spelled out in "CLI surface & consolidation" above (fold `sca` into `deps`). The remaining decision is *timing*: do the `sca`→`deps` fold and the pipeline fix (steps 1–5) ship together, or is the fold deferred until after `/dependencies/` is reliably populated? Leaning: pipeline fix first (steps 1, 4, 5), fold second — the fold is user-facing and riskier, the pipeline fix is not.
- **Is `krunner osi` really the canonical refresh path?** Step 1 assumes yes. That's fine if krunner stays the batch tool, but it means `/dependencies/` accuracy depends on someone running krunner — not on `kospex sync`. The alternative is to move dep parse+save into the sync path itself (mentioned as step 6, but it's really an architectural choice that should be made before step 1 lands).
- **Endpoints have not been verified end-to-end.** This analysis is from reading handlers and queries, not loading the pages. Before shipping the plan, spin up `kweb2` against a synced repo and confirm both `/osi/` and `/dependencies/` return non-empty responses today — otherwise step 6 ("remove WIP banner") may be hiding a separate rendering bug.

## Files touched (anticipated)

- `src/krunner.py` — DB upsert after deps.dev enrichment loop
- `src/kospex_dependencies.py` — `latest` flag demotion before upsert
- `src/kospex_query.py` — re-enable `latest=1` filter in `get_dependencies()`
- `src/kospex_cli.py` — pass `save=True` from `deps -file`; implement the `deps -repo` walk (`sync-dependencies` removed 2026-05-18)
- `src/templates/dependencies.html` — remove WIP banner once data flow is reliable
