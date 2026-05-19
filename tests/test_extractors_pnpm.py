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


class TestCollectDirectDev:
    """direct/dev name sets from importers + top-level dep blocks."""

    def test_v6_importer_root_dependencies(self):
        doc = {
            "importers": {
                ".": {
                    "dependencies": {"lodash": {"specifier": "^4", "version": "4.17.21"}},
                    "devDependencies": {"jest": {"specifier": "^29", "version": "29.0.0"}},
                }
            }
        }
        direct, dev = _collect_direct_dev(doc)
        assert direct == {"lodash"}
        assert dev == {"jest"}

    def test_v5_top_level_scalar_deps(self):
        doc = {
            "dependencies": {"lodash": "4.17.21"},
            "devDependencies": {"jest": "29.0.0"},
        }
        direct, dev = _collect_direct_dev(doc)
        assert direct == {"lodash"}
        assert dev == {"jest"}

    def test_optional_deps_count_as_direct(self):
        doc = {"optionalDependencies": {"fsevents": "2.3.2"}}
        direct, dev = _collect_direct_dev(doc)
        assert direct == {"fsevents"}

    def test_missing_blocks_yield_empty_sets(self):
        assert _collect_direct_dev({}) == (set(), set())


FIXTURES = Path(__file__).parent / "fixtures" / "pnpm"


def _by_name(records):
    return {(r["package_name"], r["package_version"]): r for r in records}


class TestExtractV6:
    def test_v6_resolved_closure(self):
        recs = extract_pnpm_lock(str(FIXTURES / "v6-simple.yaml"))
        idx = _by_name(recs)
        assert ("lodash", "4.17.21") in idx
        assert ("jest", "29.0.0") in idx
        assert ("@babel/core", "7.21.3") in idx
        assert ("@apideck/better-ajv-errors", "0.3.6") in idx
        for r in recs:
            assert r["ecosystem"] == "npm"

    def test_v6_classification(self):
        idx = _by_name(extract_pnpm_lock(str(FIXTURES / "v6-simple.yaml")))
        assert idx[("lodash", "4.17.21")]["requirements_type"] == "direct"
        assert idx[("jest", "29.0.0")]["requirements_type"] == "dev"
        assert idx[("@babel/core", "7.21.3")]["requirements_type"] == "resolved"


class TestExtractV9:
    def test_v9_resolved_closure_and_dupes(self):
        idx = _by_name(extract_pnpm_lock(str(FIXTURES / "v9-simple.yaml")))
        assert ("@alloc/quick-lru", "5.2.0") in idx
        # same package, two versions → two distinct records, no dedup
        assert ("@esbuild/aix-ppc64", "0.23.1") in idx
        assert ("@esbuild/aix-ppc64", "0.27.0") in idx

    def test_v9_classification(self):
        idx = _by_name(extract_pnpm_lock(str(FIXTURES / "v9-simple.yaml")))
        assert idx[("lodash", "4.17.21")]["requirements_type"] == "direct"
        assert idx[("jest", "29.0.0")]["requirements_type"] == "dev"


class TestExtractV5:
    def test_v5_resolved_closure(self):
        idx = _by_name(extract_pnpm_lock(str(FIXTURES / "v5-simple.yaml")))
        assert ("lodash", "4.17.21") in idx
        assert ("@babel/core", "7.12.0") in idx
        assert ("react-dom", "16.8.0") in idx  # peer suffix stripped

    def test_v5_classification(self):
        idx = _by_name(extract_pnpm_lock(str(FIXTURES / "v5-simple.yaml")))
        assert idx[("lodash", "4.17.21")]["requirements_type"] == "direct"
        assert idx[("jest", "29.0.0")]["requirements_type"] == "dev"
