# `package-lock.json` extractor

Adds the npm lockfile parser, the first of the four formats parked under
[#229](https://github.com/kospex/kospex/issues/229) ("sub-project D"). The
registry has classified `package-lock.json` since July 2026 with `scanners=()`
— named but unreadable. It is now read by both scan paths.

#229 ranked it first of the four despite `yarn.lock` being more numerous,
because it records the same install-state axes as `pnpm-lock.yaml` and in
places more. That held up.

## Files changed

| file | change |
|---|---|
| `src/kospex/extractors/npmlock.py` | new — the parser |
| `src/kospex/extractors/registry.py` | `npm-lock` gains `("sca", "osi")` and its `parse_ref`, and moves to the parser-backed block |
| `tests/test_extractors_npmlock.py` | new — 67 tests |
| `tests/test_extractor_registry.py` | `package-lock.json` is supported; `npm-lock` joins the both-scanners list |
| `tests/test_osi_outcomes.py` | moves from `unsupported` to `extracted` |
| `src/kospex_dependencies.py` | corrects a stale comment on `get_package_template` |
| `docs/dependency-flags.md` | two new sections; qualifies one existing claim |
| `docs/sitemap.xml` | `lastmod` |

No migration. The four install-state columns already exist from `0009`.

## What the format actually holds

Measured across all 31 `package-lock.json` files on disk — 14,884 install-path
entries — because the previous round of this work shipped three artifacts
repeating a remembered claim that was wrong.

Files split 25 × v3, 4 × v2, 2 × v1. The structural difference a parser must
branch on:

- **v3** — `packages:` only.
- **v2** — both `packages:` and the v1-style `dependencies:` tree, describing
  the same closure. One file here has 819 and 818. Only `packages:` is read;
  reading both would report 1637 for 715 packages.
- **v1** — `dependencies:` only, nested.

Three key shapes are *not* installed packages, and all three are excluded:

| excluded | count | why |
|---|---|---|
| root `""` entry | 29 | the project itself; would be recorded as depending on itself |
| workspace importers (`apps/desktop`, `..`) | 13 | local packages, not fetched |
| `link: true` | 13 | symlinks back at those importers |

The `link` rule is load-bearing rather than cosmetic: those 13 are **exactly**
the entries with no `version`, and `package_version` is part of the
`dependency_data` primary key, so each would have inserted a row that could
never update — only duplicate on every re-sync.

## Decisions

### `devOptional` → `is_dev=1`, `is_optional=NULL`

npm's third state, and not the union of the other two. Measured rather than
recalled: `devOptional` appears on 71 entries and **never** alongside `dev` or
`optional`, while `dev` and `optional` co-occur 555 times. So npm sets exactly
one of the three, and `devOptional` means "reachable only via a dev path *or* an
optional path" without resolving which.

It therefore sets `is_dev` — it certainly does not reach production — and leaves
`is_optional` NULL, because `false` would claim npm had confirmed the package
always installs. `declared_scope="devOptional"` keeps that distinguishable from
a plain `dev` entry, since the projection is lossy.

### An install path is not a package name

`npm i foo@npm:bar` installs **bar** at `node_modules/foo`, with the real
package in a `name` field on the entry. Taking the name from the path records a
package that is not on the registry, so the deps.dev lookup returns nothing —
the row shows no advisories and no `versions_behind` for a package that is
genuinely installed.

Not hypothetical: **30 entries** across the estate are aliases, and every entry
carrying a `name` has one that differs from its path.

```
node_modules/string-width-cjs     ->  string-width@4.2.3
node_modules/react-helmet-async   ->  @slorber/react-helmet-async@1.3.0
node_modules/react-loadable       ->  @docusaurus/react-loadable@6.0.0
```

The entry's `name` wins, falling back to the path only when the field is absent
or unusable — a lockfile is attacker-controlled input and `package_name` is part
of the primary key, so an empty or non-string `name` must not produce a nameless
row. v1 has no `name` field and encodes the same thing as
`version: "npm:left-pad@1.2.3"`, which is parsed; an `npm:` spec naming nothing
parseable is dropped rather than stored raw.

The alias is still matched when deciding `direct` / `dev`, because a project
declares an aliased dependency *by its alias*. Resolving the name without that
would have reclassified every aliased direct dependency as transitive.

**The key inventory this parser was designed from had omitted `name`** — along
with `peerDependenciesMeta` (380), `workspaces` (11) and `bundleDependencies`
(3). `docs/dependency-flags.md` is corrected. That inventory existed to stop
exactly this class of mistake, which is worth noting: an inventory is only as
good as its completeness, and a key present on 30 of 14,222 entries is easy to
miss and expensive to miss.

### An unreadable lockfile is not an empty one

**CWE-703, improper handling of an exceptional condition.** The extractors
convention is to log and return `[]`. For a lockfile parser that is wrong, and
the difference is reachable from the file being parsed.

`classify_outcome` maps an empty result to `empty`, whose documented meaning is
"examined, nothing to find" — the outcome that stops a repository being
re-queued. So anything that made the parser return `[]` reported the repository
as cleanly dependency-free:

| edit to a real lockfile | rows read | reported as |
|---|---|---|
| `lockfileVersion: 3` → `"3"` | 0 | `empty` |
| `lockfileVersion: 3` → `4` | 0 | `empty` |
| `lockfileVersion` deleted | 0 | `empty` |
| JSON truncated | 0 | `empty` |

One character, every dependency hidden, and a benign-looking result. Note this
was a *regression in signal*: before a parser existed the same file reported
`unsupported`, which is honest.

Two changes:

- **`lockfileVersion` is read liberally** — any value that unambiguously *is* an
  integer, so `"3"`, `3.0` and `3` all work. `true` does not, because `bool` is
  an `int` subclass in Python and `lockfileVersion: true` is not version 1.
- **A genuinely unreadable file raises `UnreadableLockfile`** rather than
  returning `[]`. `osi_extract` already turns an exception into a `parse_error`
  outcome; `[]` cannot express "I could not read this".

`kospex sca` called `parser(filename)` with no guard, so raising would have
crashed the command instead of reporting the file. It now catches, logs and
reports, matching what the osi path has done since #148. Every other parser
returns `[]` today, so that guard is inert for them — but the two paths no
longer disagree about what an unreadable file means, which is what #180 was for.

Genuinely empty files still read as empty: a v3 lockfile whose only `packages:`
entry is the root, and a v1 lockfile with no `dependencies` tree, both return
`[]` and report `empty`. npm always writes `packages:` for v2/v3, so its
*absence* is malformed rather than empty.

### No field of a lockfile corroborates another

**CWE-807, reliance on an untrusted input in a security decision.** This took
three attempts, and the first two were wrong in an instructive way.

Attempt one read the name from the install path. That missed real aliases.
Attempt two preferred the entry's `name`, which let a lockfile install
`evil-pkg` while claiming `name: "lodash"`. Attempt three corroborated `name`
against the `resolved` tarball URL — and that was a **conceptual error, not a
bug**: both fields come from the same untrusted document, so their agreement is
not evidence of anything. It also never validated the URL's host, so
`https://evil.example.com/lodash/-/lodash-1.0.0.tgz` corroborated a `lodash`
claim.

The pattern is the point: **any rule that elects one field as authoritative is
spoofable in whichever direction it elects.**

So no field is elected. Every identity an entry asserts — install path,
explicit `name`, and the package named in the `resolved` URL — is recorded as
its own row. They usually agree (12,687 of the 12,689 entries with a tarball
URL), in which case that is one row and nothing changes.

This is not spoofable in the direction that matters. A package cannot be hidden,
because the install path is always recorded; and a genuine alias target is
always recorded, so deps.dev is asked about what is really installed. The cost
is a row for a name that may not exist on the registry — a visible, dismissible
false positive instead of an invisible false negative. Measured: **+32 rows out
of 13,707**, taking the estate to 13,739.

It also needs no registry allowlist. Host validation was the obvious fix for the
bypass, but every `resolved` URL in the reference estate points at
`registry.npmjs.org`, and the estates kospex actually scans use Artifactory and
GitHub Packages — an allowlist would have silently degraded them.

A candidate name is still checked against npm's own rules (≤214 characters, no
leading dot or underscore, no whitespace or control characters) before it is
recorded as an identity at all, because `package_name` becomes part of a primary
key and is written into exports.

