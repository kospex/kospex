"""A file an extractor could not read must raise, never return [].

`classify_outcome` (kospex/osi_outcomes.py) ends:

    return EXTRACTED if row_count else EMPTY

and PARSE_ERROR is reachable only from its `error is not None` branch, where
`error` is the exception the parser raised. Raising is therefore the *only*
error channel an extractor has. A parser that catches its own exception and
returns [] produces row_count == 0 with error None, so the outcome recorded
against the file is `empty`, whose documented meaning is:

    EMPTY = "empty"   # parser ran, produced nothing -- genuinely declares none

"I could not read this file" is then stored as "I read this file and it
declares no dependencies", and a repository holding an unreadable manifest
reports clean. The input is third-party repository content, so this is
fail-open on attacker-influenced data in a tool whose job is to notice
dependencies.

It is also a regression in signal as coverage grows: a file with no parser
reports `unsupported`, which is honest, and gaining a parser can downgrade
that to `empty`, which is not.

`npmlock.py` was fixed first and `UnreadableLockfile` is its precedent. These
tests cover the extractors that still returned [] -- pnpm, gomod, nuget, and
pyproject.toml, which the registry-wide contract test below turned up -- and
pin the rule as a contract driven from the registry, so the next parser
inherits it.

The rule has three categories, and the third is why this is not a blanket
"replace every return [] with a raise":

  unreadable                  missing, a directory, permissions, bad
  -> raise                    encoding, invalid YAML/XML

  read but uninterpretable    unknown lockfileVersion, a non-mapping root, a
  -> raise                    `packages:` that is not a mapping, a DOCTYPE
                              refusal, a root tag that is not <Project>, a
                              go.mod with no `module` directive

  read, declares nothing      a valid go.mod with no `require`, an empty
  -> return []                <Project></Project>, pnpm `importers: {.: {}}`

The third category is real data, not a hypothetical: of 188 go.mod files on a
development estate 42 declare no `require` at all (the smallest is 27 bytes),
and 4 of 9 .csproj files declare no PackageReference. Those must keep
reporting `empty`, because for them `empty` is true.
"""

from pathlib import Path

import pytest

from kospex.extractors.errors import UnreadableManifest
from kospex.extractors.gomod import extract_gomod, parse_gomod
from kospex.extractors.nuget import extract_csproj
from kospex.extractors.pnpm import extract_pnpm_lock
from kospex.osi_outcomes import EMPTY, PARSE_ERROR, classify_outcome

PNPM_FIXTURES = Path(__file__).parent / "fixtures" / "pnpm"


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return str(path)


