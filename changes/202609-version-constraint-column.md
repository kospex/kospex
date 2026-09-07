# Recording the dependency version constraint

## Overview

Kospex computes how a dependency is constrained and then discards the answer.
"Which of our dependencies float?" cannot be asked of the database, and advisory
counts cannot be interpreted without knowing what version they refer to or when
they were last checked.

This adds four columns to `dependency_data`, a pure classifier that populates
them, and realigns the one parser that stores `package_version` differently from
the rest.

Design for #187.

## Problem

### The classification is computed, then thrown away

Both pypi and npm parsers derive constraint information and both lose it:

- `version_type` (pypi: `==`, `>=`, `~=`, `multiple`)
- `semantic` (npm: the `~` / `^` prefix)

Both are listed in `_NON_SCHEMA_FIELDS` and stripped before the upsert.

### `resolution` does not substitute for it

`clean_version_spec("^4.18.0")` returns `4.18.0`, which resolves at deps.dev, so
a caret range records `resolution = resolved` exactly like an exact pin.
Measured on a 109-repo estate:

```
pinned/exact       3115 (49.5%)      resolution: resolved          5017
^ range            1588 (25.2%)                  unresolved_spec    921
other               937 (14.9%)                  no_version         251
>= constraint       238  (3.8%)                  version_yanked      81
```

About 30% of dependencies are floating while `resolved` covers 80% of rows. The
two describe different things: `resolution` is whether the *lookup* worked,
constraint kind is what the *manifest declared*.

### Detection is uneven across parsers

Only `parse_pypi_package_declaration()` derives `version_type`.
`parse_package_json()` returns `''` for everything, so npm compound ranges —
`^1.0.0 || ^2.0.0`, `>=1.0 <2.0`, ordinary npm syntax — carry no classification
at all. npm is the bulk of a typical estate (5375 of 6386 rows measured), so a
column populated from today's parser output would be empty for most rows.

### The requirements parser flattens `>=` into a pin

`parse_pip_requirements_file()` splits the operator into `version_type` and
stores the bare version, and `version_type` is then dropped:

```
requests>=2.31.0  ->  package_version='2.31.0'  version_type='>='   (discarded)
```

Reading the source files rather than the database shows what this costs.
62 requirements files, 461 requirement lines:

```
  == (pinned)                244  (52.9%)
  bare name (no version)     123  (26.7%)
  range (>=, <=, ~=, !=)      73  (15.8%)
  range (>, <)                19  (4.1%)
```

Only 53% are pins, but ~20% carry ranges that are stored looking pinned. The
stored data reads as 94% bare, which would overstate pinning discipline by about
20 percentage points with no signal that anything was lost.

### Advisories are computed against the floor, and nothing says so

`clean_version_spec()` extracts the lowest version satisfying a constraint. For
any non-pinned dependency, `versions_behind` and `advisories` therefore describe
the **worst case** — the oldest version the constraint permits — not what is
installed. Defensible as a risk default, but only interpretable if the consumer
knows the row was a range and which version was queried. Today they cannot tell:
a `^4.18.0` row and a `4.18.0` row are indistinguishable while their advisory
counts mean different things.

### There is no record of when advisory data was checked

`dependency_data.created_at` is `DEFAULT CURRENT_TIMESTAMP`, so it is set on
INSERT and never updates. Demonstrated by re-running `krunner osi` over
`mergestat`, which re-fetched every row from deps.dev:

```
go    latest=1  created_at=2026-09-01   88 rows   (newly inserted)
npm   latest=1  created_at=2026-07-15   95 rows   (updated in place today)
```

The 95 npm rows had their advisory data refreshed that day and still read 15
July. `created_at` means "first seen", not "last refreshed". A staleness
indicator built on it would mark fresh data as seven weeks old — worse than no
indicator, because it teaches people to distrust a signal that is wrong.

## Design

### Four columns, migration `0006`

All nullable, so existing rows remain valid.

