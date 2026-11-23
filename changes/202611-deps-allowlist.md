# Look up only the packages on an allowlist

Status: design, not built.

## Overview

`KOSPEX_DEPS_PRIVATE` says which names must never be sent to a public index.
That is a deny-list: everything else goes out. Some sites need the opposite. An
organisation has looked at a manifest, knows every package in it is public, and
is willing to send those names, and only those. `-allowlist FILE` on
`kospex sca` and `krunner osi` is that vetted list: a package is looked up only
if it is named in the file.

Both lists exist for the same reason (what a lookup tells a public service
about what you build), but they answer different questions. The deny-list is
"must never be sent". The allowlist is "has been reviewed and may be sent".
They stay separate, and the deny-list always wins.

## Goals

- A reviewed file of exact packages that gates every outbound lookup.
- The file is validated in full before any scan starts, so a typo cannot
  silently allow or block the wrong thing.
- Works for every ecosystem kospex parses, not just Maven and Go.
- Lets `-enrich` work on a machine that has `KOSPEX_DEPS_ENRICH=false`,
  because a vetted list is what makes that safe.

## Non-goals

- **Prefixes and globs.** Entries are exact packages. Maven private prefixes
  stay prefixes in `KOSPEX_DEPS_PRIVATE` because of reverse-domain naming; an
  allowlist of exact names is the point.
- **Versions.** The list says which packages, not which versions.
- **Generating the file** (`kospex sca FILE -create-allow-list OUT`). Later.
- **`KOSPEX_DEPS_ALLOWLIST_FILE` and `KOSPEX_DEPS_PRIVATE_FILE`**, a maintained
  file named in `kospex.env`. Later, but the loader is built so they only add
  somewhere to read the path from.
- **kweb.** It does not take the flag. See "Process-wide state" below.

## Anti-goals

- An allowlist that looks loaded but allows nothing, or allows more than the
  file says. Every ambiguity is an error, not a guess.
- A listed package that is also private being sent. It must not be.
- Passing a file quietly turning lookups on.

## File format

One package per line. Blank lines are ignored. A line starting with `#` is a
comment; so is anything after ` #` on an entry line (a space then `#`, so a
package name or purl fragment is never cut).

Two spellings, normalised to the same key:

```
# python
pypi:requests
pkg:pypi/click

# js
npm:lodash
npm:@types/node
pkg:npm/%40types/node

# java
maven:com.google.guava:guava
pkg:maven/com.google.guava/guava

# go
go:github.com/spf13/cobra
pkg:golang/github.com/spf13/cobra
```

- **Short form:** `ecosystem:name`, split on the **first** colon, so Maven's
  `group:artifact` and npm scopes work. `gradle:` is accepted for `maven:`, as
  in `KOSPEX_DEPS_PRIVATE`.
- **purl:** `pkg:type/namespace/name`. purl types are mapped to kospex package
  types (`golang` to `go`, and so on). Percent-encoding is decoded, so `%40`
  is `@`. Maven `namespace/name` becomes kospex's `group:artifact`.
- **Ecosystems:** every type in `kospex.extractors.registry` (`package_types()`).
  An unknown ecosystem is an error that lists the supported ones.
- **Versions are rejected.** A purl with `@version`, or a short form with one,
  is an error that says so plainly, for example:
  `line 12: 'pkg:npm/lodash@4.17.21' includes a version; list the package
  without one (pkg:npm/lodash)`. The user should decide which packages to
  allow, not paste an SBOM. Qualifiers (`?x=y`) and subpaths (`#sub`) are
  rejected the same way.
- **Globs are rejected** (`*`, `?`, `[`), as in `KOSPEX_DEPS_PRIVATE`.
- **Matching** is exact on the normalised name:
  - PyPI: PEP 503 normalisation (case, and runs of `-_.` collapse to `-`).
  - npm, Maven, Go and others: case-insensitive, as the deny-list is. A wrong
    match costs one lookup the user already approved; this is the allowlist,
    so a case difference can only mean "same package, different spelling".
- **Duplicates** (after normalisation) are allowed and counted once.
- **An empty file** (no entries after comments) is an error. An allowlist that
  allows nothing is almost certainly a mistake; use `-no-enrich` for that.
- **Errors are collected**: every bad line is reported, with its line number,
  not just the first.

Code: `src/kospex/allowlist.py` (new), with `load_allowlist(path)` returning an
`Allowlist` and `Allowlist.permits(package_type, package_name)`, plus a
`parse_allowlist_text(text)` that the file loader calls, so the later
`*_FILE` settings and `-create-allow-list` reuse it.

## Behaviour

