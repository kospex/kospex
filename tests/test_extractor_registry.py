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


def test_matchers_are_total():
    # Every matcher returns a bool and never raises, for any string input.
    odd = ["", "   ", "wiérd", "a/b/c", "UPPER.TXT", "noext", ".hidden", "pyproject"]
    for e in REGISTRY:
        for s in odd:
            assert e.matches(s) in (True, False)


def test_matchers_mutually_exclusive():
    # At most one registry entry matches any real basename (deterministic dispatch).
    for fname, kind, _supported in CASES:
        if kind == Kind.UNKNOWN:
            continue
        base = os.path.basename(fname)
        hits = [e.name for e in REGISTRY if e.matches(base)]
        assert len(hits) == 1, f"{fname!r} matched {hits}"


def test_coverage_matrix_records_the_krunner_gap():
    scanners = {e.name: e.scanners for e in REGISTRY}
    # sca-only today — sub-project C makes krunner osi handle these too.
    assert scanners["go-mod"] == ("sca",)
    assert scanners["nuget-csproj"] == ("sca",)
    # shared by both scan paths.
    for name in ("pypi-requirements", "pyproject", "npm-packagejson", "pnpm-lock"):
        assert scanners[name] == ("sca", "osi")


def test_parse_ref_resolves_to_a_callable():
    # Guards typos and ties the registry to real code (not invoked in A).
    for e in REGISTRY:
        if e.parse_ref is None:
            continue
        module_name, qualname = e.parse_ref.split(":")
        obj = importlib.import_module(module_name)
        for part in qualname.split("."):
            obj = getattr(obj, part)
        assert callable(obj), e.parse_ref


def test_unsupported_entries_have_no_parse_ref():
    # If nothing scans it, there is no parser to point at (and vice versa).
    for e in REGISTRY:
        assert bool(e.scanners) == (e.parse_ref is not None), e.name
