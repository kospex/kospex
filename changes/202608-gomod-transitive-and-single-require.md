# go.mod: parse single-line requires, record indirect deps as transitive

## Overview

Two defects meant `go.mod` reported fewer dependencies than it declares. Both are
fixed here, because go.mod is not a complete view of its manifest until both are —
which is what #151 needs before `assess()` can safely demote a whole file.

Closes #177 and #178.

## Problem

### #177 — single-line `require` directives were never parsed

`parse_go_mod_from_file()` gated every parse path behind `in_require_block`, which
is only set by an exact `require (` line. A `require <module> <version>` on one
line — valid go.mod syntax, and what `go mod init` plus a single `go get`
produces — was skipped entirely.

Measured on `main` (17d0f10), deps.dev stubbed so the count reflects parsing only:

```
require github.com/spf13/cobra v1.8.0        ->  0 records
require (
    github.com/pkg/errors v0.9.1
)                                            ->  1 record
```

### #178 — indirect modules were parsed then discarded

`gomod_assess()` filtered with `if item["indirect"] is False`, so `// indirect`
modules never reached `dependency_data`. The information was available —
`parse_go_mod_from_file()` sets `indirect` correctly — it was dropped one level up.

### Also found: comment lines inside a require block became modules

A bare `// grouped for clarity` inside the block splits into two or more parts, so
it satisfied the `len(parts) >= 2` test and was recorded as a module named `//`
with version `grouped`:

```
assert [d["module"] for d in deps] == ["github.com/real/dep"]
AssertionError: assert ['//', 'github.com/real/dep'] == ['github.com/real/dep']
```

Pre-existing, unfiled, and fixed here because the parse loop was being rewritten
anyway.

## Changes

**`parse_go_mod_from_file()`** now reads both require forms. It matches on the
`require` keyword rather than parsing every non-block line, so `exclude`,
`replace` and `retract` directives are still excluded — previously they were
skipped only incidentally, because nothing but `require (` set the in-block flag,
which a naive fix could have broken. Comment-only lines are skipped.

**`gomod_assess()`** records every module, tagging `// indirect` ones
`PACKAGE_USE_TRANSITIVE` and the rest `PACKAGE_USE_DIRECT`. It previously set no
`package_use` at all, so direct Go dependencies were being stored with the column
empty.

Indirect modules get the same `depsdev_record()` enrichment as direct ones. This
deliberately differs from the pnpm extractor, which stores transitive rows but
skips enrichment: a pnpm lockfile closure can carry thousands of entries and each
lookup is an HTTP round-trip, whereas a go.mod indirect list runs to tens or low
hundreds. A transitive dependency carrying a known advisory is the most valuable
row in the table, and an unenriched one answers "what do I depend on" but not "am
I exposed". Rationale recorded on #178.

## Files changed

- `src/kospex_dependencies.py` — `parse_go_mod_from_file()`, `gomod_assess()`
- `tests/test_gomod_parsing.py` — **new**; there were no go.mod tests at all
- `CHANGELOG.md`

## Testing

Eight tests covering both require forms, mixed files, the directive exclusions,
comment lines, indirect classification, indirect enrichment, and the empty case.

The directive test is the one worth keeping: `exclude` / `replace` / `retract`
were previously excluded by accident rather than by intent, so it pins behaviour
that had no test and could regress silently.

End-to-end, against the parser survey used to scope #151:

```
before:  go.mod (2 direct + 1 indirect)  ->  2 of 3   SUBSET - 1 dropped
after:   go.mod (2 direct + 1 indirect)  ->  3 of 3   complete
```

That fixture exercises both defects at once — its indirect dependency is on a
single-line `require` outside the block.

## Impact on existing databases

go.mod repos will report more dependencies after the next sync: previously absent
transitive rows appear as current, and any repo using single-line requires gains
dependencies it never had. Nothing is deleted.

## Follow-on

go.mod is now a complete view of its manifest, which removes one of the two
blockers on #151. The remaining one is npm, where `dev_deps` defaults to `False`
and `devDependencies` are skipped unless `-dev` is passed — so `assess()` still
cannot safely demote a whole file for package.json. See the parser survey on #151.
