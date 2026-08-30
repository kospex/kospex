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
    LOCKFILE = "lockfile"      # integrity/checksum companion, not scanned alone (go.sum)
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
    Extractor("go-mod", Kind.PACKAGE, _matcher(r"go\.mod"), ("sca", "osi"), "go",
              "kospex.extractors.gomod:extract_gomod"),
    Extractor("nuget-csproj", Kind.PACKAGE, _matcher(r".*\.csproj"), ("sca", "osi"), "nuget",
              "kospex.extractors.nuget:extract_csproj"),
    # --- package: recognised but no parser yet (-> sub-project D) ---
    Extractor("yarn-lock", Kind.PACKAGE, _matcher(r"yarn\.lock"), (), "npm", None),
    Extractor("uv-lock", Kind.PACKAGE, _matcher(r"uv\.lock"), (), "pypi", None),
    Extractor("npm-lock", Kind.PACKAGE, _matcher(r"package-lock\.json"), (), "npm", None),
    Extractor("gradle-build", Kind.PACKAGE, _matcher(r"build\.gradle(\.kts)?"), (), "maven", None),
    # --- runtime ---
    Extractor("python-version", Kind.RUNTIME, _matcher(r"\.python-version"), (), None, None),
    Extractor("nvmrc", Kind.RUNTIME, _matcher(r"\.nvmrc"), (), None, None),
    # --- container ---
    Extractor("dockerfile", Kind.CONTAINER,
              _matcher(r"dockerfile(\..+)?|.+\.dockerfile"), (), None, None),
    # --- sca config (declares no deps itself) ---
    Extractor("dependabot", Kind.SCA_CONFIG, _matcher(r"dependabot\.ya?ml"), (), None, None),
    Extractor("renovate", Kind.SCA_CONFIG,
              _matcher(r"renovate\.json|\.renovaterc(\.json)?"), (), None, None),
    # --- lockfile (integrity only) ---
    Extractor("go-sum", Kind.LOCKFILE, _matcher(r"go\.sum"), (), None, None),
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
