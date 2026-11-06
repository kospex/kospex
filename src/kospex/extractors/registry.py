"""Registry of the dependency-bearing file types kospex recognises.

Single source of truth for *which* manifest / lock / config files kospex can
classify, what *kind* of dependency each declares, and whether a scanner can
parse it today. Pure: no DB, no CLI, no I/O — it classifies filenames
(strings), it never opens a file. See
changes/2026-07-17-extractor-registry-classifier-design.md.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import panopticas
from enum import Enum
from typing import Callable, Optional


class Kind(str, Enum):
    """The kind of dependency a file declares."""
    PACKAGE = "package"        # named library deps (requirements.txt, package.json, ...)
    RUNTIME = "runtime"        # a language/toolchain version (.python-version, .nvmrc)
    CONTAINER = "container"    # base image + build-time installs (Dockerfile)
    SCA_CONFIG = "sca_config"  # scan config, declares no deps itself (dependabot.yml)
    LOCKFILE = "lockfile"      # companion to a declaration, not scanned alone (go.sum, .pnp.cjs)
    UNKNOWN = "unknown"        # dependency-tagged file matching no registry entry


@dataclass(frozen=True)
class Extractor:
    """One recognised dependency-file type.

    `matches` is a pure basename predicate. `scanners` names the scan paths
    that handle this type TODAY (subset of "sca"/"osi"); support is derived
    from it. `package_type` is the DB package_type value, meaningful only for
    kind=PACKAGE. `parse_ref` is a "module:callable" reference to the in-place
    parser (documentation in A; the dispatch target in sub-project C), or None
    when no parser exists yet.
    """
    name: str
    kind: Kind
    matches: Callable[[str], bool]
    scanners: tuple[str, ...] = ()
    package_type: Optional[str] = None
    parse_ref: Optional[str] = None


@dataclass(frozen=True)
class Classification:
    """Result of classifying a filename."""
    kind: Kind
    supported: bool
    scanners: tuple[str, ...]
    extractor: Optional[Extractor]


def _matcher(pattern: str) -> Callable[[str], bool]:
    """Build a pure, total basename predicate from a regex (full, case-insensitive).

    A local pattern is a **stand-in**. Panopticas already recognises every
    manifest type below — see METADATA_RULES `exact_filename_rules` (go.mod,
    package.json, pyproject.toml, pnpm-lock.yaml) and `extension_rules`
    (.csproj) — but exposes no per-type identification API, only a bag of tags
    from get_filename_metatypes(). So there is nothing to delegate to yet for
    those, and each local pattern here is a second implementation that can
    drift from panopticas'.

    That drift is not hypothetical: this module's own requirements pattern
    excluded `requirements-wheel-test.txt` (a second hyphen) while panopticas
    matched it. Prefer `_panopticas_matcher` wherever panopticas exposes a
    predicate, and replace these as it grows an identification API.
    """
    rx = re.compile(pattern, re.IGNORECASE)

    def _match(basename: str) -> bool:
        return bool(rx.fullmatch(basename or ""))

    return _match


def _panopticas_matcher(func_name: str) -> Callable[[str], bool]:
    """Build a basename predicate that delegates to a panopticas predicate.

    Panopticas owns "what is this file"; this registry owns "what do we do with
    it" (kind, scanners, parser, package_type). Delegating recognition keeps the
    two from disagreeing — measured on a real estate, panopticas was right in
    both directions where kospex was wrong: it matched the multi-hyphen
    `requirements-wheel-*.txt` files, and it correctly rejected
    `requirements.py` / `.rst` / `.lock`, which a substring check treated as
    manifests.

    Resolved by name at call time so a panopticas version without the predicate
    degrades to False rather than breaking the import. The `basename or ""`
    guard preserves totality: the panopticas predicates raise TypeError on None.
    """
    def _match(basename: str) -> bool:
        predicate = getattr(panopticas, func_name, None)
        if predicate is None:
            return False
        return bool(predicate(basename or ""))

    return _match


REGISTRY: tuple[Extractor, ...] = (
    # --- package: supported by both scan paths ---
    # Delegated: panopticas.is_pip_requirements is the one per-type predicate it
    # exposes today, and it is the authority on this shape.
    Extractor("pypi-requirements", Kind.PACKAGE,
              _panopticas_matcher("is_pip_requirements"), ("sca", "osi"), "pypi",
              "kospex_dependencies:KospexDependencies.parse_pip_requirements_file"),
    # The entries below use LOCAL matchers as a stand-in. Panopticas already
    # recognises every one of them — `pyproject.toml`, `package.json`,
    # `pnpm-lock.yaml` and `go.mod` via METADATA_RULES exact_filename_rules,
    # `*.csproj` via extension_rules — but exposes no per-type identification
    # API to delegate to, only a tag bag from get_filename_metatypes(). Each
    # pattern here is therefore a second implementation that can drift from
    # panopticas', which is exactly how the requirements matcher went wrong.
    # Replace these with _panopticas_matcher as that API arrives.
    Extractor("pyproject", Kind.PACKAGE, _matcher(r"pyproject\.toml"),
              ("sca", "osi"), "pypi",
              "kospex_dependencies:KospexDependencies.parse_pyproject_file"),
    Extractor("npm-packagejson", Kind.PACKAGE, _matcher(r"package\.json"),
              ("sca", "osi"), "npm",
              "kospex_dependencies:KospexDependencies.parse_package_json"),
    Extractor("pnpm-lock", Kind.PACKAGE, _matcher(r"pnpm-lock\.yaml"),
              ("sca", "osi"), "npm",
              "kospex.extractors.pnpm:extract_pnpm_lock"),
    Extractor("npm-lock", Kind.PACKAGE, _matcher(r"package-lock\.json"),
              ("sca", "osi"), "npm",
              "kospex.extractors.npmlock:extract_package_lock"),
    # npm documents npm-shrinkwrap.json as the package-lock.json format under
    # a publishable name, and the parser never reads the filename.
    Extractor("npm-shrinkwrap", Kind.PACKAGE, _matcher(r"npm-shrinkwrap\.json"),
              ("sca", "osi"), "npm",
              "kospex.extractors.npmlock:extract_package_lock"),
    Extractor("go-mod", Kind.PACKAGE, _matcher(r"go\.mod"), ("sca", "osi"), "go",
              "kospex.extractors.gomod:extract_gomod"),
    Extractor("nuget-csproj", Kind.PACKAGE, _matcher(r".*\.csproj"), ("sca", "osi"), "nuget",
              "kospex.extractors.nuget:extract_csproj"),
    Extractor("uv-lock", Kind.PACKAGE, _matcher(r"uv\.lock"), ("sca", "osi"), "pypi",
              "kospex.extractors.uvlock:extract_uv_lock"),
    Extractor("yarn-lock", Kind.PACKAGE, _matcher(r"yarn\.lock"), ("sca", "osi"), "npm",
              "kospex.extractors.yarn:extract_yarn_lock"),
    Extractor("maven-pom", Kind.PACKAGE, _matcher(r"pom\.xml"), ("sca", "osi"), "maven",
              "kospex.extractors.maven:extract_pom"),
    # --- package: recognised but no parser yet (-> sub-project D) ---
    Extractor("gradle-build", Kind.PACKAGE, _matcher(r"build\.gradle(\.kts)?"), (), "maven", None),
    # Tagged `dependencies` from panopticas 0.0.22 / 0.0.23. Named here so a consumer
    # sees "no parser yet" rather than an unrecognised file. package_type is
    # the deps.dev system a future parser would query, or None where deps.dev
    # has no such system (Packagist, Hex, Pub, Deno's JSR).
    # Gradle's dependency lockfiles, and the version catalog Gradle imports
    # from gradle/. Replaced when a Gradle parser lands (#229).
    Extractor("gradle-lockfile", Kind.PACKAGE,
              _matcher(r"(buildscript-)?gradle\.lockfile"), (), "maven", None),
    Extractor("gradle-version-catalog", Kind.PACKAGE, _matcher(r"libs\.versions\.toml"), (),
              "maven", None),
    Extractor("sbt", Kind.PACKAGE, _matcher(r".+\.sbt"), (), "maven", None),
    Extractor("mill", Kind.PACKAGE,
              _matcher(r"(build|package)\.mill(\.yaml)?|build\.sc"), (), "maven", None),
    Extractor("cargo-toml", Kind.PACKAGE, _matcher(r"cargo\.toml"), (), "cargo", None),
    Extractor("cargo-lock", Kind.PACKAGE, _matcher(r"cargo\.lock"), (), "cargo", None),
    Extractor("gemfile", Kind.PACKAGE, _matcher(r"gemfile|gems\.rb"), (), "rubygems", None),
    Extractor("gemfile-lock", Kind.PACKAGE, _matcher(r"gemfile\.lock|gems\.locked"), (),
              "rubygems", None),
    Extractor("gemspec", Kind.PACKAGE, _matcher(r".+\.gemspec"), (), "rubygems", None),
    Extractor("composer-json", Kind.PACKAGE, _matcher(r"composer\.json"), (), None, None),
    Extractor("composer-lock", Kind.PACKAGE, _matcher(r"composer\.lock"), (), None, None),
    Extractor("pipfile", Kind.PACKAGE, _matcher(r"pipfile"), (), "pypi", None),
    Extractor("pipfile-lock", Kind.PACKAGE, _matcher(r"pipfile\.lock"), (), "pypi", None),
    Extractor("poetry-lock", Kind.PACKAGE, _matcher(r"poetry\.lock"), (), "pypi", None),
    Extractor("pdm-lock", Kind.PACKAGE, _matcher(r"pdm\.lock"), (), "pypi", None),
    # PEP 751: `pylock.toml` or `pylock.<name>.toml`, the name containing no
    # dot. panopticas 0.0.22 has is_pylock in panopticas.core but does not
    # export it from the package, so _panopticas_matcher would always return
    # False. This is its pattern; delegate once it is exported.
    Extractor("pylock", Kind.PACKAGE, _matcher(r"pylock\.([^.]+\.)?toml"), (), "pypi", None),
    Extractor("mix-exs", Kind.PACKAGE, _matcher(r"mix\.exs"), (), None, None),
    Extractor("mix-lock", Kind.PACKAGE, _matcher(r"mix\.lock"), (), None, None),
    Extractor("pubspec", Kind.PACKAGE,
              _matcher(r"pubspec\.yaml|pubspec_overrides\.yaml"), (), None, None),
    Extractor("pubspec-lock", Kind.PACKAGE, _matcher(r"pubspec\.lock"), (), None, None),
    # bun.lockb is Bun's binary lockfile, replaced by the text bun.lock in Bun 1.2.
    Extractor("bun-lock", Kind.PACKAGE, _matcher(r"bun\.lockb?"), (), "npm", None),
    Extractor("deno-lock", Kind.PACKAGE, _matcher(r"deno\.lock"), (), None, None),
    # F# and VB projects use the same SDK-style PackageReference as .csproj,
    # so extract_csproj probably reads them, but no real file was available
    # to measure it against.
    Extractor("nuget-fsproj-vbproj", Kind.PACKAGE, _matcher(r".+\.(fsproj|vbproj)"), (),
              "nuget", None),
    Extractor("nuspec", Kind.PACKAGE, _matcher(r".+\.nuspec"), (), "nuget", None),
    # Central package management: PackageVersion items, not PackageReference.
    Extractor("nuget-central-versions", Kind.PACKAGE, _matcher(r"directory\.packages\.props"),
              (), "nuget", None),
    # packages.lock.json, or packages.<project>.lock.json when projects share a
    # directory: panopticas' is_nuget_project_lock, which it does not export.
    Extractor("nuget-lock", Kind.PACKAGE, _matcher(r"packages(\..+)?\.lock\.json"), (),
              "nuget", None),
    Extractor("dotnet-tools", Kind.PACKAGE, _matcher(r"dotnet-tools\.json"), (), "nuget", None),
    Extractor("paket", Kind.PACKAGE,
              _matcher(r"paket\.(dependencies|lock|references)"), (), "nuget", None),
    # Mostly workspace settings, but `catalog:` / `catalogs:` declare version
    # ranges that package.json files refer to as `catalog:`. Measured: 4 of 48
    # real files have catalogs, so no parser yet.
    Extractor("pnpm-workspace", Kind.PACKAGE, _matcher(r"pnpm-workspace\.yaml"), (),
              "npm", None),
    # --- runtime ---
    Extractor("python-version", Kind.RUNTIME, _matcher(r"\.python-version"), (), None, None),
    Extractor("nvmrc", Kind.RUNTIME, _matcher(r"\.nvmrc"), (), None, None),
    Extractor("ruby-version", Kind.RUNTIME, _matcher(r"\.ruby-version"), (), None, None),
    # --- container ---
    Extractor("dockerfile", Kind.CONTAINER,
              _matcher(r"dockerfile(\..+)?|.+\.dockerfile"), (), None, None),
    # --- sca config (declares no deps itself) ---
    Extractor("dependabot", Kind.SCA_CONFIG, _matcher(r"dependabot\.ya?ml"), (), None, None),
    Extractor("renovate", Kind.SCA_CONFIG,
              _matcher(r"renovate\.json|\.renovaterc(\.json)?"), (), None, None),
    # --- lockfile (integrity only) ---
    Extractor("go-sum", Kind.LOCKFILE, _matcher(r"go\.sum"), (), None, None),
    # Yarn Plug'n'Play loaders: generated code holding the tree yarn.lock
    # already declares, so a companion like go.sum, not a source of its own.
    Extractor("yarn-pnp", Kind.LOCKFILE, _matcher(r"\.pnp\.(cjs|loader\.mjs)"), (), None, None),
)


def classify(filename: str) -> Classification:
    """Classify a dependency-file name into its kind and support state.

    Assumes the file was tagged `|dependencies|` by panopticas (callers such as
    /osi/ pre-filter on that tag); a file matching no registry entry returns
    kind=UNKNOWN. Matching is on the basename. Returns the first matching
    REGISTRY entry's (kind, bool(scanners), scanners, entry), else
    (UNKNOWN, False, (), None).
    """
    basename = os.path.basename(filename or "")
    for entry in REGISTRY:
        if entry.matches(basename):
            return Classification(entry.kind, bool(entry.scanners), entry.scanners, entry)
    return Classification(Kind.UNKNOWN, False, (), None)


def resolve_parser(extractor: Extractor, instances: Optional[dict] = None):
    """Resolve an extractor's `parse_ref` to a callable taking a file path.

    In sub-project A `parse_ref` was documentation, checked only for
    resolvability. From C it is the dispatch target, so callers need the actual
    callable — this is the one place that knows how to produce it.

    Two shapes exist. `module:function` (the extractors/ modules) resolves
    directly. `module:Class.method` needs an instance, supplied by the caller as
    {"ClassName": obj} — the parsers on KospexDependencies are methods and an
    unbound function would silently receive the path as `self`. Returns None for
    entries with no parser; raises ValueError when an instance is required but
    absent, which is a wiring error worth failing loudly on.
    """
    import importlib

    if extractor.parse_ref is None:
        return None

    module_name, qualname = extractor.parse_ref.split(":")
    module = importlib.import_module(module_name)
    parts = qualname.split(".")

    if len(parts) == 1:
        return getattr(module, parts[0])

    class_name, attr_name = parts[0], parts[-1]
    instance = (instances or {}).get(class_name)
    if instance is None:
        raise ValueError(
            f"{extractor.name}: parse_ref {extractor.parse_ref!r} is a method of "
            f"{class_name}; pass instances={{'{class_name}': <obj>}} to bind it"
        )
    return getattr(instance, attr_name)
