# npm aliases: look up the package that is installed (#350)

## Overview

npm can install a package under another name:

```json
"dependencies": {
  "react-17": "npm:react@^17",
  "string-width-cjs": "npm:string-width@^4.2.0",
  "eslint-plugin-tailwindcss": "npm:@hasparus/eslint-plugin-tailwindcss@3.17.5"
}
```

The key is the name the project imports. The `npm:` spec names the package that
is installed. kospex looked up the key on deps.dev as if it were the package, and
three things went wrong:

| alias | was | why |
|---|---|---|
| `react-17` | `package_not_found` | No package called `react-17` exists. |
| `string-width-cjs` | `version_yanked` | An unrelated `string-width-cjs` exists on npm, without that version. |
| `eslint-plugin-tailwindcss` → `@hasparus/…` | `resolved` | Checked as the original package, not the fork that is installed. The row looked clean, with the wrong package's advisories. |

The last one is the important one. A fork installed under the original's name is
exactly where a reviewer would want the real package checked.

## What changed

`KospexDependencies.lookup_dependency` unwraps an npm `npm:<name>@<range>` spec
and looks up the target name and its range. Both scan paths use it: `kospex sca`
(`_enrich_dependency_records`) and `krunner osi` (`krunner.enrich_dependency_records`).
The range then goes through the usual npm range handling (#379), so `^17` is
checked at `17.0.0`, the lowest published version it allows.

The row keeps what the manifest says:

- `package_name` stays the alias (`react-17`). It is part of the
  `dependency_data` key, and it is what the project imports. Changing it would
  insert a second row on the next scan.
- `package_version` stays the declared `npm:react@^17`, which names the target.
- `resolved_version` is the version checked (`17.0.0`).

The spec is parsed by `npm_alias_target()` in `kospex/extractors/npmlock.py`. The
v1 lockfile parser already decoded the same syntax in `_v1_identity`, which now
uses the shared helper. The Maven BOM step stays first in `lookup_dependency`.
An npm dependency never has a BOM version, so the two do not interact.

## Results

A/B on a copy of a development DB, re-running the lookup for every current npm
row whose `version_kind` is `alias`:

- 111 rows (91 distinct declarations), all from `package.json`.
- 110 now resolve. They moved from `package_not_found` (69), `version_yanked`
  (25) and `unresolved_spec` (12). 4 were already resolved.
- `classnames-npm: npm:classnames@*` stays `unresolved_spec`, because `*`
  names no version.
- 7 rows now show advisories they were missing, 16 in total: three `sharp`
  aliases (5, 3 and 3), a `loader-utils` alias (2) and two `@babel/core`
  aliases (1 each).
- The `@hasparus` fork is now looked up as the fork. It has no advisories, and
  its `source_repo` points to the original's GitHub repository, which comes from
  the fork's own package metadata.

## Decisions

- **Look up the target and keep the alias as the row's name.** The alias is how
  the project refers to the dependency. The target is what deps.dev can answer
  about.
- **`package.json` only.** The `npm:` spec there is how the project asks for the
  alias, so it is the project's own statement of what to install.
- **Lockfiles are unchanged.** A lockfile entry records every name it gives: the
  install path (`node_modules/string-width-cjs`), `name`, and the package in the
  `resolved` URL. Each is looked up. The `string-width-cjs@4.2.3` row is still a
  false `version_yanked` (also `strip-ansi-cjs` and `wrap-ansi-cjs`). See below
  for why that is left alone for now. Tracked in #405.

## Lockfile aliases: options considered

A lockfile is untrusted input, and nothing in it vouches for its own alias. Its
`name` and `resolved` fields come from the same document. Relabelling the
install-path row as "just an alias" whenever `name` differs would let a tampered
lockfile hide a real signal. When npm takes down a malicious version, the version
disappears and the package often stays (for example as `0.0.1-security`), which
deps.dev reports as exactly the 404 that becomes `version_yanked`.

1. **Ship the `package.json` fix only (chosen).** Lockfile behaviour stays as
   is. The cost is a few known false `version_yanked` rows per affected lockfile,
   documented in `docs/dependency-flags.md`. Nothing that flags attention today is
   relabelled.
2. **Check lockfile aliases against `package.json` (planned next, #405).**
   - Spot the alias: the install path names a different package from the
     entry's `name` or tarball URL.
   - If the sibling `package.json` declares it (`"string-width-cjs":
     "npm:string-width@^4.2.0"`), the alias is confirmed, and the row takes the
     target's result.
   - If it doesn't, the row gets a new resolution, `alias_unverified`. That is an
     attention status, never treated as clean.
   - Both names are still looked up either way, so a real takedown under the
     alias name is still recorded.

   This is design work because enrichment handles one file at a time. It needs a
   step after parsing that sees both files from the same directory, on both scan
   paths. A new resolution value also has to reach `docs/data-schemas.md`, the
   kweb badges, `kospex sca` output and anything that counts unresolved rows.
   Open cases: workspaces and nested `package.json` files, a lockfile with no
   manifest beside it, and `yarn.lock`, whose descriptors carry the alias.
3. **Close it and accept the false positive.** Rejected, because a lockfile entry
   whose names disagree with no manifest alias to explain them is worth knowing
   about, whether it is a mistake or tampering.

## Future: lockfile mismatches as findings

Beyond option 2, a lockfile disagreeing with itself is a signal in its own right.
It could be reported alongside the version flags:

- names that differ, with no manifest alias to explain them
- a version that differs from the version in the tarball URL
- a `resolved` host that is not the registry the project uses
- integrity hashes that look wrong for the entry

It would be a quiet signal. On the development estate, every
`package-lock.json` and `yarn.lock` entry that gives more than one name is a
genuine alias. The extra names add 32 rows in 13,739, and no URL disagrees with
its key. See `docs/dependency-flags.md` and `docs/dependency-details.md`.

## Files changed

- `src/kospex/extractors/npmlock.py`: new public `npm_alias_target()`, used by
  `_v1_identity`.
- `src/kospex_dependencies.py`: `lookup_dependency` unwraps an npm alias before
  the range lookup and writes the alias back as `package_name`.
- `tests/test_npm_alias_lookup.py`: both scan paths, with the real deps.dev
  react fixture. Covers range and pinned aliases, the row keeping its declared
  name and version, an unrelated package under the alias name not being
  consulted, a fork being looked up as the fork, ordinary dependencies being
  unchanged, and the spec parser. With the unwrap disabled, 8 of the behaviour
  tests fail.
- `docs/dependency-flags.md`, `docs/dependency-details.md`, `CHANGELOG.md`.

## Re-scan

Existing alias rows change on the next `kospex sca` or `krunner osi` run of each
repository. No migration is needed: no column changes, and the row key is the same.
