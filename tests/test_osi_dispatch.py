"""krunner osi dispatch: registry-driven, and uniform across ecosystems.

Sub-project C1 replaced osi's hand-rolled substring chain with the extractor
registry, which is what closes the go.mod / *.csproj gap. These tests exercise
the dispatch body directly — the command itself needs a synced DB — so every
supported ecosystem is proven to resolve a parser and yield records.
"""
import pytest

from kospex.extractors.registry import classify, resolve_parser


def _kdeps():
    from kospex_dependencies import KospexDependencies
    return KospexDependencies()


def _dispatch(provider, full_path, kdeps):
    """Classify, then call krunner's real extraction function.

    Deliberately NOT a re-implementation of osi()'s loop body: an earlier
    version of this helper copied it, so removing the production code left every
    test passing. It now calls krunner.extract_dependency_file directly.
    """
    import krunner

    classification = classify(provider)
    extractor = classification.extractor
    if extractor is None or not classification.supported:
        return None, []

    reqs = krunner.extract_dependency_file(
        extractor, full_path, "s~o~r", provider, "abc123", kdeps
    )
    return extractor, reqs


FIXTURES = {
    "requirements.txt": "requests==2.31.0\nclick>=8.0\n",
    "pyproject.toml": '[project]\nname="d"\ndependencies=["requests==2.31.0"]\n',
    "package.json": '{"name":"d","version":"1.0.0",'
                    '"dependencies":{"express":"4.18.0"},'
                    '"devDependencies":{"jest":"29.0.0"}}',
    "go.mod": "module x\nrequire (\n  github.com/pkg/errors v0.9.1\n"
              "  github.com/stretchr/testify v1.8.4 // indirect\n)\n",
    "demo.csproj": '<Project><ItemGroup>'
                   '<PackageReference Include="Serilog" Version="3.1.1" />'
                   '</ItemGroup></Project>',
}


def _write_all(tmp_path):
    for name, content in FIXTURES.items():
        (tmp_path / name).write_text(content)


def _enrich(results):
    """The enrichment loop from krunner.osi(), with deps.dev stubbed.

    It runs once over the pooled results and stamps the same five keys on every
    row, which is part of what makes the rows uniform by CSV time — so any
    uniformity assertion has to include this stage, not just the dispatch.
    """
    import kospex_schema as KospexSchema
    req_to_use = {
        "direct": KospexSchema.PACKAGE_USE_DIRECT,
        "dev": KospexSchema.PACKAGE_USE_DEV,
        "resolved": KospexSchema.PACKAGE_USE_TRANSITIVE,
        "indirect": KospexSchema.PACKAGE_USE_TRANSITIVE,
    }
    for d in results:
        d["versions_behind"] = None
        d["advisories"] = 0
        d["resolution"] = "resolved"
        d["published_at"] = "2026-01-01"
        d["package_use"] = req_to_use.get(d.get("requirements_type", ""), "")
    return results


@pytest.mark.parametrize("provider,entry_name,package_type", [
    ("requirements.txt", "pypi-requirements", "pypi"),
    ("pyproject.toml", "pyproject", "pypi"),
    ("package.json", "npm-packagejson", "npm"),
    ("go.mod", "go-mod", "go"),
    ("demo.csproj", "nuget-csproj", "nuget"),
])
def test_every_ecosystem_dispatches_and_yields_records(
    provider, entry_name, package_type, tmp_path
):
    _write_all(tmp_path)

    extractor, reqs = _dispatch(provider, str(tmp_path / provider), _kdeps())

    assert extractor is not None, f"{provider} did not classify"
    assert extractor.name == entry_name
    assert extractor.package_type == package_type
    assert reqs, f"{provider} produced no records"
    assert all(r["package_type"] == package_type for r in reqs)


def test_gomod_yields_direct_and_transitive(tmp_path):
    """The gap C1 closes: go.mod reached osi at all, and brought its
    indirect modules with it."""
    _write_all(tmp_path)

    _extractor, reqs = _dispatch("go.mod", str(tmp_path / "go.mod"), _kdeps())

    by_name = {r["package_name"]: r["requirements_type"] for r in reqs}
    assert by_name["github.com/pkg/errors"] == "direct"
    assert by_name["github.com/stretchr/testify"] == "indirect"


def test_csproj_yields_records(tmp_path):
    """The other half of the gap — .csproj was silently skipped by osi."""
    _write_all(tmp_path)

    _extractor, reqs = _dispatch("demo.csproj", str(tmp_path / "demo.csproj"), _kdeps())

    assert [r["package_name"] for r in reqs] == ["Serilog"]


def test_unsupported_files_are_skipped_not_parsed(tmp_path):
    """osi's old substring check treated requirements.rst as a manifest.

    Both of these are present in a real estate, and neither is a package
    manifest — classify() must decline them rather than hand them to a parser.
    """
    (tmp_path / "requirements.rst").write_text("docs, not deps\n")
    (tmp_path / "setup.py").write_text("from setuptools import setup\n")

    for provider in ("requirements.rst", "setup.py"):
        extractor, reqs = _dispatch(provider, str(tmp_path / provider), _kdeps())
        assert extractor is None, provider
        assert reqs == []


def test_rows_are_key_uniform_across_ecosystems(tmp_path):
    """write_dict_to_csv takes its header from data[0] alone, and DictWriter
    raises on a later row carrying extra keys. The parsers return very different
    shapes (3 keys from parse_pip_requirements_file, 11 from the extractors/
    template), so the dispatch has to make rows uniform before they are pooled.
    """
    _write_all(tmp_path)
    kdeps = _kdeps()

    pooled = []
    for provider in FIXTURES:
        _extractor, reqs = _dispatch(provider, str(tmp_path / provider), kdeps)
        pooled.extend(reqs)
    _enrich(pooled)

    key_sets = {frozenset(r.keys()) for r in pooled}
    assert len(key_sets) == 1, (
        "osi rows must share one key set or the assessment CSV write raises; "
        f"got {len(key_sets)} distinct shapes"
    )


def test_pooled_rows_survive_the_csv_writer(tmp_path):
    """The failure this guards is a ValueError at CSV-write time, so write one."""
    import krunner_utils as KrunnerUtils
    _write_all(tmp_path)
    kdeps = _kdeps()

    pooled = []
    for provider in FIXTURES:
        _extractor, reqs = _dispatch(provider, str(tmp_path / provider), kdeps)
        pooled.extend(reqs)
    _enrich(pooled)

    out = tmp_path / "osi.csv"
    KrunnerUtils.write_dict_to_csv(str(out), pooled)

    lines = out.read_text().strip().splitlines()
    assert len(lines) == len(pooled) + 1  # header + one row each
