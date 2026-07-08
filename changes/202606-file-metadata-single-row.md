# file_metadata — one current-state row per file (fix the multi-hash duplication)

## Overview

`file_metadata` is meant to answer one question: *"what files are in this repo as of
the last sync, what are they (language/tags + scc metrics), and when was each last
modified?"* — a **current-state** table. It is also the **only** table carrying
panopticas `tech_type` / `Language`, which many other queries depend on
(`/osi/`, tech-landscape, `find-actions`, dependency discovery).

Today it is badly polluted: **94 of 95 repos have many commit hashes flagged
`latest=1`** (apache/arrow: 1609, babel: 1540, react: 1190), when there should be
exactly one current row per file. This plan fixes the writer so each file produces a
single current-state row, and re-homes the one thing that depended on the broken
behaviour.

## The bug (evidence)

Every file ends up with **two** `latest=1` rows under two different `hash` values.
For `github.com~kospex~kospex`, `README.md`:

| hash | committer_when | Lines/Code | tech_type | written by |
|---|---|---|---|---|
| `1c4fb6d` (repo HEAD) | `None` | `None` | `Markdown` (+ tags) | `metadata_rows_from_repo_files` |
| `99c39e4` (file's last commit) | `2025-08-14` | `142`/`89` | (none) | scc enrichment loop |

Repo-wide: 234 HEAD-hash rows carry scc metrics on only 4 files; the 222 per-file-hash
rows carry metrics + `committer_when` on all of them. The two row-sets are
complementary halves of what should be one row.

## Root cause

`KospexDependencies`… no — `Kospex.file_metadata()` (`src/kospex_core.py:920`) writes
each file **twice**, keyed by different hashes:

1. **`src/kospex_core.py:949-961`** — resets `latest=0` for the repo, then
   `metadata_rows_from_repo_files()` (`src/kospex_schema.py:521`) upserts rows keyed by
   the **repo HEAD hash** (`get_repo_files` sets `data["hash"] = self.current_hash`,
   `src/kospex_git.py:564`). These rows carry `Language` + `tech_type`.
2. **`src/kospex_core.py:1011-1042`** — the scc loop builds the same files, sets
   `row["hash"] = git_hash` (HEAD) at `:1013`, then **`:1031` overwrites it** with
   `commit_info.get("hash")` — the file's *own last-commit* hash — and upserts. These
   rows carry scc metrics + `committer_when`, but no `tech_type`.

Because `hash` is in the primary key `(Provider, hash, _repo_id)`, step 2 does not
*enrich* step 1's rows — it inserts a **parallel set**. Both are `latest=1`.

Git history confirms this is hangover: the `commit_files` lookup that supplies
`committer_when` was added 2025-08 (`0553fb22`) to replace an expensive per-file git
command; in doing so it also pulled the *hash* from `commit_files` and overrode the
HEAD hash. The **date** was the goal; the **hash override** was the accident.

Secondary defect: `metadata_rows_from_repo_files` (`src/kospex_schema.py:521`) appends
`filtered_dict` *inside* the inner key loop, producing duplicate references to the same
dict. Harmless (PK-dedup'd) but should be fixed in the rework.

## Decided model (current-state, per-file hash, soft-delete)

A clone + sync of a **never-seen** repo produces exactly:

- **one `latest=1` row per file** currently in the working tree at HEAD;
- columns: `Provider`/`Filename`, `Language` + `tech_type` (panopticas — full coverage
  incl. its UNKNOWN default), scc metrics (`Lines`/`Code`/`Comments`/`Blanks`/
  `Complexity`/`Bytes`) where scc knows the type, `committer_when` (last-commit date)
  and `hash` (last-commit hash) **for every file**, `_git_*` / `_repo_id`;
- row count = file count. No history rows on first sync.

**Canonical `hash` = the file's last-commit hash** (not repo HEAD). Rationale agreed in
design discussion:

- HEAD hash repeated on every row is redundant — it's a single repo fact (belongs in
  `repos`), carries no per-file signal.
- Per-file hash is information-dense: each row says *which commit last touched this
  file*, joinable to `commits`, and serves both current-state and change analysis.
- It is the more multi-branch-resistant key (stable across branches for unchanged
  files) — relevant to the multi-branch backlog.

**History / churn is NOT this table's job.** `commit_files` already holds full
per-file history; `/hotspots/` already derives "good enough" complexity (total commits,
authors, file size) from commit data. So `file_metadata` stays current-state.

**Soft-delete is fine** (confirmed): on re-sync, `reset latest=0` then upsert current
files as `latest=1`. Files no longer present, and superseded file versions, simply
remain as `latest=0` tombstones — not hard-deleted. `latest=1` = current files. Table
growth is then bounded by *real churn*, not sync frequency (an unchanged file re-uses
its PK and updates in place).

### Invariant: a 3-file change churns 3 rows, not the whole repo

panopticas and scc both scan the **whole** working tree every sync, so the rebuild
*touches* every file — but it must **not** rewrite every file's `hash`. Each file's
`hash` + `committer_when` come from `KospexQuery.latest_commit_file_map(repo_id)` (one
windowed pass over `commit_files` — see Progress), **never** blanket-assigned the repo
HEAD. So:

- an **unchanged** file resolves to the *same* last-commit hash as last sync → same PK
  `(Provider, hash, _repo_id)` → upsert is an in-place no-op (only `latest` flips
  0→1);
- only the **changed** files resolve to a new hash → new row.

This is the protection the per-file-hash key buys; the failure mode to guard against is
a regression that assigns HEAD to all files (the old behaviour). Locked by a test:
sync a fixture, change N files, re-sync, assert exactly N new hashes/rows appear and the
rest are untouched.

### panopticas / scc version changes re-tag in place

When panopticas (or scc) is upgraded it may classify the **same** file differently. The
file's content — and therefore its last-commit hash — is unchanged, so the rebuild
upserts the new `tech_type` / metrics onto the **same** row (one row, refreshed tag),
no churn. The catch is *triggering* the rebuild at all: see the version-aware guard
below. This is the runtime-data half of draft issue #20.

## Progress

- **[DONE] One-pass per-file commit map.** `KospexQuery.latest_commit_file_map(repo_id)`
  returns `{file_path: {hash, committer_when}}` from a single windowed query over
  `commit_files`, replacing the prior load-all-into-memory + unindexed-scan-per-file
  lookup inside `file_metadata()` (~34s → ~1.4s on babel; verified identical output,
  0 mismatches). This is the cheap source of each file's last-commit hash + date that
  the single-row builder (item 1) consumes. Tests: `tests/test_latest_commit_file_map.py`.
- **[DONE] Single-row builder (item 1) + committer_when for all files (item 2).**
  `KospexSchema.build_file_metadata_rows()` merges panopticas (all files, incl
  UNKNOWN) + scc metrics (where known) + the commit map into **one row per file**,
  keyed by the per-file last-commit hash; `committer_when` is set for every committed
  file. `file_metadata()` now does a single `reset latest=0` + merged upsert (was two
  divergent upserts). Also fixed: `get_repo_files()` now excludes `.git/` explicitly
  (panopticas didn't on freshly-init'd repos). Tests:
  `tests/test_build_file_metadata_rows.py` (unit) +
  `tests/test_file_metadata_sync.py` (end-to-end sync: one row per file + the
  3-file churn invariant).
- **[DONE] Version-aware skip-guard (item 3).** `0003` adds the `repos` provenance
  columns; `needs_metadata_rebuild()` decides rebuild-vs-skip from recorded vs current
  (HEAD + `panopticas`/`scc` versions, compared as opaque strings); `file_metadata()`
  reads the provenance, rebuilds only when needed, stamps it after a successful write,
  and logs version transitions to disk via `KospexUtils.get_kospex_logger`. Degrades to
  always-rebuild on a pre-0003 DB (columns absent). Tests:
  `tests/test_metadata_rebuild_guard.py` (unit) + skip / panopticas-bump end-to-end in
  `tests/test_file_metadata_sync.py`. Also fixed a latent migrator transaction bug the
  first real migration exposed.
- **[DONE] Real-DB cleanup (item 4).** Applied `0003`, then force-rebuilt + pruned
  `latest=0` for all 104 repos (49s). `file_metadata` went 144,645 → **74,016 rows**,
  all `latest=1`; **0** providers with a duplicate `latest=1` row (was 94/95 repos);
  0 `.git` rows; provenance stamped on 104/104 repos. `/osi/` dep-file rows now carry
  `tech_type` **and** `committer_when` in one row. Rebuild used force (bypassing the
  guard) since a bare truncate would leave stamped provenance and make the guard skip
  an empty table.
- **Deferred:** the rename/path-mismatch (~0.4% of rows: files with no commit-map entry
  → HEAD-fallback hash, no date). Tracked as the next item on this feature's tail.

## Work items

1. **Single row per file.** Rework `file_metadata()` so panopticas (all files, incl.
   UNKNOWN, with `tech_type`/`Language`) and scc (metrics for known types) build **one**
   row per file, keyed by the **per-file last-commit hash**, then a single
   `reset latest=0` + `upsert latest=1`. Remove the HEAD-hash row-set; remove the
   `:1031` override as a concept (the per-file hash becomes the row's only hash). Fix
   the `metadata_rows_from_repo_files` inner-loop append bug.
2. **`committer_when` for all files.** Populate last-commit date + hash from
   `commit_files` for **every** file, not just scc-known ones (currently set only inside
   the scc loop). This also repairs `/osi/`'s "days ago" (it reads
   `get_dependency_files`, `src/kospex_query.py:238`, which today gets the HEAD-hash rows
   whose `committer_when` is `None`).
3. **Re-home the skip-guard, and make it version-aware.** The guard at
   `src/kospex_core.py:931-937` decides "already synced this HEAD?" by looking for
   HEAD-hash rows in `file_metadata` — which no longer exist after (1). Replace it with
   columns on `repos` (DB migration `0003`): **`last_sync_hash`**,
   **`last_panopticas_version`**, **`last_scc_version`**. Rebuild metadata when **any**
   of: `current HEAD != last_sync_hash`, panopticas version changed, scc version
   changed. Otherwise skip (the expensive whole-repo scan is the thing we're avoiding).
   Stamp all three on a successful rebuild.

   - panopticas version: `importlib.metadata.version("panopticas")`.
   - scc version: parse `scc --version` (cache per run); if scc is absent, record null
     and don't let its absence force rebuilds.
   - This closes the draft-#20 hole: a panopticas pin bump now forces a re-tag pass even
     when no commits landed, instead of silently serving stale `tech_type`. Because
     hashes are stable across a pure re-tag, that pass updates rows in place.
   - **No version *history*, so log transitions to disk.** The `repos` columns are
     point-in-time (each rebuild overwrites them) — they answer "what version is the
     current tagging?" not "when did it change?". When the guard rebuilds because a tool
     version changed, log it via `KospexUtils.get_kospex_logger(...)` (→ `~/kospex/logs/`)
     with the repo, reason, and old→new versions. That gives a durable, greppable audit
     trail without a schema for it. A queryable history (e.g. an `observations` row per
     re-tag) is a possible future enhancement, not built now.
4. **Data cleanup (DB preserved, only `file_metadata` rows are disposable).** Truncate
   `file_metadata` and re-sync repos under the fixed code. No row-level migration — the
   table is rebuildable from the repos + `commit_files`. Every other table is untouched.

## Files anticipated

- `src/kospex_core.py` — `file_metadata()` rewrite (single row builder, per-file hash,
  `committer_when` for all files); guard uses `repos.last_sync_hash`. Audit
  `cli_file_metadata()` (`:825`) which shares the `metadata_rows_from_repo_files` path.
- `src/kospex_schema.py` — fix `metadata_rows_from_repo_files()` (`:521`) append bug;
  confirm column set.
- `src/kospex_git.py` — `get_repo_files()` hash semantics (stop forcing
  `self.current_hash` as the canonical file hash where the per-file hash is intended).
- `src/kospex/db/migrations/0003_repos_sync_provenance.sql` — add `last_sync_hash`,
  `last_panopticas_version`, `last_scc_version` to `repos`.
- Tests (TDD): metadata sync of a small fixture repo asserts **exactly one `latest=1`
  row per file**, `committer_when` populated for **all** files (incl. an scc-unknown
  file); **3-file-change invariant** (change N files, re-sync, exactly N new hashes/rows,
  rest untouched); **guard** skips a no-change re-sync, rebuilds on HEAD change, and
  rebuilds (re-tagging in place) on a simulated panopticas-version bump.

## Out of scope / backlog

- **`file_metadata.authors` / `.commits`** (0/136k populated today) — keep the columns;
  populate via a later sync/assessment pass for per-file complexity. Tracked as a
  separate backlog item (to be filed against kospex).
- **Multi-branch** — the intended end state is a clean split of scope:
  **`file_metadata` = the default branch's current state** (one row per file as it
  exists on the checked-out/default branch), while **`commits` / `commit_files` =
  every branch** (`git log --all`) so developer-activity analytics see all work, not
  just what merged to default. That split is a feature — but it means anything that
  derives a `file_metadata` attribute *from* `commit_files` must be branch-aware:
  - `latest_commit_file_map` must scope to the **default-branch ancestry** — otherwise
    a file's "last changed" resolves to a feature-branch commit that isn't in the
    snapshotted tree (see its docstring caveat).
  - the `authors` / `commits` per-file columns inherit a decision: count across **all
    branches** (richer developer map) or **default only** (matches the row's scope).
  - `commit_files` will reference files **not** in `file_metadata` (branch-only files);
    joins must tolerate that.
  This needs a branch/ref dimension on `commit_files` and its own migration — a separate
  design. `0003`'s `last_sync_hash` stays valid then (it means the default-branch HEAD).
- **Incremental scanning** — panopticas + scc still scan the *whole* working tree each
  sync; the one-pass map only made the *commit lookup* cheap, not the *scan* itself.
  Row churn is already bounded to changed files (per-file hash) and the version-aware
  guard skips the scan entirely when nothing changed — so scanning only changed paths is
  a *compute* optimization for later, not correctness.
- Dropping any vestigial columns — not now.

## Open questions

- Exact home for `last_sync_hash` update (inside `file_metadata()` vs the higher-level
  `sync_repo`) so it's only stamped after a *successful* metadata rebuild.
- Whether `cli_file_metadata()` should be collapsed into the same single-row builder or
  left as a thin display path.
