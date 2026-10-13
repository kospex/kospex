# Dependency flags by package manager

Lockfiles and manifests record more than a name and a version. They say whether a
dependency reaches production, whether it installs at all, whether it executes code
at install time, and sometimes which project in a workspace pulled it in.

Those fields do **not** mean the same thing across package managers, and a few that
look equivalent are not. This page records what each format states, quoted from its
own documentation, so that a parser author and a reader of kospex data can tell what
a value actually asserts.

Why it matters: a flag that is nearly right is worse than one that is absent. A
column populated from a misread field is queryable, plausible and wrong, and
anything built on it — vulnerability triage, malware blast radius, a dependency
count — inherits the error silently.

---

## The short version

| question | npm | pnpm v6 | pnpm v9 | Maven | Cargo | Python | Go |
|---|---|---|---|---|---|---|---|
| reaches production? | `dev` / `devOptional` (v1: `dev`) | `dev` | not recorded | `<scope>` | section | group name | — |
| may not install? | `optional` / `devOptional` | `optional` | `optional` in `snapshots:` | — | `optional` + features | extra name | — |
| executes at install? | `hasInstallScript` (not in v1) | `requiresBuild` | — | — | `[build-dependencies]` | — | — |
| transitive? | path nesting | graph walk | `snapshots:` | transitive per scope | — | — | `// indirect` |