| Invocation | KOSPEX_DEPS_ENRICH | Result |
|---|---|---|
| `-enrich -allowlist F` | any | Enrich **listed** packages only |
| `-allowlist F` (no `-enrich`) | true | Same: listed only; the rest `not_allowlisted` |
| `-allowlist F` (no `-enrich`) | false | **Stays off.** One line: the allowlist is unused because lookups are off; add `-enrich` |
| `-no-enrich -allowlist F` | any | **Error**: the flags contradict each other |
| no `-allowlist` | any | Unchanged from today |

`-enrich` already overrides `KOSPEX_DEPS_ENRICH=false`; the allowlist narrows
what that override does rather than adding a second override.

**Order of checks for one package** (`KospexDependencies.lookup_permitted()`):

1. `KOSPEX_DEPS_PRIVATE` / `GOPRIVATE` match: not permitted, reason names the
   entry, row saved `private_skipped`. Wins over everything.
2. An allowlist is active and the package is not on it: not permitted, reason
   `not on the allowlist`, row saved `not_allowlisted`.
3. Otherwise permitted.

Every method that sends a name already calls `lookup_permitted()` first (the
list is in `changes/202611-deps-private-prefixes.md`), so a caller that skips
the scan-level decision still sends nothing.

`sca -package TYPE:NAME` is a lookup, so it is checked too: an unlisted
package is refused with the reason.

## Rows

- New resolution value **`not_allowlisted`** (`RESOLUTION_NOT_ALLOWLISTED` in
  `kospex_schema.py`). `resolution` is plain TEXT, so no migration, as for
  `not_checked` and `private_skipped`. Lookup columns are the same empty set.
- `KospexDependencies.unlooked_fields()` and `private_fields()` grow a sibling
  `not_allowlisted_fields()`; the scan paths pick by the reason
  `lookup_permitted()` returns.
- Web labels: `/dependencies/` and `/osi/` show **Not allowlisted**
  (`kweb2.py` label map and `templates/dependencies.html`).
- Start-of-run line from each command, as the private-prefix line does today:
  `allowlist: 142 packages from FILE (pypi 80, npm 50, maven 12)`.

## Process-wide state

`lookup_permitted()` is a static method that reads `HabitatConfig`, and it is
called from many places, including kweb's supply-chain page. A flag is
per-command, not config. The design:

- `allowlist.py` holds one process-level "active allowlist", set by the
  command with `activate(allowlist)` after it has loaded and validated the
  file, and cleared in a `finally`. Default is none, so kweb and every other
  caller behave exactly as today.
