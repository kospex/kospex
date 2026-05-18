"""Tests for the pnpm-lock.yaml extractor.

Mirrors tests/test_extractors_workflows.py structure: helper-level
tests, extract-level tests per lockfileVersion, classification, and
error handling.
"""
from pathlib import Path

import pytest

from kospex.extractors.pnpm import (
    extract_pnpm_lock,
    _split_at_key,
    _split_v5_key,
    _collect_direct_dev,
    _template,
)


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


class TestTemplateContract:
    """The local record template must stay in sync with KospexDependencies."""

    def test_template_matches_get_package_template(self):
        from kospex_dependencies import KospexDependencies
        assert set(_template().keys()) == set(
            KospexDependencies().get_package_template().keys()
        )
