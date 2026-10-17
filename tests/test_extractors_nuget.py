"""Tests for the pure NuGet .csproj extractor (sub-project C1).

The parsing previously lived inside nuget_assess(), which had no return
statement — so the records were built, printed as a table and discarded (#107).
Extracted here as a pure function so both scan paths can share it.

A file this extractor could not read raises UnreadableManifest rather than
returning []; the shared contract and its three categories live in
tests/test_extractor_unreadable_manifests.py.
"""
import pytest

from kospex.extractors.errors import UnreadableManifest
from kospex.extractors.nuget import _template, extract_csproj


def _write(tmp_path, content, name="demo.csproj"):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


class TestTemplateContract:
    def test_template_matches_get_package_template(self):
        from kospex_dependencies import KospexDependencies
        assert set(_template().keys()) == set(
            KospexDependencies().get_package_template().keys()
        )


class TestExtract:
    def test_records_package_references(self, tmp_path):
        path = _write(tmp_path, """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
    <PackageReference Include="Serilog" Version="3.1.1" />
  </ItemGroup>
</Project>""")

        records = extract_csproj(path)

        assert {(r["package_name"], r["package_version"]) for r in records} == {
            ("Newtonsoft.Json", "13.0.3"), ("Serilog", "3.1.1"),
        }

    def test_all_references_are_direct(self, tmp_path):
        path = _write(tmp_path, """<Project>
  <ItemGroup><PackageReference Include="Serilog" Version="3.1.1" /></ItemGroup>
</Project>""")

        assert extract_csproj(path)[0]["requirements_type"] == "direct"

    def test_records_have_the_full_template_shape(self, tmp_path):
        path = _write(tmp_path, """<Project>
  <ItemGroup><PackageReference Include="Serilog" Version="3.1.1" /></ItemGroup>
</Project>""")

        assert set(extract_csproj(path)[0].keys()) == set(_template().keys())

    def test_reference_without_version_is_still_recorded(self, tmp_path):
        """Central Package Management omits Version on the PackageReference.
        The package is still declared, so dropping it would under-report."""
        path = _write(tmp_path, """<Project>
  <ItemGroup><PackageReference Include="Serilog" /></ItemGroup>
</Project>""")

        records = extract_csproj(path)

        assert len(records) == 1
        assert records[0]["package_name"] == "Serilog"
        assert records[0]["package_version"] == ""

    def test_malformed_xml_raises(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_csproj(_write(tmp_path, "<Project><ItemGroup>"))

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_csproj(str(tmp_path / "nope.csproj"))

    def test_project_with_no_references_returns_empty(self, tmp_path):
        """Still []: 4 of 9 .csproj files on a development estate declare
        no PackageReference, and `empty` is true of them."""
        assert extract_csproj(_write(tmp_path, "<Project></Project>")) == []


class TestAssessPersistsNuget:
    """#107: nuget_assess() had no return statement and assess()'s dispatch did
    not capture its result, so NuGet dependencies were built, printed as a table
    and discarded. Two independent bugs — fixing either alone changed nothing.

    The function-level test that paired with this one is gone: nuget_assess()
    was retired when assess() moved to registry dispatch (sub-project C2), and
    a test against a deleted function proves nothing. The assess()-level
    assertion below is the one that matters, since that is the path that runs.
    """

    def _kdeps(self):
        from kospex_dependencies import KospexDependencies
        kd = KospexDependencies()
        kd.depsdev_record = lambda pt, pn, pv: {
            "package_name": pn, "package_version": pv, "package_type": pt,
        }
        return kd

    def test_assess_returns_nuget_records(self, tmp_path):
        """The dispatch must capture the result, not just call it."""
        import contextlib
        import io
        path = _write(tmp_path, """<Project>
  <ItemGroup>
    <PackageReference Include="Serilog" Version="3.1.1" />
  </ItemGroup>
</Project>""")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            results = self._kdeps().assess(path)

        assert [r["package_name"] for r in results] == ["Serilog"]


class TestEntityExpansion:
    """CWE-776: uncontrolled XML entity expansion.

    Manifests come from cloned third-party repositories, so the parser input is
    attacker-influenced. Python's ElementTree refuses *external* entities but
    does expand *internal* ones — four nested levels reach 10,000 characters,
    nine or ten reach gigabytes, exhausting memory during a scan.

    Internal entities must be declared in a DTD, and no legitimate MSBuild
    project file carries a DOCTYPE, so rejecting it removes the class outright.

    The refusal RAISES. It returned [] until the fail-open fix, which recorded
    the guard's own outcome as a project with no NuGet packages — so a hostile
    file was refused correctly and then reported as a clean scan.
    """

    def test_doctype_is_rejected(self, tmp_path):
        path = _write(tmp_path, """<?xml version="1.0"?>
<!DOCTYPE root [
 <!ENTITY a "AAAAAAAAAA">
 <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
 <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
]>
<Project><PackageReference Include="&c;" Version="1.0" /></Project>""")

        with pytest.raises(UnreadableManifest):
            extract_csproj(path)

    def test_doctype_rejected_before_any_expansion(self, tmp_path):
        """The guard must run before the parser, not filter results after it —
        otherwise the expansion has already allocated the memory.

        Asserting that the DOCTYPE refusal is the branch that fired is what
        makes this non-vacuous. The previous version asserted `not any("AAAA"
        in str(r) for r in records)` against a list it had already asserted was
        empty, so it passed for any implementation; if the guard moved after
        `ET.fromstring` the expansion would already have run and the failure
        would arrive as a parse error instead.
        """
        path = _write(tmp_path, """<!DOCTYPE root [
 <!ENTITY a "AAAAAAAAAA">
 <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
]>
<Project><PackageReference Include="&b;" Version="1.0" /></Project>""")

        with pytest.raises(UnreadableManifest, match="DOCTYPE") as caught:
            extract_csproj(path)

        assert "AAAA" not in str(caught.value)

    def test_ordinary_xml_declaration_is_still_accepted(self, tmp_path):
        """Rejecting DOCTYPE must not reject a normal prolog."""
        path = _write(tmp_path, """<?xml version="1.0" encoding="utf-8"?>
<Project>
  <ItemGroup><PackageReference Include="Serilog" Version="3.1.1" /></ItemGroup>
</Project>""")

        assert extract_csproj(path)[0]["package_name"] == "Serilog"
