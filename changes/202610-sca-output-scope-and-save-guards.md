# `kospex sca`: output, `-scope`, `-json` and guarded `-save`

Issues: [#237](https://github.com/kospex/kospex/issues/237) (output and flags),
[#151](https://github.com/kospex/kospex/issues/151) (`assess()` never demoted).

## Overview

`kospex sca FILE` is an analyst's look at one dependency file. Before this change
it printed every enriched record as a Python dict (twice, inside a git repo),
then a PrettyTable with an `authors` column that was nearly always `0` or `-`
and a `malware` column backed by a deprecated service. It also **saved by
default with no way to stop it**, from whatever state the worktree was in.

Now:

```
$ kospex sca package.json
package.json
express	4.18.2	2022-10-08T20:14:32Z
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ package_name ┃ package_version ┃ versions_behind ┃ days_ago ┃ published_at         ┃ default ┃ advisories ┃ source_repo                          ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ express      │          4.18.2 │              20 │     1471 │ 2022-10-08T20:14:32Z │   False │          2 │ https://github.com/expressjs/express │
└──────────────┴─────────────────┴─────────────────┴──────────┴──────────────────────┴─────────┴────────────┴──────────────────────────────────────┘
sca run with scope [direct]: 1 of 4 packages shown. Other scopes in this file: dev (2), peer (1). Use -scope dev,peer or -scope all.
```

## Usage

| Flag | Behaviour |
|---|---|
| `-scope LIST` | Comma-separated: `direct`, `dev`, `peer`, `optional`, `transitive`, `unknown`, `all`. Default `direct`. Filters display only. |
| `-json` | Table rows as JSON on stdout; everything else on stderr. |
| `-out FILE` | CSV of the table (same rows, same columns). |
| `-save` | Write to `dependency_data` via `save_dependencies(source="kospex sca")`. Guarded. |
| `-malware` | Deprecated; prints a notice and is ignored. |

## Decisions

- **One column list drives table, CSV and JSON** (`get_table_field_names()`), so
  they cannot drift. `-json` emits the table's contents, not the full enriched
  record.
- **`-scope` filters display, never extraction or saving.** Extraction has been
  complete since #181. This is what makes the whole-file demote safe.
- **`unknown` is a scope.** `package_use` is NULL when the parser could not
  determine it (`PACKAGE_USE_UNKNOWN`); without a name for it, those rows could
  only be reached via `all`.
- **The footer lists only scopes present in the file, with counts.** Listing
  every possible scope would send the reader to empty tables.
- **`-dev` is removed, not kept as an alias.** It was `is_flag=True,
  default=True`, so it could never be turned off; nothing could have depended on
  it doing anything.
- **`-save` guard rails** (`KospexDependencies.save_preconditions()`), checked
  before `assess()` so a refused run spends no deps.dev lookups. All failures are
  reported, not just the first:
  - file is inside a git repository
  - `repos` has the `_repo_id`
  - `commits` has `(_repo_id, HEAD)`
  - `git status --porcelain --untracked-files=no` is empty
  - the file itself is tracked (`git ls-files --error-unmatch`)

  Untracked files elsewhere do not count: nearly every working checkout has some,
  and they cannot change what HEAD contains.
- **Save routes through `save_dependencies()`**, the `krunner osi` writer, which
  is now the **only** writer to `dependency_data` (`krunner osi -all`,
  `osi -next`, and `assess()`). Of `assess()`'s three callers only `sca` passes
  `save` — `kospex deps -file` and the kweb upload never did, and the
  `kospex deps -save` that #151 cites no longer exists. It does a whole-file demote + upsert in one
  `atomic()` transaction, plus a `source` stamp. **Outcome recording
  (`record_outcome`) is deliberately not done**: it writes one repo-level record
  mapping *every* manifest to its outcome, and a single-file sca run would
  replace it with a one-file map.
- **`days_ago` is computed once, in `depsdev_record()`**, as integer days since
  `published_at` of the looked-up version (the floor of a range). `assess()` used
  to overwrite it with `KospexUtils.days_ago()`, a two-decimal float that is
  `0.0` for an empty string.
- **`authors` lookup is kept** in `depsdev_record()`: `kospex deps -file` still
  uses it for its "Dependency:Repo Author Ratio". Only the column is gone.
- **Rich cells are `Text`**, not `str`, so `mkdocstrings[python]` is not parsed as
  markup. Columns use `overflow="fold"`, so a pipe (rendered at 80 columns) wraps
  values rather than ellipsising them.

## Found along the way

- **`-out` never wrote anything.** The CLI passed `out`; `assess()` read
  `results_file` and never used it.
- **`find_git_base()` ignored `.git` files**, so in a linked worktree or submodule
  it walked up to the outer checkout. The `-save` guard checked the wrong repo,
  and `assess()` stamped rows with the outer repo's id and HEAD. Fixed with
  `os.path.exists`.

## Follow-ups

- Removing the malware lookup itself (`check_malware()`, the `-malware` flag, the
  `supply_chain.html` red-on-malware styling) is tracked separately.
- A `build` scope needs a parser that records one; none does yet.

## Files changed

- `src/kospex_cli.py` — `sca` command rewritten
- `src/kospex_dependencies.py` — table/scope/JSON helpers, `save_preconditions()`,
  `assess()` quiet mode and save via `save_dependencies()`, PrettyTable removed
- `src/kospex_utils.py` — `find_git_base()` accepts a `.git` file
- `tests/test_sca_output_and_save.py` — new
- `docs/kospex.md` (new `sca` section; `authors` dropped from `deps`),
  `docs/refreshing-data.md`, `CHANGELOG.md`
