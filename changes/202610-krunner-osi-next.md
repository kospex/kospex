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

### Queue definition — two axes, not one

> **Superseded by the daily-read requirement.** Dependencies must be read every
> day, because `versions_behind` and `advisories` change with upstream activity
> rather than with manifest edits. A daily full pass makes the change-driven axis
> redundant as a *priority* — everything is scanned every day regardless — and with
> only one axis left nothing can starve, so **`--max-age` is dropped**. The
> implemented queue is plain oldest-first, never-examined first.
>
> The reasoning below is kept because it establishes *why* change-driven priority
> alone would be wrong, which still matters: it would stop new-CVE detection on
> stable code. The change signal survives only as a tiebreaker between repositories
> examined at the same moment, which is marginal.

Order `repos` by the extraction-outcome record rather than by `dependency_data`.
But "least recently examined" alone is the wrong priority, and so is "recently
modified" alone. The queue needs both, for different reasons:

**Change-driven** — a repository whose manifest has changed has *new dependencies*
to learn about. The precise signal is the dependency file itself, not the
repository: `file_metadata.committer_when` for files tagged `|dependencies|`,
which is populated for **1080 of 1081** such files on the current estate. A repo
with 500 commits to source and no manifest change has nothing new for osi, so
repo-level activity is the wrong proxy. `file_metadata.hash` gives the
complementary content-identity check — if the hash recorded in the last outcome
differs, the manifest was rewritten.

`repos.last_fetch` is tempting and should **not** be the primary signal: it is
populated for only 111 of 167 repositories, because a repo synced via
`sync-directory` without a pull never records one.

**Age-driven** — and this is the part that is easy to get wrong: **an unchanged
manifest still needs re-checking, because advisories are published against
versions you already have.** A manifest untouched for two years can become
vulnerable tomorrow. A purely change-driven queue would stop detecting new CVEs on
stable code, which is the opposite of what the tool is for.

So the ordering is:

1. **Never examined** — no outcome record at all
2. **Manifest changed since last examination** — any `|dependencies|` file whose
   `committer_when` is later than the outcome `created_at`, or whose `hash`
   differs from the one recorded
3. **Starvation floor** — anything not examined within `--max-age DAYS` jumps
   ahead of (2) regardless of whether it changed
4. **Oldest examined** — everything else, `created_at` ascending

Step 3 is what keeps the two axes honest, and it is deliberately a hard floor
rather than a weighted score. A weighting needs tuning and gives no guarantee; a
floor gives a property that can be stated and tested: *nothing goes unexamined
for longer than `--max-age`.* Default it generously (say 30 days) so it rarely
fires, and when it does, it is correct that it should.

Cost is O(repos), not O(dependency rows): one row per repository rather than a
scan over millions of dependency rows per tick. At 6,700 repositories that is
~6,700 rows to sort. The change check joins `file_metadata` on `_repo_id`, which
migration `0007` indexes.

## The extraction-outcome record (#148)

One row per repository per run, in the existing `observations` table — which is
present, correctly shaped, and currently **empty** (0 rows). No schema change.

| column | value |
|---|---|
| `observation_key` | `OSI_EXTRACTION` |
| `observation_type` | `REPO` |
| `_repo_id` | the repository |
| `hash`, `file_path` | `''` — the PK is `(_repo_id, hash, file_path, observation_key, latest)` and this is a repo-level fact |
| `latest` | always `1` — **replace in place, never demote** (see below) |
| `created_at` | when the repository was examined |
| `format` | `JSON` |
| `data` | per-file outcomes, see below |

### Do not demote prior rows — it fails on the third run

`observations`' primary key is
`(_repo_id, hash, file_path, observation_key, latest)`, and `latest` is **in the
key**. The usual kospex pattern — `UPDATE ... SET latest = 0` then insert the new
row at `latest = 1` — therefore works twice and raises on the third write:

```
run1:  [(1, 'run1')]
run2:  [(1, 'run2'), (0, 'run1')]
run3:  IntegrityError: UNIQUE constraint failed:
       observations._repo_id, _hash, _file_path, _observation_key, _latest
```

