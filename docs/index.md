# kospex

Kospex is a CLI which aims to _"look at the guts of your code"_ to help gain insights into your developers and technology landscape.
It uses database structure from the excellent [Mergestat lite](https://github.com/mergestat/mergestat-lite) to model data from git repositories. 

## Getting started

Follow our guide on [Installation and setup](getting-started)

Also check out the [list of commands](commands) that are part of the kospex toolkit.

## Key Use Cases and features

 - Identify technology landscape
 - Identify active developers (e.g. who's had their code committer in the last 90 days)
 - Identify key person or offboarding risk
 - Identify potential complexity challenges (or conceptual integrity concerns)
 - Aggregate repo metadata into a single database for easier and faster querying

## General description of aging "things"

Many reports or commands generate a description of active, aging, stale or unmaintained. This description is a simple calculation based on give date using the following default rules.

| Description   | Rule |
| -----------   | ---- |
| Acitve        | < 90 days |
| Aging         | > 90 and < 180 days |
| Stale         | > 180 and < 365 days |
| Unmaintained  | >  365 days |

There are several places where this description may be used:
 - On the last commit in a repo, to say it looks like it's "aging" 
 - On a last updated date of package manager file, where we'd expect them to be updated monthly to quarterly
 - On the release date of a package or libraries we're using. 

It's possibly that something labelled "unmaintained" is feature complete and doesn't require changes. However, generally there are any external dependencies, a code or library usually needs a change a couple of times a year.

## What is a kospex?

We're aiming to [k]now your c[o]de by in[spe]cting the haruspe[x].
From Wikipedia, _The Latin terms haruspex and haruspicina are from an archaic word, hīra = "entrails, intestines"_

So we're going to help look at the "guts of your code" to gain an understanding of the applications, technology landscape (sprawl?) and developers.