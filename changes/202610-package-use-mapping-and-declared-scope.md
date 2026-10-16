# One package_use mapping, and declared_scope for the manifest parsers

## Overview

`package_use` is the column that answers "how does this repository use this
dependency" — direct, dev, transitive, peer, optional. Two scan paths write it,
and they each held their own copy of the `requirements_type -> package_use`
mapping. The copies agreed on every value they both had a key for, and disagreed
on the one case that mattered: a `requirements_type` neither of them mapped.

`parse_package_json` emits `"peer"` and `"optional"`. `parse_pyproject_file`
emits `"optional"`. Neither table had a key for either, so the **fallback**
decided what got stored:

| path | fallback | stored for a peerDependency |
|---|---|---|
| `KospexDependencies._enrich_dependency_records` (`sca`) | `PACKAGE_USE_DIRECT` | `direct` |
| `krunner.enrich_dependency_records` (`osi`) | `""` | `''` |

So `kospex sca` recorded a peer dependency as **`direct`** — an assertion the
manifest never made, since a `peerDependency` is declared as supplied by the
consumer and explicitly *not* installed by the package declaring it — while
`krunner osi` recorded the same row as empty. Measured on a populated database:
**497 rows** carried `package_use = ''`, 185 from `package.json` and 312 from
`pyproject.toml`.

`PACKAGE_USE_PEER` and `PACKAGE_USE_OPTIONAL` were defined, exported in
`PACKAGE_USE_VALUES`, and unreachable — constants describing a distinction the
database could not hold.

Separately, `parse_pyproject_file` **read the extras group name and discarded
it**: `for group, deps in optional.items()` never used `group`. Across a real
estate that collapsed 129 distinct group names over 432 declared dependencies
into one undifferentiated value.

## Files changed

- `src/kospex_schema.py` — `REQ_TO_PACKAGE_USE` (the single mapping, now
  including `peer` and `optional`) and `PACKAGE_USE_UNKNOWN = None`.
- `src/kospex_dependencies.py` — `_REQ_TO_USE` is now the shared object;
  fallback is `PACKAGE_USE_UNKNOWN`; `NPM_SECTION_TO_REQ_TYPE` hoisted to a
  module constant; `declared_scope` populated by `parse_package_json` and
  `parse_pyproject_file`.
- `src/krunner.py` — `_REQ_TO_USE` is the same shared object; fallback is
  `PACKAGE_USE_UNKNOWN`.
- `tests/test_requirements_type_mapping.py` — new, 20 tests.
- `tests/test_declared_scope_manifests.py` — new, 12 tests.

## The four changes

### 1. One table, not two mirrored copies

`KospexSchema.REQ_TO_PACKAGE_USE` is imported by both paths. Not copied —
the same object, which `test_both_scan_paths_use_the_same_object` asserts with
`is` rather than `==`, because equality is satisfied by two copies that happen
to agree today. That was the previous state, and the old comment in `krunner.py`
("Mirrors `KospexDependencies._REQ_TO_USE`") was a request that two structures
stay in step, which is not a mechanism.

`peer` and `optional` are now mapped, making both constants reachable.

### 2. An unmapped value stores NULL

Both fallbacks are now `PACKAGE_USE_UNKNOWN`, which is `None`. This follows the
rule migration `0009` already set for the install-state columns: **NULL means
unknown, not false.** The two previous fallbacks were both assertions — `direct`
claimed the manifest said something it did not, and `""` is indistinguishable
from a parser that produced no value at all, which is precisely how this went
unnoticed in 497 rows.

Not a raise. The mapping runs inside `enrich_dependency_records`, which is
**outside** the per-file guard that converts a parser exception into a
`parse_error` outcome, so raising there would end the whole batch rather than
mark one file.

### 3. A contract test that fails when a parser invents a value

`tests/test_requirements_type_mapping.py` is the guard, because the original
defect was not a wrong mapping but a *missing* one — and nothing failed while it
was missing. It checks three complementary things:

- **An AST scan** of every parser source for string literals assigned to
  `requirements_type`, asserting each is a key in the mapping. Parsed with `ast`
  rather than grepped so prose is not mistaken for code — `npmlock.py` *names*
  "peer" and "optional" in a comment explaining why it deliberately emits
  neither, and a grep-based check would find them there and prove nothing.
  `test_npmlock_does_not_emit_peer_or_optional` pins that distinction.
- **The npm section table directly** — `NPM_SECTION_TO_REQ_TYPE` is a module
  constant so the test can assert over the structure the parser iterates. This
  exists because hoisting that loop turned its values into loop variables, which
  no source scan can see; an AST scan alone would have silently stopped covering
  npm. The scan and the structural check each catch what the other misses.
- **A hardcoded vocabulary list**, so a scan that silently returned an empty set
  could not pass both it and `test_scan_actually_finds_the_values`.

### 4. declared_scope on both manifest parsers

The raw section name, in the file format's own vocabulary:

| manifest | `declared_scope` |
|---|---|
| `package.json` | `dependencies`, `devDependencies`, `peerDependencies`, `optionalDependencies` |
| `pyproject.toml` | `dependencies`, or the extras group name (`dev`, `test`, `socks`, …) |

