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
| reaches production? | `dev` / `devOptional` | `dev` | not recorded | `<scope>` | section | group name | — |
| may not install? | `optional` / `devOptional` | `optional` | `optional` in `snapshots:` | — | `optional` + features | extra name | — |
| executes at install? | `hasInstallScript` | `requiresBuild` | — | — | `[build-dependencies]` | — | — |
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

### Attribution is free

Keys are paths — `node_modules/a/node_modules/b` — so nesting, and therefore which
package pulled a dependency in, is readable directly. 259 of 1285 entries in one
real file are nested.

### lockfileVersion 1 records none of this

v1 has no `packages:` section, only a nested `dependencies` tree and no flags at
all. One file in a real estate has 493 entries and cannot answer any question on
this page. A parser reading v1 must record *unknown*, not *false*.

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
