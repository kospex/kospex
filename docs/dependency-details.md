# Dependency file details

Per-format notes on **how kospex parses each dependency file, when it refuses to, and
why**. One section per file type the registry knows about, including the types nothing
parses yet.

This page is deliberately about *parsing mechanics and decisions*. Two sibling pages
cover different ground:

- [Dependency flags by package manager](dependency-flags.md) — what each format
  *states* (dev / optional / install-script / attribution), quoted from its own docs.
- [Data schemas](data-schemas.md) — the columns those values land in.

It is also the input for a **periodic format-drift check**: package managers change
their lockfile formats, and kospex finds out either because a parser refuses a file or,
worse, because it quietly reads less than the file holds. Every section therefore ends
with a *drift watch* — the specific thing to look at upstream.

Measurements quoted as "on a development estate" were taken 2026-10-17 against ~374
synced repositories unless stated otherwise.

---

## Using this page for the format check

Suggested monthly pass. It is three questions per format, and the answers live in the
section for that format.

1. **Has the format version we read been superseded?** Compare *versions read* below
   against the upstream doc. A new version is the dangerous case, because the file is
   still valid YAML/JSON/XML and still has the name we match on.
2. **Has a field we populate changed meaning, or has a new one appeared that we
   should?** The flags page is the detailed record; this page notes where a parser
   deliberately ignores something.
3. **Did anything we refuse become legitimate?** Every raise below is a judgement that
   a shape is not a real file of that type. If upstream starts emitting that shape,
   the raise becomes a false positive and turns working scans into `parse_error`.

### Format versions read, and what is on the estate

| file | versions the parser reads | seen on the estate (count of files) |
|---|---|---|
| `package-lock.json` | `lockfileVersion` 1, 2, 3 | 3 ×98, 2 ×13, 1 ×9 |
| `npm-shrinkwrap.json` | as `package-lock.json` (same parser) | 1 file, a test fixture |
| `pnpm-lock.yaml` | `lockfileVersion` major 5, 6, 9 | 9.0 ×25 (**6 of them two-document**), 6.0 ×2, 5.3 ×1, one 0-byte file |
| `go.mod` | no version field; grammar is stable | 188 files |
| `*.csproj` | SDK-style `PackageReference` | 9 files |
| `requirements.txt` | PEP 508 per line | 43 files |
| `pyproject.toml` | PEP 621 `project.dependencies` / `optional-dependencies` | 131 files |
| `package.json` | the five dependency sections | 11,486 files (mostly vendored copies) |
| `yarn.lock` | v1, Berry `__metadata.version` 6, 8, 10 | v1 ×116, Berry `__metadata.version` 10 ×39, 8 ×4, 6 ×1 |
| `uv.lock` | `version` 1, any `revision` (1–3 measured) | `version=1` ×24 — `revision` 3 ×19, 2 ×4, 1 ×1 |
| `build.gradle(.kts)` | Groovy and Kotlin DSL, read not evaluated | 1,291 files on disk |
| `pom.xml` | POM model 4.0.0 | 14 files, all `modelVersion` 4.0.0 |

The *versions seen* column comes from reading the working copies directly, with
`node_modules` excluded; the *estate* file counts quoted in each section below come
from `file_metadata`. The two differ a little, because the database holds only
repositories that have been synced — so treat the version tally as "what shapes exist"
and the per-format counts as "what kospex currently knows about".

**A package manager's own version number is not its format version.** pnpm 12.x writes
`lockfileVersion: '9.0'`, so "pnpm 12 is out" is not by itself a reason to expect a
parser change — and conversely, a `lockfileVersion` bump can arrive in a minor release.
Check the format field, not the release notes.

---

## The error contract

Every parser in kospex obeys one rule, and it is the single most important thing on
this page. Established in #252; `package-lock.json` got there first in #243.

| the file is | the parser must |
|---|---|
| **unreadable** — missing, a directory, bad encoding, invalid YAML/JSON/XML/TOML | raise `UnreadableManifest` |
| **read but uninterpretable** — unknown format version, wrong root shape, a declared section holding something unusable, refused by a security guard | raise `UnreadableManifest` |
| **read, well-formed, and genuinely declares nothing** | return `[]` |

### Why raising is the only option

`kospex.osi_outcomes.classify_outcome` reaches `PARSE_ERROR` *solely* from its
`error is not None` branch, and otherwise ends:

```python
return EXTRACTED if row_count else EMPTY
```

So **raising is the only error channel an extractor has.** A parser that catches its
own exception and returns `[]` has its file recorded as `empty`, whose documented
meaning is "parser ran, produced nothing — genuinely declares none". "I could not read
this file" gets stored as "I read this file and it declares no dependencies", and a
repository holding an unreadable manifest reports clean.

That is CWE-392 (missing report of an error condition), on input kospex does not
control — manifests come from cloned third-party repositories. It is also a regression
in signal as coverage grows: an unparsed file type reports `unsupported`, which is
honest, and *gaining* a parser could downgrade that to `empty`, which is not.

Measured before it was fixed: changing `lockfileVersion: 3` to `"3"` in a real
`package-lock.json`, bumping it to `4`, deleting the field, or truncating the JSON each
read **0 of 2** real packages and reported the repository as cleanly `empty`.

`UnreadableManifest` lives in `src/kospex/extractors/errors.py`.
`npmlock.UnreadableLockfile` is a subclass, so one `except` covers every extractor.

### An empty file is unreadable, not empty

A 0-byte `pnpm-lock.yaml` raises `no YAML document, so not a lockfile`. There is one on
the estate — a test fixture in kospex's own `panopticas` repo at
`src/tests/pnpm-lock.yaml` — and it is a good illustration: nothing was declared,
because nothing was readable. `[]` is reserved for a file that *says* "no
dependencies", which is a different claim.

### Both scan paths

`osi_extract` wraps the parser and turns an exception into a `parse_error` outcome for
that one file. `KospexDependencies.assess` called `parser(filename)` **unguarded**
until the contract landed, so raising crashed `kospex sca`. A parser contract only one
caller honours is not a contract — when adding a parser, check both.

### What a raise does not change

Queue behaviour is unchanged. `parse_error` already sorts first in `OUTCOME_PRIORITY`
(`['parse_error', 'unclassified', 'unsupported', 'extracted', 'empty',
'not_a_manifest']`), so a repository with one unreadable file reports `parse_error` at
the headline — the same as a repository where *every* file failed. The per-file map is
stored in full, so the information to tell those apart exists; only the derived
headline collapses it.

### Adding a structural gate

A "this is not really a file of that type" gate is a raise, and a raise on a real file
is a scan that used to work and now does not. The rule applied here: **measure the gate
against every real file of that format first.** `go.mod` must declare `module`
(188/188 do) and a `.csproj` root must be `<Project>` (9/9) ship as raises for that
reason. An unsplittable pnpm `packages` key does *not* raise, because a git or tarball
key fails to split the same way and no lockfile on the estate carries one to measure
against — it logs instead.

A gate can also *create* the hole it closes: adding the `<Project>` root check without
making `PackageReference` lookup namespace-agnostic would have made a legal `xmlns`
project pass the gate and then match no packages.

---

# Supported formats

Nine file types have a parser, and all nine are claimed by both scan paths
(`scanners=("sca", "osi")`).

## `requirements.txt` — pip

| | |
|---|---|
| registry entry | `pypi-requirements`, `Kind.PACKAGE`, `package_type="pypi"` |
| parser | `KospexDependencies.parse_pip_requirements_file` |
| matcher | delegated to `panopticas.is_pip_requirements` |

Recognition is delegated to panopticas rather than matched locally, because the local
pattern was wrong in both directions: it missed multi-hyphen names like
`requirements-wheel-test.txt`, and a substring check treated `requirements.py`,
`.rst` and `.lock` as manifests.