def _write_bytes(tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    return str(path)


class TestSharedErrorType:
    """One error type across the extractors package.

    npmlock raised `UnreadableLockfile` from its own module, so a caller that
    wanted to treat "could not read" uniformly had to import from each
    extractor and keep the tuple in step. The shared base makes the contract
    expressible in one `except`.
    """

    def test_unreadable_lockfile_is_an_unreadable_manifest(self):
        """npmlock's existing name keeps working, and the base catches it."""
        from kospex.extractors.npmlock import UnreadableLockfile

        assert issubclass(UnreadableLockfile, UnreadableManifest)

    def test_the_base_is_catchable_as_an_exception(self):
        assert issubclass(UnreadableManifest, Exception)

    def test_an_unreadable_manifest_is_classified_as_a_parse_error(self):
        """The whole point: the outcome must not be a benign one."""
        outcome = classify_outcome(
            "pnpm-lock.yaml", row_count=0,
            error=UnreadableManifest("could not read"))

        assert outcome == PARSE_ERROR
        assert outcome != EMPTY


class TestRegistryWideContract:
    """Every registered parser raises for a path it cannot read.

    A contract only one extractor honours is not a contract -- the npmlock fix
    had to patch `assess` for exactly that reason. Driving the registry rather
    than a hand-written list means a parser added later is covered on the day
    it is registered, which is the only time this check is cheap.

    A directory is the probe because it is unreadable for every format at once:
    `open()` raises IsADirectoryError regardless of what the parser expected to
    find inside.
    """

    def _parsers(self):
        from kospex_dependencies import KospexDependencies
        from kospex.extractors.registry import REGISTRY, resolve_parser

        kdeps = KospexDependencies()
        return [
            (entry.name, resolve_parser(entry, {"KospexDependencies": kdeps}))
            for entry in REGISTRY
            if entry.parse_ref is not None
        ]

    def test_the_registry_has_parsers_to_check(self):
        """Guards against a vacuous pass if resolution silently returns none."""
        parsers = self._parsers()

        assert len(parsers) >= 7, f"only found {len(parsers)} parsers"
        assert all(callable(fn) for _name, fn in parsers)

    def test_no_parser_returns_empty_for_a_directory(self, tmp_path):
        fail_open = []

        for name, parser in self._parsers():
            try:
                records = parser(str(tmp_path))
            except Exception:
                continue                      # raising is the contract
            fail_open.append((name, len(records)))

        assert not fail_open, (
            "these parsers returned a value for a path they could not read, "
            f"which is recorded as `empty`: {fail_open}"
        )


class TestPnpmUnreadable:
    """pnpm-lock.yaml had six `return []` paths on unreadable input."""

    def test_missing_file(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_pnpm_lock(str(tmp_path / "nope.yaml"))

    def test_invalid_yaml(self):
        with pytest.raises(UnreadableManifest):
            extract_pnpm_lock(str(PNPM_FIXTURES / "malformed.yaml"))

    def test_directory(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_pnpm_lock(str(tmp_path))

    def test_binary_content(self, tmp_path):
        path = _write_bytes(tmp_path, "binary.yaml", b"\xff\xfe\x00\x01\x02\x03")

        with pytest.raises(UnreadableManifest):
            extract_pnpm_lock(path)

    def test_empty_file(self, tmp_path):
        """pnpm always writes lockfileVersion, so a 0-byte file is not one.

        There is a real 0-byte pnpm-lock.yaml on the development estate (a test
        fixture inside another project). `parse_error` is the honest outcome
        for it: nothing can be concluded about a repository's npm dependencies
        from an empty lockfile.
        """
        with pytest.raises(UnreadableManifest):
            extract_pnpm_lock(_write(tmp_path, "empty.yaml", ""))

    def test_non_mapping_root(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_pnpm_lock(_write(tmp_path, "list.yaml", "- a\n- b\n"))

    def test_unknown_lockfile_version(self):
        """The loud choice, deliberately.

        pnpm v10 exists and this parser knows 5, 6 and 9, so every v10
        lockfile would otherwise report `empty`. The fixture wraps a real
        package, which is the evidence that returning [] here hides data
        rather than reporting its absence.
        """
        with pytest.raises(UnreadableManifest, match="99.0"):
            extract_pnpm_lock(str(PNPM_FIXTURES / "unknown-version.yaml"))

    def test_packages_present_but_not_a_mapping(self, tmp_path):
        """Read but uninterpretable: the section exists and cannot be used."""
        path = _write(tmp_path, "pnpm-lock.yaml",
                      "lockfileVersion: '6.0'\npackages: nonsense\n")

        with pytest.raises(UnreadableManifest):
            extract_pnpm_lock(path)


class TestPnpmGenuinelyEmpty:
    """The third category. These must keep returning []."""

    def test_no_packages_section_is_genuinely_empty(self):
        """`importers: {.: {}}` is a project that declares nothing.

        pnpm omits `packages:` entirely in that case, so its absence is a fact
        about the project, not a malformed file -- unlike a `packages:` key
        holding something unusable.
        """
        assert extract_pnpm_lock(str(PNPM_FIXTURES / "no-packages.yaml")) == []

    def test_empty_packages_mapping_is_genuinely_empty(self, tmp_path):
        path = _write(tmp_path, "pnpm-lock.yaml",
                      "lockfileVersion: '6.0'\npackages: {}\n")

        assert extract_pnpm_lock(path) == []


class TestGomodUnreadable:
    """go.mod had a bare `except Exception` returning []."""

    def test_missing_file(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_gomod(str(tmp_path / "nope.mod"))

    def test_directory(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_gomod(str(tmp_path))

    def test_binary_content(self, tmp_path):
        path = _write_bytes(tmp_path, "go.mod", b"\xff\xfe\x00\x01\x02\x03")

        with pytest.raises(UnreadableManifest):
            extract_gomod(path)

    def test_no_module_directive(self, tmp_path):
        """`module` is required by the go.mod spec, so its absence means this
        is not a go.mod.

        Without this check go.mod is the one format in the trio with no
        structural gate at all: any text file under that name reads as a
        dependency-free module. All 188 go.mod files on a development estate
        declare `module`, so nothing real is reclassified by requiring it.
        """
        path = _write(tmp_path, "go.mod", "this is not a go module file\n")

        with pytest.raises(UnreadableManifest):
            extract_gomod(path)

    def test_the_underlying_parser_raises_too(self, tmp_path):
        """`parse_gomod` is public and reached separately.

        `KospexDependencies.parse_go_mod_from_file()` delegates to it, so the
        contract has to hold at the parser, not only at the extractor wrapper.
        """
        with pytest.raises(UnreadableManifest):
            parse_gomod(str(tmp_path / "nope.mod"))


class TestGomodGenuinelyEmpty:
    def test_module_with_no_requires(self, tmp_path):
        """The measured case: 42 of 188 real go.mod files look like this.

        The smallest is 27 bytes. A library with no third-party dependencies is
        the normal shape for a small Go module, and `empty` is true of it.
        """
        path = _write(tmp_path, "go.mod", "module example.com/x\n\ngo 1.21\n")

        assert extract_gomod(path) == []

    def test_an_empty_require_block_is_still_empty(self, tmp_path):
        path = _write(tmp_path, "go.mod",
                      "module example.com/x\n\ngo 1.21\n\nrequire (\n)\n")

        assert extract_gomod(path) == []


class TestCsprojUnreadable:
    """.csproj had four `return []` paths, one of them a security refusal."""

    def test_missing_file(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_csproj(str(tmp_path / "nope.csproj"))

    def test_directory(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_csproj(str(tmp_path))

    def test_malformed_xml(self, tmp_path):
        path = _write(tmp_path, "a.csproj", "<Project><ItemGroup>")

        with pytest.raises(UnreadableManifest):
            extract_csproj(path)

    def test_root_tag_is_not_a_project(self, tmp_path):
        """Well-formed XML that is not an MSBuild project.

        Read but uninterpretable: `.//PackageReference` finds nothing in it, so
        without this gate any XML file renamed to .csproj reports as a project
        with no NuGet packages. All 9 .csproj files on a development estate
        have a <Project> root.
        """
        path = _write(tmp_path, "a.csproj", "<html><body>hi</body></html>")

        with pytest.raises(UnreadableManifest):
            extract_csproj(path)


class TestCsprojDoctypeRefusalIsNotAnEmptyProject:
    """CWE-776: the entity-expansion guard reported its refusal as `empty`.

    The guard itself is right -- no legitimate MSBuild file carries a DOCTYPE,
    and refusing before parsing removes the expansion class outright. What was
    wrong is what it returned: a hostile .csproj was refused correctly and then
    recorded as a project with no NuGet packages, so the security control's own
    outcome was indistinguishable from a clean scan.
    """

    BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE root [
 <!ENTITY a "AAAAAAAAAA">
 <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
 <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
]>
<Project><PackageReference Include="&c;" Version="1.0" /></Project>"""

    def test_doctype_raises(self, tmp_path):
        path = _write(tmp_path, "a.csproj", self.BILLION_LAUGHS)

        with pytest.raises(UnreadableManifest):
            extract_csproj(path)

    def test_the_refusal_branch_is_the_one_that_fired(self, tmp_path):
        """Proves the guard ran *before* the parser, non-vacuously.

        The previous version of this check asserted `not any("AAAA" in str(r)
        for r in records)` against a list it had already asserted was empty,
        which passes for any implementation. Naming the DOCTYPE in the error is
        a real discriminator: if the guard were moved after `ET.fromstring`,
        the expansion would already have run and the failure would arrive as a
        parse error or as records, not as this refusal.
        """
        path = _write(tmp_path, "a.csproj", self.BILLION_LAUGHS)

        with pytest.raises(UnreadableManifest, match="DOCTYPE"):
            extract_csproj(path)

    def test_an_ordinary_xml_declaration_is_still_accepted(self, tmp_path):
        """Refusing DOCTYPE must not refuse a normal prolog."""
        path = _write(tmp_path, "a.csproj", """<?xml version="1.0" encoding="utf-8"?>
<Project>
  <ItemGroup><PackageReference Include="Serilog" Version="3.1.1" /></ItemGroup>
</Project>""")

        assert [r["package_name"] for r in extract_csproj(path)] == ["Serilog"]


class TestCsprojGenuinelyEmpty:
    def test_project_with_no_package_references(self, tmp_path):
        """4 of 9 real .csproj files declare no PackageReference."""
        path = _write(tmp_path, "a.csproj", "<Project></Project>")

        assert extract_csproj(path) == []

    def test_project_with_an_empty_item_group(self, tmp_path):
        path = _write(tmp_path, "a.csproj",
                      "<Project><ItemGroup></ItemGroup></Project>")

        assert extract_csproj(path) == []

    def test_update_and_remove_references_are_not_declarations(self, tmp_path):
        """An Update= or Remove= entry adjusts an existing reference.

        Still [] rather than a raise: the file is well-formed and genuinely
        declares nothing new.
        """
        path = _write(tmp_path, "a.csproj", """<Project><ItemGroup>
  <PackageReference Update="Serilog" Version="3.1.1" />
  <PackageReference Remove="Newtonsoft.Json" />
</ItemGroup></Project>""")

        assert extract_csproj(path) == []


class TestBothScanPathsSurviveARaise:
    """A parser contract only one caller honours is not a contract.

    `osi_extract` wraps the parser and records PARSE_ERROR; `assess` was
    unguarded until the npmlock fix, so raising crashed `kospex sca` instead.
    Both are now broad, and these drive the real functions rather than
    reasoning about them.
    """

    def test_assess_reports_an_unreadable_file_without_crashing(self, tmp_path):
        import contextlib
        import io

        from kospex_dependencies import KospexDependencies

        path = _write(tmp_path, "a.csproj", "<Project><ItemGroup>")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = KospexDependencies().assess(path)

        assert result is None
        assert "could not parse" in buf.getvalue().lower()

    def test_outcome_for_a_raising_parser_is_parse_error(self):
        """The end-to-end fact: the file is recorded as unread, not as empty."""
        for provider in ("pnpm-lock.yaml", "go.mod", "Acme.csproj"):
            assert classify_outcome(
                provider, row_count=0,
                error=UnreadableManifest("boom")) == PARSE_ERROR
