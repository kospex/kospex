# Sub-project C2a: registry-driven dispatch for assess()

## Overview

`kospex sca` and `kospex deps` reached six hand-rolled branches to decide which
parser handled a manifest, while `krunner osi` used the extractor registry.
`assess()` now dispatches the same way, from the same catalog, with one shared
enrichment path.

Second half of sub-project C from
`changes/2026-07-17-extractor-registry-classifier-design.md`. Closes #181,
advances #180.

## Problem

`assess()` selected a parser with `basefile ==`, `is_npm_package()`,
`is_nuget_package()` and `is_pip_requirements_file()`, then called one of five
`*_assess()` functions that each did their own parsing, deps.dev enrichment and
table printing. Three consequences, all measured:

**`dev_deps` filtered extraction, not display.** `npm_assess()` skipped
`devDependencies` unless the flag was set, so `assess()` returned an incomplete
manifest. That is what made `sca -dev` inert (#181 — `sca` passes `dev`,
`assess()` reads `dev_deps`, so the flag never arrived), and what blocked a
whole-file demote in `assess()` (#151).

**The pypi paths never set `package_use`.** `requirements.txt` and
`pyproject.toml` rows carried NULL where npm, go and nuget carried a value.

**npm rows from the two scan paths did not reconcile.** `assess()` stripped the
`~`/`^` prefix before storing, `osi` stored the declared text:

```
assess() stores : [('express', '4.18.0'),  ('lodash', '>=4.0.0')]
osi path stores : [('express', '^4.18.0'), ('lodash', '>=4.0.0')]
```

`package_version` is part of the `dependency_data` primary key, so the same
dependency scanned both ways produced two rows rather than one.

## Changes

**Dispatch comes from the registry.** `classify()` selects the entry,
`resolve_parser()` returns its parser. Same mechanism `krunner osi` uses.

**One enrichment path**, `_enrich_dependency_records()`, replacing five. Every
ecosystem goes through `depsdev_record()` with the registry's `package_type`,
which doubles as the deps.dev system name. Two per-ecosystem extras are kept as
explicit hooks: npm records the `~`/`^` prefix as `semantic`, pypi falls back to
`get_pypi_source_repo()` when deps.dev has no source repo.

**`package_version` keeps the declared text; only the lookup is normalised**
through `clean_version_spec()`. This follows the rule
`parse_pypi_package_declaration()` already states — rewriting a primary-key
column inserts duplicate rows on re-sync instead of updating. It also aligns
`assess()` with `osi`, so npm rows now reconcile.

**`dev_deps` controls display only.** Extraction and the DB write are always
complete; dev dependencies are tagged `PACKAGE_USE_DEV`, and the printed table
filters them out unless `-dev` is passed, reporting how many were hidden.

**`package_use` is always set**, defaulting to `PACKAGE_USE_DIRECT`.

## Verification

666 passed, 75 skipped (647 before).

`assess()` output was characterised per ecosystem *before* the refactor and
compared after. Exactly three intended changes, nothing else:

```
                          before                    after
package.json dev=False    n=1 ['express']           n=2 ['express','jest']
pyproject / requirements  use=['None']              use=['direct']
go.mod, demo.csproj       unchanged                 unchanged
```

## Impact on existing databases

- **npm projects report more dependencies.** `devDependencies` are now always
  extracted. Filter on `package_use = 'direct'` to compare against an earlier
  run.
- **npm `package_version` changes for caret/tilde dependencies** written by
  `sca`/`deps` — `4.18.0` becomes `^4.18.0`, matching what `osi` already wrote
  and what the manifest declares. Since `package_version` is in the primary key,
  the old rows do not collide; an `osi` run demotes them, `assess()` does not
  (#151).
- **`requirements.txt` and `pyproject.toml` rows gain `package_use = 'direct'`**
  where they previously had NULL.

## Follow-on

**C2b** — move `parse_pip_requirements_file`, `parse_pyproject_file` and
`parse_package_json` into `extractors/`, and retire the `*_assess()` functions.
They are now **dead in production**: nothing in `src/` calls
`npm_assess`, `pypi_assess`, `pypi_assess2`, `gomod_assess` or `nuget_assess`,
and only tests keep them alive. Deferred because the parsers pull a dependency
chain (`detect_encoding`, `parse_pypi_package_declaration`, its
`_PYPI_NAME_EXTRAS_RE`, `get_package_template`) — around 150 lines of pure
movement whose regression risk is best reviewed on its own, separate from the
behaviour changes here.

**#151** — `assess()` extraction is now complete for every ecosystem, which was
the blocker on routing its write through `save_dependencies()` and demoting.

**#187** — the enrichment now lands in one place, which is where a
pinned-vs-floating column would be populated.
