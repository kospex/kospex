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
    # Multi-hyphen and dot-separated suffixes. Both forms occur in the reference
    # estate and krunner osi's substring check already parsed them, so the
    # matcher must too — otherwise routing osi through classify() drops them.
    ("requirements-wheel-test.txt", Kind.PACKAGE, True),
    ("requirements-wheel-build.txt", Kind.PACKAGE, True),
    ("requirements.dev.txt", Kind.PACKAGE, True),
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


def test_requirements_matching_is_delegated_to_panopticas():
    """Recognition is panopticas' job; this registry adds kind/scanners/parser.

    Both directions matter, and both were wrong before delegating. The
    multi-hyphen forms were missed by the local pattern, and `requirements.py`
    / `.rst` / `.lock` are matched by krunner osi's substring check but are not
    pip requirements files — all four names are present in a real estate.
    """
    for name in ("requirements.txt", "requirements-wheel-test.txt",
                 "requirements.dev.txt", "requirements-docs.in"):
        assert classify(name).extractor.name == "pypi-requirements", name

    for name in ("requirements.py", "requirements.rst",
                 "requirements.lock", "requirements-dev.lock"):
        c = classify(name)
        assert c.extractor is None, f"{name} is not a pip requirements file"
        assert c.supported is False, name


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
            assert isinstance(e.matches(s), bool)


def test_matchers_mutually_exclusive():
    # At most one registry entry matches any real basename (deterministic dispatch).
    for fname, kind, _supported in CASES:
        if kind == Kind.UNKNOWN:
            continue
        base = os.path.basename(fname)
        hits = [e.name for e in REGISTRY if e.matches(base)]
        assert len(hits) == 1, f"{fname!r} matched {hits}"

    # Adversarial filename shapes outside CASES — lock exclusivity for shapes,
    # not just the fixed cases (sub-project C's dispatch relies on this).
    for fname in ("Dockerfile.prod", "app.Dockerfile", "Project.Tests.csproj",
                  ".renovaterc.json", "requirements-dev.in", "build.gradle.kts"):
        hits = [e.name for e in REGISTRY if e.matches(os.path.basename(fname))]
        assert len(hits) <= 1, f"{fname!r} matched {hits}"


def test_coverage_matrix_is_shared_across_both_scan_paths():
    """The krunner gap is closed (sub-project C1): go.mod and *.csproj are now
    handled by krunner osi as well as kospex sca, so every package type with a
    parser is claimed by both paths.

    This assertion is the living record of scanner parity — it fails if someone
    adds a parser to one path without the other, which is how the gap opened.
    """
    scanners = {e.name: e.scanners for e in REGISTRY}
    for name in ("pypi-requirements", "pyproject", "npm-packagejson", "pnpm-lock",
                 "go-mod", "nuget-csproj"):
        assert scanners[name] == ("sca", "osi"), name


def test_every_parser_backed_entry_is_shared():
    """Generalises the matrix: no entry may claim exactly one scanner.

    A parser that only one path can reach is the defect sub-project C removes,
    so a single-scanner entry should fail here rather than be discovered later
    as an ecosystem silently missing from one command.
    """
    for e in REGISTRY:
        if not e.scanners:
            continue
        assert set(e.scanners) == {"sca", "osi"}, f"{e.name}: {e.scanners}"


class TestResolveParser:
    """resolve_parser turns parse_ref into something callable with a path.

    Sub-project C makes parse_ref the dispatch target rather than documentation,
    so the registry has to hand back a callable. Entries come in two shapes:
    module functions (the extractors/ modules) and KospexDependencies methods,
    which need binding to an instance.
    """

    def _entry(self, name):
        return next(e for e in REGISTRY if e.name == name)

    def test_module_function_ref_resolves(self):
        from kospex.extractors.registry import resolve_parser
        fn = resolve_parser(self._entry("go-mod"))
        assert callable(fn)
        assert fn.__name__ == "extract_gomod"

    def test_class_method_ref_binds_to_the_supplied_instance(self):
        from kospex_dependencies import KospexDependencies
        from kospex.extractors.registry import resolve_parser
        kdeps = KospexDependencies()

        fn = resolve_parser(self._entry("pypi-requirements"),
                            {"KospexDependencies": kdeps})

        assert callable(fn)
        assert fn.__self__ is kdeps

    def test_class_method_ref_without_an_instance_raises(self):
        """Better to fail loudly than to hand back an unbound function that
        would silently receive the path as `self`."""
        import pytest
        from kospex.extractors.registry import resolve_parser
        with pytest.raises(ValueError):
            resolve_parser(self._entry("pypi-requirements"))

    def test_entry_without_a_parser_resolves_to_none(self):
        from kospex.extractors.registry import resolve_parser
        assert resolve_parser(self._entry("yarn-lock")) is None

    def test_every_supported_entry_resolves_and_takes_a_path(self):
        """The parity guarantee: everything both scanners claim to handle must
        actually be reachable through the registry."""
        import inspect
        from kospex_dependencies import KospexDependencies
        from kospex.extractors.registry import resolve_parser
        instances = {"KospexDependencies": KospexDependencies()}

        for e in REGISTRY:
            if not e.scanners:
                continue
            fn = resolve_parser(e, instances)
            assert callable(fn), e.name
            params = list(inspect.signature(fn).parameters)
            assert params, f"{e.name}: parser takes no arguments"


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