Blanks are genuinely absent, not merely unparsed. Where a format cannot answer,
kospex records NULL rather than false — see [NULL means unknown](#null-means-unknown).

---

## npm — `package-lock.json`

Documentation: [package-lock.json](https://docs.npmjs.com/cli/v10/configuring-npm/package-lock-json)

Flags live on each entry in `packages:` (lockfileVersion 2 and 3).

| field | documented definition |
|---|---|
| `dev` | "If the package is strictly part of the `devDependencies` tree, then `dev` will be true." |
| `optional` | "If it is strictly part of the `optionalDependencies` tree, then `optional` will be set." |
| `devOptional` | "If it is both a `dev` dependency *and* an `optional` dependency of a non-dev dependency, then `devOptional` will be set." |
| `hasInstallScript` | "A flag to indicate that the package has a `preinstall`, `install`, or `postinstall` script." |
| `inBundle` | "A flag to indicate that the package is a bundled dependency." |
| `link` | "A flag to indicate that this is a symbolic link. If this is present, no other fields are specified." |

### `devOptional` is a third state, not the union of two

Note the word **strictly** in the `dev` and `optional` definitions. A package that
is both does *not* get both flags — it gets `devOptional` instead. Counted across
the `package-lock.json` files in one real estate:

```
dev=1 optional=0 devOptional=0   15860
dev=0 optional=0 devOptional=0   10920
dev=1 optional=1 devOptional=0     955
dev=0 optional=1 devOptional=0     741
dev=0 optional=0 devOptional=1     175
```

Five observed states. `devOptional` never co-occurs with either flag, yet
`dev=1 optional=1` is common — so the two are not mutually exclusive in general,
and `devOptional` is not simply shorthand for both.

**Consequence for a parser:** reading only `dev` records "in the production
closure" for those 175 entries, which is wrong — they are dev-only. Two booleans
cannot represent five states.

### Attribution is free in the file, but kospex does not store it

Keys are paths — `node_modules/a/node_modules/b` — so nesting, and therefore which
package pulled a dependency in, is readable directly. 259 of 1285 entries in one
real file are nested; across all 31 files, 2331 entries sit at depth 2 and 47 at
depth 3.

kospex does not record it. `dependency_data`'s primary key is
`(_repo_id, hash, file_path, package_type, package_name, package_version)` with no
install-path column, so the parser reads the paths and then discards them. Storing
the parent is tracked in [#227](https://github.com/kospex/kospex/issues/227), which
owns attribution for every format.

### One package, several paths

Because the key is a path, the same `name@version` can appear many times. Across
the 31 `package-lock.json` files on disk, 14,884 install-path entries hold 13,729
distinct `name@version` — so 1,155 collapse onto a row that already exists, in 479
groups of two or more.

Those entries can disagree, because `dev` and `optional` describe how a package was
*reached*, not what it is:

| flag | duplicate groups that disagree |
|---|---|
| `dev` | 53 |
| `optional` | 48 |
| `inBundle` | 18 |
| `devOptional` | 4 |
| `peer` | 4 |
| `hasInstallScript` | 0 |

`chalk@5.6.2` is a clear case: four entries, three reached from production paths
and one from `npm/`'s dev tree. It plainly ships.

**kospex aggregates rather than letting the last write win**, which would otherwise
pick a value by JSON key order:

- `dev`, `optional`, `devOptional` — true only when **every** path agrees. One
  production path means the package ships; one required path means it installs. The
  same rule pnpm v9 needs for `snapshots:`.
- `hasInstallScript`, `peer`, `inBundle` — true when **any** path says so. These
  describe the package itself, which is consistent with `hasInstallScript` being the
  one flag no duplicate group disagrees on.

`declared_scope` is built from the aggregate, so it cannot contradict the booleans
beside it: the `chalk` row above reads `is_dev=0` with an empty scope, not `dev`.

The effect is not marginal. In one v1 file, 93 entries carry `optional` but only 55
packages are optional by every path — reading the flag per entry would mark 38
packages as "may not install" when a required path installs them.

### lockfileVersion 1 records two of the five flags

**Corrected 2026-10-11.** This section previously claimed v1 "has no flags at all"
and cited a 493-entry file that does not exist. Both were wrong, and the error
mattered: it would have sent a v1 parser to NULL for fields the file answers.
Re-measured by enumerating every key in both v1 files on disk.

v1 has no `packages:` section — only a nested `dependencies` tree, where each
entry's own transitive dependencies are the `dependencies` map inside it. Entry
keys across the two v1 files:

```
lodash            v1  673 entries   version 673 · resolved 673 · integrity 673
                                    dev 673 · requires 312 · optional 93
Dist-DataTables   v1    2 entries   version 2 · resolved 2 · integrity 2 · dev 2
```

So v1 **does** record `dev` and `optional`. What it lacks is `devOptional`,
`hasInstallScript`, `inBundle`, `peer` — and `license`, `engines`, `os`/`cpu` and
every other metadata field v2/v3 carry.

**Consequence for a parser:** v1 can answer "does this reach production" and "may
this fail to install", and must record *unknown* — not *false* — for whether a
package runs an install script. Note also that v1 cannot distinguish the
`devOptional` case at all, so a package that is both dev and optional is
indistinguishable from one that is merely dev.

### What else each entry carries

The flags above are a small part of the file. Measured across all 31
`package-lock.json` files on disk (14,264 `packages:` entries), by key:

| key | entries | example |
|---|---|---|
| `version` | 14221 | `"1.1.1"` |
| `resolved` | 12732 | registry tarball URL |
| `integrity` | 12719 | `sha512-…` |
| `license` | 11507 | `"MIT"` |
| `engines` | 8285 | `{"node": ">=0.4.2"}` |
| `dependencies` | 7434 | the resolved edge list for that entry |
| `dev` | 7414 | `true` |
| `funding` | 4012 | |
| `peerDependencies` | 1155 | |
| `optional` | 1092 | `true` |
| `os` / `cpu` / `libc` | 908 / 905 / 113 | `["darwin"]` / `["arm64"]` / `["glibc"]` |
| `bin` | 778 | `{"cake": "bin/cake"}` |
| `peer` | 271 | |
| `optionalDependencies` | 180 | |
| `inBundle` | 143 | |
| `devOptional` | 71 | |
| `deprecated` | 68 | npm's own deprecation text |
| `hasInstallScript` | 46 | |
| `link` | 13 | workspace link |

**Corrected 2026-10-13.** The table above omitted four keys. Re-enumerated over
the 14,222 *installed* entries (excluding the 29 root and 13 importer entries,
which is the population a parser iterates):

| key | entries | what it means |
|---|---|---|
| `peerDependenciesMeta` | 380 | per-peer `{"optional": true}` |
| **`name`** | **30** | **the real package, when the install path is an alias** |
| `workspaces` | 11 | workspace globs, on an importer |
| `bundleDependencies` | 3 | names bundled into the tarball |

`name` is the one that matters, and it was the omission that caused a bug. See
the next section.

### An install path is not a package name

`npm i foo@npm:bar` installs **bar** at `node_modules/foo`, and npm records the
real package in a `name` field on that entry. The path segment is the alias the
project chose.

Every one of the 30 entries carrying a `name` has one that differs from its path:

```
node_modules/string-width-cjs     ->  string-width@4.2.3
node_modules/strip-ansi-cjs       ->  strip-ansi@6.0.1
node_modules/react-helmet-async   ->  @slorber/react-helmet-async@1.3.0
node_modules/react-loadable       ->  @docusaurus/react-loadable@6.0.0
```

**Consequence for a parser:** taking the name from the path records a package
that does not exist on the registry, so the deps.dev lookup returns nothing and
the row carries no advisories and no `versions_behind` — for a package that is
genuinely installed and may genuinely be vulnerable. A clean result and an
unanswerable one become indistinguishable, which is the failure this whole page
exists to prevent.

**kospex records every identity an entry asserts** — the install path, an
explicit `name`, and the package named in the `resolved` URL — as separate rows,
and treats none of them as authoritative.

That is because a lockfile is untrusted input and **no field of it corroborates
another**: `name` and `resolved` come from the same document, so their agreement
is not evidence. Any rule electing one field is spoofable in whichever direction
it elects. Recording all of them is not: a package cannot be hidden, because the
install path is always recorded, and a genuine alias target is always recorded,
so it is checked for advisories.

They almost always agree — 12,687 of the 12,689 entries carrying a tarball URL —
so this is one row per package in practice. Across the estate it adds 32 rows out
of 13,739.

**What a reader should take from this:** a row whose name is an install-path
alias may not be a real registry package, and will show no advisory data for that
reason. The row beside it, under the real name, is the one that answers the
security question.

What remains unresolvable here: a lockfile that lies consistently across `name`,
`resolved` and `integrity`. Settling that needs the tarball fetched and hashed,
which a parser cannot do.

The alias is still matched when deciding `direct` / `dev`, because a project
declares an aliased dependency *by its alias*.

v1 has no `name` field and encodes the same thing in the version:
`{"foo": {"version": "npm:left-pad@1.2.3"}}`. Stored verbatim that is a package
called `foo` at a version that is not a version.

Two of these are worth more than the install-state flags this page was written
for. `license` is present on 81% of entries, and `deprecated` carries npm's own
notice as text. `dependencies` is the resolved graph per entry, which is what
makes attribution free (above).

Structurally: **v3** has only `packages:`; **v2** carries both `packages:` and the
v1-style `dependencies:` tree for backwards compatibility — one file here has 819
and 638 — so a v2 parser should read `packages:` and ignore the duplicate; **v1**
has only `dependencies:`.

---

## pnpm — `pnpm-lock.yaml`

Documentation: [pnpm/spec](https://github.com/pnpm/spec) — a separate file per
lockfile version
([5.2](https://github.com/pnpm/spec/blob/master/lockfile/5.2.md),
[6.0](https://github.com/pnpm/spec/blob/master/lockfile/6.0.md),
[9.0](https://github.com/pnpm/spec/blob/master/lockfile/9.0.md)).

### Version 6

| field | documented definition |
|---|---|
| `dev` | "If `false` then this dependency is neither a development dependency ONLY of the top level module nor a transitive dependency of one." |
| `optional` | "If true then this dependency is either an optional dependency ONLY of the top level module or a transitive dependency of one." |
| `requiresBuild` | "This field is `true` if the package has lifecycle scripts **or** the package is a native module that needs to be built." |

### `requiresBuild` is **not** npm's `hasInstallScript`

This is the sharpest trap on this page, because the two look interchangeable.

- npm `hasInstallScript` — the package **declares** a `preinstall`, `install` or
  `postinstall` script.
- pnpm `requiresBuild` — lifecycle scripts **or** a native module needing a build.

pnpm's is a strict superset. Observed consequence: in one repository
`@swc/core` is flagged by both, while its ten platform-specific siblings
(`@swc/core-darwin-arm64` and so on) are flagged **only** by pnpm. Those are
prebuilt native artifacts with no scripts — native modules, so `requiresBuild`;
no scripts, so no `hasInstallScript`.

Mapping one onto the other therefore changes the claim. "Executes code at install
time" is `hasInstallScript`; `requiresBuild` answers "needs build work", which is
a different and broader question.

### Version 9 moved the graph and dropped the flags

v9 splits the resolved graph into a separate `snapshots:` section.
`packages:` keeps version-level metadata — `peerDependencies`,
`peerDependenciesMeta`, `engines`, `os`, `cpu`, `libc`, `deprecated`,
`bundledDependencies`, `resolution`, `hasBin` — and `snapshots:` carries
`dependencies`, `optionalDependencies` and `transitivePeerDependencies` per
dependency path.

**Neither `dev` nor `requiresBuild` appears in the v9 `packages:` section**, and the
9.0 spec document does not mention them. Measured across three real lockfiles:

| file | version | entries | `requiresBuild` | `dev` |
|---|---|---|---|---|
| A | 6.0 | 2079 | 50 | 876 |
| B | 9.0 | 306 | 0 | 0 |
| C | 9.0 | 569 | 0 | 0 |

So a parser handling only `packages:` gets rich install-state data from v6 and
**nothing** from v9, silently. v9 is the current format, so that is the common case
rather than the edge case.

#### `snapshots:` does carry `optional`

One of the three survives, in the other section. A `snapshots:` entry can hold an
`optional` key alongside its dependency edges, and the observed key set across two
v9 files is `dependencies`, `optionalDependencies`, `transitivePeerDependencies`
and `optional`:

```yaml
snapshots:

  '@esbuild/darwin-arm64@0.27.7':
    optional: true
```

Snapshot keys use the same `name@version(peer@x)` form as `packages:` keys, so
after stripping the peer suffix they line up 1:1 with the packages entries — 569 of
569 and 306 of 306 in the two files measured. In file C, 208 packages are optional
by this route, which a parser reading only `packages:` records as *not optional*.

Two cautions for anyone implementing it:

- **Match on the normalised key, not the raw one.** Peer suffixes mean a package
  can appear in `snapshots:` under several keys while `packages:` has one entry.
  Exact-key matching found 204 of the 208.
- **A package is optional only if *every* path to it is optional.** Those multiple
  variants can disagree — in file C, one package of 569 is optional on one peer
  path and required on another. Since the question is "may this fail to install",
  one required path settles it: it installs.

`dev` and `requiresBuild` have no equivalent in v9 at all. Dev-ness is recoverable
only by walking the graph from each importer's `devDependencies`, and pnpm tracks
which packages were built outside the lockfile entirely.

### Workspace attribution requires a graph walk

pnpm records each workspace project under `importers:` with its *direct*
dependencies, and (in v6) each package's own `dependencies` map — 1254 of 2079
entries in one real file. Which importer pulled a transitive dependency in is
therefore derived by traversal, not read from a field. Unlike npm, where the key
path gives it away.

Attribution is also **many-to-many**. Walking one real workspace of 7 importers:

| reachable from | packages |
|---|---|
| 1 importer | 639 |
| 2 importers | 628 |
| 3 importers | 335 |
| 5 importers | 1 |

60% are reachable from more than one project, so "which importer pulled this in"
has a set as its answer, not a value.

---

## Maven — `pom.xml`

Documentation: [Introduction to the Dependency
Mechanism](https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html)

Maven uses `<scope>`, which has **six values** and is not reducible to a boolean.

| scope | documented definition | compile cp | runtime cp | transitive |
|---|---|---|---|---|
| `compile` | "the default scope … available in all classpaths … propagated to dependent projects" | yes | yes | yes |
| `provided` | "much like `compile`, but indicates you expect the JDK or a container to provide the dependency at runtime … added to the classpath used for compilation and test, but not the runtime classpath. It is not transitive." | yes | no | no |
| `runtime` | "not required for compilation, but is for execution … in the runtime and test classpaths, but not the compile classpath" | no | yes | yes |
| `test` | "not required for normal use of the application, and is only available for the test compilation and execution phases. This scope is not transitive." | test only | no | no |
| `system` | "required for compilation and execution. However, Maven will not download the dependency … looks for a jar in the local file system" | yes | yes | no |
| `import` | "only supported on a dependency of type `pom` in the `<dependencyManagement>` section … replaced with the effective list of dependencies" | n/a | n/a | n/a |

Only `test` maps to "dev". `provided` is needed to build but not shipped;
`runtime` is shipped but not needed to build — opposite answers that a single
boolean collapses. `import` is not a dependency at all.

Scope values observed across the `pom.xml` files in one real estate: `test` 27,
`import` 4, `provided` 1, `compile` 1.

---

## Cargo — `Cargo.toml` / `Cargo.lock`

Documentation: [Specifying
Dependencies](https://doc.rust-lang.org/cargo/reference/specifying-dependencies.html)

Three sections, and the third has no equivalent elsewhere on this page.

| section | purpose | in the published artifact | transitive to consumers |
|---|---|---|---|
| `[dependencies]` | normal use | yes | yes |
| `[dev-dependencies]` | "compiling tests, examples, and benchmarks" | no | "not propagated to other packages which depend on this package" |
| `[build-dependencies]` | "for use in your build scripts" | not directly | no — "the build script does not have access to regular dependencies" |

`[build-dependencies]` runs on the build machine during a release build. It is not
dev (a release build needs it) and not shipped. Tempting to equate with
"executes at install" — but the mechanism is a `build.rs` compiled separately,
not an install hook, and "A package itself and its build script are built
separately, so their dependencies need not coincide."

Also:

- `optional = true` on a dependency is tied to the `[features]` section — optional
  **under which feature**, so the feature name is part of the answer.
- `[target.'cfg(windows)'.dependencies]` is platform-conditional, which is
  optional-with-a-condition rather than a boolean.

Observed across one real estate: `[dependencies]` 51 files,
`[build-dependencies]` 7, `[dev-dependencies]` 6,
`[target.'cfg(windows)'.dependencies]` 1.

---

## Python — `pyproject.toml`, `requirements.txt`, `uv.lock`

Documentation:
[PEP 621](https://peps.python.org/pep-0621/) ·
[PEP 735](https://peps.python.org/pep-0735/) ·
[Poetry groups](https://python-poetry.org/docs/managing-dependencies/)

Python's extra dependencies are **named groups**, not booleans.

- `[project.optional-dependencies]` (PEP 621) — named extras, installed as
  `pip install pkg[postgres]`
- `[dependency-groups]` (PEP 735) — named groups such as `dev`, `test`, `lint`,
  not published as package metadata
- `[tool.poetry.group.<name>.dependencies]` — Poetry's equivalent

Observed across one real estate: `project.optional-dependencies` in 33 files,
`dependency-groups` in 25, `tool.poetry.group.dev` in 1.

A boolean `is_optional` records that a dependency is optional and discards *which
extra* it belongs to — and the extra name is usually the point, since `[postgres]`
and `[dev]` have very different significance. Plain `requirements.txt` has no group
concept at all; the `-r dev-requirements.txt` convention is a filename, not
metadata.

---

## Go — `go.mod` / `go.sum`

Documentation: [Go Modules Reference](https://go.dev/ref/mod)

`go.mod` marks transitive requirements with a `// indirect` comment and has **no
dev/test distinction** — test dependencies are ordinary requirements. `go.sum`
carries integrity hashes only and declares nothing about install state.

So Go can answer "transitive?" and nothing else on this page. That is a property of
the format, not a gap in parsing.

---

## NULL means unknown

Given the above, kospex distinguishes three states per flag, not two:

| stored | meaning |
|---|---|
| true | the format says so |
| false | the format says otherwise |
| **NULL** | **the format cannot say** |

A `requirements.txt` cannot state whether a package runs an install script. A
`lockfileVersion: 1` `package-lock.json` records no flags. A `go.mod` has no dev
concept. Writing `false` in those cases asserts something the file does not say,
and a reader cannot then tell "we checked and it does not" from "we never knew".

The same distinction is already drawn twice in the kospex schema:
`dependency_data.resolution` for whether deps.dev could resolve a version, and
`last_checked` (migration 0006), whose own comment insists a staleness indicator
"must render NULL as *never checked*, not *checked long ago*".

---

## Consequences for kospex

1. **A boolean pair cannot carry npm's five states.** `devOptional` needs
   representing, or 175 dev-only packages per estate read as production.
2. **`requiresBuild` and `hasInstallScript` are not interchangeable.** Mapping one
   to a shared column changes what the column asserts.
3. **pnpm v9 has no flags in `packages:`.** A parser must read `snapshots:` or
   produce NULL, and v9 is the current format.
4. **Maven, Cargo, and Python are not booleans at all** — six scopes, three
   sections, named groups. Recording the **raw declared value** alongside any
   derived boolean keeps the information that the projection loses, in the same way
   migration 0006 kept `version_operator` next to the normalised `version_kind`.

## Caveats on the measurements

Counts marked "one real estate" come from a single 167-repository development
install and are illustrative of shape, not representative proportions. The quoted
definitions are from the linked documentation; the counts are reproducible by
parsing the files named.

Not verified here: yarn (v1 and Berry), Gradle configurations, NuGet
`PrivateAssets` / `developmentDependency`, and Composer. Each is believed to have
its own vocabulary and none should be assumed to match a table above.