Migration `0009` created the column for this: *"declared_scope holds the RAW
string the file used, verbatim, in that format's own vocabulary... This column
exists because every error in building the booleans happened at write time,
where it is unrecoverable."*

**What is deliberately not done: inferring dev-ness from the group name.** 29%
of the extras dependencies measured sit in a group named `dev`, `test`, `docs`
or `lint` — and kospex has `PACKAGE_USE_DEV`. Promoting them would be wrong
twice over. PEP 621 extras mean "installed only when requested", which is not
npm's `devDependencies`; and the judgement calls (`check`, `cover`, `enabler`, `type`)
would be guesses. Recognition-based gating is the pattern this codebase has
rejected elsewhere. The name is stored as evidence, so classifying it later is a
query rather than a re-scan.

### A fail-open introduced and removed during this change

`parse_package_json` was restructured from four `if "X" in data:` blocks into
one loop over `NPM_SECTION_TO_REQ_TYPE`. The first version of that loop carried
an `isinstance(declared, dict): continue` guard — added as defensive hygiene,
and a regression:

| `package.json` | before | with the guard | now |
|---|---|---|---|
| `"dependencies": ["lodash"]` | raises | **0 records, silent** | raises |
| `"dependencies": "lodash"` | raises | **0 records, silent** | raises |
| `"dependencies": null` | raises | **0 records, silent** | raises |

`parse_package_json` deliberately has no try/except: a malformed file raises,
both scan paths convert that to a `parse_error` outcome, and the file is reported
as unread. Skipping a malformed section instead reports the file as read with
fewer dependencies than it declares — and a file whose every section is
malformed as `empty`, "examined, nothing to find". The guard is gone, and
`TestMalformedSectionStillFailsClosed` pins the property so the same
plausible-looking refactor fails next time.

### And one pre-existing fail-open fixed

Found while checking the above. The top-level document was never validated, and
failed open inconsistently: a `package.json` containing a JSON **array or
string** returned zero records — `"dependencies" in ["x"]` is a legitimate
membership test that answers `False` — while a **number** raised `TypeError`.
All three are valid JSON, so `json.load` cannot catch them, and npm rejects such
a file outright. Zero records meant "this repository declares no npm
dependencies" for a file kospex could not read. It now raises `ValueError`.

## Verification

Full suite: **1379 passed, 8 skipped, 1 xfailed** with a live `kweb`, so the 67
web tests ran rather than skipping.

Run against real manifests in a development estate:

```
~/code/github.com/vercel/next.js/packages/next/package.json  (238 records)
  req_type   declared_scope         package_use   n
  dev        devDependencies        dev           225
  direct     dependencies           direct          6
  peer       peerDependencies       peer            6     <- was '' / direct
  optional   optionalDependencies   optional        1     <- was '' / direct

~/code/github.com/magicstack/uvloop/pyproject.toml  (12 records)
  optional   test                   optional        6
  optional   dev                    optional        3     <- all 12 were
  optional   docs                   optional        3        identical rows
```

## Notes

`package_use` is currently **written by two paths and read by none** — no
template, no query in `kospex_query.py`, no SQL. This is a correctness fix to
stored data, not a change to anything a user sees today. Existing rows keep
their old values until the estate is re-scanned.

### Found while auditing, not fixed here

The three parsers in `kospex_dependencies.py` —
`parse_pip_requirements_file`, `parse_pyproject_file`, `parse_package_json` —
are the last ones still living as methods there rather than in the
`kospex.extractors` package, and they predate both `_REQ_TO_USE` and migration
`0009`. That is the shape of this defect: a parser registered before a
convention existed, never reconciled with it. Also observed, each needing its
own decision:

- **`[dependency-groups]` (PEP 735) is not read.** 39 of 151 `pyproject.toml`
  files in the measured estate use it, declaring **763 dependencies** that reach
  no scanner. The largest single gap found.
- **`[tool.poetry]` is not read** — 4 files, 50 dependencies. A Poetry-only
  `pyproject.toml` has no `[project]` table, so the parser returns `[]`, which
  reports as `empty` ("examined, nothing to find") rather than as unread.
- **`[build-system].requires` is not read** — 128 files. Probably correct (build
  backends are not application dependencies), but it should be a recorded
  decision rather than an omission.
- **`-r` / `-c` includes in `requirements.txt` are not followed** — 4 in the
  estate. A file that only includes others yields nothing.
- **A dev-named requirements file is not treated as dev** — 16 files named
  `requirements-dev.txt` and similar record every row as `direct`.
- **28 `package.json` files declare a package in two sections at the same
  version**, which collides on the `dependency_data` primary key
  `(_repo_id, hash, file_path, package_type, package_name, package_version)`.
  One row survives, and which `package_use` it carries is decided by section
  iteration order — so `@seald-io/nedb`, declared in both `dependencies` and
  `optionalDependencies`, records as `optional` and loses the fact that it is
  also a production dependency. A further 186 declare one at differing versions,
  which yields two legitimate rows.

One thing checked and found *not* to be a defect: `parse_pip_requirements_file`
skips only column-0 `#` comments, so 1,886 indented comment lines in the estate
reach `parse_pypi_package_declaration` — which rejects them via
`InvalidRequirement` and returns `None`. No bogus rows result.
