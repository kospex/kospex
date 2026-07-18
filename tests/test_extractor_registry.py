"""Tests for the dependency-file extractor registry + classifier (sub-project A).

See changes/2026-07-17-extractor-registry-classifier-design.md.
"""
import importlib
import os

import pytest

from kospex.extractors.registry import Kind, REGISTRY, classify

# (filename, expected kind, expected supported) — drawn from the reference DB audit.
CASES = [
    ("requirements.txt", Kind.PACKAGE, True),
    ("requirements-dev.txt", Kind.PACKAGE, True),
    ("requirements.in", Kind.PACKAGE, True),
    ("requirements_merge_arrow_pr.txt", Kind.PACKAGE, True),
    ("pyproject.toml", Kind.PACKAGE, True),
    ("package.json", Kind.PACKAGE, True),
    ("pnpm-lock.yaml", Kind.PACKAGE, True),
    ("go.mod", Kind.PACKAGE, True),        # sca-only, but supported == any scanner
    ("Foo.csproj", Kind.PACKAGE, True),    # sca-only
    ("yarn.lock", Kind.PACKAGE, False),
    ("uv.lock", Kind.PACKAGE, False),
    ("package-lock.json", Kind.PACKAGE, False),
    ("build.gradle", Kind.PACKAGE, False),
    (".python-version", Kind.RUNTIME, False),
    (".nvmrc", Kind.RUNTIME, False),
    ("Dockerfile", Kind.CONTAINER, False),
    ("dependabot.yml", Kind.SCA_CONFIG, False),
    ("renovate.json", Kind.SCA_CONFIG, False),
    ("go.sum", Kind.LOCKFILE, False),
    ("mystery.xyz", Kind.UNKNOWN, False),
]


@pytest.mark.parametrize("fname,kind,supported", CASES)
def test_classify(fname, kind, supported):
    c = classify(fname)
    assert c.kind == kind
    assert c.supported is supported
    assert c.supported == bool(c.scanners)


def test_classify_uses_basename():
    assert classify("path/to/requirements.txt").kind == Kind.PACKAGE
    assert classify("/abs/dir/Dockerfile").kind == Kind.CONTAINER


def test_package_json_matcher_is_exact_not_lockfile():
    # The tightened package.json matcher must NOT swallow package-lock.json,
    # which is a separate (unsupported) registry row.
    assert classify("package.json").extractor.name == "npm-packagejson"
    assert classify("package-lock.json").extractor.name == "npm-lock"