**What remains, stated plainly:** a lockfile that lies consistently across
`name`, `resolved` and `integrity` is beyond a parser. Settling it requires
fetching the tarball and hashing it, which is a different kind of tool. This
work stops at "every identity the file asserts is visible".

The mechanism is not theoretical. The only two entries in the estate whose
`resolved` URL names a different package than their install path —
`node_modules/express-exp` resolving to the `exit` tarball — are both in malware
test fixtures.

### The same rule, in the version column and the path

Having settled "no field is authoritative" for names, two places had not been
brought into line.

**`version` was still trusted alone.** Both it and the version inside the
`resolved` tarball URL are attacker-supplied, so a lockfile could declare
`version: "99.0.0"` while the URL fetched `1.2.3` — deps.dev has nothing to
answer for a version that does not exist, so a genuinely vulnerable package
reported no advisories. Both versions are now recorded. Measured: they agree on
all **12,719** entries where both exist, so a disagreement never occurs in an
honest file.

**A dependency at an unusual path disappeared.** A `packages:` key with no
`node_modules/` segment is a workspace importer — a local directory, built
rather than downloaded — and was skipped on the strength of the key alone. So
`"vendor/evil-pkg": {version, resolved, integrity}` was invisible. A real
importer is never fetched: measured, **none** of the 13 in the estate carries
both a version and a registry tarball URL, so an entry that carries both is
treated as the dependency it is. Real importers still contribute their
`dependencies` blocks, so an aliased or workspace-declared package stays
`direct`.

