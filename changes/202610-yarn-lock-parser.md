# yarn.lock parser

Part of sub-project D, [#229](https://github.com/kospex/kospex/issues/229)
(parsers for the classified-but-unread formats). `build.gradle` and `pom.xml` remain.

## Overview

`yarn.lock` was registered with no parser, so every one reported `unsupported`. It
now has one, `kospex.extractors.yarn:extract_yarn_lock`, claimed by both scan paths
(`kospex sca` and `krunner osi`). It reads both formats that share the name:

- **v1 (Yarn Classic):** a custom, indentation-based format with a
  `# yarn lockfile v1` header.
- **Berry (Yarn 2+):** YAML with `__metadata.version`, read for versions 6, 8 and 10.

Neither format has a spec to parse against. v1 is described, not specified, on the
[Classic `yarn.lock` docs page](https://classic.yarnpkg.com/lang/en/docs/yarn-lock/).
Berry has no format page at all, and the nearest upstream reference is
[Yarn configuration](https://yarnpkg.com/configuration/yarnrc), which covers the
settings, not the lockfile. So the parser was written against measurements of every
real file on a 376-repo development DB (156 files). The full
per-format record is the `yarn.lock` section of `docs/dependency-details.md`: what
raises, what returns `[]`, every decision, and the drift watch.

### Upstream sources checked (2026-10-28)

A search of yarnpkg.com, the Berry repository and third-party parsers found no
published lockfile spec for either format.

- **v1:** the Classic page describes the format by example and gives no grammar. The
  reference is Yarn Classic's own handwritten parser,
  [`@yarnpkg/lockfile`](https://classic.yarnpkg.com/en/package/@yarnpkg/lockfile).
- **Berry:** no yarnpkg.com page covers the format.
  [Getting started Q&A](https://yarnpkg.com/getting-started/qa) only says to commit
  `yarn.lock`, and the docs source in the Berry repository has no lockfile page. The
  version is a constant,
  [`LOCKFILE_VERSION` in `packages/yarnpkg-core/sources/Project.ts`](https://github.com/yarnpkg/berry/blob/master/packages/yarnpkg-core/sources/Project.ts),
  currently `10`. Its comment says to bump it when the fields of a package entry
  change, and no format notes come with it. So 10 is the newest version, and the
  parser accepts it.
- **Third-party parsers disagree** on which Yarn release writes which
  `__metadata.version`
  ([aube-lockfile](https://docs.rs/crate/aube-lockfile/1.22.0/source/src/yarn/mod.rs),
  [@antongolub/lockfile](https://www.npmjs.com/package/@antongolub/lockfile)). That
  is why the accepted versions come from measured files, not from a mapping table.

## Behaviour

- One record per distinct installed `name@version`, `ecosystem="npm"`.
- **`requirements_type` is always `resolved`, and install state is NULL.** Neither
  format states dev or optional per entry. A Berry workspace's `dependencies` merges
  prod and dev (385 devDependencies measured there), so claiming `direct` would be
  wrong for dev tooling.
- **Every identity and version an entry asserts is recorded**, the same rule as the
  `package-lock.json` parser. All 142 multi-identity entries on the estate are npm
  aliases.
- **`patch:` is unwrapped** to its npm version.
- **Local packages are skipped** (`link:`, `file:`, `workspace:`, `portal:`), but
  only when the entry also looks local: no fetched URL in v1, `linkType: soft` in
  Berry.
- **Error contract:** unreadable or uninterpretable files raise
  `UnreadableManifest`. That covers no header of either kind, a v1 line outside the
  measured grammar, an unmeasured Berry version, and an entry without `resolution`.

## Result on the development DB

| | files | outcome | rows |
|---|---|---|---|
| v1 | 111 | all extracted | 49,271 |
| Berry | 44 | 40 extracted, 4 empty (local entries only) | 12,966 |
| neither header | 1 | `parse_error` (a comment-only test fixture) | 0 |

All 156 files in under 5 seconds. No entry had disagreeing versions.

These rows reach the DB only when `krunner osi` next examines each repository. The
outcome for these files changes from `unsupported` to `extracted` / `empty` /
`parse_error`.

## Files changed

- `src/kospex/extractors/yarn.py` - the parser. It reuses npmlock's name, URL and
  version helpers, so the npm-family identity rules stay in one place. A v1 entry
  key is split into descriptors by a linear scanner, not a regex: the first
  version's `[^,"]+\s*(?:,|$)` overlapped on whitespace and backtracked
  quadratically on a long key line (CWE-1333). Across 300,000 random lines and
  all 49,229 real v1 key lines, the scanner accepts and returns exactly what the
  regex did.
- `src/kospex/extractors/registry.py` - `yarn-lock` gets `scanners=("sca", "osi")`
  and a `parse_ref`.
- `tests/test_extractors_yarn.py` - 60 tests, every fixture synthetic.
- `tests/test_osi_dispatch.py`, `tests/test_assess_registry_dispatch.py` - yarn.lock
  added to the per-ecosystem dispatch checks for both scan paths.
- `tests/test_extractor_registry.py`, `test_osi_extract_repo.py`,
  `test_osi_extraction_view.py`, `test_osi_outcomes.py`,
  `test_dependencies_empty_state.py` - these used yarn.lock as the example of an
  unsupported file. They now use `pom.xml` / `build.gradle`.
- `docs/dependency-details.md` - the yarn.lock section moves from "Classified, no
  parser yet" to the supported formats, and the parser count is updated.
- `docs/dependency-flags.md` - yarn is no longer listed as unverified. What its
  lockfile states was measured instead.
- `CLAUDE.md` - parser count.