- `lookup_permitted()` consults the active allowlist after the deny-list.
- It does **not** go through `os.environ` (project rule: never write a setting
  into the environment, because an exported value outranks the user's own).

This is the one place option A costs something: a module-level holder. It is
the smallest change that gets every existing call site, including the ones in
`maven_bom_versions()` and the PyPI lookups, gated without threading a new
argument through them.

## Files changed (expected)

- `src/kospex/allowlist.py`: new.
- `src/kospex_dependencies.py`: `lookup_permitted()`, `enrich_decision()`
  (takes the allowlist into account), `not_allowlisted_fields()`,
  `unlooked_fields()`.
- `src/kospex_schema.py`: `RESOLUTION_NOT_ALLOWLISTED`.
- `src/kospex_cli.py` (`sca`) and `src/krunner.py` (`osi`, including `-next`):
  `-allowlist FILE` option, load before any work, report, activate.
- `src/kweb2.py`, `src/templates/dependencies.html`: label.
- `docs/`: configuration and the dependency docs page; `CHANGELOG.md` under
  `## Unreleased`; CLAUDE.md command list.
- Tests: `tests/test_allowlist.py` (parser table, one case per rule above,
  errors collected); precedence (private beats allowed); behaviour table;
  socket-level test that an unlisted package causes no outbound connection on
  both commands, as the enrich-setting tests do; `-next` path.

## Approaches considered

- **A. Separate module beside `private_packages.py` (chosen).** Parser and
  `Allowlist` are their own thing; `lookup_permitted()` asks the deny-list,
  then the allowlist. Keeps "must never be sent" and "has been vetted"
  distinct and the deny-list code untouched. Cost: the process-level holder
  above.
- **B. Extend `PrivatePackages` to also say "private unless listed".** Reuses
  the existing plumbing, but blends two ideas in one class, makes
  `private_skipped` and `not_allowlisted` hard to tell apart, and puts new
  logic in security-sensitive code that is already well tested.

## Decisions from review (author's calls, not yet confirmed unless marked)

Code facts the first draft got wrong or skipped, and how the design handles them.

**Refusal reason must reach the row.** `_private()` returns a bool, and
`lookup_dependency()` and the other ~14 call sites hardcode `private_fields()`.
Change `_private()` to return the refusal (kind and reason) and add
`refusal_fields(kind)`, which returns `private_fields()` or
`not_allowlisted_fields()`. The log line says "private" or "not allowlisted"
to match. `lookup_permitted()` returns `(permitted, reason)` as now; the kind is
derived from which check refused.

**Every place that branches on `resolution` handles the new value.**
`save_dependencies` needs its own branch (the `else` stamps `last_checked`, which
would falsely attest a check); also `looked_up` (~1023), `_earlier_lookups`
(~1345), `kospex_web.py` labels (~145) beside `kweb2.py` and the template, the
`krunner` start-of-run reports, and the `sca` "N packages are private" summary,
which gets an allowlist counterpart.

**Earlier lookups are kept** for a package now off the list, as for
`not_checked`. Nothing is sent, so there is no privacy reason to discard the old
data, unlike `private_skipped`.

**`enrich_decision(flag, allowlist=None)`** takes the allowlist. It raises the
`-no-enrich -allowlist` contradiction and returns the "allowlist unused" reason,
so both commands share it. Commands load and validate the file **first**, then
call `enrich_decision`, so a bad file is the first error a user sees.

**Parser details:**
- Test the `pkg:` prefix (case-insensitive) before splitting on a colon.
- A version is an `@` not at position 0 of the name (so `npm:@types/node` is fine,
  `npm:foo@1.0` is rejected).
- Short-form Maven must be exactly `group:artifact`; `maven:g:a:jar` is an error,
  as is `pkg:maven/guava` (no namespace).
- Go keeps a `/v2` suffix as part of the name. `%2F` and other percent-escapes
  are decoded before matching.
- purl type table: `golang` to `go`, `gem` to `rubygems`; `cargo`, `maven`,
  `npm`, `nuget`, `pypi` as is. Short forms use the registry names
  (`rubygems:`, `go:`) plus `gradle:`. Ecosystem and purl-type matching is
  case-insensitive. The "supported" list in the unknown-ecosystem error is
  `package_types()`.
- Normalisation is **case-folding only**, except PyPI (PEP 503). Treating `-`
  and `_` as equal for Cargo would allow a name the user did not list, and the
  allowlist errs strict.
- File reading: UTF-8 with optional BOM, CRLF accepted, tab or space before an
  inline `#`, surrounding whitespace stripped, inner whitespace an error. A
  missing path, a directory, or a non-UTF-8 file is a clean error, not a
  traceback. Non-`pkg:` URL schemes, and an empty type or name, are errors.
- The report counts unique keys, and warns for each entry that is also private
  ("on the allowlist but private: never looked up"). Punctuation follows
  `PrivatePackages.summary()` (ends in a period).

**Real-file check is required, not optional.** Before merge, run the parser over
the names stored in `dependency_data` and report how many do not round-trip
through normalisation. Parsers may hand `lookup_permitted()` names carrying
extras (`requests[security]`), npm aliases or Maven `${property}` placeholders,
and a name that cannot be normalised would be refused with no diagnostic.

**State and flow:**
- The active allowlist is set through a context manager, not a bare
  activate/clear, so every exit path (`-next`, early `sys.exit`, `-all`,
  normal) clears it. It wraps the whole command body. It is a plain module
  global: written once before work starts, read-only after, so it is safe for
  any threaded lookup.
- `sca -package` with `-allowlist` is allowed: the file is loaded, summarised and
  checked, and an unlisted package exits 1 with "not on the allowlist". The
  existing `-package` conflict list gets no new entry.

**Transitive and BOM lookups: strict (author's decision).** The allowlist gates
every outbound name, so an unlisted transitive dependency or imported Maven BOM
is skipped, not followed from its parent. The end-of-run report says how many
were skipped and by which rule (`not on the allowlist`). The graph node flag
`node["private"]` is renamed to carry the refusal kind. Anyone who wants
transitives lists them. Possible later features, out of scope now:
extracting transitives (and BOM contents) into a generated list, or an
`-include-transitive` switch.

## Left to the builder

- The exact purl-type to kospex-package-type table (use `registry` as the
  source; check every type it knows, including NuGet's case, which has
  already differed between code paths).
- Wording of the error and report lines, following the existing
  `KOSPEX_DEPS_PRIVATE` messages.
- Whether `krunner osi -next`, which runs several batches, loads the file once
  per invocation (expected) or per batch.
- A real-file check: run the parser over the purls and short forms emitted for
  every manifest in the dev DB before settling the normalisation rules, as was
  done for the extractor gates.
