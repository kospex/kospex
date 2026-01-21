"""
Kospex Package - Modern Python Package Structure

This package provides git analytics and DuckDB-based commit extraction.
New features and refactored code should be added here following Python best practices.

The package coexists with legacy flat-file modules in /src/ during the migration period.
"""

from importlib.metadata import version, PackageNotFoundError

# Import new classes and functions to expose at package level
from .git_ingest import GitIngest
from .git_duckdb import GitDuckDB
from .habitat_config import HabitatConfig

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
