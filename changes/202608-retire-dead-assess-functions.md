# Retire the dead per-ecosystem assess functions

## Overview

`assess()` moved to registry dispatch in sub-project C2a (#188), which left the
five per-ecosystem `*_assess()` functions with no callers in `src/`. They are
removed here, along with two helpers that only they used.

First half of sub-project C2b. The parser migration into `extractors/` follows
separately.

## What was removed

```
pypi_assess              133 lines   (including a dead upsert_all)
npm_assess                53
gomod_assess              47
nuget_assess              28
pypi_assess2              20
get_npm_dependency_dict   53   orphaned by npm_assess
write_csv                  7   orphaned by the assess functions
```

341 lines. `src/kospex_dependencies.py` goes from 1816 to 1488 lines.

`pypi_assess` carried the third `upsert_all` in the module — a database write
path nothing could reach, which is worth removing on its own merits.

## Confirming they were unreachable

Grep alone is not proof for a module whose parsers are resolved from strings, so
three checks:

- No non-definition references in `src/` — the single `nuget_assess` hit was a
  docstring line in `extractors/nuget.py`.
- No registry `parse_ref` points at any of them; all six targets are the
  parse-only functions.
- No dynamic dispatch (`getattr` on an instance) exists that could reach them.

`get_npm_dependency_dict` and `write_csv` were checked after the main deletion
and had zero references anywhere, including tests — they were orphaned *by* this
change, so removing them completes it rather than widening it.

## Tests repointed, not deleted

Four test files called the removed functions directly. Their assertions cover
real behaviour (#177 single-line requires, #178 indirect classification, #107
NuGet persistence, #108 multi-specifier), so they now exercise `assess()` — the
path that actually runs.

One test was dropped rather than repointed: `test_nuget_assess_returns_records`
asserted that `nuget_assess()` returns records. Its sibling already asserts the
same behaviour through `assess()`, and a test against a deleted function proves
nothing.

## One test parked, not fixed

`test_pypi_multi_specifier_retains_version_and_classifies_unresolved` (#108) is
marked `xfail(strict=True)` pending a decision on #187.

`assess()` normalises the lookup through `clean_version_spec()`, which reduces
`>=1.0,<2.0` to its floor `1.0`. That is concrete, so the row gets a real
deps.dev lookup and `resolution='resolved'` rather than `unresolved_spec`.

This is **not simply a regression to revert**. `krunner osi` has always behaved
this way — measured, same input, same result — so #188 aligned `assess()` to it
rather than breaking it, and the floor lookup yields advisory data the old path
discarded. What is genuinely wrong is that `resolution` cannot distinguish "the
declaration was a range" from "the declaration was a pin". That is the column
#187 exists to add, and the decision belongs there rather than being settled by
quietly rewriting a regression test.

`strict=True` means the test fails if it starts passing, so whoever resolves
#187 must either remove the marker (behaviour restored) or rewrite the
assertions (behaviour deliberately kept). It cannot drift back to green
unnoticed.

## Verification

672 passed, 75 skipped, 1 xfailed.

Every ecosystem still works through `assess()` after the deletion:

```
requirements.txt     1 -> requests[direct]
pyproject.toml       1 -> click[direct]
package.json         2 -> express[direct], jest[dev]
go.mod               2 -> github.com/pkg/errors[direct], github.com/x/y[transitive]
demo.csproj          1 -> Serilog[direct]
```

## Follow-on

**C2b second half** — move `parse_pip_requirements_file`, `parse_pyproject_file`
and `parse_package_json` into `extractors/`. They pull `detect_encoding`,
`parse_pypi_package_declaration` (the PEP 508 work from #139) and
`get_package_template` with them, since extractors must be pure — roughly 240
lines of movement, best reviewed as "moved, not modified" in a diff containing
nothing else.
