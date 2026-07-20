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
    """Build a pure, total basename predicate from a regex (full, case-insensitive)."""
    rx = re.compile(pattern, re.IGNORECASE)

    def _match(basename: str) -> bool:
        return bool(rx.fullmatch(basename or ""))

    return _match


REGISTRY: tuple[Extractor, ...] = (
    # --- package: supported by both scan paths ---
    Extractor("pypi-requirements", Kind.PACKAGE,
              _matcher(r"requirements([-_][\w.]*)?\.(txt|in)"), ("sca", "osi"), "pypi",
              "kospex_dependencies:KospexDependencies.parse_pip_requirements_file"),
    Extractor("pyproject", Kind.PACKAGE, _matcher(r"pyproject\.toml"),
              ("sca", "osi"), "pypi",
              "kospex_dependencies:KospexDependencies.parse_pyproject_file"),
    Extractor("npm-packagejson", Kind.PACKAGE, _matcher(r"package\.json"),
              ("sca", "osi"), "npm",
              "kospex_dependencies:KospexDependencies.parse_package_json"),
    Extractor("pnpm-lock", Kind.PACKAGE, _matcher(r"pnpm-lock\.yaml"),
              ("sca", "osi"), "npm",
              "kospex.extractors.pnpm:extract_pnpm_lock"),
    # --- package: supported by kospex sca only (krunner osi gap -> sub-project C) ---
    Extractor("go-mod", Kind.PACKAGE, _matcher(r"go\.mod"), ("sca",), "go",
              "kospex_dependencies:KospexDependencies.gomod_assess"),
    Extractor("nuget-csproj", Kind.PACKAGE, _matcher(r".*\.csproj"), ("sca",), "nuget",
              "kospex_dependencies:KospexDependencies.nuget_assess"),
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