Demoting run2 to `latest = 0` collides with run1 already occupying that slot. The
table can hold exactly one current and one previous row per key, and the
transition to a third fails hard.

So the outcome record is written with `INSERT OR REPLACE` at `latest = 1` and
**never demoted**: one row per repository per key, always current. Verified stable
across repeated writes.

This is a real constraint on the table, not a quirk of this feature. `observations`
is empty today, so nothing has hit it — anyone adding a second `observation_key`
needs to know. **Consequence: `observations` cannot store history.** That is fine
for the queue, which only needs "when was this last examined and what happened",
but it is why run diagnostics need their own table (below).

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

## Run diagnostics

Two outputs, because they answer different questions and only one of them needs to
survive log rotation.

### Structured log lines — always

Through the existing per-module logger (`krunner.log`, daily rotation,
`KOSPEX_LOG_RETENTION_DAYS`). Human-readable narrative of a run:

```
osi -next: run 20261007T0915Z starting, N=10, scope=all
osi -next:   github.com~acme~svc            4 files   37 packages   2.4s  extracted
osi -next:   github.com~acme~legacy         1 file     0 packages   0.1s  empty
osi -next:   github.com~acme~infra          2 files    0 packages   0.0s  not_a_manifest
osi -next: run 20261007T0915Z done, 10 repos, 184 packages, 31.2s
osi -next: queue depth 157, oldest examined 2026-09-14, bound ~1.4h at this cadence
```

Free, no schema change, and the right medium for "what happened last Tuesday".

### A queryable run table — migration `0008`

The log lines cannot answer the questions that matter over time: *is this
repository getting slower? is the estate? what N actually fits my interval?* Those
need rows, and they need history.

`observations` cannot hold history — see the `IntegrityError` finding above — so
this needs its own table. One row per (run, repository):

| column | purpose |
|---|---|
| `run_id` | one per `-next` invocation, e.g. UTC timestamp |
| `_repo_id` | the repository |
| `started_at` | when this repository began |
| `duration_ms` | how long it took |
| `files` | `|dependencies|` files examined |
| `packages` | dependency rows written |
| `lookups` | deps.dev requests actually made (cache misses) |
| `outcome` | the worst per-file outcome, from the vocabulary above |
| `_git_server` / `_git_owner` / `_git_repo` | the usual derived columns |

Primary key `(run_id, _repo_id)`, so history accumulates rather than colliding.

`lookups` is worth recording separately from `packages`: it is the only way to see
whether the cache TTL from #220 is doing its job, and it is the term that actually
drives wall-clock time.

What this makes answerable, which nothing is today:

- **Sizing.** `SELECT avg(duration_ms) FROM … WHERE run_id = ?` against the
  interval tells the operator whether `N` fits, instead of them inferring it from
  overlap warnings.
- **Regression.** A repository whose `duration_ms` trends upward is either growing
  or hitting a slow path.
- **Cache effectiveness.** `sum(lookups) / sum(packages)` per run, over time.
- **Coverage honesty.** Per-run repo counts show whether the schedule is actually
  completing passes or quietly falling behind.

