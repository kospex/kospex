"""assess() dispatches through the extractor registry (sub-project C2).

`kospex sca` / `kospex deps` reached six hand-rolled branches — `basefile ==`,
`is_npm_package()`, `is_nuget_package()`, `is_pip_requirements_file()` — while
`krunner osi` used the registry. That is the divergence #180 exists to remove.

It also settles `dev_deps`: extraction is always complete and dev dependencies
are tagged PACKAGE_USE_DEV, with the flag controlling only what the printed
table shows. Partial extraction was what made `sca -dev` inert (#181) and what
blocked a whole-file demote in assess() (#151).
"""
import contextlib
import io

import pytest

import kospex_schema as KospexSchema
from kospex_dependencies import KospexDependencies


def _kdeps():
    kd = KospexDependencies()
    kd.depsdev_record = lambda pt, pn, pv: {
        "package_name": pn, "package_version": pv, "package_type": pt,
        "versions_behind": 1, "advisories": 0,
        "published_at": "2026-01-01T00:00:00Z", "resolution": "resolved",
        "source_repo": "",
    }
    kd.get_pypi_source_repo = lambda name: ""
    return kd


FIXTURES = {
    "requirements.txt": "requests==2.31.0\nclick>=8.0\n",
    "pyproject.toml": '[project]\nname="d"\ndependencies=["requests==2.31.0"]\n',
    "package.json": '{"name":"d","version":"1.0.0",'
                    '"dependencies":{"express":"^4.18.0"},'
                    '"devDependencies":{"jest":"~29.0.0","eslint":"8.0.0"}}',
    "go.mod": "module x\nrequire (\n  github.com/pkg/errors v0.9.1\n"
              "  github.com/stretchr/testify v1.8.4 // indirect\n)\n",
    "demo.csproj": '<Project><ItemGroup>'
                   '<PackageReference Include="Serilog" Version="3.1.1" />'
                   '</ItemGroup></Project>',
}


def _write(tmp_path, name):
    p = tmp_path / name
    p.write_text(FIXTURES[name])
    return str(p)


def _assess(kd, path, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return kd.assess(path, **kw) or []


class TestExtractionIsAlwaysComplete:
    """#181: dev_deps filtered extraction, so sca and deps disagreed and neither
    returned the whole manifest."""

    def test_dev_dependencies_are_extracted_without_the_flag(self, tmp_path):
        kd = _kdeps()
        path = _write(tmp_path, "package.json")

        records = _assess(kd, path)

        assert sorted(r["package_name"] for r in records) == [
            "eslint", "express", "jest",
        ]

    def test_dev_dependencies_are_tagged_dev(self, tmp_path):
        kd = _kdeps()
        path = _write(tmp_path, "package.json")

        by_name = {r["package_name"]: r for r in _assess(kd, path)}

        assert by_name["express"]["package_use"] == KospexSchema.PACKAGE_USE_DIRECT
        assert by_name["jest"]["package_use"] == KospexSchema.PACKAGE_USE_DEV
        assert by_name["eslint"]["package_use"] == KospexSchema.PACKAGE_USE_DEV

    def test_the_flag_does_not_change_what_is_extracted(self, tmp_path):
        """-dev is a display control, so both settings must return the same set."""
        kd = _kdeps()
        path = _write(tmp_path, "package.json")

        without = sorted(r["package_name"] for r in _assess(kd, path))
        with_flag = sorted(r["package_name"] for r in _assess(kd, path, dev_deps=True))

        assert without == with_flag

    def test_sca_and_deps_kwargs_agree(self, tmp_path):
        """#181: sca builds kwargs via locals() producing `dev`, deps sets
        `dev_deps`. With extraction unconditional the mismatch cannot cause a
        silent under-count."""
        kd = _kdeps()
        path = _write(tmp_path, "package.json")

        deps_style = sorted(r["package_name"] for r in _assess(kd, path, dev_deps=True))
        sca_style = sorted(r["package_name"] for r in _assess(
            kd, path, dev=True, save=False, malware=False, out=None, repo=None))

        assert deps_style == sca_style


class TestRegistryDispatch:
    @pytest.mark.parametrize("name,package_type,expected", [
        ("requirements.txt", "pypi", ["click", "requests"]),
        ("pyproject.toml", "pypi", ["requests"]),
        ("go.mod", "go", ["github.com/pkg/errors", "github.com/stretchr/testify"]),
        ("demo.csproj", "nuget", ["Serilog"]),
    ])
    def test_every_ecosystem_dispatches(self, name, package_type, expected, tmp_path):
        kd = _kdeps()

        records = _assess(kd, _write(tmp_path, name))

        assert sorted(r["package_name"] for r in records) == expected
        assert all(r["package_type"] == package_type for r in records)

    def test_unsupported_file_returns_nothing(self, tmp_path):
        kd = _kdeps()
        p = tmp_path / "notes.rst"
        p.write_text("not a manifest\n")

        assert _assess(kd, str(p)) == []

    def test_gomod_keeps_direct_and_transitive(self, tmp_path):
        """#178's classification must survive the dispatch change."""
        kd = _kdeps()

        by_name = {r["package_name"]: r for r in _assess(kd, _write(tmp_path, "go.mod"))}

        assert by_name["github.com/pkg/errors"]["package_use"] == \
            KospexSchema.PACKAGE_USE_DIRECT
        assert by_name["github.com/stretchr/testify"]["package_use"] == \
            KospexSchema.PACKAGE_USE_TRANSITIVE


class TestPackageUseIsAlwaysSet:
    """Before C2 the pypi paths left package_use unset, so requirements.txt and
    pyproject.toml rows carried NULL where npm/go/nuget carried a value."""

    @pytest.mark.parametrize("name", ["requirements.txt", "pyproject.toml"])
    def test_pypi_rows_are_marked_direct(self, name, tmp_path):
        kd = _kdeps()

        records = _assess(kd, _write(tmp_path, name))

        assert records
        assert all(
            r["package_use"] == KospexSchema.PACKAGE_USE_DIRECT for r in records
        ), f"{name} left package_use unset"

    @pytest.mark.parametrize("name", list(FIXTURES))
    def test_no_ecosystem_leaves_package_use_empty(self, name, tmp_path):
        kd = _kdeps()

        for r in _assess(kd, _write(tmp_path, name)):
            assert r.get("package_use"), f"{name}: {r['package_name']} has no package_use"


class TestEnrichmentIsPreserved:
    """The per-ecosystem enrichment extras must survive unification."""

    def test_npm_keeps_the_semantic_prefix(self, tmp_path):
        kd = _kdeps()

        by_name = {r["package_name"]: r for r in _assess(kd, _write(tmp_path, "package.json"))}

        assert by_name["express"]["semantic"] == "^"
        assert by_name["jest"]["semantic"] == "~"

    def test_pypi_falls_back_to_the_pypi_source_repo(self, tmp_path):
        """deps.dev often has no source repo for pypi; the fallback queries PyPI."""
        kd = _kdeps()
        kd.get_pypi_source_repo = lambda name: f"https://github.com/src/{name}"

        records = _assess(kd, _write(tmp_path, "requirements.txt"))

        assert all(r["source_repo"].startswith("https://github.com/src/") for r in records)