| column | holds | empty when |
| --- | --- | --- |
| `version_kind` | normalised classification (below) | never — `none` covers no declaration |
| `version_operator` | the raw declared operator, verbatim | a bare pin declared no operator |
| `resolved_version` | what was sent to deps.dev | nothing resolved (`commit`, `latest`, `none`) |
| `last_checked` | UTC timestamp, written on every save | never, once written |

`last_checked` is a new column rather than a repair of `created_at`.
`created_at` legitimately means "first seen" and something may depend on it;
redefining it would silently reinterpret 6,386 existing rows.

`version_operator` for a bare pin stores the empty string, not an implicit `==`.
The manifest wrote nothing, and `version_kind='pinned'` already carries the
meaning.

### The `version_kind` vocabulary

Thirteen values. Each carries a distinct risk meaning, which is why they are not
collapsed into pinned/floating.

`tilde` was not in the twelve shown when this was agreed — it is added here
because `~29.0.0` is ordinary npm syntax with narrower drift than `^`, and
folding it into `caret` would misreport how much a dependency can move. Called
out rather than absorbed silently, since it changes an agreed vocabulary.

| kind | example | meaning |
| --- | --- | --- |
| `pinned` | `1.4.3`, `==2.31.0` | exact version |
| `commit` | `v0.0.0-20230828082145-3c4c8a2d2371` | pinned to a commit, no published release |
| `caret` | `^4.18.0` | minor and patch drift permitted |
| `tilde` | `~29.0.0` | patch drift permitted |
| `gte` | `>=2.0` | open floor, no upper bound |
| `bounded` | `>=1.0,<2.0`, `^1.0.0 \|\| ^2.0.0` | windowed — an upper bound exists |
| `latest` | `latest` | unpinned entirely |
| `workspace` | `workspace:^` | internal monorepo reference |
| `link` | `link:../scripts/repo-utils` | internal, local path |
| `catalog` | `catalog:dev` | internal, pnpm catalog |
| `alias` | `npm:@babel/core@7.24.4` | row name is not the installed package |
| `patch` | `patch:rollup-plugin-dts@npm%3A6.1.0#...` | shipped code differs from the published artefact |
| `none` | (empty) | no version declared |

`commit` is deliberately distinct from `pinned`: a Go pseudo-version is
*maximally* pinned, yet today it lands in `unresolved_spec` alongside
`>=1.0,<2.0`, which is the opposite. It is also distinct from `pinned` for
advisory purposes — deps.dev has nothing to match against.

Internal references (`workspace`, `link`, `catalog`) remain rows. Dropping them
would shrink dependency counts for monorepos with nothing recording why, which
is the failure mode #148 exists about. Consumers exclude them by kind.

### A pure classifier

`src/kospex/extractors/constraints.py`:

```python
classify_constraint(declared_version, package_type) -> (kind, operator)
```

Pure — no DB, no network, no `KospexDependencies` — following the `extractors/`
convention established by `pnpm.py`, `gomod.py` and `nuget.py`, with its own
tests.

It must be ecosystem-aware: `^` is npm syntax, `v0.0.0-<timestamp>-<sha>` is a
Go pseudo-version, and `catalog:` is pnpm. The same string does not mean the
same thing everywhere.

### Population

Two call sites, one per write path:

- `KospexDependencies._enrich_dependency_records()` — the `assess()` path. It
  already computes the lookup version, so `resolved_version` is free here.
- `KospexDependencies.save_dependencies()` — the `krunner osi` path.

Both stamp `last_checked`. Both must, or a row refreshed by one path and not the
other would carry a misleading timestamp — the same defect `created_at` has.

`version_type` and `semantic` stay in `_NON_SCHEMA_FIELDS`: they are the
parser's raw working values, and the new columns are what persists.

### Realigning `package_version`

