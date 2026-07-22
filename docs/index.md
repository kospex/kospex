# kospex

Kospex maps the **knowledge**, **technology** and **maintenance risk** hiding in your git repositories.

It answers questions that are surprisingly hard to answer at scale:
*Who still knows this code? What are we actually built on? What's quietly going stale?*

Kospex works by inspecting cloned repositories and aggregating everything into a single
queryable database, so you can ask questions across one repo or thousands.

There are two ways to use it:
- a **CLI** for scanning, querying, reporting and automation
- a **Web UI** (`kweb`) for navigating the data and insights interactively

It uses the database structure from the excellent [Mergestat lite](https://github.com/mergestat/mergestat-lite)
to model data from git repositories.

## Getting started

Follow our guide on [Installation and setup](getting-started).

Also check out the [list of commands](commands) that are part of the kospex toolkit,
and the [Web UI guide](kweb/) for exploring the data.

If you are after some generally useful git commands, take a look at [Useful Git commands](useful-git-commands).

## What kospex gives you

### Knowledge mapping

Who knows what, and whether they're still around.

- Active developers (e.g. who's committed in the last 90 days) vs. historical contributors
- Contribution depth at repo, directory and file level
- Derived code ownership — top and most-recent committers who still work here
- Collaboration patterns between developers and repositories

### Technology identification

More than just languages — kospex identifies the whole toolchain from filenames, paths and content:

- **Languages** and file types (with complexity metrics via `scc`)
- **Infrastructure as code** — Docker, Terraform and friends
- **Package managers and build tools** — npm/yarn/pnpm, pip/uv, Maven, Gradle, Go modules, RubyGems, Composer, Cargo, NuGet
- **CI/CD pipelines** — GitHub Actions, GitLab CI, Jenkins, Azure DevOps, Bitbucket Pipelines, CircleCI, Buildkite, Travis
- **Linters and config** — eslint, SQLFluff, and other quality tooling

This gives you a technology landscape you can compare over time — what you're building
with now, versus twelve months ago.

### Open source libraries

- Declared dependencies extracted from manifest and lock files across supported ecosystems
- How far behind the current release each dependency is
- Security advisory counts, sourced from [deps.dev](https://deps.dev)

### Maintenance indicators

Signals that code needs attention — or an owner:

- **Orphaned repos** — no active committer still contributing (`kospex orphans`)
- **Key person risk** — files and repos with a single active committer, for offboarding
  and knowledge-transfer planning
- **Aging and unmaintained code** — based on last commit activity
- **Out of date libraries** — dependencies well behind current, or with known advisories
- **Complexity hotspots** — files that change often and are hard to change

Longer write-ups are in [use cases](use-cases).

If some of your repos run off a non-default branch, see [Scanning a non-default branch](branch-aware-sync).

## General description of aging "things"

Many reports and commands describe something as active, aging, stale or unmaintained.
This is a simple calculation based on a given date, using the following default rules.

| Description   | Rule |
| -----------   | ---- |
| Active        | < 90 days |
| Aging         | > 90 and < 180 days |
| Stale         | > 180 and < 365 days |
| Unmaintained  | > 365 days |

There are several places this description may be used:
- On the last commit in a repo, to say it looks like it's "aging"
- On the last updated date of a package manager file, where we'd expect updates monthly to quarterly
- On the release date of a package or library we're using

It's possible that something labelled "unmaintained" is feature complete and doesn't
require changes. However, where there are external dependencies, code or a library
usually needs a change a couple of times a year.

## Thoughts and articles

["What are orphaned repos?"](https://kospex.io/articles/orphaned-repos/) — some basics of knowledge loss and the challenges.

["Are security vulnerabilities an indicator of development testing practices?"](https://kospex.io/articles/vulnerabilities-testing-indicator/)

## What is a kospex?

We're aiming to [k]now your c[o]de by in[spe]cting the haruspe[x].
From Wikipedia, _The Latin terms haruspex and haruspicina are from an archaic word, hīra = "entrails, intestines"_ —
so yes, we do look at the "guts of your code" to understand your applications,
technology landscape (sprawl?) and developers.
