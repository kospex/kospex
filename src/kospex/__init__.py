"""
Kospex Package - Modern Python Package Structure

This package provides git analytics and DuckDB-based commit extraction.
New features and refactored code should be added here following Python best practices.

The package coexists with legacy flat-file modules in /src/ during the migration period.
"""

from importlib import import_module
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("kospex")
except PackageNotFoundError:
    __version__ = "unknown"

# Public API - what gets imported with "from kospex import *"
__all__ = [
    "GitIngest",
    "GitDuckDB",
    "HabitatConfig",
    "__version__",
]

# Lazily expose the package-level classes. Importing them eagerly here pulls in
# git_ingest -> kospex_git -> kospex_query, which creates a circular import for
# any module that reaches the package via a lightweight submodule such as
# kospex.db.introspect (imported by kospex_schema). PEP 562 __getattr__ keeps
# `from kospex import GitIngest` working while deferring the heavy imports until
# the name is actually accessed.
_LAZY_EXPORTS = {
    "GitIngest": ".git_ingest",
    "GitDuckDB": ".git_duckdb",
    "HabitatConfig": ".habitat_config",
}


def __getattr__(name):
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_path, __name__)
    return getattr(module, name)
