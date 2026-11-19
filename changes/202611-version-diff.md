# Version difference by major / minor / patch, and the upgrade path

## Overview

Two ways to ask "how far apart are these two versions?":

- **Offline**: `kospex.dependencies.version_diff`, a pure module that reads only the version numbers.
  - `version_diff(a, b, system)` returns the raw per-component `b - a` as `{"major", "minor", "patch"}`.
  - `change_level(a, b, system)` returns `"major" | "minor" | "patch" | "none" | "downgrade"`.
  - `level_between(a, b)` is `change_level` over tuples `parse_release()` returned.
  - `parse_release(v, system)` returns the release numbers as a tuple, padded to 3.
- **Online**: `KospexDependencies.releases_between(type, name, from, to=None, releases=None)`
  reads deps.dev's version list (the cached `release_list()`, or one the caller passes) and
  counts the releases in `(from, to]` along the **upgrade path**.

Both are shown by:

- **`krunner osi`**: nine columns at the end of the CSV (`BEHIND_FIELDS`):
  `latest_version`, `behind_level`, `behind_major`, `behind_minor`, `behind_patch`,
  `behind_other`, `behind_releases`, `behind_on_major`, `latest_on_major`.
- **`kospex sca FILE`**: `latest` and `behind (M/m/p)` columns (e.g. `major (3/8/4)`), also
  in `-out` and `-json` as `latest_version` and `behind`.
- **`kospex sca -package TYPE:NAME@VERSION`**: the upgrade path to the latest release, and
  how far behind the newest release on the major in use.

None of this is stored: the fields are stripped before `dependency_data` is written
(`_NON_SCHEMA_FIELDS`). Storing them is the package facts tables plan,
`202611-package-facts-tables.md`, which uses these column names.

## Files changed

- `src/kospex/dependencies/__init__.py`, `src/kospex/dependencies/version_diff.py` (new)
- `src/kospex_dependencies.py`: `releases_between()`, `behind_fields()`,
  `empty_behind_fields()`, `BEHIND_FIELDS`, `behind_label()`; `depsdev_record()` fetches the
  package listing once for both `versions_behind` and the behind fields; `version_detail()`
  adds `behind`; the sca table gains `latest_version` and `behind`
- `src/krunner.py`: `enrich_dependency_records()` adds the CSV columns
- `src/kospex_cli.py`: `_upgrade_path_lines()` for `sca -package`
- `tests/test_version_diff.py`, `tests/test_behind_fields.py` (new)

## Usage

```python
from kospex.dependencies.version_diff import version_diff, change_level

version_diff("1.9.9", "3.0.0")   # {"major": 2, "minor": -9, "patch": -9}
change_level("1.9.9", "3.0.0")   # "major"

kd.releases_between("npm", "react", "16.12.0")
# {"from": "16.12.0", "to": "19.3.1", "level": "major",
#  "diff": {"major": 3, "minor": -9, "patch": 1},
#  "major": 3, "minor": 8, "patch": 4, "other": 0,
#  "releases": 44, "versions": [...], "path": [...],
#  "on_major": 3, "latest_on_major": "16.14.0"}
```

```
$ kospex sca -package npm:react@16.12.0
...
16.12.0 is 43 releases behind the latest (19.3.1).
Upgrade path to 19.3.1: a major change, 3 major, 8 minor and 4 patch releases (44 releases in between, counting fixes to older lines).
On its own major: 3 newer releases, up to 16.14.0.
```

`to` defaults to `_latest_release()`: deps.dev's default version, or the highest release by
version when the default is a pre-release or missing. The same rule as `versions_behind` and
`sca -package`'s Latest.

## The upgrade path

`major` / `minor` / `patch` count the releases an upgrade from `from` to `to` passes through,
not every release in between:

- the first release of every minor line (each minor or major step), and
- the patches on the newest minor line of each major.

Patches to a minor line that a newer one superseded are maintenance an upgrade never
installs. react 16.12.0 -> 19.3.1 lists 44 releases. 19 of them are fixes to 19.0.x and
19.1.x, most published from December 2025, after 19.2.0. The path is 3 major (17.0.0, 18.0.0,
19.0.0), 8 minor (16.13, 16.14, 18.1, 18.2, 18.3, 19.1, 19.2, 19.3) and 4 patch (17.0.1,
17.0.2, 18.3.1, 19.3.1). 16.13.1 is not on it: 16.14 superseded 16.13.

Each path release is classified against the one before it. `other` counts a step that moves
only a fourth component (NuGet `1.2.3.1`), so the four counts sum to `len(path)`.
`releases` / `versions` keep every release in range.

`on_major` / `latest_on_major` count every release newer than `from` on its own major, which
answers "is there a non-breaking upgrade?". 0 on a `major` gap means the next step is a major.

`behind_level` is `ahead` when the version in use is above kospex's latest (deps.dev's
default can trail a release); the counts are then empty. A pre-release in use gives every
field empty, as `versions_behind` does.

## Implementation notes

- **The diff is raw.** Each component is subtracted on its own, so a negative minor or patch
  under a positive major is normal. Use `change_level()` for direction and level. It is the
  first component that differs, not the sign of the diff.
- **Only release numbers are compared.** Pre-release and build tags (`-rc.1`, `+build`,
  `.Final`, `-jre`) are ignored, so `1.2.3-rc.1` to `1.2.3` is `"none"`. No semver meaning
  is applied: a 0.x minor bump is not treated as major, and CalVer is compared numerically.
- **Parsing.** PyPI goes through PEP 440 (`packaging`), so epochs, `.postN` and `rc1` are
  handled. Everything else, and any PyPI version that `packaging` refuses, uses the leading
  dotted digits after an optional `v`. A range (`^1.2.3`, `~=1.0`) or a tag (`latest`) gives
  `None`, so resolve a range to a version first. The regex has a separator before each
  repetition, which keeps it linear (CWE-1333).
- **`releases_between` orders by version; `versions_behind` by publish date.** They differ
  when a package backports. For react 16.12.0 they give 44 and 43: `versions_behind` counts
  15.7.0 and 0.14.10 (published in 2020 but below 16.12.0), and misses 19.0.9, 19.1.10 and
  19.2.9, published after the default. `versions_behind` is unchanged and still stored.
- **One package listing per lookup.** `depsdev_record()` fetches `release_list()` once and
  passes it to both `get_versions_behind()` and `behind_fields()`. A failed fetch is not
  passed on, because `get_versions_behind(releases=None)` would fetch again.
- **CSV shape.** `write_dict_to_csv` takes its header from row 0 and `DictWriter` raises on
  a later row with extra keys, so every row gets all nine fields (None when not measured,
  including `-no-enrich`). They are added last, so the CSV's existing columns keep their
  positions.
- Neither bound has to be in deps.dev's list, because the bounds are compared by number.
  Maven flavours (`-jre` / `-android`) count only their own flavour, as `versions_behind` does.
