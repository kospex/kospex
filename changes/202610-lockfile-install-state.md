# Lockfile install state: dev, optional, install-script, and the raw declared string

Migration `0009` adds four columns to `dependency_data`, populated by the pnpm
extractor. Lockfiles state per entry whether a dependency reaches production,
whether it installs at all, and whether it **executes code at install time**.
kospex read the name and version and discarded the rest.

Issue [#227](https://github.com/kospex/kospex/issues/227), PR
[#231](https://github.com/kospex/kospex/pull/231). The reference page for the
formats is `docs/dependency-flags.md` (PRs #233, #234).

## Files changed

| file | change |
|---|---|
| `src/kospex/db/migrations/0009_dependency_install_state.sql` | new — `is_dev`, `is_optional`, `runs_install_script` (INTEGER), `declared_scope` (TEXT) |
| `src/kospex/extractors/pnpm.py` | `_install_state_from_packages` (v5/v6), `_install_state_from_snapshot` + `_snapshot_optionals` (v9) |
| `src/kospex/extractors/gomod.py`, `nuget.py` | record templates extended (contract test) |
| `src/kospex_dependencies.py` | `get_package_template()` gains the four keys |
| `src/krunner.py` | keys `setdefault`-ed at the dispatch's uniformity point |
| `tests/test_install_state_flags.py` | new — 24 tests |
| `tests/test_db_health.py`, `test_kospex_schema.py`, `test_kospex_utils.py` | migration counts 6 → 7 |
| `docs/dependency-flags.md` | new — per-format reference, definitions quoted from each format's own docs |

**Existing databases need `kospex upgrade-db -apply`.** Until it runs, the new
code hard-fails on save — `sqlite_utils` is not called with `alter=True`, so
`save_dependencies` raises `OperationalError: table dependency_data has no column
named declared_scope` and `krunner osi` dies rather than degrading. The window
opens at `git pull`, not at merge, because the migrator scans the migrations
directory on disk.

## Why `requirements_type` could not carry this

Its `dev` value means "declared as a devDependency", and a transitive entry has
no declaration of its own. On `chartjs/chart.js` that recorded 834 dev-only
transitive packages identically to the production ones. The lockfile knew.

`runs_install_script` is the column to notice. It answers *which of these executes
during install*, which is what separates a compromised package that ran from one
merely present in a tree — worth populating before a malware feed rather than
after, since retrofitting means re-parsing every lockfile in an estate.

## Install state is a per-lockfile-version fact

The first implementation read all three flags off each `packages:` entry with a
`False` default, on this stated assumption:

> pnpm omits each of these keys when false, so absence means false rather than
> unknown — the format answers the question for every entry.

True of v5/v6. **False of v9**, which moved the resolved graph into a separate
`snapshots:` section and records none of the three in `packages:`. Measured over
the real lockfiles on disk:

| file | lockfileVersion | `packages:` | `dev` | `optional` | `requiresBuild` |
|---|---|---|---|---|---|
| chartjs/Chart.js | 6.0 | 2079 | 1462 | 35 | 50 |
| maplibre/maplibre-native | 9.0 | 306 | 0 | 0 | 0 |
| tailwindlabs/tailwindcss | 9.0 | 569 | 0 | 0 | 0 |

So it wrote a confident `False` for all 875 v9 entries across two files — not a
blank, a wrong answer, in columns whose entire point is that NULL and false are
different facts. 208 of those are entries `snapshots:` marks optional.

What each version can answer:

| | v5 / v6 | v9 |
|---|---|---|
| `is_dev` | `packages:` `dev` | **NULL** — not recorded anywhere |
| `is_optional` | `packages:` `optional` | `snapshots:` `optional` |
| `runs_install_script` | `packages:` `requiresBuild` | **NULL** — not recorded anywhere |

v9 dev-ness is derivable only by walking the graph from each importer's
`devDependencies`, which this parser does not do (still open on #227). pnpm
tracks which packages were built outside the lockfile entirely.

Two implementation details that only surfaced against real files:

- **Normalise the snapshot key before matching.** Snapshot keys carry the same
  `(peer@x)` suffixes as `packages:` keys, so one package can appear under
  several. Exact-key matching found 204 of the 208.
- **A package is optional only if *every* path to it is optional.** Those
  variants can disagree — one package of 569 is optional on one peer path and
  required on another. `is_optional` means "may not install", so one required
  path settles it. `any()` and `all()` differ by exactly one package here, which
  is why the rule is pinned by its own synthetic test rather than left to that
  count to catch.

## `declared_scope` holds the raw string

Verbatim, in that format's own vocabulary. The three booleans are a projection
of it.

The column exists because **every mapping error in this work happened at write
time**, where it is unrecoverable:

- `requiresBuild` was read as equivalent to npm's `hasInstallScript`. The pnpm
  spec defines it as "lifecycle scripts **or** a native module that needs to be
  built" — a strict superset. `@swc/core` is flagged by both; its ten
  platform-specific siblings only by pnpm, being prebuilt native artifacts with
  no scripts.
- An absent key was read as false, per the section above.

With the declared string stored, a mapping that turns out wrong is a query to fix
rather than a re-parse of every lockfile in an estate.

Deliberately **not** normalised, so no mapping decision is baked in at write
time:

| format | values |
|---|---|
| pnpm v6 | `dev`, `optional,requiresBuild`, … (sorted, comma-joined) |
| pnpm v9 | `snapshots:optional` (qualified by section) |
| npm | `dev`, `optional`, `devOptional`, `hasInstallScript` (#229) |
| Maven | `test`, `provided`, `runtime`, `compile`, `system`, `import` (#229) |
| Cargo | `dev-dependencies`, `build-dependencies` (#229) |
| Python | the extra or group **name**, which is the information (#229) |

`dev,optional` must stay distinguishable from npm's `devOptional`, which the
definitions' use of *strictly* makes a third state rather than the union of two.
Cross-ecosystem queries use the booleans. Same split as `0006`'s
`version_operator` beside `version_kind`.

## NULL means unknown, not false

The load-bearing part of the column semantics.

| stored | meaning |
|---|---|
| true | the format says so |
| false | the format says otherwise |
| **NULL** | **the format cannot say** |

`requirements.txt` cannot state whether a package runs an install script. A
`lockfileVersion: 1` `package-lock.json` records no flags — one in the dev estate
has 493 entries and none of these fields. `go.mod` has no dev concept. Writing
`false` there asserts something the file does not say, and a reader then cannot
tell "we checked and it does not" from "we never knew".

The same distinction is already drawn twice in this schema: `resolution` for the
resolve layer, and `last_checked` in `0006`, whose comment insists a staleness
indicator must render NULL as *never checked* rather than *checked long ago*.

## Verification

End to end on a copy of the live database after applying `0009`:

```
v6  chart.js      2079 rows   (none) 1155 · dev 834 · dev,optional,requiresBuild 24
                              dev,requiresBuild 12 · optional,requiresBuild 11
                              requiresBuild 3
v9  tailwindcss    569 rows   snapshots:optional 208
                              is_dev and runs_install_script NULL throughout
```

The 50 `runs_install_script=1` rows are `@swc/core` and its platform binaries,
and `declared_scope` shows which field produced each — `requiresBuild` for the
package with an actual script, `optional,requiresBuild` for the ten prebuilt
native siblings only pnpm's superset flags.

24 tests. Full suite with a live kweb: **1202 passed, 8 skipped**. The two
`test_web_endpoints` failures are `/repos/` (11.2s) and `/orphans/` (7.8s)
straddling the 10s test timeout — [#218](https://github.com/kospex/kospex/issues/218)
/ #61, both returning 200, neither touching `dependency_data`. `/dependencies/`,
which does read it, serves in 0.08s.

## Notes for the next change here

- **`0009` was amended in place** rather than corrected by a `0010`, because no
  database had recorded its checksum — `main` stopped at `0008` and so did the
  dev install. The rule is "do not edit a migration once committed **and
  shipped**"; a corrective `0010` would have shipped a broken column and then
  fixed it.
- **A `;` inside a `--` comment breaks the migration run.** Hit while writing
  this one: the splitter treats it as a statement break, and the run failed with
  `near "declared_scope": syntax error`. Documented in the migrations README;
  easy to walk into anyway.
- **Three contract tests and one uniformity test caught this change being
  incomplete**, which is them working as designed. `gomod` and `nuget` keep their
  own copies of the record template and `TestTemplateContract` fails when they
  drift from `get_package_template`. `test_osi_dispatch` asserts every row shares
  one key set, because `write_dict_to_csv` takes its header from row 0 and
  `DictWriter` raises on a later row with extra keys — hence the `setdefault`s at
  the dispatch's documented uniformity point, so the 3-key pip parser gets None
  while a lockfile parser keeps its real values.
- **A correction to what #227 originally claimed**, recorded there as a comment.
  The issue said `requirements_type` was "actively wrong" because `react` is
  labelled `direct`. Checking the lockfile, `react` genuinely *is* a direct
  dependency — of `test/integration/react-browser`. The column is coarse rather
  than wrong, and there was no mislabelling to fix. What was lost is the flags.

## Still open

- **Workspace attribution** (#227) — gated on the schema choice in that issue:
  60% of packages are reachable from more than one importer, so it is
  many-to-many and `dependency_data`'s primary key has no importer column.
- **v9 `is_dev`** (#227) — the graph walk from each importer's `devDependencies`,
  which would populate the one flag v9 currently leaves NULL.
- **The other formats** (#229) — `declared_scope` is populated by pnpm alone, so
  npm's five states, Maven's six scopes and Python's named groups are documented
  but unread. Land them in these columns rather than new ones; that is why they
  are named format-neutrally.
