# Sub-project C1: registry-driven dispatch for krunner osi

## Overview

`krunner osi` and `kospex sca` decided which manifests they could handle using
separate, hand-rolled filename checks, so `go.mod` and `*.csproj` were handled by
`sca` and silently skipped by `osi`. Both now dispatch off the extractor
registry, which is what closes that gap.

First half of sub-project C from
`changes/2026-07-17-extractor-registry-classifier-design.md`. Closes #107,
advances #180.

## Problem

The design named this in July:

> `kospex sca` (via `assess()`) and `krunner osi` dispatch on different,
> hand-rolled filename checks and disagree... Parity is a property you get by
> *sharing the extractor*, not by coincidence.

The registry recorded the gap as `scanners=("sca",)` on the `go-mod` and
`nuget-csproj` entries, and `parse_ref` was documentation only — "recorded, not
invoked", checked by a test that it resolved to a callable.

## Changes

**Dispatch comes from the registry.** `classify()` picks the entry,
`resolve_parser()` turns its `parse_ref` into a callable, and `package_type`
comes from the entry. `resolve_parser` is new: it handles both `module:function`
refs (the `extractors/` modules) and `module:Class.method` refs, which need
binding to an instance — an unbound function would silently receive the path as
`self`.

**Parsers moved into `extractors/`.** `gomod.py` and `nuget.py` join `pnpm.py` —
pure, no DB, no CLI, no enrichment, returning records shaped to
`get_package_template()`. `parse_go_mod_from_file()` and `nuget_assess()`
delegate to them rather than keeping their own copies.

**#107 closed.** `nuget_assess()` returns its records and `assess()`'s dispatch
captures them. Both bugs had to go; fixing either alone still yielded nothing.

**`package_type` for Go is `"go"`, not `"Go module"`.** Every other ecosystem
uses the lowercase deps.dev system name, the registry always declared `"go"`, and
`"Go module"` is not a system deps.dev accepts — measured:

```
'go'         -> HTTP 200  versionKey.system='GO'
'Go module'  -> HTTP 404
```

It only ever reached the DB column because `gomod_assess()` hardcoded `"go"` for
the lookup. It appeared exactly once in the codebase, so nothing read it.

**`ecosystem` → `eco_to_type` → `package_type` is gone.** `package_type` doubles
as the deps.dev system name; `pypi`, `npm`, `go` and `nuget` were each confirmed
to resolve there, so the mapping (and the drift it allowed) is unnecessary.

**Requirements matching delegates to panopticas.** Measured against a real
estate, panopticas was right in both directions where kospex was wrong:

| filename | osi substring | kospex regex | panopticas |
| --- | --- | --- | --- |
| `requirements-wheel-test.txt` | parsed | **missed** | matched |
| `requirements.rst` | **parsed** | — | rejected |
| `requirements.lock` | **parsed** | — | rejected |

The local pattern excluded a second hyphen; the substring check treated an `.rst`
documentation file as a manifest. Both forms exist in the reference estate. The
remaining matchers are commented as stand-ins — panopticas recognises every one
of them but exposes no per-type identification API to delegate to, only a tag bag
from `get_filename_metatypes()`.

## Files changed

- `src/kospex/extractors/gomod.py`, `nuget.py` — **new**, pure parsers
- `src/kospex/extractors/registry.py` — `resolve_parser()`,
  `_panopticas_matcher()`, `scanners=("sca","osi")` for go-mod and nuget-csproj
- `src/krunner.py` — registry dispatch; `extract_dependency_file()` extracted
- `src/kospex_dependencies.py` — delegation, `nuget_assess` returns, `"go"`
- `tests/` — `test_extractors_gomod.py`, `test_extractors_nuget.py`,
  `test_osi_dispatch.py` new; registry and nuget tests extended

## Testing

643 passed, 75 skipped (604 before).

The coverage-matrix test that recorded the gap now asserts parity, and a second
test fails any entry claiming exactly one scanner — the gap cannot reopen
silently.

Verified end-to-end against the reference estate, read-only:

```
discovery found 15 go.mod / .csproj files
  mergestat            go.mod    88 deps (direct=26, indirect=62)
  mergestat-lite       go.mod    77 deps (direct=29, indirect=48)
  kinlyze-library      go.mod     3 deps (direct=1, indirect=2)
  graphify             sample.csproj  4 deps (direct=4)
  ...
  TOTAL go-mod:        168 rows osi previously produced 0 of
  TOTAL nuget-csproj:    7 rows osi previously produced 0 of
```

Files reporting 0 were checked rather than assumed: `hugo-book`'s `go.mod` has no
`require` block and `Domain.csproj` has no `PackageReference`.

### Two defects found while testing

**A crash I introduced.** Dropping the `ecosystem` field made `osi` rows ragged.
`write_dict_to_csv` takes its header from `data[0]` alone and `DictWriter` raises
on a later row with extra keys, so a repo mixing `requirements.txt` with
`pnpm-lock.yaml` would have failed the assessment CSV write. The parsers return
genuinely different shapes — 3 keys from `parse_pip_requirements_file` against
the 11-key template — so uniformity is now produced explicitly in
`extract_dependency_file()`.

**A vacuous test.** The first version of `test_osi_dispatch.py` re-implemented
`osi`'s loop body rather than calling it, so deleting the production line left
all ten tests green. `extract_dependency_file()` was extracted so the test calls
the real function; the falsification check now fails with the genuine
`ValueError: dict contains fields not in fieldnames: 'ecosystem'`.

## Impact on existing databases

- **Go and .NET repos report dependencies from `krunner osi` for the first time.**
  On the reference estate that is 175 rows where there were none.
- **Go rows change `package_type` from `"Go module"` to `"go"`.** It is part of
  the `dependency_data` primary key, so new rows do not collide with old ones.
  An `osi` run over the same file demotes the old rows (the demote keys on repo +
  file path), but `assess()` has no demote — see #151.
- **`requirements.py` / `.rst` / `.lock` stop being parsed as manifests.** Any
  rows they produced remain until demoted.
- **`requirements-wheel-*.txt` files start being parsed.**

## Follow-on

- **C2** — route `assess()` through the registry too and migrate the remaining
  parsers. `assess()` still has its own dispatch predicates, one of which (#181)
  makes `sca -dev` inert.
- **#182** — `packages.config` (legacy NuGet) has no registry entry or parser.
- **#183** — Rye lockfiles are untagged by panopticas, so invisible to everything.
- **#137** — `setup.py` / `setup.cfg` remain the only files panopticas tags that
  the registry does not recognise.
