# kospex

[![PyPI version](https://img.shields.io/pypi/v/kospex.svg)](https://pypi.org/project/kospex/)
[![Python](https://img.shields.io/pypi/pyversions/kospex.svg)](https://pypi.org/project/kospex/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-docs.kospex.io-blue)](https://docs.kospex.io/)

Kospex maps the **knowledge**, **technology** and **maintenance risk** hiding in your git repositories.

It answers questions that are surprisingly hard to answer at scale:

- *Who still knows this code?*
- *What are we actually built on?*
- *What's quietly going stale?*

Kospex inspects cloned repositories on disk and aggregates everything into a single
queryable database, so you can ask those questions across one repo or thousands.
There are two ways to use it: a **CLI** for scanning, querying and automation, and a
**Web UI** (`kweb`) for exploring the data interactively.

Inspired by the excellent [Mergestat lite](https://github.com/mergestat/mergestat-lite),
whose database structure we use to model data from git repositories.

## Quickstart

Requires Python 3.12+. A virtual environment is optional but strongly recommended.

```bash
# 1. Install
pip install kospex

# 2. Set up ~/kospex (config, database, logs) and ~/code (cloned repos)
kospex init --create --verbose

# 3. Clone a repo — this clones into ~/code/GIT_SERVER/ORG/REPO and syncs it
kgit clone https://github.com/mergestat/mergestat-lite

# 4. Ask some questions
kospex summary
kospex developers -days 90
kospex tech-landscape -metadata

# 5. Explore it all in the browser at http://127.0.0.1:8000
kweb
```

Already have repositories cloned somewhere? Sync the lot in one go:

```bash
kospex sync-directory /path/to/your/repos
```

For complexity metrics and much better file type detection, install the
[scc](https://github.com/boyter/scc) binary (`brew install scc`). It's optional, but recommended.

Full walkthrough: [Getting started](https://docs.kospex.io/getting-started).

## What kospex gives you

### Knowledge mapping

Who knows what, and whether they're still around.

- Active developers (e.g. who's committed in the last 90 days) vs. historical contributors
- Contribution depth at repo, directory and file level
- Derived code ownership — top and most-recent committers who still work here
- Key person and offboarding risk — repos and files with a single active committer

### Technology identification

More than just languages — kospex identifies the whole toolchain from filenames, paths and content:

- **Languages** and file types, with complexity metrics via `scc`
- **Infrastructure as code** — Docker, Terraform and friends
- **Package managers and build tools** — npm/yarn/pnpm, pip/uv, Maven, Gradle, Go modules, RubyGems, Composer, Cargo, NuGet
- **CI/CD pipelines** — GitHub Actions, GitLab CI, Jenkins, Azure DevOps, Bitbucket Pipelines, CircleCI, Buildkite, Travis
- **Linters and config** — eslint, SQLFluff and other quality tooling

The result is a technology landscape you can compare over time — what you build with
now, versus twelve months ago.

### Open source libraries

- Declared dependencies extracted from manifest and lock files across supported ecosystems
- How far behind the current release each dependency is
- Security advisory counts, sourced from [deps.dev](https://deps.dev)

### Maintenance indicators

Signals that code needs attention — or an owner:

- **Orphaned repos** — no active committer still contributing
- **Aging and unmaintained code** — based on last commit activity
- **Out of date libraries** — dependencies well behind current, or with known advisories
- **Complexity hotspots** — files that change often and are hard to change

Longer write-ups are in [use cases](https://docs.kospex.io/use-cases).

## Commands you'll use most

Most commands need data synced into the kospex DB first.

| Command | What it does |
| ------- | ------------ |
| `kospex summary` | Overview of every synced repo — developers, activity and status |
| `kospex developers -days 90` | Developers who've committed recently |
| `kospex tech-landscape -metadata` | Technology stack across everything you've synced |
| `kospex stats REPO_ID` | Developer stats and key person analysis for one repo |
| `kospex key-person PATH/TO/REPO` | Top all-time and top active committers |
| `kospex orphans` | Repos with no still-active committers (experimental) |
| `kospex hotspot -repo PATH/TO/REPO` | Files that change often and are complex |
| `kospex deps -repo PATH/TO/REPO` | Find dependency manifest and lock files |
| `kospex sca` | Lightweight software composition analysis |
| `kospex list-repos -db` | Everything synced into the database (add `-repo_id` for the ID column) |
| `kweb` | Start the Web UI on http://127.0.0.1:8000 |

Most query commands accept `-repo PATH/TO/REPO` for a repo on disk, or `-repo_id` /
`-org_key` / `-server` to query synced data. For example:

```bash
kospex developers -repo_id github.com~kospex~kospex
kospex tech-landscape -repo_id github.com~kospex~kospex
kospex developers -server github.com -days 365
```

Run `kospex COMMAND --help` for the switches on any command, or see the full
[command reference](https://docs.kospex.io/commands).

### Keeping data fresh

```bash
kgit pull --all                  # git pull + re-sync every known clone
kgit pull --check --all          # offline staleness report, no network
kgit pull --org github.com~myorg # or scope to one org, server or repo_id
kospex sync-metadata -repo PATH/TO/REPO
```

Syncing a repo does **not** refresh its dependencies — see
[Refreshing data](https://docs.kospex.io/refreshing-data) for which command updates which table.

## How data is organised

Kospex uses a `GIT_SERVER/ORG/REPO` directory layout for cloned repos:

| Directory | Purpose |
| --------- | ------- |
| `~/kospex/` | Config files, the kospex DB (SQLite3) and logs |
| `~/code/` | Cloned repositories, in a `GIT_SERVER/ORG/REPO` structure |

For example:

```
~/code/
  github.com/
    kospex/kospex
    mergestat/mergestat-lite
  bitbucket.org/
    myorg/myrepo
```

This gives a deterministic way of separating orgs, and different git instances as well
(e.g. an on-premise Bitbucket alongside GitHub.com). Override the defaults with the
`KOSPEX_HOME` and `KOSPEX_CODE` environment variables.

Most tables have a `_repo_id` column in the format `GIT_SERVER~OWNER~REPO`, so
`https://github.com/kospex/kospex` becomes `github.com~kospex~kospex`. Most queries use
`author_email` from git to mean "a developer".

### Describing aging "things"

Many reports describe something as active, aging, stale or unmaintained. That's a simple
calculation from a given date, using these default rules:

| Description  | Rule |
| ------------ | ---- |
| Active       | < 90 days |
| Aging        | > 90 and < 180 days |
| Stale        | > 180 and < 365 days |
| Unmaintained | > 365 days |

It's applied to the last commit in a repo, the last update of a package manager file, or
the release date of a library you depend on. Something labelled "unmaintained" may well be
feature complete — but where there are external dependencies, code usually needs a change
a couple of times a year.

## Design principles

- Precompute data where possible and useful
- Flatten tables, data warehouse style, to enable easier querying and slicing by git server, owner and repo
- Be as agnostic to the git provider (GitHub, Bitbucket, GitLab) as possible for base use cases
- Be mindful that "there is no perfect", only indicators
- Separate cloning and pull updates from the analysis

## Documentation

- [Getting started](https://docs.kospex.io/getting-started) — installation, authentication and your first sync
- [Commands](https://docs.kospex.io/commands) — the full `kospex`, `kgit`, `kweb` and `krunner` reference
- [Web UI guide](https://docs.kospex.io/kweb/) — what each view shows you
- [Use cases](https://docs.kospex.io/use-cases) — the longer write-ups
- [Refreshing data](https://docs.kospex.io/refreshing-data) — which command refreshes which table
- [Troubleshooting](https://docs.kospex.io/troubleshooting)
- [CHANGELOG](CHANGELOG.md)

## Contributing

Bug reports and pull requests are welcome via
[GitHub issues](https://github.com/kospex/kospex/issues).

To work on kospex, clone it and install in editable mode. The `[test]` extra adds
`pytest` and `httpx`, which the test suite needs:

```bash
git clone https://github.com/kospex/kospex
cd kospex
pip install -e ".[test]"
pytest
```

Quote the extra (`".[test]"`) — zsh treats bare brackets as a glob. If you only want to
run kospex rather than develop it, `pip install -e .` is enough.

Frontend assets are built with `npm install && npm run build`.

## What is a kospex?

We're aiming to [k]now your c[o]de by in[spe]cting the haruspe[x].
From Wikipedia, _The Latin terms haruspex and haruspicina are from an archaic word,
hīra = "entrails, intestines"_ — so yes, we do look at the "guts of your code" to
understand your applications, technology landscape (sprawl?) and developers.

## License

[MIT](LICENSE) © Peter Freiberg
