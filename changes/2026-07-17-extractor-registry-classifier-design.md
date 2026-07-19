# Extractor registry + `manifest_support()` classifier — design

**Date:** 2026-07-17
**Status:** Approved (design) — ready for implementation plan
**Sub-project:** A of a 4-part program (A registry+classifier · B `/osi/` display · C scanner parity · D new extractors)

## Motivation

`/osi/` lists the dependency files kospex has discovered (769 in the reference
DB) but says nothing about whether kospex can actually *scan* each one. A DB
audit found that of those files:

- ~76% are types with a working parser (`package.json`, `pyproject.toml`,
  `requirements*.txt`, `pnpm-lock.yaml`).
- ~13% are real dependency manifests kospex has **no parser for yet**
  (`yarn.lock` 49, `uv.lock` 22, `package-lock.json` 14, `build.gradle` 11,
  `go.sum` 2).
- ~11% are files panopticas tags `|dependencies|` that are a **different kind
  of dependency** — a runtime version (`.python-version`, `.nvmrc`), a
  container base image + build installs (`Dockerfile`), or scan *config*
  (`dependabot.yml`) — not package manifests.

There is also no single definition of "supported": `kospex sca` (via
`assess()`) and `krunner osi` dispatch on different, hand-rolled filename
checks and disagree (e.g. `go.mod` and `*.csproj` are handled by `sca` but
silently skipped by `krunner osi`; `requirements.in` vs `.txt` differ). The one
ecosystem they agree on — pnpm — is the one already extracted into
`src/kospex/extractors/pnpm.py` and shared. Parity is a property you get by
*sharing the extractor*, not by coincidence.

This sub-project builds the **single source of truth** that later work reads
from: a registry of the dependency-bearing file types kospex recognises, each
tagged with its *kind* and current *support state*, plus a pure classifier over
filenames. It is **purely additive** — one new module and its tests. No parser
moves, no scanner/CLI/DB/UI behaviour changes.

## Goal & scope

**In scope (A):**

- `src/kospex/extractors/registry.py`: a `Kind` enum, an `Extractor`
  descriptor dataclass, a `REGISTRY` catalog, and a `classify(filename)`
  function returning `(kind, supported, scanners, extractor)`.
- Tests: classifier truth-table over real filenames, matcher purity/totality,
  matcher mutual-exclusivity, the scanner-coverage matrix, and parse-ref
  resolvability.

**Out of scope (later sub-projects):**

- **B** — the `/osi/` display that consumes `classify()` (badges + commentary
  grouped by kind).
- **C** — routing `assess()` and `krunner osi` dispatch through the registry
  (this is what closes the `go.mod`/nuget krunner gap) and migrating parsers
  into `extractors/`.
- **D** — writing *new* parsers (`package-lock.json`, `yarn.lock`, container,
  runtime).
- GitHub Actions — a **separate interface** (does not exist yet); this registry
  does not own actions. See Deferred N4.

A registers real extractors only for `package`-kind types that already have a
parser. Every other kind is cataloged as *recognised-but-unsupported* so the
classifier and `/osi/` can name it honestly.

## Model

### Dependency *kinds*

"Dependency" is broader than "package". A single `Extractor` descriptor carries
a `kind` discriminator; the heterogeneous *record shape* each kind produces
lives in the parse function, exactly as `workflows.py` (action records) and
`pnpm.py` (package records) already differ. We do **not** use a class per kind —
that buys type-safety we don't consume and fights the "one module returns its
own dict shape" convention. Promote a kind to its own class only if it ever
needs structured metadata a plain row can't hold (YAGNI).