Neither change costs anything on real data — the estate total is identical at
13,739 rows, because no honest file triggers either rule.

### A missing key must not hide an entry

`version` is part of the primary key, so an entry without one cannot be written
— and skipping it silently meant deleting a single key removed a package from
the scan while `resolved` and `integrity` still told npm exactly what to fetch.

The version is now recovered from the tarball filename when the field is absent,
including prereleases (`1.0.0-beta.1`). Measured: all 13 version-less entries in
the estate are `link: true` workspace symlinks, which have no version to recover
and are not fetched packages, so a non-link entry without one does not occur
naturally and recovering it costs nothing. An entry with no version anywhere is
logged rather than dropped silently.

### A single key must not hide a package

Three places dropped an entry silently, each reachable by adding or editing one
key. All three now record:

- **`link: true`** — real link entries are symlinks to workspace directories and
  carry only `{link, resolved}`; all 13 in the estate have no `version`, so the
  version check already excludes them. Skipping on `link` as well meant one key
  could remove any dependency.
- **A v2 package present only in the legacy `dependencies:` tree** — npm 6
  installs from that tree while npm 7+ installs from `packages:`, so a package
  there is one a real client installs and kospex never saw. `packages:` still
  owns the flags for anything it lists, because the legacy tree cannot express
  `hasInstallScript` and merging it would flip an aggregate computed with
  `all()`. Where the two sections disagree on a version, both are reported
  rather than one being chosen silently.
- **An unparseable `npm:` alias in v1** — `version: "npm:anything"` dropped the
  entry. It is now recorded as declared. A row kospex cannot enrich is still a
  row a reader can see; a dropped row is not.

The principle behind all three: **prefer recording something visible over
recording nothing.** A gap in a dependency inventory is indistinguishable from
an absence, which is the failure this whole format matters for.

