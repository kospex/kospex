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
    ("package-lock.json", Kind.PACKAGE, True),
    ("go.mod", Kind.PACKAGE, True),        # sca-only, but supported == any scanner
    ("Foo.csproj", Kind.PACKAGE, True),    # sca-only
    ("yarn.lock", Kind.PACKAGE, True),
    ("uv.lock", Kind.PACKAGE, True),
    ("build.gradle", Kind.PACKAGE, False),
    ("pom.xml", Kind.PACKAGE, True),
    (".python-version", Kind.RUNTIME, False),
    (".nvmrc", Kind.RUNTIME, False),
    ("Dockerfile", Kind.CONTAINER, False),
    ("dependabot.yml", Kind.SCA_CONFIG, False),
    ("renovate.json", Kind.SCA_CONFIG, False),
    ("go.sum", Kind.LOCKFILE, False),
    # Newly tagged `dependencies` in panopticas 0.0.22 / 0.0.23 (#349).
    ("npm-shrinkwrap.json", Kind.PACKAGE, True),
    ("gradle.lockfile", Kind.PACKAGE, False),
    ("buildscript-gradle.lockfile", Kind.PACKAGE, False),
    ("libs.versions.toml", Kind.PACKAGE, True),
    ("build.sbt", Kind.PACKAGE, False),
    ("plugins.sbt", Kind.PACKAGE, False),
    ("build.mill", Kind.PACKAGE, False),
    ("package.mill.yaml", Kind.PACKAGE, False),
    ("build.sc", Kind.PACKAGE, False),
    ("Cargo.toml", Kind.PACKAGE, False),
    ("Cargo.lock", Kind.PACKAGE, False),
    ("Gemfile", Kind.PACKAGE, False),
    ("gems.rb", Kind.PACKAGE, False),
    ("Gemfile.lock", Kind.PACKAGE, False),
    ("gems.locked", Kind.PACKAGE, False),
    ("red-arrow.gemspec", Kind.PACKAGE, False),
    ("composer.json", Kind.PACKAGE, False),
    ("composer.lock", Kind.PACKAGE, False),
    ("Pipfile", Kind.PACKAGE, False),
    ("Pipfile.lock", Kind.PACKAGE, False),
    ("poetry.lock", Kind.PACKAGE, False),
    ("pdm.lock", Kind.PACKAGE, False),
    ("pylock.toml", Kind.PACKAGE, False),
    ("pylock.314.toml", Kind.PACKAGE, False),
    ("mix.exs", Kind.PACKAGE, False),
    ("mix.lock", Kind.PACKAGE, False),
    ("pubspec.yaml", Kind.PACKAGE, False),
    ("pubspec_overrides.yaml", Kind.PACKAGE, False),
    ("pubspec.lock", Kind.PACKAGE, False),
    ("bun.lock", Kind.PACKAGE, False),
    ("bun.lockb", Kind.PACKAGE, False),
    ("deno.lock", Kind.PACKAGE, False),
    ("pnpm-workspace.yaml", Kind.PACKAGE, False),
    ("App.fsproj", Kind.PACKAGE, False),
    ("App.vbproj", Kind.PACKAGE, False),
    ("App.nuspec", Kind.PACKAGE, False),
    ("Directory.Packages.props", Kind.PACKAGE, False),
    ("packages.lock.json", Kind.PACKAGE, False),
    ("packages.My.App.lock.json", Kind.PACKAGE, False),
    ("dotnet-tools.json", Kind.PACKAGE, False),
    ("paket.dependencies", Kind.PACKAGE, False),
    ("paket.lock", Kind.PACKAGE, False),
    ("paket.references", Kind.PACKAGE, False),
    (".ruby-version", Kind.RUNTIME, False),
    (".pnp.cjs", Kind.LOCKFILE, False),
    (".pnp.loader.mjs", Kind.LOCKFILE, False),
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
    # which is a separate registry row with its own parser — a different
    # format answering different questions, not a variant of package.json.
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
                 "npm-lock", "go-mod", "nuget-csproj", "uv-lock"):
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
        assert resolve_parser(self._entry("cargo-toml")) is None

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


def test_npm_shrinkwrap_reads_with_the_package_lock_parser(tmp_path):
    """npm documents npm-shrinkwrap.json as the package-lock.json format under
    a publishable name. Routing it to that parser is only right if the parser
    does not care about the filename, so read one."""
    from kospex.extractors.registry import resolve_parser
    path = tmp_path / "npm-shrinkwrap.json"
    path.write_text(
        '{"name": "app", "lockfileVersion": 3, "packages": {'
        '"": {"name": "app", "version": "1.0.0"},'
        '"node_modules/lodash": {"version": "4.17.21", "resolved":'
        ' "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz"}}}')
    parse = resolve_parser(classify(str(path)).extractor)
    assert [(r["package_name"], r["package_version"]) for r in parse(str(path))] == [
        ("lodash", "4.17.21")]


#: panopticas tags these `dependencies`, but they were tagged before #349 and
#: each still has no registry entry. Each is listed with where it is tracked,
#: so a NEW panopticas tag fails the test below instead of appearing on /osi/
#: as an unrecognised file.
KNOWN_UNREGISTERED = {
    "setup.py": "#137",
    "setup.cfg": "#137",
    "packages.config": "#182",
    "package.swift": "#363",
    "package.resolved": "#363",
    "podfile": "#363",
    "podfile.lock": "#363",
    "cartfile": "#363",
    "cartfile.private": "#363",
    "cartfile.resolved": "#363",
}


def test_every_panopticas_dependency_file_has_a_registry_entry():
    """#349: panopticas 0.0.22 and 0.0.23 tagged 40-odd more filenames
    `dependencies` and the registry knew none of them, so each would have
    shown as an unrecognised dependency file. Walk panopticas' own rules so
    the next release that adds a tag fails here instead.

    An older panopticas has fewer rules, so this only gets stricter as the
    dependency moves forward. Function rules (is_pip_requirements, ...) have
    no fixed name to probe and are covered by CASES.
    """
    from panopticas.constants import METADATA_RULES

    missing = []
    for rule_set, probe in (("exact_filename_rules", lambda k: k),
                            ("extension_rules", lambda k: "x" + k)):
        for key, tags in METADATA_RULES.get(rule_set, {}).items():
            if "dependencies" not in tags or key in KNOWN_UNREGISTERED:
                continue
            if classify(probe(key)).extractor is None:
                missing.append(key)
    assert not missing, f"dependency-tagged by panopticas, no registry entry: {missing}"


def test_known_unregistered_are_still_unregistered():
    """When one of KNOWN_UNREGISTERED gains an entry, take it off the list."""
    stale = [name for name in KNOWN_UNREGISTERED if classify(name).extractor is not None]
    assert not stale, f"now registered, remove from KNOWN_UNREGISTERED: {stale}"


def test_package_types_lists_each_registry_package_type_once():
    """package_types() is what `kospex sca -package` accepts as TYPE, so a
    package type added to the registry is accepted without a second list."""
    from kospex.extractors.registry import package_types

    types = package_types()
    assert types == sorted(set(types))
    assert None not in types
    assert {e.package_type for e in REGISTRY if e.package_type} <= set(types)
    assert {"npm", "pypi", "maven", "go"} <= set(types)