**Retention.** One row per repo per run is 6,700 rows per full pass on a large
estate — at a daily pass, ~2.4M rows a year. That is small for SQLite but not
nothing, so the table needs a documented pruning story (a `kreaper` target, or a
`--prune-older-than` on `-next`) decided before it ships rather than after it
grows.

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
krunner osi -next N --csv          # opt in to OSI-NEXT-{scope}.csv
```

- `-next` is a third mode alongside `-all` and a bare `REQUEST_ID`. It **composes**
  with a scope: `-next 10 github.com~acme` batches within that org.
- `-next` and `-all` are mutually exclusive — `-all` is "everything now", `-next`
  is "a slice".

### Two existing behaviours that must change for `-next`

- **`sys.exit(1)` on empty results** (`krunner.py:742`) must not fire for `-next`.
  An empty batch means nothing is due, which is success. As it stands cron would
  report a failure every time the estate is fully current.
- **CSV output is off by default, with an opt-in `--csv` flag.**
  `AssessmentTypes.generate_filename` produces `OSI-{scope}.csv` with no timestamp
  (`krunner.py:756`), so a scheduled `-next` would overwrite the estate-wide export
  with one batch of five repos, 288 times a day — making the file actively
  misleading rather than merely stale. The live dev install has a 1.08 MB
  `OSI-all.csv` that this would destroy.

  Nothing in kospex reads these files — `get_assessments_path` appears only in
  write positions (`krunner.py:540`, `:764`, `:1124`), and the only `csv.DictReader`
  in `src/` parses scc output. The `/osi/` and `/dependencies/` pages read
  `dependency_data`. So they are exports for people and external tooling, and
  suppressing them by default costs kospex nothing.

  **`-next --csv` writes to a separate filename namespace: `OSI-NEXT-{scope}.csv`,
  never `OSI-{scope}.csv`.** This is the load-bearing part. A batched run must not
  be able to clobber a full run's export, however the operator invokes it. Within
  that namespace the file is last-batch-only and overwritten each run, which is
  honest for a flag someone passes deliberately on a manual run, and is why it is
  not the default.

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
- **The outcome record survives a third run** — the specific `IntegrityError` the
  demote pattern causes. Worth an explicit test, since the pattern is idiomatic
  elsewhere in kospex and someone will reintroduce it.
- Change-driven priority: a repo whose dependency file `committer_when` moved is
  selected ahead of an unchanged repo examined at the same time.
- Starvation floor: an unchanged repo past `--max-age` is selected ahead of
  changed repos. This is the guarantee, so it needs a test rather than a comment.
- The run table records one row per (run, repo), and two runs over the same repo
  produce two rows rather than colliding.

## Alternative design: split extraction from enrichment

Everything above is **repo-centric**: `-next N` visits N repositories and does
both jobs for each. A daily read requirement exposes a problem with that, because
the two jobs change on completely different schedules.

| | changes when | how often |
|---|---|---|
| **manifest content** | someone edits a dependency file | rarely |
| **package metadata** (`versions_behind`, `advisories`) | upstream publishes a release or a CVE lands | continuously |

`versions_behind` and `advisories` are properties of
**`(package_type, package_name, package_version)`** — not of a repository. A
repository's row is manifest content × package metadata. Scanning repositories
daily therefore re-parses 1,081 manifests that almost never changed, in order to
refresh metadata that is not repo-specific in the first place.

### The split

Two jobs, two schedules, one axis each:

| job | unit of work | driven by | cadence |
|---|---|---|---|
| **extraction** | repository | manifest changed, or repo is new (`file_metadata.committer_when` / `hash`) | on change |
| **enrichment** | distinct package+version | oldest `last_checked` first, NULLs first | daily |

This is what the two-axis queue above was really trying to be. Conflating the two
jobs is why it needed a starvation floor: separate them and each gets exactly one
natural ordering, and nothing can starve.

### Work volume

On the current estate:

| | repo-centric daily | package-centric daily |
|---|---|---|
| manifests parsed | 1,081 | only those that changed |
| deps.dev lookups | 6,387 row-lookups, deduped by cache to ~4,864 | **exactly 4,864** |
| scales with | repository count | **distinct dependency surface** |

Package-centric makes the lookup count exact by construction rather than dependent
on cache hits landing inside the TTL window. More importantly it scales on the
right axis: two estates with 6,700 repositories each cost very differently if one
has 50,000 distinct package+versions and the other 200,000, and it is the latter
number that determines whether a daily pass is achievable.

### The fan-out update

One lookup updates every row holding that package identity:

```sql
UPDATE dependency_data
   SET versions_behind = ?, advisories = ?, published_at = ?,
       resolution = ?, resolved_version = ?, last_checked = ?
 WHERE latest = 1
   AND package_type = ? AND package_name = ? AND package_version = ?
