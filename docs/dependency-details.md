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
| `pnpm-lock.yaml` | `lockfileVersion` major 5, 6, 9 | 9.0 ×25 (**6 of them two-document**), 6.0 ×2, 5.3 ×1, one 0-byte file |
| `go.mod` | no version field; grammar is stable | 188 files |
| `*.csproj` | SDK-style `PackageReference` | 9 files |
| `requirements.txt` | PEP 508 per line | 43 files |
| `pyproject.toml` | PEP 621 `project.dependencies` / `optional-dependencies` | 131 files |
| `package.json` | the five dependency sections | 11,486 files (mostly vendored copies) |
| `yarn.lock` | **not parsed** — #229 | v1 ×116, Berry `__metadata.version` 10 ×39, 8 ×4, 6 ×1 |
| `uv.lock` | **not parsed** — #229 | `version=1` ×24 (`revision` 1–3) |
| `build.gradle(.kts)` | **not parsed** — #229 | 68 files |
| `pom.xml` | **not parsed** — #229 | 12 files |

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

Seven file types have a parser, and all seven are claimed by both scan paths
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

**Upstream docs:** [package.json](https://docs.npmjs.com/cli/v11/configuring-npm/package-json/)

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
  deps.dev can answer about.
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

---

# Classified, no parser yet

Four types the registry recognises and nothing reads. They report `unsupported` —
honest, and distinguishable from `empty`. Tracked as sub-project D, #229.

Two rules from the parsers above are worth applying from the start rather than
retrofitting: **an unreadable file raises**, and **record every identity the file
asserts**. Prefer over-reporting: in a dependency inventory a false positive is visible
and dismissible, a false negative is not.

## `yarn.lock`

| | |
|---|---|
| registry entry | `yarn-lock`, `Kind.PACKAGE`, `package_type="npm"`, `parse_ref=None` |
| estate | 154 files across 20 repositories — v1 ×116, Berry ×44 |

**Two incompatible formats under one name.** v1 is a custom (non-YAML) format and is
famously flat: no dev/prod flags, so it yields the least per file of any format here.
Berry (v2+) is YAML with a `__metadata.version` field — the estate carries
`__metadata` versions 6, 8 and 10. A parser must detect which it is holding, and
`__metadata` is the discriminator.

By repository *reach* yarn.lock is last of the four despite being first by file count:
its 154 files cluster in 20 repositories (7.7 per repo), so a parser mostly yields more
rows about repositories already covered.

**Upstream docs:** [yarn.lock (v1, classic)](https://classic.yarnpkg.com/lang/en/docs/yarn-lock/)
· [Yarn configuration](https://yarnpkg.com/configuration/yarnrc)

## `uv.lock`

| | |
|---|---|
| registry entry | `uv-lock`, `Kind.PACKAGE`, `package_type="pypi"`, `parse_ref=None` |
| estate | 24 files across 23 repositories — all `version = 1`, `revision` 1–3 |

**The recommended next pick.** 1.0 files per repository, so it reaches more
repositories than any other unread format and the reach is real rather than
concentrated in monorepos.

Note the two-field versioning: `version` is the format version and `revision` is a
finer-grained bump within it. A parser should gate on `version` and tolerate unknown
`revision` values, or it will raise on a file it could actually read.

**Upstream docs:** [uv project layout and the lockfile](https://docs.astral.sh/uv/concepts/projects/layout/)
· [uv resolver internals](https://docs.astral.sh/uv/reference/resolver-internals/)

## `build.gradle` / `build.gradle.kts`

| | |
|---|---|
| registry entry | `gradle-build`, `Kind.PACKAGE`, `package_type="maven"`, `parse_ref=None` |
| estate | 68 files across 11 repositories |

**The hardest of the four, and the one where "parsed it" and "read what it declares"
come apart most.** It is a Groovy or Kotlin DSL, not a data format: dependencies can be
computed, read from a version catalog (`gradle/libs.versions.toml`), or added by a
plugin. A regex over `implementation '...'` lines will read *some* of the truth from
most files and report the rest as absent.

That makes it the format where the registry's `supported` flag is least able to stand
in for coverage, and where a file-by-file check against real projects matters most.

**Upstream docs:** [Declaring dependencies](https://docs.gradle.org/current/userguide/declaring_dependencies.html)

## `pom.xml` — Maven

| | |
|---|---|
| registry entry | `maven-pom`, `Kind.PACKAGE`, `package_type="maven"`, `parse_ref=None` |
| estate | 12 files across 5 repositories |

Gained its registry entry in #221, so it moved from `unclassified` to `unsupported`.

XML, and therefore subject to the same entity-expansion consideration as `.csproj`
(CWE-776) — a `pom.xml` parser should carry the DOCTYPE refusal from the start. Unlike
`.csproj`, `pom.xml` legitimately uses parent POMs and property interpolation
(`${project.version}`), so a declared version may not be a literal.

**Upstream docs:** [POM reference](https://maven.apache.org/pom.html)

---

# Classified, declares no packages

These have registry entries so that kospex can say *what they are* rather than
reporting `unclassified`. None should grow a package parser; a few could grow a
different kind of reader.

## `.python-version`, `.nvmrc` — `Kind.RUNTIME`

A language or toolchain version, not a library dependency. Worth reading eventually as
a *runtime* fact — "this repo pins Python 3.8" is an end-of-life question, not an
advisory question — which is a different table, not `dependency_data`.

**Upstream docs:** [uv Python versions](https://docs.astral.sh/uv/concepts/python-versions/)
· [pyenv](https://github.com/pyenv/pyenv) · [nvm](https://github.com/nvm-sh/nvm)

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

---

# Where these decisions come from

Every rule on this page was argued somewhere, and the PR is where the reasoning and the
measurements live. This is **provenance for the decisions above**, not a changelog —
the parser layer was built by a small, finite set of PRs and that set is worth naming.

| merged | PR | what it established |
|---|---|---|
| 2026-05-05 | #98 | Swallowed a malformed `pyproject.toml` so a batch run could continue (#97). Right at the time, and **reversed by #252** once both scan paths had per-file guards — the one decision on this page that has been undone. |
| 2026-07-20 | #114 | The extractor registry and classifier — one source of truth for what kospex recognises, what kind each file is, and which scanner handles it (sub-project A). |
| 2026-08-10 | #139 | `requirements.txt` parsed with `packaging` (PEP 508) instead of substring-matching operators, and unpinned declarations stopped being dropped. |
| 2026-08-30 | #179 | `go.mod`: read the single-line `require` form, and record `// indirect` so transitive Go dependencies reach the database at all. |
| 2026-08-30 | #185 | `krunner osi` dispatches through the registry (C1), closing the gap where `go.mod` and `.csproj` were handled by one scan path only. |
| 2026-08-30 | #188 | `KospexDependencies.assess` dispatches through the registry too (C2a) — the change that makes a parser contract hold for *both* callers. |
| 2026-09-07 | #191 | The declared operator is kept in `package_version` and `version_operator`, so `flask>=2.0` is no longer indistinguishable from a pin. |
| 2026-10-09 | #228 | pnpm transitive dependencies are looked up by `kospex sca` (#225). |
| 2026-10-10 | #231 | Install state — `is_dev`, `is_optional`, `runs_install_script`, `declared_scope` — and migration `0009` (#227). Also established NULL-means-unknown. |
| 2026-10-12 | #240 | A docs correction: an earlier claim about what `lockfileVersion` 1 records was wrong. Worth noting as precedent — the flags page is a source of truth and gets corrected when it is wrong. |
| 2026-10-13 | #241 | `pom.xml` gains a registry entry (#221), moving it from `unclassified` to `unsupported`. |
| 2026-10-15 | #243 | The `package-lock.json` parser, all three lockfile versions: install paths, flag aggregation across depths, "record every identity, elect none", and "was this fetched at all" rather than registry recognition. |
| 2026-10-16 | #248 | The three hand-written parsers in `kospex_dependencies.py`: the UTF-16 BOM, the PEP 621 array that fabricated packages, and the `package.json` section that must raise rather than be skipped. |
| 2026-10-17 | #252 | The error contract — `UnreadableManifest`, the three categories, the `module` and `<Project>` gates — and the multi-document pnpm lockfile. |

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
| `*.csproj` | the XML spec permits a BOM | tolerant | reads it (ElementTree consumes it) |
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
