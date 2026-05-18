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


class TestSplitHelpers:
    """Name/version extraction from packages: keys, per lockfile family."""

    def test_at_key_unscoped_v6_leading_slash(self):
        assert _split_at_key("/lodash@4.17.21") == ("lodash", "4.17.21")

    def test_at_key_scoped_v6(self):
        assert _split_at_key("/@babel/core@7.21.3") == ("@babel/core", "7.21.3")

    def test_at_key_v6_peer_suffix_stripped(self):
        assert _split_at_key("/@apideck/better-ajv-errors@0.3.6(ajv@8.12.0)") == (
            "@apideck/better-ajv-errors",
            "0.3.6",
        )

    def test_at_key_v9_no_leading_slash(self):
        assert _split_at_key("@alloc/quick-lru@5.2.0") == ("@alloc/quick-lru", "5.2.0")

    def test_at_key_v9_unscoped(self):
        assert _split_at_key("lodash@4.17.21") == ("lodash", "4.17.21")

    def test_at_key_garbage_returns_none(self):
        assert _split_at_key("no-at-sign-here") == (None, None)

    def test_v5_unscoped(self):
        assert _split_v5_key("/lodash/4.17.21") == ("lodash", "4.17.21")

    def test_v5_scoped(self):
        assert _split_v5_key("/@babel/core/7.12.0") == ("@babel/core", "7.12.0")

    def test_v5_peer_suffix_stripped(self):
        assert _split_v5_key("/react-dom/16.8.0_react@16.8.0") == (
            "react-dom",
            "16.8.0",
        )

    def test_v5_garbage_returns_none(self):
        assert _split_v5_key("nosuchslash") == (None, None)