```

Every column there is a function of the package identity alone, so writing it
uniformly across rows is correct.

**What this update must NOT touch**, because these are not package properties:

- `package_use` — `direct` / `dev` / `transitive` depends on *which manifest*
  declared it, and differs between rows for the same package
- `version_kind`, `version_operator` — set at extraction from the declared string
- anything `_repo_id`-derived

That boundary is checkable and worth a test, because the whole design rests on it.

It needs an index. The query is currently a full scan — the primary key leads with
`_repo_id`:

```
EXPLAIN QUERY PLAN → SCAN dependency_data
```

So migration `0008` would add `(package_type, package_name, package_version, latest)`.

### It fixes `last_checked` properly

Under this split, the only thing that writes `last_checked` is the thing that
actually asks deps.dev. The column finally means what its name says, rather than
"when a row was last written by a process that happened to have enriched first".

That requires extraction to stop stamping it — which means **parameterising the
timestamp in `save_dependencies`**, exactly what the concurrent-session brief
asked for and I argued against. I was right that it is wrong for the *current*
design, where the stamp is deliberate and four tests specify it. It becomes right
under *this* design. Worth recording, because the brief's instinct was about where
this should go, not about what the code currently means.

Those four tests (`test_last_checked_is_set`,
`test_pnpm_transitive_skips_lookup_but_still_classifies`,
`test_last_checked_updates_on_re_save`,
`test_resolved_version_does_not_survive_an_unenriched_re_save`) encode the current
meaning and would need revisiting deliberately, not deleting.

May also resolve [#193](https://github.com/kospex/kospex/issues/193)
(*resolved_version means different things depending on which tool wrote the row*),
since one writer would own the column.

### Costs and risks

1. **Two commands and two schedules to operate**, not one. The main argument
   against.
2. **A newly extracted package has no advisory data until enrichment reaches it.**
   Under repo-centric it is enriched in the same breath. Mitigated by enrichment
   ordering NULLs first — a never-checked package is always at the queue head —
   but the window is real and should be stated rather than hidden.
3. **Migration `0008`** plus its write cost on `dependency_data`.
4. **Four tests to revisit**, as above.
5. The enrichment pass is only correct if `latest = 1` rows are an accurate
   current set, which depends on the demote logic in `save_dependencies` (#217).

### Recommendation

**Package-centric is the better fit for a daily read requirement**, because it
makes cost scale with the dependency surface rather than the repository count, and
because it makes `last_checked` honest instead of incidental.

But repo-centric `-next` is a smaller change and is sufficient while the estate is
small — at 167 repositories a daily pass needs N=1 every 5 minutes, which is
trivially affordable even with the duplicate parsing. The cost of being wrong is
also asymmetric: `-next` is additive, whereas the split touches the write path and
the meaning of an existing column.

A reasonable path is to build `-next` first (it is needed either way for new and
changed repositories), run it, and use the run-diagnostics table to measure whether
duplicate parsing and cache-dependent dedup actually cost anything at the target
scale — then split if the numbers say so, with data instead of this argument.

## Open questions

1. **Should the outcome record be per-file as well as per-repo?** The repo-level
   row carries per-file detail in `data`, which serves the queue and is
   queryable as JSON. Separate `observation_type='FILE'` rows would be
   relationally queryable, at one row per manifest per run (~1,081 per run on the
   current estate). Deferred — the repo-level row is what `-next` needs, and the
   reporting shape #148 ultimately wants can be decided when something consumes it.
2. **Pruning the run table.** One row per repo per run accumulates. A `kreaper`
   target or a `--prune-older-than` flag — decided before `0008` ships, not after
   the table grows.
3. ~~Should `-next` prioritise recently-modified repositories?~~ **Decided: yes**,
   via the two-axis queue above. The signal is the dependency file's
   `committer_when` and `hash`, not `repos.last_fetch` (populated for only 111 of
   167) and not repo-level commit activity (a repo can be busy with no manifest
   change). The `--max-age` floor is what stops it starving unchanged
   repositories, which would otherwise silently stop new-CVE detection on stable
   code.
