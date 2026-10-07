# `krunner osi -next N` — batched, cron-friendly dependency scanning

Design doc. No code yet.

`krunner osi -all` is a single sweep that takes hours on a large estate, which
makes it unrunnable from cron and all-or-nothing when it fails. `-next N`
processes the N repositories least recently examined, so a schedule of small runs
converges on full coverage without ever holding the machine for hours.

This also implements the extraction-outcome record that
[#148](https://github.com/kospex/kospex/issues/148) asks for, because `-next`
cannot build a correct work queue without it.

## What is already in place

Four changes landed first, because `-next` is not viable without them.

| | effect |
|---|---|
| #217 | `save_dependencies` resets `advisories`/`versions_behind`, so stale advisory counts cannot sit under a fresh `last_checked` |
| #219 | osi queries the DB directly instead of copying the estate into RAM |
| #219 | migration `0007` indexes `file_metadata(_repo_id, latest)` |
| #220 | external response cache TTL 1 hour → 1 day, configurable |

Measured consequence on a 167-repo estate: the osi read path went **11.51s →
0.42s**, and per-repo cost is now independent of estate size. That is the property
that makes a fixed-size batch meaningful — before, every invocation paid an
estate-wide cost regardless of `N`.

## The queue

### Repo-granular, not row-granular

`dependency_data.last_checked` (migration `0006`) looks like a ready-made queue:

```sql
ORDER BY last_checked ASC NULLS FIRST
```

It is not sufficient, for one decisive reason: **a repository that produced no
`dependency_data` rows never appears in it at all.** A newly added manifest in a
previously dependency-free repository would never be discovered.

On the current estate that is not an edge case:

| | count |
|---|---|
| repositories | 167 |
| with `krunner osi` rows | 82 |
| **with no `krunner osi` rows** | **85 (51%)** |
| of those, with no `|dependencies|`-tagged files at all | 9 |

So roughly 76 repositories have dependency files that produced nothing, and
nothing records why. A row-derived queue silently skips all 85.

The mirror-image mistake is just as bad. A queue built from `repos` LEFT JOINed to
`MAX(last_checked)` puts those 85 permanently at the front, so `-next 10` cycles
the same repositories forever and never reaches the other 82.

**Both failure modes are #148.** Neither is fixable by ordering; both need a
record of "this repository was examined, and here is what happened".

### Queue definition

Order `repos` by the extraction-outcome record, not by `dependency_data`:

1. repositories with no outcome record — never examined — first
2. then by outcome `created_at` ascending — least recently examined
3. optionally filtered by `--older-than DAYS`, to skip anything examined recently

Cost is O(repos), not O(dependency rows): one row per repository rather than a
scan over millions of dependency rows per tick. At 6,700 repositories that is
~6,700 rows to sort.

## The extraction-outcome record (#148)

One row per repository per run, in the existing `observations` table — which is
present, correctly shaped, and currently **empty** (0 rows). No schema change.

| column | value |
|---|---|
| `observation_key` | `OSI_EXTRACTION` |
| `observation_type` | `REPO` |
| `_repo_id` | the repository |
| `hash`, `file_path` | `''` — the PK is `(_repo_id, hash, file_path, observation_key, latest)` and this is a repo-level fact |
| `latest` | `1`, demoting any prior row for the same key |
| `created_at` | when the repository was examined |
| `format` | `JSON` |
| `data` | per-file outcomes, see below |

### Outcome vocabulary

`dependency_data.resolution` already works as a model for the *resolve* layer —
six categories that make an unresolvable dependency reportable rather than
invisible. This mirrors it for the *parse-file* layer:

| outcome | meaning | action implied |
|---|---|---|
| `extracted` | parser ran, produced rows | none |
| `empty` | parser ran, produced zero rows | none — the manifest genuinely declares nothing |
| `unsupported` | classified, no parser yet (`yarn.lock`, `uv.lock`, `package-lock.json`, `build.gradle`) | write a parser — sub-project D |
| `unclassified` | matched no registry entry — `Kind.UNKNOWN` | investigate: either a real manifest kospex cannot name, or a mis-tag |
| `not_a_manifest` | `RUNTIME` / `CONTAINER` / `SCA_CONFIG` / `LOCKFILE` kinds | none — declares no dependencies by nature |
| `parse_error` | parser raised; currently caught and skipped silently | investigate — this is the one that is actually broken |

`empty` and `parse_error` are the two that are indistinguishable today, and they
want opposite responses. That is the core of #148.

### What this cannot record

The **discovery** layer from #148 — panopticas not tagging a file as a manifest at
all — is invisible here by construction. `get_dependency_files()` only returns
files already tagged `|dependencies|`, so osi never learns about a manifest that
was never tagged. Instrumenting that belongs with panopticas, not here.

The **parse-line** layer (#145 — `-e .`, `git+…`, `-c …` silently dropped) is
inside each parser, so it needs recording at parser level, not per file. Out of
scope for this doc.

## Enrichment must stay in lockstep with the save

**Do not** extract manifests estate-wide and then enrich only the batch.

`save_dependencies` stamps `last_checked` on every row it writes and never calls
deps.dev. Today that is safe only because it has exactly one caller
(`krunner.py:746`), which enriches at line 738 immediately before. Decoupling the
two would advance `last_checked` over rows whose advisory data was never
refreshed — the same defect `0006` exists to eliminate, one column over.

#217 fixed the worst symptom: `advisories` and `versions_behind` are now reset, so
stale counts cannot masquerade as fresh. But the honest fix is structural — each
batch extracts, enriches and saves as one unit, so the timestamp always attests
work that actually happened.

## Cadence and the staleness bound

Both `N` and the interval are operator-chosen. Neither is a default worth
imposing, because the tradeoff is real and local. What the tool should do is make
the consequence explicit:

```
worst-case staleness  =  (estate size ÷ N) × interval
```

| estate | N | interval | full pass |
|---|---|---|---|
| 167 | 10 | 5 min | ~1.4 hours |
| 1,000 | 10 | 5 min | ~8.3 hours |
| 6,700 | 10 | 5 min | ~2.3 days |
| 6,700 | 25 | 5 min | ~22 hours |
| 6,700 | 200 | 6 hours | ~8.4 days |

`-next` should print this bound when it runs, so an operator who has chosen a
cadence that cannot keep up finds out from the tool rather than from stale data.

Two couplings worth stating:

- **Per-repo cost is network-bound, not SQL-bound.** After #219 the queue and
  lookups are milliseconds; the time is deps.dev round-trips, roughly
  (packages in repo × latency). Size `N` against the interval on that basis.
- **Any consumer that reports measurement age from `last_checked` needs its
  staleness threshold set to match the chosen cadence.** A threshold of one day
  against a two-week full pass will escalate on every run, truthfully but
  uselessly. The cache TTL from #220 is a day for the same reason.

## Locking

A tick that starts while a previous one is still running must not double-scan.

- Lockfile under `KOSPEX_HOME`, containing PID and start time so a stale lock is
  diagnosable rather than mysterious.
- A tick that finds a **live** lock logs and **exits 0**. Cron stays quiet; an
  overlapping tick is normal operation under a small interval, not a failure.
- A lock older than a configurable staleness window is treated as abandoned: warn,
  take it, proceed. Without this, one crashed run blocks the schedule
  indefinitely — which is a worse failure than double-scanning.

Exiting 0 on contention is deliberate. The alternative, surfacing overlap as a
cron failure, makes a correctly-configured frequent schedule noisy. The
staleness-bound output above is the better signal for "the batch is too big for
the interval".

## CLI shape

```
krunner osi -next N [REQUEST_ID]
krunner osi -next N --older-than DAYS
```

- `-next` is a third mode alongside `-all` and a bare `REQUEST_ID`. It **composes**
  with a scope: `-next 10 github.com~acme` batches within that org.
- `-next` and `-all` are mutually exclusive — `-all` is "everything now", `-next`
  is "a slice".

### Two existing behaviours that must change for `-next`

- **`sys.exit(1)` on empty results** (`krunner.py:742`) must not fire for `-next`.
  An empty batch means nothing is due, which is success. As it stands cron would
  report a failure every time the estate is fully current.
- **CSV output must be suppressed.** `AssessmentTypes.generate_filename` produces
  `OSI-{scope}.csv` with no timestamp (`krunner.py:756`), so every tick would
  overwrite the file with just that batch's rows — strictly worse than not writing
  it. The database is the store for batched runs. A per-batch filename is the
  alternative, but 288 files a day at a 5-minute interval is not obviously better.

## Testing

- Queue ordering: never-examined first, then oldest-first. Must include a
  repository with zero dependency files, and one whose files produced zero rows.
- A repository examined in a batch must get an outcome row, and must not be
  selected again on the next tick.
- Each outcome category, driven through the real command rather than a
  re-implementation of its loop — `tests/test_osi_reads_disk_db.py` established
  that pattern.
- `-next` with nothing due exits **0**, writes no CSV.
- A second concurrent run exits 0 and does no work; a stale lock is taken over.
- `last_checked` only advances on rows whose enrichment ran in the same batch.

## Open questions

1. **Should the outcome record be per-file as well as per-repo?** The repo-level
   row carries per-file detail in `data`, which serves the queue and is
   queryable as JSON. Separate `observation_type='FILE'` rows would be
   relationally queryable, at one row per manifest per run (~1,081 per run on the
   current estate). Deferred — the repo-level row is what `-next` needs, and the
   reporting shape #148 ultimately wants can be decided when something consumes it.
2. **Should `-next` also re-examine repositories whose `last_fetch` moved?** A
   repository pulled since its last scan is more likely to have changed
   dependencies than one merely old in the queue. That is a smarter priority
   function, and worth considering once the simple one is working.