`parse_pypi_package_declaration()` stops splitting the operator out of
`package_version`. `requests>=2.31.0` stores `>=2.31.0`, matching what
`pyproject.toml`, `package.json`, `go.mod` and `.csproj` already store and what
the codebase's own rule states — `package_version` holds the declared text,
because rewriting a primary-key column inserts duplicate rows on re-sync.

Today requirements.txt is inconsistent even with itself: single-operator lines
are stripped to `1.4.3`, compound ones keep their text (`>=23.0, <24.3`),
because the parser cannot split them.

Nothing computes on the column. All three templates rendering it
(`dependencies.html`, `package_check.html`, and the unrelated
`supply_chain_search.html` form input) interpolate it verbatim — no parsing, no
comparison, no sorting. The change is therefore display-only downstream.

## Impact on existing databases

- **About 317 requirements rows change `package_version`** (`2.0` → `>=2.0`). It is
  part of the primary key, so new rows do not collide; the old ones are demoted
  by the next `krunner osi` run over the same file. `assess()` has no demote
  (#151). Of 465 total pypi rows from requirements-like files, 317 stored bare
  versions and actually change, 129 carried empty versions (no specifier) and are
  unchanged, and 19 already carried operators (multi-specifier branch) and are
  unchanged.
- **All four columns are NULL on existing rows** until re-sync. Any staleness UI
  must render NULL as "never checked", not "checked long ago" — the distinction
  that makes the indicator trustworthy.
- **`/dependencies/` shows `>=2.0`** where it showed `2.0` for those rows.
  Rows from other parsers in the same table already display ranges this way, so
  this makes the column consistent rather than introducing a new form.

## Testing

**Classifier truth-table** across all thirteen kinds and all five ecosystems,
drawn from values that actually occur in the reference estate rather than
invented ones:

```
workspace:^          catalog:dev        link:../scripts/repo-utils
npm:@babel/core@7.24.4                  patch:rollup-plugin-dts@npm%3A6.1.0#...
v0.0.0-20230828082145-3c4c8a2d2371      ^1.0.0 || ^2.0.0
>=23.0, <24.3        latest             (empty)
```

**Totality**: the classifier returns a `(kind, operator)` tuple for every input
including empty string, `None` and malformed values, and never raises. A
classifier that throws would break extraction for an entire manifest.

**`last_checked` updates on re-save** — asserted by writing a row, re-saving it,
and checking the timestamp moved. This is the precise bug `created_at` has, so
it needs a test that would fail if the column were given a DB default instead of
being written explicitly.

**`package_version` realignment** — a requirements.txt line with an operator
stores the declared text, and the corresponding `version_operator` and
`version_kind` are populated.

**Both write paths populate all four columns** — a row through `assess()` and a
row through `save_dependencies()` are both complete. Testing one and assuming
the other is how the `v`-prefix defect reached production twice.

**The clean-install invariant** (`test_fresh_db_has_no_pending_migrations`) picks
up `0006` automatically and will fail until it applies cleanly on a fresh
database.

## Risks

**This is the first migration since `0005`.** Two open bugs sit exactly on that
path: #175 (`upgrade-db` cannot recover a database whose schema is ahead of its
ledger) and #176 (`upgrade-db -apply` fails with a raw traceback rather than
diagnosing a foreseeable conflict). Neither blocks writing `0006`, but both make
a failed application harder to diagnose. Worth watching during local
verification; if either fires, fix it before shipping rather than working around
it.

**The classifier encodes judgement.** Whether `^1.0.0 || ^2.0.0` is `bounded` or
deserves its own kind is a decision, not a fact. Recorded here so it can be
challenged rather than discovered later in a query that returns something
surprising.

## Out of scope

- Backfilling existing rows. They populate on re-sync.
- A staleness indicator in the web UI. This adds the data; rendering it is
  separate work, and it needs the NULL-versus-stale distinction settled first.
- Changing what `resolution` means. It stays "did the lookup succeed"; the new
  columns carry what the declaration was.
