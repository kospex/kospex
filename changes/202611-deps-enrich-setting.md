# Parse dependencies without looking them up

## Overview

`kospex sca` and `krunner osi` do two things with a dependency file: parse it,
then look every package up on deps.dev (and, for `sca`, PyPI). The second step
sends each package name and version to those services. Some machines shouldn't
do that, or should do it only when someone asks. Until now there was no way to
get the parsed inventory without the lookups.

## The change

- **Setting:** `KOSPEX_DEPS_ENRICH`, in the environment or `~/kospex/kospex.env`
  (`HabitatConfig.deps_enrich`). Default `true`. `true/1/yes/on` and
  `false/0/no/off` are accepted; anything else is an error rather than a guess,
  because a typo that silently re-enabled lookups would defeat the point.
  `kospex init`'s template lists it, commented out.
- **Flags:** `-enrich` / `-no-enrich` on `kospex sca` and `krunner osi`
  (including `-next`). The flag beats the environment, which beats
  `kospex.env`. `KospexDependencies.enrich_decision()` resolves it once per
  command.
- **Nothing is contacted when lookups are off.** The skipped calls are
  `lookup_dependency()` (including the version-list fetch for a range), the
  Maven BOM fetch and the PyPI source-repo lookup. Tests block outbound
  connections at the socket and run ten manifest and lockfile formats through
  both commands.
- **`sca -package`** is only a lookup, so it refuses while lookups are off
  unless given `-enrich`.

## Rows

- New resolution value **`not_checked`** (`RESOLUTION_NOT_CHECKED`; plain TEXT,
  no migration). Lookup columns are empty, counts are NULL ("not asked", not
  "none found") and `last_checked` is NULL.
- **Earlier lookups are kept.** If a row with the same key (repo, commit, file,
  package, version) was already looked up, a run with lookups off keeps that
  lookup whole, its date included, so it ages into the usual staleness
  warning. "Looked up" means a `last_checked` date; legacy rows without one
  aren't kept. Rows for a new commit are `not_checked`, because the file may
  have changed. A row is never a mix of the two.
- **Start-of-run report.** With lookups off, `krunner osi` counts the current
  rows in its scope that carry a lookup
  (`KospexQuery.looked_up_dependencies()`) and says so, or confirms there are
  none. On a machine set up with lookups off from the start, it should always
  confirm none.

## Web UI

- `/dependencies/` and `/osi/` label these rows **Not checked**, never "Up to
  date".
- They have no check date, so the existing *No check date recorded* panel lists
  them. A file the latest extraction read, whose rows are all `not_checked`, is
  explained as *Parsed with lookups off*; any other outcome (unreadable, empty,
  removed) keeps its own reason, since it matters more.

## Not covered

- Other routes that call deps.dev, such as kweb's supply-chain page, don't read
  the setting. It applies to `kospex sca` and `krunner osi`.
- The CSV exports from a run describe that run, so a row that kept an earlier
  lookup in the DB is `not_checked` in the CSV.
- Re-enriching rows from the DB, and parsing without lookups during sync or
  pull, are later work. Today a later `krunner osi` with lookups on replaces
  `not_checked` rows.