| `kind` | Declares | Files (A's catalog) |
|---|---|---|
| `PACKAGE` | named library deps | **supported:** `requirements*.txt/.in`, `pyproject.toml`, `package.json`, `pnpm-lock.yaml` · **sca-only (gap→C):** `go.mod`, `*.csproj` · **unsupported:** `yarn.lock`, `uv.lock`, `package-lock.json`, `build.gradle` |
| `RUNTIME` | a language/toolchain **version** | `.python-version`, `.nvmrc` |
| `CONTAINER` | base **image** + build-time installs | `Dockerfile` |
| `SCA_CONFIG` | scan config, declares no deps itself | `dependabot.yml/.yaml`, `renovate.json` |
| `LOCKFILE` | integrity/checksum companion, not independently scanned | `go.sum` |
| `UNKNOWN` | — | any `|dependencies|`-tagged file matching no entry |

### `Extractor` descriptor

```python
class Kind(str, Enum):
    PACKAGE = "package"
    RUNTIME = "runtime"
    CONTAINER = "container"
    SCA_CONFIG = "sca_config"
    LOCKFILE = "lockfile"
    UNKNOWN = "unknown"        # not a registry row; returned by classify() on no match

@dataclass(frozen=True)
class Extractor:
    name: str                        # stable id, e.g. "pypi-requirements", "npm-packagejson"
    kind: Kind
    matches: Callable[[str], bool]   # pure predicate over a basename; total (never raises)
    scanners: tuple[str, ...]        # scan paths that handle it TODAY: subset of ("sca", "osi")
    package_type: str | None = None  # DB package_type value; only meaningful for kind=PACKAGE
    parse_ref: str | None = None     # dotted "module:callable" ref to the in-place parser; None if no parser
```

- `supported` is **derived**: `bool(scanners)`. Per the approved decision,
  `/osi/` treats "supported" as *scannable by any path* (Option 1), so a
  `go.mod` (scanners `("sca",)`) is `supported=True`; the `scanners` tuple lets
  B refine the presentation ("supported via `kospex sca`").
- `parse_ref` is **recorded, not invoked** in A — it documents where the logic
  lives today and becomes the dispatch target in C. Points at the pure parser
  where one exists, else at the current `*_assess` entry point (noted for C to
  normalise).

### Catalog (`REGISTRY`)

| `name` | `kind` | matches (basename) | `scanners` | `package_type` | `parse_ref` |
|---|---|---|---|---|---|
| pypi-requirements | PACKAGE | `requirements([-_][\w.]*)?\.(txt\|in)` | `sca, osi` | pypi | `kospex_dependencies:KospexDependencies.parse_pip_requirements_file` |
| pyproject | PACKAGE | `pyproject.toml` | `sca, osi` | pypi | `…:parse_pyproject_file` |
| npm-packagejson | PACKAGE | `package.json` (exact) | `sca, osi` | npm | `…:parse_package_json` |
| pnpm-lock | PACKAGE | `pnpm-lock.yaml` | `sca, osi` | npm | `kospex.extractors.pnpm:extract_pnpm_lock` |
| go-mod | PACKAGE | `go.mod` | `sca` | go | `…:gomod_assess` (assess; C normalises) |
| nuget-csproj | PACKAGE | `*.csproj` | `sca` | nuget | `…:nuget_assess` (assess; C normalises) |
| yarn-lock | PACKAGE | `yarn.lock` | — | npm | None |
| uv-lock | PACKAGE | `uv.lock` | — | pypi | None |
| npm-lock | PACKAGE | `package-lock.json` | — | npm | None (see N2) |
| gradle-build | PACKAGE | `build.gradle(.kts)?` | — | maven | None |
| python-version | RUNTIME | `.python-version` | — | None | None |
| nvmrc | RUNTIME | `.nvmrc` | — | None | None |
| dockerfile | CONTAINER | `Dockerfile` / `Dockerfile.*` / `*.Dockerfile` | — | None | None |
| dependabot | SCA_CONFIG | `dependabot.yml` / `dependabot.yaml` | — | None | None |
| renovate | SCA_CONFIG | `renovate.json` / `.renovaterc(.json)?` | — | None | None |
| go-sum | LOCKFILE | `go.sum` | — | None | None |

Matchers are authored fresh to reflect *real* union support — deliberately
tighter than the existing loose predicates (e.g. `package.json` is exact so it
does **not** swallow `package-lock.json`, which is a separate unsupported row).
All matching is on `os.path.basename(filename)`, case-insensitively where the
ecosystem is case-insensitive.

### Classifier

```python
@dataclass(frozen=True)
class Classification:
    kind: Kind
    supported: bool
    scanners: tuple[str, ...]
    extractor: Extractor | None

def classify(filename: str) -> Classification:
    """Classify a dependency-file basename. Assumes the file was tagged
    `|dependencies|` by panopticas (callers such as /osi/ pre-filter on that
    tag); a non-dependency file returns kind=UNKNOWN. Returns the first
    matching REGISTRY entry's (kind, bool(scanners), scanners, entry), or
    (UNKNOWN, False, (), None) when nothing matches."""
```

`REGISTRY` order is significant only in that matchers must be mutually
exclusive (asserted by a test), so "first match" is unambiguous.

## Scanner-coverage matrix (parity target for C)

Encoded in each row's `scanners` and asserted by a test — this is the living
record of the gap C closes:

| ecosystem | `sca` | `osi` |
|---|:---:|:---:|
| requirements / pyproject / package.json / pnpm-lock | ✅ | ✅ |
| **go.mod** | ✅ | ❌ |
| **\*.csproj (nuget)** | ✅ | ❌ |

## File structure

- `src/kospex/extractors/registry.py` — `Kind`, `Extractor`, `Classification`,
  `REGISTRY`, `classify()`. Pure: no DB, no CLI, no I/O. Follows the extractors
  package conventions in `__init__.py`.
- `tests/test_extractor_registry.py` — see Testing.

## Testing

1. **Classifier truth-table** (parametrized, drawn from real DB data): each of
   the catalog's representative filenames → expected `(kind, supported)`. Plus
   `package-lock.json` → `(PACKAGE, False)` (guards the tightened `package.json`
   matcher), `go.mod` → `(PACKAGE, True)`, `.python-version` → `(RUNTIME,
   False)`, `dependabot.yml` → `(SCA_CONFIG, False)`, `go.sum` → `(LOCKFILE,
   False)`, and a nonsense name → `(UNKNOWN, False)`.
2. **Matcher totality**: every `entry.matches` returns a `bool` and never raises
   across a set of odd inputs (`""`, weird unicode, paths with directories).
3. **Mutual exclusivity**: for the catalog's representative filenames, at most
   one registry entry matches (keeps C's future dispatch deterministic).
4. **Coverage matrix**: assert `go.mod` and `*.csproj` are `("sca",)` and the
   four shared ecosystems are `("sca", "osi")` — fails if someone silently
   changes support without updating the matrix.
5. **`parse_ref` resolvability**: for every entry with a non-`None` `parse_ref`,
   the dotted `module:callable` resolves to a callable (imports the module,
   walks `Class.method` if present). Catches typos and ties the registry to
   real code without invoking it.

## Deferred / open questions (captured, not scoped here)

- **N1 — `docker-compose.yml`**: `CONTAINER` kind (image refs + service deps);
  parser and registry row deferred to D. Noted here so it isn't lost.
- **N2 — `package-lock.json` / `yarn.lock` parsing (D)** needs design thinking
  *before* a parser is written: a rich lockfile yields `direct`/`dev` **plus** a
  fully-resolved **LOCK** set with exact versions + integrity hashes. That
  implies (a) a new `package_use` value (e.g. `locked`/resolved-transitive) and
  (b) a *declared-vs-locked* relationship with `package.json` for the same repo,
  so dedup/precedence must be decided. In A these stay `PACKAGE`,
  `scanners=()`.
- **N3 — `go.sum`**: `LOCKFILE` kind, integrity only; `go.mod` is the scanned
  source. Never enriched on its own.
- **N4 — GitHub Actions get their own interface** (does not exist yet). Actions
  are dependencies (git repos pinned by tag or commit SHA — a SHA is exact, a
  tag is mutable, closer to `unresolved_spec`), already extracted by
  `workflows.py`, but versioned by git ref not semver. This dependency registry
  does **not** own them; a separate actions interface will.
- **N5 — `/osi/` as umbrella (B)**: `/osi/` should surface *everything* —
  packages, actions, runtime, container — aggregating this registry **and** the
  separate actions interface, grouped by kind. That means B (or the actions
  interface) also pulls CI/workflow-tagged files, not only `|dependencies|`-
  tagged ones.
- **N6 — kind-based drill-down (B)**: a `PACKAGE` row links to `/dependencies/`;
  an action row links to a different surface (`/cicd/` or similar, owned by the
  actions interface); other kinds have no detail view yet.

## Release note

A is additive (one module + tests), changes no runtime behaviour, and is safe
to include in the upcoming OSS release — whose user-facing content is already
carried by the merged `[Unreleased]` items (resolution status #106, #108, the
package-check fix). The visible consumer (`/osi/`, sub-project B) can fast-follow
as its own spec.