### One package, several paths: aggregate, don't overwrite

Found by writing real rows into a copy of the live database rather than trusting
the unit tests. The parser offered 2775 rows and the database stored 2458.

`packages:` keys are install paths, and the `dependency_data` primary key has no
install-path column, so the same `name@version` at two depths collapses onto one
row. Across the estate, 14,884 entries hold 13,729 distinct `name@version` —
1,155 collapse, in 479 groups.

Those entries disagree more often than is comfortable, because `dev` and
`optional` describe how a package was *reached*:

| flag | groups that disagree |
|---|---|
| `dev` | 53 |
| `optional` | 48 |
| `inBundle` | 18 |
| `devOptional` | 4 |
| `peer` | 4 |
| `hasInstallScript` | 0 |

Left to the upsert, which value survives depends on JSON key order. So the
parser reconciles them first:

- `dev` / `optional` / `devOptional` — true only when **every** path agrees. One
  production path means it ships; one required path means it installs. This is
  the rule already settled for pnpm v9 `snapshots:`, for the same reason.
- `hasInstallScript` / `peer` / `inBundle` — true when **any** path says so.
  These describe the package itself, which is consistent with
  `hasInstallScript` being the one flag no duplicate group disagrees on.

`declared_scope` is built from the aggregate, so it cannot contradict the
booleans in its own row.

The effect is not marginal. In one v1 file, 93 entries carry `optional` but only
55 packages are optional by every path — per-entry reading would mark 38
packages "may not install" when a required path installs them. `chalk@5.6.2` has
four entries, three from production paths and one from a dev tree; it ships.

### v1 reports nothing as `direct`

v1's top level is the **hoisted** install set, not the declared set, so a
transitive lifted to the root is indistinguishable from a declared dependency.
Claiming `direct` for the top level would be a guess, so every v1 row is
`transitive`. `is_dev` still carries what npm did state, at every depth.

This is the one place the parser is less informative than the file feels, and it
is a property of v1 rather than a shortcut. Two files on disk are affected.

### `peer` does not become `package_use`

`PACKAGE_USE_PEER` and `PACKAGE_USE_OPTIONAL` exist in `kospex_schema.py`, but
neither scan path's `_REQ_TO_USE` maps them, and the two disagree on the
fallback: `KospexDependencies` defaults to `direct`, `krunner` to `""`. Emitting
them would write silently wrong values down one path or the other. The flags are
preserved in `declared_scope` instead, and the mapping gap is left as it was
rather than quietly worked around.

## What this adds to the database

13,707 rows across the estate, from 7,383 distinct `name@version`, of which
~5,900 are not currently in `dependency_data` at all. A first sweep is therefore
~5,900 deps.dev lookups — roughly half an hour at the 0.33s per lookup measured
in [#226](https://github.com/kospex/kospex/issues/226). That is the same bet
[#225](https://github.com/kospex/kospex/issues/225) already took deliberately
for pnpm transitives, but it lands on serial enrichment rather than avoiding it.

Verified end to end through `classify()` and `resolve_parser()` — not by
importing the parser directly — with `requirements_type` mapped through *both*
scan paths' real `_REQ_TO_USE`. Every value maps, the two paths agree on all of
them, and no column holds a value of the wrong type for it. Rows were then
written into a throwaway copy of `kospex.db` via `save_dependencies()`, the same
function `krunner osi` calls: 2458 offered, 2458 stored.

## Out of scope, deliberately

- **Attribution** — nesting is readable for free (2,331 entries at depth 2, 47
  at depth 3) and is then discarded, because there is no column for it.
  [#227](https://github.com/kospex/kospex/issues/227) owns it across all formats.
- **`license`** — on 81% of entries and stored nowhere;
  [#239](https://github.com/kospex/kospex/issues/239).
- **`deprecated`** — npm's own notice on 68 entries, no home;
  also #239.
- **The other three formats** in #229: `yarn.lock`, `uv.lock`, `build.gradle`.
