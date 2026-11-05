# Runtime versions declared in repository files

Planning notes for [#351](https://github.com/kospex/kospex/issues/351). Nothing is
implemented yet. The untagged version files are raised in panopticas as
[kospex/panopticas#109](https://github.com/kospex/panopticas/issues/109).

## Overview

kospex records package dependencies, but not which language runtimes a repository
targets: Node, Python, Go, Java, Ruby, Rust, .NET and so on. The goal is to record each
declared runtime version per repository, and what kind of declaration it is, so that
questions like "which repositories target an end-of-life Node or Python?" can be
answered.

The main finding from measuring is that **dedicated version files are rare, and most
runtime versions are declared inside files kospex already reads for other reasons**:
`package.json`, `go.mod`, `pyproject.toml`, CI workflows and Dockerfiles.

## What kospex has today

- The extractor registry has two `Kind.RUNTIME` entries, `.python-version` and
  `.nvmrc` (`src/kospex/extractors/registry.py`). They are classified so `/osi/` can
  name them, but nothing reads them, and they show as having no parser.
- panopticas 0.0.22 also tags `.ruby-version`, `rust-toolchain` /
  `rust-toolchain.toml`, `.mill-version` and `.mill-jvm-version`. It tags the
  runtime pins `dependencies`, which is why they reach the dependency views at all.
- Nothing reads `engines`, the `go` directive, `requires-python` or any of the other
  embedded declarations below. The only mention of `engines` in the code is a comment
  in the pnpm parser listing fields it ignores.

## Measured 2026-11-05

Every file listed in `file_metadata` on a development DB of public repositories,
`node_modules/` excluded, read from its working copy. These count **presence** of a
declaration, matched by regex or a JSON/TOML key. No values were parsed or validated,
so a count says how many files declare something, not what they declare.

### Dedicated version files

| file | files | repos | panopticas 0.0.22 |
|---|---|---|---|
| `mise.toml` | 28 | 3 | not tagged |
| `.nvmrc` | 27 | 21 | tagged, registry `RUNTIME` |
| `.python-version` | 7 | 7 | tagged, registry `RUNTIME` |
| `rust-toolchain.toml` | 5 | 3 | tagged |
| `.java-version` | 5 | 5 | not tagged |
| `.bazelversion` | 4 | 3 | not tagged |
| `.node-version` | 2 | 2 | not tagged |
| `global.json` | 1 | 1 | not tagged |
| `.ruby-version` | 1 | 1 | tagged |
| `.go-version` | 1 | 1 | not tagged |
| `.mill-version`, `.mill-jvm-version` | 1 each | 1 | tagged |

Also looked for and not found: `.tool-versions`, `.mise.toml`, `.sdkmanrc`,
`.terraform-version`, `.bun-version`, `runtime.txt`, `rust-toolchain` (no extension).

### Declarations inside other files

| source | files | repos |
|---|---|---|
| `package.json` `engines.node` | 557 | 110 |
| GitHub workflow `actions/setup-node` with `node-version` | 271 | 91 |
| GitHub workflow `actions/setup-python` with `python-version` | 227 | 87 |
| `go.mod` `go` directive | 186 | 112 |
| GitHub workflow `actions/setup-go` with `go-version` | 113 | 77 |
| Gradle Java toolchain / compatibility (`languageVersion`, `sourceCompatibility`, `jvmTarget`) | 99 | 15 |
| `pyproject.toml` `project.requires-python` | 92 | 74 |
| GitHub workflow `actions/setup-java` with `java-version` | 63 | 17 |
| Dockerfile `FROM <runtime image>:<tag>` (node, python, golang, ruby, openjdk, temurin, corretto, php, rust, dotnet) | 41 | 19 |
| `package.json` `packageManager` | 38 | 25 |
| workflow `setup-node` / `setup-go` / `setup-python` with no explicit version | 16 / 11 / 3 | 6 / 4 / 3 |
| `Cargo.toml` `rust-version` | 7 | 7 |
| `package.json` `engines.npm` | 6 | 6 |
| `package.json` `devEngines.runtime` / `.node` / `.packageManager` | 6 / 4 / 2 | 2 / 2 / 2 |
| `*.csproj` / `*.fsproj` `TargetFramework(s)` | 6 | 3 |
| `pom.xml` `maven.compiler.*` or `java.version` | 6 | 4 |
| workflow `ruby/setup-ruby` with `ruby-version` | 6 | 4 |
| `go.mod` `toolchain` directive | 3 | 3 |
| `package.json` `engines.yarn` / `engines.pnpm` | 3 / 2 | 3 / 2 |
| `pyproject.toml` `tool.poetry.dependencies.python` | 2 | 2 |
| `composer.json` `require.php` | 2 | 2 |
| `package.json` `volta` | 1 | 1 |
| `Gemfile` `ruby` directive | 1 | 1 |

Files read: `package.json` 11,395, `build.gradle(.kts)` 1,491, GitHub workflows 1,363,
Dockerfiles 250, `go.mod` 188, `pyproject.toml` 131, `Cargo.toml` 126, Gemfiles 15,
`pom.xml` 13, `.csproj`/`.fsproj` 9, `composer.json` 5.

`engines.vscode` (9 files) is a VS Code extension's host version, not a runtime, and is
excluded by design.

## What the sources mean

They are not interchangeable, and a report that mixes them is wrong. A `>=18` range
and a pinned `18.12.1` answer different questions.

| kind | meaning | sources |
|---|---|---|
| **pinned** | the exact version developers use | `.nvmrc`, `.node-version`, `.python-version`, `.java-version`, `.go-version`, `rust-toolchain(.toml)`, `mise.toml`, `.tool-versions`, `global.json` |
| **supported range** | what the project claims to run on | `engines.*`, `requires-python`, `rust-version`, `composer.json` `php`, Gemfile `ruby` |
| **minimum** | the lowest language version the module builds with | `go.mod` `go` (since Go 1.21 also a requirement); `toolchain` names the preferred toolchain |
| **tested** | versions CI runs against, often a matrix | workflow `setup-*` steps |
| **deployed** | the runtime the shipped artefact runs on | Dockerfile `FROM` |
| **build target** | the language level compiled for | Gradle toolchain / compatibility, `pom.xml` compiler properties, `TargetFramework` |

Two chains to handle rather than double-count:

- `setup-node` and `setup-python` can read their version from a file
  (`node-version-file: .nvmrc`). The workflows counted as "no explicit version"
  above either do that or rely on the runner default. Which, was not measured.
- `mise.toml` and `.tool-versions` list several tools in one file, so one file can
  yield several runtime records.

## Open questions

1. **First sources.** Dedicated version files, `package.json` `engines.node`,
   `go.mod` and `pyproject.toml` together cover most repositories, and each is a
   simple read. CI workflows (matrices, expressions, version files) and Dockerfiles
   (multi-stage builds, `ARG`-driven tags) are harder, and could follow.
2. **Storage.** A runtime is not a package, so `dependency_data` is probably the
   wrong table: it is keyed on a package name and version for deps.dev lookups. A
   separate table (repo, file, runtime, declared version, kind) is the likely shape,
   which means a migration.
3. **End-of-life data.** deps.dev does not cover runtimes. An external source would be
   needed, chosen separately. Ranges need care: `>=18` includes supported versions
   even if 18 itself is end of life.
4. **panopticas tagging.** The untagged files above are raised in
   kospex/panopticas#109, pending research into each format. The existing runtime pins
   are tagged `dependencies`. If panopticas adds a separate runtime tag, kospex's
   dependency views would stop listing them as files with no parser.
5. **The registry's `Kind.RUNTIME`.** Today it exists so `/osi/` can name these files.
   Whether runtime reading goes through the extractor registry or a separate pass is
   part of the design.

## Method

Survey script run against the development DB's working copies, reading each file
listed in `file_metadata` with `latest=1`. JSON and TOML files were parsed for key
presence. `go.mod`, Gemfiles, Gradle, `pom.xml`, `.csproj`, workflows and Dockerfiles
were matched by regex. A regex match can be inside a comment, so those rows are upper
bounds.
