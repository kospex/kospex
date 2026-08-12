# kospex

Kospex maps the **knowledge**, **technology** and **maintenance risk** hiding in your git repositories.

It answers questions that are surprisingly hard to answer at scale: *who still knows this
code? what are we actually built on? what's quietly going stale?*

Kospex inspects cloned repositories on disk and aggregates everything into a single
queryable database. There are two ways to use it: a **CLI** for scanning, querying and
automation, and a **Web UI** (`kweb`) for exploring the data interactively.

Inspired by the excellent [Mergestat lite](https://github.com/mergestat/mergestat-lite),
whose database structure we use to model data from git repositories.

For details on changes, see the [changelog](https://github.com/kospex/kospex/blob/main/CHANGELOG.md).

## Installation, setup and usage

See the official [installation documentation](https://docs.kospex.io/getting-started).

## What is a kospex?

We're aiming to [k]now your c[o]de by in[spe]cting the haruspe[x].
From Wikipedia, _The Latin terms haruspex and haruspicina are from an archaic word, hīra = "entrails, intestines"_

So we're going to help look at the "guts of your code" to gain an understanding of the repositories, technology landscape (sprawl?) and developers.
