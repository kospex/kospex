"""Dependency analysis helpers for kospex.

Keep this file import-free: eager imports in package __init__ files have caused
circular imports with the flat kospex_* modules. Import submodules directly,
e.g. `from kospex.dependencies.version_diff import version_diff`.
"""