**Parsing is delegated to `packaging.requirements.Requirement` (PEP 508)**, not to
substring matching on operators (#139). The old approach tested operators against the
whole line *including the environment marker*, so `requests; sys_platform == 'win32'`
split on the marker's `==` and produced a package named `"requests; sys_platform "`.

### Decisions and quirks

- **An unversioned declaration is a valid requirement** and is recorded with an empty
  `package_version` (#139). It used to return `None`, which the caller discards as
  falsy, so unpinned packages never reached the database — one real repo declaring ten
  packages, nine unpinned, produced a single row.
- **The declared name is kept as written** (`MarkupSafe`, not `markupsafe`).
  `packaging` is used to parse, never to rename, because `package_name` is part of the
  `dependency_data` primary key and rewriting it inserts duplicate rows on re-sync
  instead of updating.
- **A multi-specifier line keeps the author's text verbatim.** `str(SpecifierSet)`
  sorts its members, turning `>=1.0,<2.0` into `<2.0,>=1.0`, and the ordering is
  information. 15 lines on the estate are written `>=X, <Y`.
- **A single specifier has internal whitespace removed**, so `tox ~= 4.4` and
  `tox~=4.4` store one value. This goes the opposite way to the rule above for the
  same reason — a primary-key column must not hold one constraint under two
  identities. PEP 440 forbids whitespace inside a version token, so nothing is lost.
- **BOM handling is `"utf-16"`, not `"utf-16-le"`/`"utf-16-be"`** (#248). The suffixed codecs
  do not consume the BOM, so it survived as a leading U+FEFF and the first declaration
  became `"﻿requests==2.31.0"` — not a valid PEP 508 requirement, so it was
  dropped. A function that exists to handle BOMs had two of its three cases wrong.
- A URL, `-e .` or `-r other.txt` line returns `None` — "not a package declaration",
  not "no version". `pypi_assess` falls back to source-repo URL extraction for those.

**Upstream docs:** [pip requirements file format](https://pip.pypa.io/en/stable/reference/requirements-file-format/)
· [PEP 508](https://peps.python.org/pep-0508/) · [PEP 440](https://peps.python.org/pep-0440/)

**Drift watch:** new PEP 508 syntax, and pip's own additions to the file format
(`--hash`, nested `-r`, `--index-url`) which are currently ignored rather than
recorded.

## `pyproject.toml` — PEP 621

| | |
|---|---|
| registry entry | `pyproject`, `Kind.PACKAGE`, `package_type="pypi"` |
| parser | `KospexDependencies.parse_pyproject_file` |

### Raises

- invalid TOML (`tomllib.TOMLDecodeError`)
- unreadable file (`OSError`)
- `project.dependencies` or an `optional-dependencies` group that is **not an array**
  (`_require_array`)

### Returns `[]`

A valid TOML file with no `project.dependencies` and no `optional-dependencies` —
common for a pyproject that only configures tools.

### Decisions and quirks

- **This reverses the #97 fix** (made in #98, reversed in #252). The parser used to log and return `[]` "so batch
  callers can continue processing other files". Both scan paths now wrap the parser in
  a broad per-file guard, so the batch no longer needs the parser to swallow anything
  — and swallowing recorded the file as `empty`. One real `pyproject.toml` of 131 on
  the estate does not parse (an unclosed array) and was being reported that way.
- **An unvalidated array was worse than a fail-open: it fabricated data** (#248).
  `dependencies = "requests"` was iterated character by character into eight rows named
  `r, e, q, u, e, s, t, s` — and `e`, `q`, `u`, `s`, `t` and `p` all exist as real
  PyPI packages, so a malformed manifest made kospex report a repo as depending on
  packages it has nothing to do with, with their genuine advisories attached. **Check
  for invented data, not only missing data.**
- **The extras group name is kept verbatim in `declared_scope`** and is *not* promoted
  to `requirements_type="dev"` when the group is called `dev`. PEP 621 extras mean
  "installed only if requested", which is not npm's `devDependencies`, and `check` /
  `cover` / `type` would be guesses. 129 distinct group names across the estate used to
  collapse to one value.
- An individual malformed requirement string inside a valid array is logged and
  skipped, not raised — the file was readable and the rest of it is good data.
- **A leading UTF-8 BOM raises, and the message names it.** pip cannot read such a
  file either, so refusing it is honest — but `tomllib`'s own message is "Invalid
  statement (at line 1, column 1)", which points at a line that is correct. See
  [Byte order marks](#byte-order-marks).

**Upstream docs:** [pyproject.toml specification](https://packaging.python.org/en/latest/specifications/pyproject-toml/)
· [PEP 621](https://peps.python.org/pep-0621/)

**Drift watch:** PEP 735 dependency groups (`[dependency-groups]`) are **not read** —
they are neither `project.dependencies` nor `optional-dependencies`, and tooling is
adopting them. Also `project.dynamic`, where dependencies are declared to come from
somewhere else entirely.

## `package.json` — npm

| | |
|---|---|
| registry entry | `npm-packagejson`, `Kind.PACKAGE`, `package_type="npm"` |
| parser | `KospexDependencies.parse_package_json` |

### Raises

- invalid JSON (no `try`/`except` around the load, deliberately)
- a document that is **not a JSON object** — `ValueError`
- a dependency section that is not an object — `.items()` raising is the point

### Decisions and quirks

- **The "not an object" check exists because valid JSON can still be the wrong shape.**
  A top-level array or string returned no records at all — `"dependencies" in ["x"]` is
  a legitimate membership test that answers `False` — while a number raised
  `TypeError`. npm rejects such a file outright, so reporting it as a repository with
  no npm dependencies was wrong in the direction that looks like good news.
- **No `isinstance(...): continue` guard on sections, deliberately** (#248). Adding one reads
  as defensive hygiene and converts this parser from fail-closed to fail-open:
  `{"dependencies": ["lodash"]}` would go from raising to reporting zero dependencies,
  and a file with every section malformed would report `empty`. Where exceptions are
  the error channel, swallowing bad input is not defence, it is silence. Pinned by
  `tests/test_declared_scope_manifests.py::TestMalformedSectionStillFailsClosed`.
- The npm section name is kept verbatim in `declared_scope` alongside the mapped
  `requirements_type`, so a mapping that turns out wrong can be redone from the
  database instead of a re-scan.
- **Read with `utf-8-sig`, because npm reads a BOM'd `package.json`** and refusing one
  reported a working repository as broken. See [Byte order marks](#byte-order-marks).
- **An `npm:` alias is stored as written and looked up as its target** (#350).
  `"react-17": "npm:react@^17"` becomes a row named `react-17` at `npm:react@^17`.
  Those two values are part of the `dependency_data` key, and they are what the
  project imports and declares. The parser does not unwrap the alias.
  `KospexDependencies.lookup_dependency` does that, using
  `kospex.extractors.npmlock.npm_alias_target`, and then applies the usual range
  handling to `^17`. Before this, the alias name was looked up as a package: most
  aliases returned `package_not_found`, an alias named like an unrelated package
  returned `version_yanked`, and a fork installed under the original's name was
  checked as the original. `npm:classnames@*` unwraps to `classnames` at `*`, which
  names no version, so it stays `unresolved_spec`. It is the one alias on the estate
  that doesn't resolve. A spec the helper can't read, such as `npm:react` with no
  version, is looked up as before.

**Upstream docs:** [package.json](https://docs.npmjs.com/cli/v11/configuring-npm/package-json/)
· [npm install `<alias>@npm:<name>`](https://docs.npmjs.com/cli/v11/commands/npm-install)

**Drift watch:** new dependency sections. The parser maps a fixed list
(`NPM_SECTION_TO_REQ_TYPE`); a section npm adds is silently not read, which is the one
fail-open shape this parser still has.

## `package-lock.json` — npm

| | |
|---|---|
| registry entry | `npm-lock`, `Kind.PACKAGE`, `package_type="npm"` |
| parser | `kospex.extractors.npmlock:extract_package_lock` |
| landed | #243 (all decisions in this section) |

Three structurally distinct formats:

- **v1** — no `packages:` section; a nested `dependencies` tree where each entry's
  transitives are the `dependencies` map inside it.
- **v2** — carries **both** `packages:` and the v1-style tree for compatibility. They
  describe the same closure, so only `packages:` is read.
- **v3** — `packages:` only.

### Raises

- invalid JSON, missing file, unreadable file
- a document that is not an object
- an **unknown or ambiguous `lockfileVersion`**
- a v2/v3 file with no `packages` section

### Decisions and quirks

- **`lockfileVersion` is coerced, not type-checked.** `"3"` (a string) must read as 3,
  because insisting on the `int` type failed open on a one-character edit. `float` is
  accepted when integral. **`bool` is checked first**, because it is an `int` subclass
  in Python and `lockfileVersion: true` is not version 1.
- **`packages:` keys are install *paths*, not names**
  (`node_modules/findup-sync/node_modules/glob`), which is what makes nesting visible
  without walking a graph. One package can appear at several depths with **different
  flags**, while `dependency_data`'s primary key has no install-path column — so those
  entries are reconciled into one record rather than overwriting each other
  (`_aggregate_flags`). Without that, 317 of 2,775 rows silently vanished and the
  surviving flags were decided by JSON key order. The paths themselves are then
  discarded; storing the parent is #227.
- **Record every identity the file asserts; elect none as authoritative.** An entry can
  assert a name in up to three places — install path, explicit `name`, and the package
  inside the `resolved` tarball URL — and they can disagree. All are recorded, because
  any rule electing one field as authoritative is spoofable in whichever direction it
  elects, and a manifest is one untrusted document so cross-field corroboration is not
  a security control. Measured cost: **10 extra rows in 13,739**, all ten real
  published packages, because the second identity is the alias *target* — the thing
  deps.dev can answer about. The install-path row is still looked up by its own
  name, so `string-width-cjs@4.2.3` is a false `version_yanked`. Unlike
  `package.json` (#350), it is not unwrapped, because nothing in the lockfile vouches
  for the alias. Checking it against the sibling `package.json` is the planned fix
  (#405, `changes/202611-npm-alias-lookup.md`).
- **"Was this fetched at all?", not "do I recognise this registry."** An entry at an
  unusual path (`vendor/evil-pkg`) is treated as a dependency if it carries both a
  version and a `resolved` URL *with a scheme*. A gate asking whether the URL looked
  like a public-npm tarball path shipped broken for private registries: Artifactory
  (`/api/npm/npm-remote/lodash/-/lodash-4.17.21.tgz`) and Nexus
  (`/repository/npm-group/...`) do not match that shape, so it closed the hole for
  public-registry estates and left it open for the enterprise ones kospex exists to
  scan. Note `npm.pkg.github.com` *does* match the public shape, so a single
  spot-check would have hidden the bug.
- **Resist a host allowlist for `resolved`.** Every URL on the development estate is
  `registry.npmjs.org`; real estates use Artifactory and GitHub Packages, and an
  allowlist degrades them silently.
- Integrity/checksum hashes are dropped — SCA does not consume them. Producing
  checksum-bearing SBOMs is the out-of-scope "generator" category.
- **Read with `utf-8-sig`**, as for `package.json`: npm resolves from a BOM'd lockfile.
  See [Byte order marks](#byte-order-marks).

**Upstream docs:** [package-lock.json](https://docs.npmjs.com/cli/v11/configuring-npm/package-lock-json/)

**Drift watch:** `lockfileVersion` 4. The parser raises on it rather than guessing,
which is correct but means every v4 lockfile on an estate becomes `parse_error` the day
npm ships it — that is the signal to write the v4 branch, not a bug.

## `pnpm-lock.yaml` — pnpm

| | |
|---|---|
| registry entry | `pnpm-lock`, `Kind.PACKAGE`, `package_type="npm"` |
| parser | `kospex.extractors.pnpm:extract_pnpm_lock` |
| changed by | #228 (sca transitives), #231 (install state), #252 (error contract, multi-document) |

pnpm publishes a per-version spec at [github.com/pnpm/spec](https://github.com/pnpm/spec)
(`lockfile/{5,5.2,6.0,9.0}.md`). Three structurally distinct families:

- **v5.x** — `packages:` keyed `/name/version` (path style; scoped
  `/@scope/name/version`); peer suffix `_peer@x`.
- **v6.0** — `packages:` keyed `/name@version`; peer suffix `(peer@x)`.
- **v9.0** — `packages:` keyed `name@version` (no leading `/`, YAML-quoted when
  scoped); the resolved graph moved to a separate `snapshots:` section.

### A lockfile can be a multi-document YAML stream

This is the quirk most likely to bite a new parser, and it is **documented, intentional
pnpm output** — not corruption. Per pnpm's [lockfile docs](https://pnpm.io/lockfile):

- the **env document** is written first and only when present, carrying
  `configDependencies` and `packageManagerDependencies` (the resolved pnpm version)
  plus the `packages` and `snapshots` they need;
- the **project document** is written last and is always present.

The env document appears when a project has config dependencies, or when pnpm records
a resolved version — which happens with a pnpm 12+ `packageManager` pin or
`devEngines.packageManager`.

`yaml.safe_load` accepts exactly one document and raises `ComposerError: expected a
single document in the stream`, so **the whole file produced nothing** — and before the
error contract landed, that nothing was reported as `empty`. On the development estate
**six lockfiles across three repositories held 7,337 package entries between them,
every one invisible**; the largest is a 3,652-entry closure for one repository.

Three further points, each from pnpm's own docs:

- Nothing in the file announces the shape. `lockfileVersion` "describes the schema of
  the entries inside a document, not how many documents the file contains", and both
  documents declare the same value.
- **Do not merge the documents.** Both use the `.` importer key and would overwrite
  each other. kospex extracts **per document**, because classification is a
  per-document fact: pooling the stream's importers resolves a direct dependency of
  document 2 against document 1's importers, which do not mention it.
- pnpm's guidance by tool category: dependency-graph builders and update bots may read
  the last document; **vulnerability scanners and SBOM generators must read every
  document**, since config dependencies appear only in the env document. kospex is the
  second category.

Every document is validated before any is extracted, so a stream whose second document
is unusable cannot return the first one's packages as though the file had been read in
full. A trailing `---` yields a `None` document, which declares nothing and is dropped
rather than rejected.

### Raises

- invalid YAML, missing file, unreadable file
- a stream with no usable document (including a 0-byte file)
- a document that is not a mapping
- an **unrecognised `lockfileVersion` major** — explicitly including v10+, because
  staying quiet there reports a whole closure as `empty`
- `packages` present but not a mapping

### Returns `[]`

`"packages" not in doc`. pnpm omits the section entirely for a project that resolves
nothing (`importers: {.: {}}`), so its *absence* is a fact about the project. A key
that is present and unusable is a different thing and raises.

### Decisions and quirks

- **One silent skip remains**, and it is deliberate: a `packages` key that does not
  split into name and version. For a local workspace dependency that is right — there
  is no registry identity to record — and measured, 3 of 23,769 real entries, all
  `file:../<dir>`. A known non-registry prefix (`file:`, `link:`, `workspace:`) is
  skipped quietly; anything else is **logged**, so the case leaves a trace. It does not
  raise because a git or tarball key (`github.com/owner/repo/<sha>`) also fails to
  split and no lockfile on the estate carries one to measure against.
- **v9 install state comes from `snapshots:`, not `packages:`.** In v9, `packages:`
  carries resolution / engines / os / cpu / peerDependencies and nothing about install
  state. Only `optional` is recorded, and a package counts as optional **only if every
  path to it is optional** — one required path means it installs. On one real v9
  lockfile `all()` and `any()` differ by a single package of 569; the rule is chosen
  for being right, not for the count.
- Snapshot keys carry the same `(peer@x)` suffixes as `packages:` keys and are
  normalised with the same splitter. Matching on the raw key instead missed 5 of 209
  optional packages on one real lockfile.
- `requiresBuild` (v6) is **not** npm's `hasInstallScript` — see the flags page.
- Integrity hashes dropped, as for npm.

**Upstream docs:** [pnpm lockfile](https://pnpm.io/lockfile) ·
[pnpm/spec](https://github.com/pnpm/spec)

**Security note:** the env document is a supply-chain surface in its own right.
[CVE-2026-55698](https://app.opencve.io/cve/CVE-2026-55698) — pnpm below 10.34.2, and
11.0.0 up to 11.5.3 — skipped fresh package-manager resolution when the committed env
lockfile had matching `pnpm` and `@pnpm/exe` versions, so a crafted lockfile could
select the bytes pnpm then installed and ran (CWE-345/494/502/829). Recording what the
env document pins is therefore useful, not noise.

**Drift watch:** a `lockfileVersion` 10 (the parser will raise, loudly and on purpose);
and whether pnpm starts writing a third document. Open issues: #253 (env-document rows
carry no marker distinguishing pnpm's own toolchain from the project's dependencies)
and #254 (`packageManagerDependencies` and `configDependencies` are importer
declarations the parser does not read, so a pinned pnpm is classified `resolved` rather
than `direct`).

## `go.mod` — Go modules

| | |
|---|---|
| registry entry | `go-mod`, `Kind.PACKAGE`, `package_type="go"` |
| parser | `kospex.extractors.gomod:extract_gomod` |
| changed by | #179 (single-line `require`, `// indirect`), #252 (error contract, `module` gate) |

Both declaration forms are read — grouped in a `require ( ... )` block, and one per
line as `require <module> <version>`.

### Raises

- missing or unreadable file
- **no `module` directive** — see below

### Returns `[]`

A valid `go.mod` that requires nothing. This is the normal shape of a small Go library:
**42 of 188** `go.mod` files on the estate declare no `require` at all, the smallest of
them 27 bytes.

### Decisions and quirks

- **The `module` gate exists because `go.mod` otherwise has no structural check at
  all.** Every line the parser does not understand is skipped, so arbitrary text under
  the name would read as a module that requires nothing. `module` is required by the
  go.mod grammar; all 188 files on the estate declare it, so nothing real is
  reclassified. The block form `module (` satisfies the same prefix check.
- **`// indirect` modules are recorded** with `requirements_type="indirect"`, which the
  scanners map to `PACKAGE_USE_TRANSITIVE`. Dropping them meant transitive Go
  dependencies never reached the database at all.
- **`exclude` / `replace` / `retract` are deliberately not read** — they do not declare
  a dependency. Matching the `require` keyword rather than parsing every non-block line
  is what keeps them out.
- A bare comment inside a `require` block would otherwise be recorded as a module named
  `//`.
- The raise lives in `parse_gomod`, not only in `extract_gomod`, because
  `KospexDependencies.parse_go_mod_from_file()` is public and delegates there. Two
  parsers agreeing today is not the same as two parsers staying in agreement.
- **A leading UTF-8 BOM raises, checked *before* the `module` gate** so the message
  names the BOM rather than claiming the file has no `module` directive. Go refuses
  the file too. See [Byte order marks](#byte-order-marks).

**Upstream docs:** [Go modules reference](https://go.dev/ref/mod) ·
[go.mod file reference](https://go.dev/doc/modules/gomod-ref)

**Drift watch:** the `toolchain` and `godebug` directives, and workspace `go.work`
files, none of which are read. `go.work` is not in the registry at all.

## `*.csproj` — NuGet

| | |
|---|---|
| registry entry | `nuget-csproj`, `Kind.PACKAGE`, `package_type="nuget"` |
| parser | `kospex.extractors.nuget:extract_csproj` |
| changed by | #252 (error contract, DOCTYPE refusal, `<Project>` gate, namespace-agnostic lookup) |

One record per `<PackageReference>`. This parsing previously lived inside
`nuget_assess()`, which built the records, printed them as a table and then fell off
the end without returning them — so NuGet dependencies were never persisted by any path
(#107).

### Raises

- missing or unreadable file
- not well-formed XML
- **a `<!DOCTYPE` declaration** — refused, see below
- **a root element that is not `<Project>`**

### Returns `[]`

A readable project file that declares no package — **4 of 9** `.csproj` files on the
estate are legitimately in that state.

### Decisions and quirks

- **The DOCTYPE refusal is a security guard (CWE-776, uncontrolled entity expansion).**
  ElementTree refuses *external* entities but does expand internal ones: four nested
  levels reach 10,000 characters, nine or ten reach gigabytes and exhaust memory
  mid-scan. Internal entities must be declared in a DTD, and no legitimate MSBuild
  project carries a DOCTYPE, so refusing it removes the class outright.
- **The check sits before parsing, and outside the read's `try`.** Filtering afterwards
  would mean the expansion already ran — and inside the `try`, a broad `except` would
  have swallowed the refusal it raises. A refusal is not a reading: returning `[]` here
  recorded the guard's own outcome as a project with no NuGet packages, so a hostile
  file was refused correctly and then reported as a clean scan.
- **`PackageReference` is matched ignoring any XML namespace.** SDK-style project files
  — the only kind that uses `PackageReference` — omit `xmlns`, and none on the estate
  carries one, but a namespace-qualified document is legal and a namespace-blind
  `findall(".//PackageReference")` would find nothing in it and report the project as
  declaring nothing. Adding the `<Project>` root gate without this would have opened a
  *fresh* silent-empty path.
- **A `PackageReference` with no `Version` is still a declared dependency** and is
  recorded with an empty version. Central Package Management moves the version to
  `Directory.Packages.props`, which kospex does not read.
- An `Update=` or `Remove=` reference adjusts an existing entry rather than declaring
  one, and is skipped.

**Upstream docs:** [PackageReference in project files](https://learn.microsoft.com/en-us/nuget/consume-packages/package-references-in-project-files)
· [Central Package Management](https://learn.microsoft.com/en-us/nuget/consume-packages/central-package-management)

**Drift watch:** `Directory.Packages.props` (versions kospex cannot see), and
`.vbproj` / `.fsproj`, which use the same `PackageReference` element and are **not
matched** by the registry's `.*\.csproj` pattern.

## `uv.lock` — uv

| | |
|---|---|
| registry entry | `uv-lock`, `Kind.PACKAGE`, `package_type="pypi"` |
| parser | `kospex.extractors.uvlock:extract_uv_lock` |
| estate | 24 files across 23 repositories, 2,434 `[[package]]` entries → 2,406 rows |

TOML: a `version` / `revision` header, then a `[[package]]` array. Every package names
its `source` and lists its edges in three places — `dependencies` (always installed),
`optional-dependencies.<extra>` and `dev-dependencies.<group>`. The project itself and
each workspace member are `[[package]]` entries too.

**Every gate below was checked against uv itself** (0.11.11, `uv export --frozen`), not
inferred from the docs — that is how the `revision` decision came out the opposite way
from `package-lock.json`'s `lockfileVersion`.

### Raises

- missing file, unreadable file, a directory
- not UTF-8 (including UTF-16 — uv: "stream did not contain valid UTF-8")
- invalid TOML, including a truncated file
- **no `version`** — including a 0-byte file, which uv also refuses
- **`version` other than the integer 1.** uv: *"unsupported schema version (v2, but
  only v1 is supported)"*. A string `"1"` or `true` is refused too, because uv's own
  TOML typing refuses them — so unlike `package-lock.json` this is not coerced, and
  refusing is the fail-closed direction.
- `revision` present but not a non-negative integer (uv refuses `revision = "x"`)
- `package` that is not an array of tables; an entry with no `name` or no `source`;
  a `version` that is not a string; a dependency list that is not a list, an extras or
  groups section that is not a table, or an edge with no `name`

### Returns `[]`

A lockfile with no `[[package]]` at all, or whose only entries are first-party — the
project and its workspace members, locking nothing else. uv reads both.

### Decisions and quirks

- **The project and its workspace members are not dependencies**, so they produce no
  record. They are the entries with `source = { editable = ... }` or
  `{ virtual = ... }` — 28 of 2,434 on the estate. This is the same class of problem
  as the pnpm `.` importer (#253, #254): reporting them would attribute a
  repository's own packages to it as supply chain. Agreeing with pnpm, which produces
  no record for a local workspace directory.
- That also disposes of the version question: **all 13 entries without a `version`
  are first-party** (12 editable, 1 virtual). A third-party entry without one is
  logged and skipped, as `package-lock.json` does, because `package_version` is in the
  primary key and a version-less row could never update. None exists on the estate.
- **`revision` is not a gate.** It is uv's compatible bump within `version`, and uv
  reads `revision = 99` normally, as well as a file with no `revision` at all.
  Refusing an unknown revision — the `package-lock.json` precedent — would turn every
  lockfile from the next uv release into a `parse_error` that uv itself does not
  have. A revision above 3 is logged so the drift is visible.
- **A git source is recorded.** Two entries on the estate come from git. They are
  third-party and assert a name and version, and dropping them would hide a
  dependency. What deps.dev then says depends on the name, **not on the source**:
  `pydantic-docs` is unpublished and lands as `package_not_found`, but a git *fork* of
  `hypercorn` pinned at `0.15.0` resolves against **PyPI's** `hypercorn 0.15.0` — so
  its advisory data describes the upstream release, not the fork's code. Nothing
  records the source today; read advisories on a git-sourced row with that in mind.
  Local `directory` / `path` / URL sources are treated the same way — none exists on
  the estate.
- **Install state comes from walking the graph**, not from per-entry flags — uv.lock
  has none. Every first-party entry is a root. A package reached through any
  `dependencies` chain is runtime (`is_dev = 0`, `is_optional = 0`); one reached only
  through an extra is `is_optional = 1`; only through a dependency group,
  `is_dev = 1`; through both but never at runtime, npm's `devOptional` state —
  `is_dev = 1`, `is_optional` NULL. A package on no path at all is NULL throughout
  (none on the estate: every locked package is reachable).
- **An edge requesting extras is followed.** `{ name = "requests", extra = ["socks"] }`
  installs `requests`' `socks` extra too, under the same label — an extra that a
  runtime dependency asks for installs at runtime.
- **`declared_scope` names the extra or group**, e.g. `dev-dependencies:docs` or
  `optional-dependencies:socks`, comma-joined when several reach the package, and
  NULL for anything installed at runtime. The group name is the useful part:
  `[postgres]` and `[dev]` mean different things (see the flags page).
  **It is an over-approximation**: every extra or group whose tree *names* the
  package, ignoring markers. uv drops a path whose markers cannot all hold at once,
  and the walk does not. Flask's `gha-update = ["gha-update ; python_full_version >=
  '3.12'"]` reaches `{ name = "exceptiongroup", marker = "python_full_version <
  '3.11'" }`; no interpreter satisfies both, so uv never installs `exceptiongroup`
  for that group, but the walk lists `dev-dependencies:gha-update` anyway. 15 of
  2,406 rows on the estate over-list like this; none under-lists, and none has a
  flag changed by it. Making the walk marker-aware means enumerating Python
  versions and platforms, and an incomplete enumeration would under-list instead —
  not worth it for one column.
- `requirements_type` is `direct` / `optional` / `dev` from the first-party edge that
  names a package, in that precedence, else `resolved`. An extra outranks a group
  because it is published metadata that reaches the project's own users.
- **`runs_install_script` is NULL** — uv.lock records nothing about build hooks.
- A name can lock at **several versions** (uv forks resolution by marker); the edge
  then carries `version` and `source` to say which. All 5,140 edges on the estate
  resolve to exactly one entry. Markers themselves are not evaluated: a
  platform-specific package is still locked, so it is still reported.
- **Validated against uv's own closures, exactly.** For all 24 files, with
  R = `uv export --no-default-groups`, E = `… --all-extras` and G = `--all-groups`,
  every row keyed by `(name, version)` gets the flags those sets imply: in R →
  (0, 0); E only → (0, 1); G only → (1, 0); E and G → (1, NULL). All 2,406 rows match
  (388 / 430 / 1,446 / 142), and no package uv installs lacks a row. `declared_scope`
  was checked against one `--extra X` / `--group G` export each: 2,391 match, and the
  15 that differ are the marker over-listing described above. (Run a nested lock
  from its own directory, or uv reads the enclosing workspace's lockfile instead —
  `pydantic-core/uv.lock` inside `pydantic` does exactly that.)
- Integrity hashes (`sdist`, `wheels`) dropped, as everywhere.
- **`advisories` and `versions_behind` default to NULL**, not the `""` the older
  templates use (#258).

**Upstream docs:** [uv project layout and the lockfile](https://docs.astral.sh/uv/concepts/projects/layout/)
· [uv resolver internals](https://docs.astral.sh/uv/reference/resolver-internals/)
· [dependency groups and extras](https://docs.astral.sh/uv/concepts/projects/dependencies/)

**Drift watch:** a `version = 2` (the parser raises, as uv does — the signal to write
the branch). A new `source` kind that is first-party, which would otherwise be reported
as a dependency. The top-level `[manifest]` table: for a workspace whose root is not a
project, `manifest.requirements` and `manifest.dependency-groups` hold the root's
requirements, and they are **not read as roots** — packages reached only that way
would report NULL install state rather than a wrong one. The estate has `members`
only. Edges carrying `group = [...]` (a dependency on a member's group) are likewise
not followed; none on the estate. The top-level `conflicts` key (groups or extras
that cannot be installed together; urllib3 declares two pairs) makes uv write edges
conditional on the active group — `marker = "extra == 'group-7-urllib3-dev-min-pyopenssl'"`
— and the walk, not evaluating markers, follows them under every label. Today that
only over-lists `declared_scope` (2 of the 15 rows above); a lockfile where a
package is reachable *only* through a conflict-excluded path would get a flag uv
would not give it.

## `yarn.lock` — Yarn

| | |
|---|---|
| registry entry | `yarn-lock`, `Kind.PACKAGE`, `package_type="npm"` |
| parser | `kospex.extractors.yarn:extract_yarn_lock` |
| estate | 156 files across 21 repositories, 62,502 entries → 62,237 rows |

**Two incompatible formats under one name.** v1 (Yarn Classic) is a custom,
indentation-based format, identified by its `# yarn lockfile v1` header comment.
Berry (Yarn 2+) is YAML with a `__metadata.version` field, and the estate carries
versions 6, 8 and 10. The parser checks for the v1 header first, then loads YAML and
requires `__metadata`.

**Neither format has a grammar to parse against.** v1 is described on the Classic docs
page, but not specified. Berry has no published format spec at all: the format is
whatever Yarn's own implementation writes, and `__metadata.version` moves between Yarn
releases with no changelog for the lockfile itself. So the parser was written against
real files, and every gate below was measured against all 156 of them first.

By repository *reach* yarn.lock is the narrowest lockfile here despite its file count:
its files cluster in 21 repositories, so it mostly adds rows about repositories
already covered through `package.json`.

### Raises

- missing file, unreadable file, a directory, not UTF-8
- **neither the v1 header nor Berry `__metadata`**, including a 0-byte file and the
  one comment-only test fixture on the estate. yarn always writes one or the other,
  so nothing can be concluded from a file with neither, the same call pnpm makes for
  a 0-byte lockfile
- **v1: any line outside the grammar.** Every non-comment line is an entry key, a
  two-space `field value`, a two-space `section:`, or a four-space entry inside a
  section. Measured: all lines of all 111 v1 files fit, so a line that does not is
  a file the parser has not understood, and skipping it could skip a package. Bad
  JSON quoting raises too
- **Berry: a `__metadata.version` other than 6, 8 or 10.** Only measured versions are
  read, because with no spec an unmeasured version is a guess. The value is coerced
  as `package-lock.json`'s `lockfileVersion` is (`"10"` reads; `true` does not)
- Berry: invalid YAML, an entry that is not a mapping, an entry with no
  `resolution` (all 13,273 real entries have one)

### Returns `[]`

A v1 file holding only its header, and a Berry file holding only `__metadata` and
local entries. Four real Berry files are the second kind: benchmark and test
projects whose only entries are their own workspaces or links.

### Decisions and quirks

- **Everything is `resolved`, and install state is NULL.** A lockfile is the install
  set, not the declared set. v1 has no dev or optional flag at all. A Berry
  workspace entry's `dependencies` **merges** the project's `dependencies` and
  `devDependencies`: measured, 385 devDependencies sat in root workspace
  `dependencies`. So `direct` would claim production for dev tooling. Reading the
  sibling `package.json` would answer it, but an extractor reads one file.
- **Every identity an entry asserts is recorded**, as in `package-lock.json`. That
  means each key descriptor's name, an `npm:` alias's target, the name in a v1
  `resolved` tarball URL, and Berry's `resolution` name. 142 entries on the estate
  assert two names, and all 142 are aliases (`string-width-cjs@npm:string-width@…`).
  None is a URL disagreeing with its key.
- **Every version is recorded too.** That is v1's `version` plus the one in the
  tarball URL, or Berry's `resolution` version plus `version`. They agree on every
  real entry. Berry `version` is used only when YAML reads it as a string:
  `version: 1.10` is the float `1.1`, while the resolution string is exact.
- **`patch:` is unwrapped.** `fsevents@patch:fsevents@npm%3A2.3.3#…` is still
  `fsevents` 2.3.3 from npm. 49 entries on the estate.
- **Local packages produce no record.** That covers `link:`, `file:`, `workspace:`
  and `portal:`, because a local directory has nothing for deps.dev to answer about.
  But the protocol alone is not trusted, because then one edit to a key could hide a
  dependency. A v1 local entry is skipped only with no fetched `resolved` URL (true
  of all 12), and a Berry one only when `linkType: soft` (true of all 317). Anything
  else is recorded.
- **A mirror or tarball URL does not change identity.** 483 v1 entries resolve from a
  public npm mirror, and two from a GitHub tarball. They are recorded by name and
  version like any other.
- **An unmeasured Berry protocol is recorded and logged.** None is on the estate
  (git, http tarballs, `exec:`). Its declared `version` is recorded, which over-reports
  where skipping would hide.
- Integrity hashes and checksums are dropped, as everywhere.

### Measured 2026-10-28

Every `yarn.lock` listed in `file_metadata` (156 files, 21 repositories), read from its
working copy. This is a different method from the summary table above, which counts
working copies with `node_modules` excluded, hence v1 ×111 here against ×116 there.
Entries are top-level blocks for v1 and `resolution:` lines for Berry.

| format | files | repos | entries | parser result |
|---|---|---|---|---|
| v1 | 111 | 14 | 49,229 | 111 extracted, 49,271 rows |
| Berry `__metadata.version` 10 | 39 | 4 | 8,758 | 40 extracted, 4 empty, 12,966 rows across all Berry files |
| Berry 8 | 4 | 3 | 4,514 | |
| Berry 6 | 1 | 1 | 1 | |
| neither header | 1 | 1 | 0 | `parse_error` |

A repository can carry more than one format, so the repo column sums past 21. The
whole run takes under 5 seconds.

**v1 version forms.** Every entry has an explicit `version` line (0 of 49,229 missing).
Where the `resolved` line points:

| source | entries |
|---|---|
| public npm registry | 48,732 |
| a public npm mirror | 483 |
| a GitHub tarball | 2 |
| no `resolved` line | 12 |

All 12 without `resolved` are local packages, keyed `name@link:<path>` or
`name@file:<path>`. They have a `version` but no registry identity.

**Berry `resolution` protocols.**

| protocol | entries | registry identity? |
|---|---|---|
| `npm:` | 12,907 | yes |
| `workspace:` | 307 | no, local code |
| `patch:` | 49 | yes, once the wrapped `npm:` descriptor is unwrapped |
| `link:` | 8 | no, local code |
| `portal:` | 2 | no, local code |

**Upstream docs:** [yarn.lock (v1, classic)](https://classic.yarnpkg.com/lang/en/docs/yarn-lock/)
· [Yarn configuration](https://yarnpkg.com/configuration/yarnrc)

**Drift watch:** a new `__metadata.version`. The parser raises on it, and that is the
signal to re-run the survey over every real file before adding it to the accepted list.
With no spec to diff, the files are the only evidence of what changed. Upstream, the
version Yarn writes is
[`LOCKFILE_VERSION` in `packages/yarnpkg-core/sources/Project.ts`](https://github.com/yarnpkg/berry/blob/master/packages/yarnpkg-core/sources/Project.ts)
(10 as of 2026-10-28). It is bumped when entry fields change, so a higher value there
means a new version is coming. Also watch for
a Berry protocol other than `npm:` / `patch:` / local appearing on the estate, which is
logged and recorded by declared version until someone looks at it.

## `pom.xml` — Maven

| | |
|---|---|
| registry entry | `maven-pom`, `Kind.PACKAGE`, `package_type="maven"` |
| parser | `kospex.extractors.maven:extract_pom` |
| estate | 14 files across 7 repositories on disk (12 files across 5 in `file_metadata`) |

One record per `<dependency>` in the project's own `<dependencies>`, named
`groupId:artifactId` (deps.dev's name for a Maven package), plus one per BOM imported
in its `<dependencyManagement>`.

### Raises

- missing or unreadable file, or not UTF-8 (UTF-16 is refused, as everywhere)
- not well-formed XML
- **a `<!DOCTYPE` declaration** — CWE-776, the same refusal and placement as
  `.csproj`; no `pom.xml` on the estate carries one
- **a root element that is not `<project>`** — all 14 on the estate have one
- **a `<dependency>` with no `groupId` or `artifactId`** — Maven refuses such a pom,
  and skipping the entry would report fewer dependencies than the file declares

A **parent** pom that cannot be read never raises. The file being scanned was read; a
broken parent costs only the versions it would have supplied, and is logged.

### Returns `[]`

A readable pom with no `<dependencies>` and no BOM import. The gson root pom is one: a
parent whose `<dependencyManagement>` pins versions for its modules but which depends
on nothing itself.

### Where a version comes from

The main problem with this format. **24 of 51** direct dependencies on the estate have
no `<version>`. Maven refuses to build a pom whose unversioned dependency nothing
manages, so each is a real declaration whose version lives in another POM:

| source | on the estate | read by kospex |
|---|---|---|
| the file's own `<properties>`, `${project.version}`, `${project.parent.version}` | 7 `${...}` versions | **yes** — tier 1 |
| a parent pom inside the same repository (`<relativePath>`, default `../pom.xml`) | 11 missing versions, all gson modules | **yes** — tier 2 |
| an imported BOM (`<scope>import</scope>`) | 10 — jsoup ← `netty-bom`, okhttp ×2 ← `okhttp-bom`, gson graal test ← `junit-bom` | **at enrichment** — tier 3, deps.dev; 4 of the 10 (okhttp's BOM is a snapshot range) |
| a parent only in a Maven repository | 3 — jackson-core ← `tools.jackson:jackson-base:3.3.0-SNAPSHOT` | no |
| build-time `-Drevision=`, `.mvn/maven.config`, `${env.*}`, `settings.xml` | none | no |

After tiers 1 and 2: **38 resolved, 13 blank, 0 unresolved `${...}`.**

- **A version kospex cannot find is recorded as `""`**, as `.csproj` does under Central
  Package Management. Enrichment reports it as `no_version`. Nothing marks *why* it is
  missing, and for Maven nothing needs to: a blank version always means "managed by a
  BOM or a parent kospex did not read".
- **Tier 3 runs in enrichment, not the parser** (`KospexDependencies.maven_bom_versions`,
  called by both `sca` and `osi`). For a blank row in a pom that imports a BOM, it
  asks deps.dev for that BOM's `:requirements`, which lists the managed versions
  already resolved, and looks the row up at that version. `package_version` stays
  blank (what the file says) and the version used goes in `resolved_version`, so
  "declared nothing, looked one up" identifies a BOM-supplied version. The first
  declared BOM wins, as in Maven. A snapshot or range BOM, a 404, a network error or
  an artifact the BOM does not list leaves the row `no_version`, and the reason is
  logged only (`kospex_dependencies.log`). A BOM imported by an in-repo *parent* is
  not applied to its children. See `changes/202611-maven-bom-version-resolution.md`.
- **An unresolvable `${...}` is kept verbatim**, not blanked, so enrichment reports
  `unresolved_spec` and the row still names the property it was waiting on.
- **Interpolation happens after inheritance**, as in Maven: `${project.version}` in a
  parent's `<dependencyManagement>` means the *child's* version. Nearer declarations
  win — child over parent over grandparent — and an explicit `<version>` wins over
  management.
- **Property expansion is capped** (10 passes, 1024 characters). `<p1>${p0}${p0}</p1>`
  doubles at each level — CWE-776's mechanism without a DTD — and the input comes from
  cloned repositories. Past the cap the declared text is kept. The length is
  checked while each pass is built, not after: one pass substituting a large
  property into many references is a blow-up of its own, and a check after
  `re.sub` let it allocate first (250 references to a 200 KB property peaked at
  50 MB). A declared value already longer than the cap is not expanded at all.
- **A parent is followed only inside the repository.** The boundary is the nearest
  enclosing directory holding `.git`; the path is resolved through symlinks before the
  check, since `<relativePath>` is third-party input. With no `.git` above the file, no
  parent is read at all. `<relativePath/>` (empty) means "repository only" and is
  honoured. The file found must carry the `groupId:artifactId` (and version, when the
  child names one) that `<parent>` asks for — Maven falls back to a repository when it
  does not, and so does this. Cycles and chains deeper than 10 stop.
- **Management is keyed as Maven keys it**: `groupId`, `artifactId`, `type` (default
  `jar`) and `classifier`.

### Decisions and quirks

- **`declared_scope` is the raw `<scope>`; absent stays NULL.** Maven reads an absent
  scope as `compile`, but the file did not say `compile`. `is_dev` is true for `test`
  only — see [`dependency-flags.md`](dependency-flags.md) for why `provided` and
  `runtime` are not "dev". A scope supplied by a parent's management is not read.
- **`<optional>true</optional>` is not `is_optional`.** In Maven it means "not passed on
  to consumers", not "may not install". `is_optional` and `runs_install_script` are NULL.
- **BOM imports are rows** (`requirements_type="bom"`, `package_use="bom"`,
  `declared_scope="import"`, `is_dev` NULL): they are where the missing versions come
  from. They are not `direct`: a BOM puts nothing on the classpath, so counting it
  would overstate direct dependencies (jsoup would show 7, not 6). A BOM import is
  `<scope>import</scope>` **and** `<type>pom</type>` inside `<dependencyManagement>`;
  the name (`-bom`) is only a habit (`spring-boot-dependencies` is one too). An
  `import` entry of any other type is logged and not recorded: Maven warns ("must be
  'pom' to import the managed dependencies") and does not import it, leaving an
  ordinary management entry. None on the estate. Other
  `<dependencyManagement>` entries are not — they pin versions, they do not make the
  project depend on anything.
- **Not read:** `<build><plugins>` dependencies (8 on the estate; the build tool's
  classpath) and `<profiles>` (none on the estate; they apply only when active).
- **Version ranges** use the bracket notation NuGet adopted from Maven, so
  `constraints.py` reads intervals for `maven` too. Both okhttp copies import their BOM
  with `[5.2.0-SNAPSHOT,6.0.0-SNAPSHOT)` (`bounded`). A bare Maven version is a "soft
  requirement" that mediation can replace, and is classified `pinned`. A multi-range
  set such as `(,1.0],[1.2,)` is not recognised and classifies `none`.
- **Elements are matched by local name.** Every pom on the estate declares the Maven
  namespace; it is optional, and matching one spelling only would report the other as
  declaring nothing.

**Upstream docs:** [POM reference](https://maven.apache.org/pom.html)
· [Introduction to the Dependency Mechanism](https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html)
· [Version range specification](https://maven.apache.org/enforcer/enforcer-rules/versionRanges.html)
· [CI-friendly versions](https://maven.apache.org/guides/mini/guide-maven-ci-friendly.html)

**Drift watch:** POM model version 4.1.0 (Maven 4), which changes inheritance and
adds `<subprojects>`. A pom in a non-UTF-8 encoding with non-ASCII bytes, which Maven
reads and this raises on (none on the estate). `${revision}` CI-friendly versions,
which will surface as `unresolved_spec`. BOM resolution lives in enrichment, not
here, because it needs the network; external-parent resolution would go there too.
deps.dev's `:requirements` response shape (`maven.dependencyManagement[].resolvedName`
and `.resolvedVersion`, skipping `origin: "import"`) is v3alpha and may change. Match on
`resolvedName`: an entry a BOM inherits from its own imports or parent has a blank
`name` (1,157 of 1,607 for `spring-boot-dependencies` 3.5.7).

## `libs.versions.toml` — Gradle version catalog

| | |
|---|---|
| registry entry | `gradle-version-catalog`, `Kind.PACKAGE`, `package_type="maven"` |
| parser | `kospex.extractors.gradle:extract_version_catalog` |
| estate | 20 catalogs on disk (19 named `libs.versions.toml`), 1,237 rows; 19 in `file_metadata` |

Phase 1 of the Gradle design, `changes/202611-gradle-dependency-parsing.md`. A catalog
is where a Gradle build sets the versions its modules use. Build files decide which of
them a module uses (phase 2, see `build.gradle` below).

One record per `[libraries]` entry, named `groupId:artifactId`, and one per `[plugins]`
entry, named by its marker artifact `<id>:<id>.gradle.plugin` (deps.dev knows these, and
Google's Maven repository: `androidx.*`, `com.google.firebase.*`). `[bundles]` only group
libraries and give no rows. `requirements_type` is `version-catalog`, which has its own
`package_use` of the same name: a catalog row gets advisories like any row, but it isn't
a direct dependency, and build-file rows will be. Library rows have `is_dev` and
`declared_scope` None, because a catalog doesn't say whether a library is for tests.
Plugin rows have `declared_scope="plugin"` and `is_dev=True`, since they are build tooling.

**`ecosystem` is `gradle`, `package_type` stays `maven`.** A bare Gradle version is a
`require` version, which conflict resolution can raise, so it's classified `gte`, where
Maven's is `pinned`. `classify_constraint` is given `gradle` from the record's
`ecosystem` on both write paths (`KospexDependencies._constraint_system`), because
`package_type` can't tell the two apart.

### Raises

- missing or unreadable file, not UTF-8, or not valid TOML
- `[versions]`, `[libraries]` or `[plugins]` that isn't a table
- **an entry Gradle refuses**, which makes Gradle reject the whole catalog: a library
  that isn't `group:artifact` (string, `module`, or `group` + `name`), a plugin with no
  `id`, a `version.ref` naming no `[versions]` key, or a version that is neither a string
  nor a rich version table

### Returns `[]`

A catalog with no `[libraries]` or `[plugins]` entry, or an empty file. None on disk:
the smallest, androidx's `bench-flame-diff`, has one plugin and so one row.

### Quirks and decisions

- **The version recorded is the one Gradle requires**: `strictly`, else `require`, else
  `prefer`. A rich version with only `reject` records "". `strictly` is recorded as its
  plain value, so it's classified `gte`, not `pinned`. Gradle's `!!` shorthand would
  classify correctly, but `clean_version_spec` passes `1.2.3!!` to deps.dev unchanged and
  the lookup fails. Two entries on disk use `strictly`.
- **No version → ""**: 22 libraries leave the version to a `platform(...)` in a build
  file. The catalog row stays `no_version`; the build file's row for the same library
  gets the platform's version, because the platform is in that file.
- **refreshVersions `_` → ""**: lottie-android's 63 libraries all use the placeholder
  `_`, whose version is in `versions.properties`, not read. Logged, reported
  `no_version`.
- **Exact duplicates are one row.** Two aliases for the same artifact at the same version
  would collide on the primary key; at two versions they're two rows.
- **Only the name `libs.versions.toml` is matched.** Gradle imports it from `gradle/`
  automatically. A catalog with another name is declared in `settings.gradle(.kts)`
  (`importMaven.versions.toml` in androidx, 9 libraries, unused by the build) and is not
  read.
- **An existing database needs the files re-tagged first.** panopticas tags
  `libs.versions.toml` as `dependencies` from 0.0.22/0.0.23 (#349), and `krunner osi` only reads tagged
  files. A repo synced with an older panopticas shows nothing until
  `krunner file-metadata` re-tags it (see `docs/refreshing-data.md`). Measured on the
  development database: timber, synced with 0.0.19, had its catalog untagged.
- **Measured against deps.dev** (sca path, timber, retrofit, firebase-android-sdk): 212
  of 214 rows resolve. The two that don't are versions deps.dev doesn't list
  (`androidx.appcompat:appcompat` 1.7.1, the Crashlytics plugin 3.0.4), recorded as
  `version_yanked` by the existing lookup-miss classification.
- **`versions_behind` inherits #377.** deps.dev's default version for the Android Gradle
  plugin (`com.android.tools.build:gradle`) is 2.3.0 from 2017, so a current version
  reads 0 behind.

**Upstream docs:** [Version catalogs](https://docs.gradle.org/current/userguide/version_catalogs.html),
[Declaring rich versions](https://docs.gradle.org/current/userguide/dependency_versions.html)

**Drift watch:** the catalog `[metadata] format.version` (1.1 today), new rich-version
keys, and Gradle supporting a catalog location other than `gradle/` by default. A
panopticas release that stops tagging `libs.versions.toml`, which would hide every catalog
from `osi`.

## `build.gradle` / `build.gradle.kts` — Gradle build files

| | |
|---|---|
| registry entry | `gradle-build`, `Kind.PACKAGE`, `package_type="maven"` |
| parser | `kospex.extractors.gradle:extract_build_gradle` |
| estate | 1,291 build files on disk (20 repositories), 9,853 rows |

Phase 2 of `changes/202611-gradle-dependency-parsing.md`. A build file is a Groovy or
Kotlin program, and Gradle isn't run: the reader recognises the declaration forms
measured on the estate and counts the rest as unreadable. Of about 12,900 declarations,
3,695 are first-party `project(...)` references, which aren't packages, and 119 are
unreadable: about 1.3% of package declarations.

Each recognised declaration is a row named `groupId:artifactId`, `requirements_type`
`direct`, with `declared_scope` the configuration as written and `is_dev` from it.
`platform(...)` / `enforcedPlatform(...)` is a `bom` row, so the existing
`maven_bom_versions()` lookup fills versionless rows in the same file, unchanged.
`ecosystem` is `gradle`, as for the catalog, so a bare version is `gte`.

### Raises

- missing or unreadable file, a directory, or not UTF-8
- **declarations and none readable** (decision 3): a file whose dependencies are all
  project helpers would otherwise report `empty`. 10 files on disk: five of
  kotlinx.coroutines' (its `version("x")` helper from `buildSrc`) and five androidx test
  projects whose coordinate is a property the test harness injects.

### Returns `[]`

A file with no package declarations: no `dependencies { }`, only `project(...)` and
`files(...)` references, or only plugins without a version. 151 files on disk, 101 of
them androidx modules that depend only on sibling projects. 1,130 return rows.

### Where a version comes from

- **The literal**, `"g:a:1.2"`. `g:a:1.2:classifier` and `g:a:1.2@aar` keep `1.2`.
- **`$var` / `${var}`**, filled from `gradle.properties` (the build root's, then the
  module's, which wins), from simple assignments in the root build file (`ext { }`,
  `ext.x =`, `extra["x"] =`) and from this file (`val`, `def`), including
  `val x = property("y")` (decision 4) and `libs.versions.x.get()`. A version that can't
  be filled is kept as written, so enrichment reports `unresolved_spec`, as for
  `${revision}` in a pom. A *name* that can't be filled is unreadable.
- **The build's catalog** for `libs.x` and `libs.bundles.x`. The catalog is
  `gradle/libs.versions.toml` beside the nearest `settings.gradle(.kts)`, as Gradle reads
  it, not the nearest `gradle/` folder: androidx's `car/app/app-samples` has its own
  catalog but no settings file, so Gradle builds it with androidx's. A settings file's
  `versionCatalogs { create("x") { from(files("...")) } }` adds a catalog when the path is
  a literal inside the repository (okhttp's and coil's included builds point at
  `../gradle/libs.versions.toml`).
- **A `platform(...)` in the same file**, at enrichment time, as for a pom's imported BOM.

### Decisions and quirks

- **`is_dev` is True for test configurations**: those starting `test`, `androidTest` or
  `testFixtures`, or with `Test` as a camel-case word (`debugTestImplementation`,
  `kspAndroidTest`), and for `buildscript` classpath and plugins (build tooling).
  `compileOnly`, `kapt`, `ksp` and `annotationProcessor` stay False, as the pom parser
  treats `provided`. So do androidx's `docs` / `kmpDocs` / `lintChecks` (decision 3).
- **Kotlin Multiplatform** (decision 2): `declared_scope` is
  `<sourceSet>.<configuration>`, for `commonMain.dependencies { }`,
  `sourceSets { jvmTest { dependencies { } } }`, `val x by getting { }` and
  `named("x") { }`, and for JVM test suites. A source set ending `Test` is dev.
- **Plugins** (decision 1): only a top-level `plugins { }`, and only a plugin with a
  version (`id("x") version "y"`, `kotlin("jvm") version "y"`, `alias(libs.plugins.x)`),
  named by its marker artifact. 2,774 `id("x")` without a version give no row.
  `gradlePlugin { plugins { } }` defines plugins and isn't read.
- **One row per (name, version)**, the primary key's pair. When a package is declared for
  tests and for the product, the product use wins.
- **`1.2!!` is stored as `1.2`**, as the catalog stores `strictly`.
- **`kotlin("x")` is `org.jetbrains.kotlin:kotlin-x` with no version** unless one is
  given: the Kotlin plugin supplies it. 345 of the 521 blank-version rows on disk. Filling
  it from the build's Kotlin plugin version is possible later.
- **No row, by design:** `project(...)`, `projects.x` and `testFixtures(project(...))`
  (first-party, #399), `files(...)` / `fileTree(...)` (a checked-in jar, logged, #398),
  `gradleApi()` and the other Gradle helpers, and `constraints { }` (versions only,
  decision 5).
- **Declarations in `if`/`else`, `subprojects { }` and `allprojects { }` are read** as if
  unconditional. The file declares them, and an inventory prefers over-reporting.
- **Unreadable declarations are logged with file and line** at INFO. The most common on
  disk: androidx's `projectOrArtifact(...)`, kotlinx.coroutines' `version("x")`, and
  string concatenation (`"g:a:" + v`).
- **Not read:** convention plugins in `buildSrc/` and `build-logic/` that add dependencies
  to every module, `apply from:` scripts, and `ext` maps of coordinates (`deps.okhttp`).

**Upstream docs:** [Declaring dependencies](https://docs.gradle.org/current/userguide/declaring_dependencies.html),
[Dependency configurations](https://docs.gradle.org/current/userguide/dependency_configurations.html),
[Kotlin Multiplatform dependencies](https://kotlinlang.org/docs/multiplatform/multiplatform-add-dependencies.html)

**Drift watch:** new DSL forms for declaring dependencies (Gradle 9's declarative DSL),
the default catalog location, Android Gradle plugin source-set names (`androidHostTest`,
`androidDeviceTest` are recent), and a rise in the unreadable count from the logs,
which means a new idiom worth supporting. Re-run the measurement script approach from
the design doc against every build file on disk before changing a rule.

---

# Classified, no parser yet

Types the registry recognises and nothing reads. They report `unsupported` —
honest, and distinguishable from `empty`. Tracked as sub-project D, #229.

Two rules from the parsers above are worth applying from the start rather than
retrofitting: **an unreadable file raises**, and **record every identity the file
asserts**. Prefer over-reporting: in a dependency inventory a false positive is visible
and dismissible, a false negative is not.

## Tagged by panopticas 0.0.22 and 0.0.23 — #349

panopticas 0.0.22 and 0.0.23 tagged about 40 more filenames `dependencies`, and the
registry knew none of them, so each showed as an unrecognised dependency file. They now
have entries, so they report `unsupported` (no parser yet) rather than `unclassified`.
`package_type` records the deps.dev system a future parser would query, or `None` where
deps.dev has none (Packagist, Hex, Pub, JSR).

`tests/test_extractor_registry.py::test_every_panopticas_dependency_file_has_a_registry_entry`
walks panopticas' own `METADATA_RULES`, so the next release that tags a new file fails
the suite instead. Files tagged before #349 that still have no entry are listed there in
`KNOWN_UNREGISTERED`, each with where it is tracked.

Measured 2026-11-06 on the development estate. The counts are files in `file_metadata`,
`node_modules` excluded. Several of the single-file entries are test fixtures in one
repository, not real use.

| entry | files | `package_type` | notes |
|---|---|---|---|
| `cargo-toml` `Cargo.toml` | 126 | `cargo` | one per workspace member; parser #359 |
| `pnpm-workspace` `pnpm-workspace.yaml` | 48 | `npm` | see below |
| `sbt` `*.sbt` | 32 | `maven` | any `*.sbt` is build definition, not only `build.sbt` |
| `gradle-version-catalog` `libs.versions.toml` | 19 | `maven` | Gradle imports it from `gradle/`; has a parser now, see its section above |
| `cargo-lock` `Cargo.lock` | 15 | `cargo` | parser #359 |
| `gemfile` `Gemfile`, `gems.rb` | 15 | `rubygems` | Ruby code, so a parser reads a subset; #360 |
| `gemspec` `*.gemspec` | 9 | `rubygems` | Ruby code building a `Gem::Specification`; #360 |
| `composer-json` / `composer-lock` | 5 / 1 | `None` | |
| `mill` `build.mill`, `package.mill`, `*.mill.yaml`, `build.sc` | 4 | `maven` | `build.sc` is the pre-0.12 name |
| `gemfile-lock` `Gemfile.lock`, `gems.locked` | 3 | `rubygems` | parser #360 |
| `bun-lock` `bun.lock`, `bun.lockb` | 3 | `npm` | `bun.lockb` is binary, replaced by text `bun.lock` in Bun 1.2 |
| `pipfile` / `pipfile-lock` | 2 / 1 | `pypi` | |
| `poetry-lock`, `pdm-lock` | 2, 0 | `pypi` | |
| `pylock` `pylock.toml`, `pylock.<name>.toml` | 4 | `pypi` | PEP 751; local pattern, see below |
| `gradle-lockfile` `gradle.lockfile`, `buildscript-gradle.lockfile` | 1 | `maven` | replaced when a Gradle parser lands (#229) |
| `mix-exs`, `mix-lock` | 1, 1 | `None` | Elixir; umbrella projects have a `mix.exs` per app |
| `pubspec` `pubspec.yaml`, `pubspec_overrides.yaml` / `pubspec-lock` | 1 / 0 | `None` | Dart |
| `deno-lock` `deno.lock` | 1 | `None` | mixes JSR and npm specifiers |
| `nuspec` `*.nuspec` | 2 | `nuget` | |
| `nuget-fsproj-vbproj` `*.fsproj`, `*.vbproj` | 0 | `nuget` | see below |
| `nuget-central-versions` `Directory.Packages.props` | 0 | `nuget` | central package management: `PackageVersion`, not `PackageReference` |
| `nuget-lock` `packages.lock.json`, `packages.<project>.lock.json` | 0 | `nuget` | local pattern, see below |
| `dotnet-tools` `dotnet-tools.json` | 0 | `nuget` | local .NET tools, which are NuGet packages |
| `paket` `paket.dependencies`, `.lock`, `.references` | 0 | `nuget` | |

**`pnpm-workspace.yaml` is mostly configuration.** Its top-level keys on the estate are
`packages`, `allowBuilds`, `minimumReleaseAge` and other settings, and pnpm 11 moved its
settings here. But `catalog:` / `catalogs:` declare version ranges that `package.json`
files refer to as `catalog:`, so a `package.json` dependency on `catalog:` has no version
unless this file is read. Measured: 4 of 48 files have catalogs, 53 entries in all. A
parser is worth it once catalogs are common.

**`*.fsproj` and `*.vbproj` use the same SDK-style `PackageReference` as `*.csproj`,** so
`extract_csproj` probably reads them unchanged. No real file was on the estate to
measure that against, so they have no parser yet. Pointing them at it is a one-line
change after a measurement.

**Two local patterns copy a panopticas predicate.** `is_pylock` and
`is_nuget_project_lock` exist in `panopticas.core`, but the package does not export
them, and `_panopticas_matcher` looks names up on the package, so it would always
return False. The patterns are panopticas' own. Delegate once
[kospex/panopticas#110](https://github.com/kospex/panopticas/issues/110) exports them.

**Upstream docs:** [Gradle dependency locking](https://docs.gradle.org/current/userguide/dependency_locking.html)
· [Gradle version catalogs](https://docs.gradle.org/current/userguide/version_catalogs.html)
· [sbt library dependencies](https://www.scala-sbt.org/1.x/docs/Library-Dependencies.html)
· [Mill](https://mill-build.org/)
· [Cargo manifest](https://doc.rust-lang.org/cargo/reference/manifest.html)
· [Cargo.toml vs Cargo.lock](https://doc.rust-lang.org/cargo/guide/cargo-toml-vs-cargo-lock.html)
· [Bundler Gemfile](https://bundler.io/guides/gemfile.html)
· [gemspec reference](https://guides.rubygems.org/specification-reference/)
· [composer.json schema](https://getcomposer.org/doc/04-schema.md)
· [Composer basic usage](https://getcomposer.org/doc/01-basic-usage.md)
· [Pipfile](https://pipenv.pypa.io/en/latest/pipfile.html)
· [Poetry basic usage](https://python-poetry.org/docs/basic-usage/)
· [PDM lockfile](https://pdm-project.org/en/latest/usage/lockfile/)
· [pylock.toml specification](https://packaging.python.org/en/latest/specifications/pylock-toml/)
· [Mix.Project](https://mix.hexdocs.pm/Mix.Project.html)
· [pubspec](https://dart.dev/tools/pub/pubspec)
· [Dart dependencies](https://dart.dev/tools/pub/dependencies)
· [Bun lockfile](https://bun.sh/docs/pm/lockfile)
· [Deno modules](https://docs.deno.com/runtime/fundamentals/modules/)
· [pnpm settings](https://pnpm.io/settings)
· [pnpm catalogs](https://pnpm.io/catalogs)
· [NuGet central package management](https://learn.microsoft.com/en-us/nuget/consume-packages/central-package-management)
· [PackageReference and lock files](https://learn.microsoft.com/en-us/nuget/consume-packages/package-references-in-project-files)
· [.nuspec reference](https://learn.microsoft.com/en-us/nuget/reference/nuspec)
· [.NET local tools](https://learn.microsoft.com/en-us/dotnet/core/tools/local-tools-how-to-use)
· [Paket](https://fsprojects.github.io/Paket/)

**Drift watch:** each panopticas release. `test_every_panopticas_dependency_file_has_a_registry_entry`
catches a new `dependencies` tag. Also re-run the catalog count on `pnpm-workspace.yaml`
before deciding on a parser.

---

# Classified, declares no packages

These have registry entries so that kospex can say *what they are* rather than
reporting `unclassified`. None should grow a package parser; a few could grow a
different kind of reader.

## `.python-version`, `.nvmrc`, `.ruby-version` — `Kind.RUNTIME`

A language or toolchain version, not a library dependency. Worth reading eventually as
a *runtime* fact — "this repo pins Python 3.8" is an end-of-life question, not an
advisory question — which is a different table, not `dependency_data`. Recording
runtime versions is #351. `.ruby-version` gained its entry in #349.

**Upstream docs:** [uv Python versions](https://docs.astral.sh/uv/concepts/python-versions/)
· [pyenv](https://github.com/pyenv/pyenv) · [nvm](https://github.com/nvm-sh/nvm)
· [rbenv](https://github.com/rbenv/rbenv)

## `Dockerfile` — `Kind.CONTAINER`

A base image plus build-time installs. Both are real dependencies and neither is a
registry package: `FROM node:18` and `apt-get install` lines need an image/OS-package
model, not `name@version` against deps.dev.

**Upstream docs:** [Dockerfile reference](https://docs.docker.com/reference/dockerfile/)

## `dependabot.yml`, `renovate.json` / `.renovaterc` — `Kind.SCA_CONFIG`

Scan *configuration*. These declare no dependencies at all, and their presence is
evidence about a repository's practice rather than its contents — which makes them
interesting to report on (116 `dependabot.yml` on the estate) and wrong to parse for
packages.

**Upstream docs:** [Dependabot options reference](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-ref)
· [Renovate configuration options](https://docs.renovatebot.com/configuration-options/)

## `go.sum` — `Kind.LOCKFILE`

An integrity/checksum companion, not scanned alone. `go.mod` is the dependency
declaration and already has a parser, so Go is fully covered — **this entry exists
mainly to stop a consumer counting unreadable lockfiles concluding Go is unparsed.**
The registry's `Kind` distinction is what prevents that, and it is worth keeping
explicit as sub-project D grows sub-issues.

**Upstream docs:** [go.sum files](https://go.dev/ref/mod#go-sum-files)

## `.pnp.cjs`, `.pnp.loader.mjs` — `Kind.LOCKFILE`

Yarn Plug'n'Play loaders: generated JavaScript holding the dependency tree that
`yarn.lock` already declares, and `yarn.lock` has a parser. Like `go.sum`, a companion
rather than a source of its own, so they are not reported as lacking a parser.
Registered in #349. None on the estate.

**Upstream docs:** [Yarn Plug'n'Play](https://yarnpkg.com/features/pnp)

---

# Not in the registry at all

These report `unclassified` — kospex cannot even name the file.

## `setup.py` / `setup.cfg` — #137

15 `setup.py` on the estate. `setup.py` is executable Python, so "parsing" it in the
general case means running it; `setup.cfg` is declarative and tractable. Worth
splitting the issue on that line rather than treating them as one format.

**Upstream docs:** [setuptools dependency management](https://setuptools.pypa.io/en/latest/userguide/dependency_management.html)

## `packages.config` — #182

Legacy NuGet, pre-`PackageReference`. XML, flat, and simple to read — and the same
DOCTYPE consideration applies.

**Upstream docs:** [packages.config reference](https://learn.microsoft.com/en-us/nuget/reference/packages-config)

## Rye lockfiles — #183

`requirements.lock` and `requirements-dev.lock`. A different shape of gap: **not tagged
by panopticas**, so they are invisible to kospex upstream of the registry — a registry
entry here would never be consulted. Nothing in this repository can fix that; the fix
is in panopticas' metadata rules.

Note the trap in the names: they look like `requirements*.txt` but are not, and
`panopticas.is_pip_requirements` correctly rejects a `.lock` extension. Reading them as
pip requirements files would be wrong anyway — they are fully-resolved lockfiles.

**Upstream docs:** [Rye](https://rye.astral.sh/)

## Swift, CocoaPods, Carthage — #363

`Package.swift` / `Package.resolved`, `Podfile` / `Podfile.lock` and `Cartfile` /
`Cartfile.private` / `Cartfile.resolved`. panopticas tagged these `dependencies` before
#349, and none has a registry entry yet. They are listed in `KNOWN_UNREGISTERED` in
`tests/test_extractor_registry.py`, so they do not fail the panopticas coverage test.

On the development estate: `Package.resolved` 8, `Podfile` 6, `Package.swift` 5,
`Podfile.lock` 4, `Cartfile` 3, `Cartfile.resolved` 3, `Cartfile.private` 2.

#363 registers them first, with `package_type` None since deps.dev has no system for
any of the three, then parses the lockfiles. `Package.resolved` is JSON, `Podfile.lock`
YAML and `Cartfile.resolved` a line format. `Package.swift` and `Podfile` are code and
can only be read in part, without evaluating them.

**Upstream docs:** [Package.resolved](https://github.com/swiftlang/swift-package-manager/blob/main/Sources/PackageManagerDocs/Documentation.docc/ResolvingPackageVersions.md)
· [Podfile.lock](https://guides.cocoapods.org/using/using-cocoapods.html)
· [Carthage artifacts](https://github.com/Carthage/Carthage/blob/master/Documentation/Artifacts.md)

---

# Where these decisions come from

Every rule on this page was argued somewhere, and the PR is where the reasoning and the
measurements live. This is **provenance for the decisions above**, not a changelog —
the parser layer was built by a small, finite set of PRs and that set is worth naming.

| merged | PR | what it established |
|---|---|---|
| 2026-05-17 | #98 | Swallowed a malformed `pyproject.toml` so a batch run could continue (#97). Right at the time, and **reversed by #252** once both scan paths had per-file guards — the one decision on this page that has been undone. |
| 2026-05-22 | #101 | The first standalone extractor, `extractors/pnpm.py`: a pure file-in, records-out module with no DB access, the shape every later parser follows. It warned and returned `[]` on an unreadable file, which #252 replaced with a raise. |
| 2026-07-20 | #114 | The extractor registry and classifier — one source of truth for what kospex recognises, what kind each file is, and which scanner handles it (sub-project A). |
| 2026-08-10 | #139 | `requirements.txt` parsed with `packaging` (PEP 508) instead of substring-matching operators, and unpinned declarations stopped being dropped. |
| 2026-08-30 | #179 | `go.mod`: read the single-line `require` form, and record `// indirect` so transitive Go dependencies reach the database at all. |
| 2026-08-30 | #185 | `krunner osi` dispatches through the registry (C1), closing the gap where `go.mod` and `.csproj` were handled by one scan path only. |
| 2026-08-30 | #188 | `KospexDependencies.assess` dispatches through the registry too (C2a) — the change that makes a parser contract hold for *both* callers. |
| 2026-09-07 | #191 | The declared operator is kept in `package_version` and `version_operator`, so `flask>=2.0` is no longer indistinguishable from a pin. |
| 2026-10-07 | #222 | The per-file extraction outcome — `extracted`, `empty`, `unsupported`, `parse_error` and the rest — and `OUTCOME_PRIORITY` (#148). The error contract rests on it: `[]` is recorded as `empty`. |
| 2026-10-09 | #228 | pnpm transitive dependencies are looked up by `kospex sca` (#225). |
| 2026-10-09 | #233 | `docs/dependency-flags.md`: what each format records about install state, quoted from its own documentation rather than recalled. |
| 2026-10-10 | #231 | Install state — `is_dev`, `is_optional`, `runs_install_script`, `declared_scope` — and migration `0009` (#227). Also established NULL-means-unknown. |
| 2026-10-12 | #240 | A docs correction: an earlier claim about what `lockfileVersion` 1 records was wrong. Worth noting as precedent — the flags page is a source of truth and gets corrected when it is wrong. |
| 2026-10-13 | #241 | `pom.xml` gains a registry entry (#221), moving it from `unclassified` to `unsupported`. |
| 2026-10-15 | #243 | The `package-lock.json` parser, all three lockfile versions: install paths, flag aggregation across depths, "record every identity, elect none", and "was this fetched at all" rather than registry recognition. |
| 2026-10-16 | #248 | The three hand-written parsers in `kospex_dependencies.py`: the UTF-16 BOM, the PEP 621 array that fabricated packages, and the `package.json` section that must raise rather than be skipped. |
| 2026-10-17 | #252 | The error contract — `UnreadableManifest`, the three categories, the `module` and `<Project>` gates — and the multi-document pnpm lockfile. |
| 2026-10-18 | #257 | Whether a file with a byte-order mark may be refused is decided by the tool that owns the format, checked against that tool — so `package.json` and `package-lock.json` are read with `utf-8-sig`, because npm reads them. |
| 2026-10-19 | #261 | The `uv.lock` parser: gates taken from what uv itself refuses, so an unknown `revision` is read, not raised (the deliberate departure from `package-lock.json`); first-party `editable`/`virtual` entries as walk roots rather than rows; install state from a graph walk, validated exactly against `uv export`; and `declared_scope` documented as marker-blind. |

**There is deliberately no running list of future PRs here.** A hand-maintained list
goes stale silently, and a stale table in a reference doc is worse than no table: it
reads as current. Derive it instead:

```bash
# every change to a parser, newest first
git log --date=short --format='%ad %h %s' --name-only -- \
    src/kospex/extractors/ src/kospex_dependencies.py

# which PR brought a given commit in
git log --merges --oneline --ancestry-path <sha>..origin/main | tail -1
```

Commit subjects in this area are written to be the summary, so that log is more
informative than a list of numbers. What belongs *on this page* is the decision and
its reasoning; the PR reference belongs next to the decision it explains, which is how
the sections above cite them.

# Byte order marks

A UTF-8 BOM is what a Windows editor writes, and it is never content. Whether kospex
may refuse a file carrying one is therefore decided by **the ecosystem that owns the
format**, not by what our reader finds convenient — a raise on a file the real tool
installs happily is a false positive, and a dependency inventory that reports a working
repository as broken is worse than one that is quiet.

Each row below was checked against the actual tool, not inferred:

| format | BOM'd file | the ecosystem | kospex |
|---|---|---|---|
| `package.json` | npm **reads** it — `npm pkg get name` returns the name, `npm ls` resolves the tree | tolerant | **reads it** (`utf-8-sig`) |
| `package-lock.json` | npm **reads** it — `npm ls --package-lock-only` resolves the locked version | tolerant | **reads it** (`utf-8-sig`) |
| `pnpm-lock.yaml` | the YAML spec permits a BOM | tolerant | reads it (PyYAML consumes it) |
| `uv.lock` | uv **reads** it — `uv export --frozen` (uv 0.11.11) exports the locked closure | tolerant | **reads it** (`utf-8-sig`) |
| `*.csproj` | the XML spec permits a BOM | tolerant | reads it (ElementTree consumes it) |
| `pom.xml` | the XML spec permits a BOM; Maven itself not checked | tolerant | reads it (ElementTree consumes it) |
| `requirements.txt` | — | tolerant | reads it, via `detect_encoding` |
| `pyproject.toml` | **pip refuses it** — `pip install .` dies in `pip/_internal/pyproject.py` at `tomllib.loads` | refuses | **raises, naming the BOM** |
| `go.mod` | **Go refuses it** — `golang.org/x/mod/modfile` has no BOM handling, and `ModulePath` tests `bytes.HasPrefix(line, moduleStr)` | refuses | **raises, naming the BOM** |

So the rule is neither "strip every BOM" nor "refuse every BOM".

**Where the format's tooling refuses the file, say *why*.** The `go.mod` case is the
cautionary one: the `module` gate reported *"no `module` directive, so not a go.mod"*
about a file whose first directive is `module`, sending the reader after a defect that
was not there. Both refusals now name the BOM, and the `go.mod` check sits **before**
the `module` gate for that reason — while still letting a file genuinely missing
`module` produce the original message.

**UTF-16 is refused everywhere**, which matches npm: a UTF-16 `package.json` fails with
`EJSONPARSE`. Reading one would report dependencies npm itself cannot see. The single
exception is `requirements.txt`, where `detect_encoding` handles UTF-16 because a real
file on a development estate was UTF-16 and lost its first dependency to it.

**How this was found**, because the method transfers: after the error contract landed,
a sweep of all 374 repositories through the production path listed every file now
reporting `parse_error`. Nine files across six repositories — eight were deliberately
malformed test fixtures committed by the upstream projects, and the ninth was a BOM. The
contract did not cause that bug; it made it visible, because the file used to fail open
as `empty`. **After changing how parsers report failure, read the new failures
one by one** — the list is short and it is where the false positives are.

# Cross-cutting decisions

Rules that apply to every parser, recorded once.

- **Extractors are pure.** No DB, no CLI, no deps.dev enrichment. Both scan paths
  enrich centrally after extraction, which is what lets them share a parser and stay in
  parity.
- **Every extractor emits the same keys**, from a local copy of
  `get_package_template()`'s shape — duplicated rather than imported to keep the
  extractor free of the DB stack, with a contract test
  (`test_template_matches_get_package_template`) failing if the two drift.
  `write_dict_to_csv` takes its header from row 0 alone, so a missing key is a missing
  column.
- **NULL means unknown, not false.** Where a format cannot answer — `is_dev` for a
  `go.mod`, `runs_install_script` for a v9 pnpm lockfile — the column is NULL. "We did
  not look" and "we looked and it is not" are different facts.
- **The declared text is kept.** `package_name` and `package_version` are part of the
  `dependency_data` primary key, so normalising either inserts duplicate rows on
  re-sync instead of updating. Parse with a library; never rename with one.
- **Integrity hashes are dropped** everywhere. SCA does not consume them.
- **Recognition belongs to panopticas, interpretation to the registry.** Panopticas
  owns "what is this file"; the registry owns "what do we do with it" (kind, scanners,
  parser, package_type). Local matchers in `registry.py` are stand-ins for types
  panopticas recognises but exposes no predicate for, and each one is a second
  implementation that can drift — which is exactly how the requirements matcher went
  wrong.
- **`supported` is derived from `scanners`, not from `parse_ref`.**
  `test_unsupported_entries_have_no_parse_ref` asserts
  `bool(e.scanners) == (e.parse_ref is not None)` across the registry, which is what
  keeps a registered parser from being unreachable, or a file from being claimed as
  supported with nothing to parse it.
- **The registry is the right place to drive a contract test from.** The highest-value
  test in the error-contract work resolves *every* entry with a `parse_ref` and asserts
  none returns a value for a directory — unreadable for every format at once. It found
  a fourth fail-open nobody had looked for, and it covers a parser added later on the
  day it is registered.
