# osi read path: drop the in-memory copy, index file_metadata

Two changes to how `krunner osi` reads the database, and a migration that makes
the repo-scoped query shape fast for every caller, not just osi.

Prerequisite work for `krunner osi -next` (batched, cron-friendly runs), which
needs per-repo cost to be independent of estate size.

## Files changed

| file | change |
|---|---|
| `src/krunner.py` | `load_dependency_memory_db()` removed; `osi` queries `kospex.kospex_query` directly |
| `src/kospex/db/migrations/0007_file_metadata_repo_index.sql` | new — `INDEX file_metadata (_repo_id, latest)` |
| `src/kospex/db/introspect.py` | docstring no longer names osi as an in-memory-DB caller |
| `tests/test_osi_reads_disk_db.py` | new — first end-to-end test of the `osi` command |
| `tests/test_file_metadata_repo_index.py` | new — index exists, column order, and query plans |
| `tests/test_db_health.py`, `tests/test_kospex_schema.py`, `tests/test_kospex_utils.py` | migration counts 4 → 5 |

## Why the in-memory copy had to go

`load_dependency_memory_db()` copied `commit_files`, `file_metadata`, `repos` and
`url_cache` into an in-memory database — and it ran **before** `osi` scoped to
the requested repos, so `krunner osi <repo_id>` paid a whole-estate copy to look
at one repo.

Tracing the SQL the osi path actually issues against that copy:

```
commit_files    0 statements
file_metadata   5 statements
repos           1 statement
url_cache       0 statements
```

`url_cache` could not be used even in principle: `KospexDependencies` is
constructed on the on-disk handle, so deps.dev cache reads and writes go to disk
regardless.

The copy also carried **no indexes**. `create_memory_kospex_query()` builds its
tables with `CREATE TABLE AS SELECT`, which does not reproduce the primary key,
so each per-repo lookup was a full scan of RAM rather than a seek. It was never a
substitute for an index — which is the second half of this work.

Cost measured on a 167-repo estate (246,586 `file_metadata` rows):

| | time | resident |
|---|---|---|
| memory DB | 11.51 s (8.74 s copy + 2.77 s scan) | ~840 MB |
| on disk, no index | 5.24 s | — |
| on disk + `0007` | **0.40 s** | — |

The copy cost more to build than the entire scan it was meant to accelerate, and
it grows with the estate while the benefit does not: at 111 repos the copy took
3.3 s, at 167 it takes 8.74 s.

## Why the index

`file_metadata`'s only index was its primary key `(Provider, hash, _repo_id)`,
led by the filename. Repo-scoped is the common shape, and all of these scanned
the whole table:

- `get_dependency_files()` — `krunner osi`, `/dependencies/`, `/osi/`
- `file_metadata(repo_id)` — repo detail page
- `repo_files(repo_id)` — repo file list
- `tech_landscape(repo_id)` — per-repo technology panel
- `UPDATE file_metadata SET LATEST = 0 WHERE _repo_id = ?` — **every sync**

Median-sized repo, before → after:

| query | 131k rows | 8.05M rows |
|---|---|---|
| `file_metadata(repo_id)` | 25.11 ms → 0.19 ms | 8,506 ms → 1.63 ms |
| `get_dependency_files` | 14.30 ms → 0.04 ms | 16,612 ms → 0.31 ms |
| `tech_landscape(repo_id)` | 15.07 ms → 0.03 ms | 4,759 ms → 0.03 ms |

The 8M-row column is the real argument. A several-thousand-repo estate puts
`file_metadata` there, and an unindexed repo page then spends **8 to 17 seconds
in a single query**. The CLI benefit is incidental by comparison.

### It helps the write path too

Every sync resets `latest` for the repo before writing, on the same unindexed
column. At 2.04M rows that `UPDATE` went **294 ms → 2.4 ms**, against ~9% added
to bulk insert — so the index pays for itself roughly 30× over on the write side
alone, and the gap widens with table size (the scan is O(n), the index O(log n)).

### Cost

- ~9% on bulk insert (measured over 3 runs: +5.3%, +8.2%, +14.2%)
- +12.6% on the `file_metadata` table; +8.5% on the real 2.0 GB dev database
- Index build: 7.4 s at 8.05M rows, seconds on the dev database

## Verification

Applied to a **copy of the real populated v6 database**, not only a fresh one:
version 6 → 7, all five migrations recorded, and the repo-scoped plan changes
from `SCAN file_metadata` to
`SEARCH file_metadata USING INDEX idx_file_metadata_repo_latest`.

`tests/test_osi_reads_disk_db.py` is the first test to drive the real `osi`
command end to end — `test_osi_dispatch.py` calls `extract_dependency_file` and
the enrichment loop directly, neither of which touched the memory DB, so this
path had no coverage at all.

## Deliberately out of scope

- **Estate-wide queries.** `tech_landscape()` with no repo scope and
  `repos_with_tech()` never constrain `_repo_id`, so they cannot use this index.
  They need their own, and measuring that is separate work.
- **`/repos/` and `/orphans/` at ~11 s.** Read paths over `repos`/`commits`;
  unaffected by this index. Tracked separately.
- **The other five in-memory callers** — `key-person`, `developer-tech`,
  `dependencies`, `devs-by-tag`, `authors` — read `commits`/`commit_files` and
  genuinely use them. Untouched.
