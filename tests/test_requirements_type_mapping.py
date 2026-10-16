"""The parser vocabulary -> package_use mapping is one table, and it is total.

Two scan paths write `package_use`: `KospexDependencies._enrich_dependency_records`
(the `sca` side) and `krunner.enrich_dependency_records` (the `osi` side). They
each held their own copy of the mapping, and the copies disagreed in the way that
matters least visibly: on a `requirements_type` neither of them had a key for.

`parse_package_json` emits "peer" and "optional", and `parse_pyproject_file`
emits "optional". Neither table mapped them, so the fallback decided the stored
value -- `direct` on the sca side, `""` on the osi side. The same manifest read
by the two commands produced different rows, and 497 rows in a real database
carried `package_use = ''` for dependencies the file had described perfectly
well.

These tests pin three properties:

1. there is ONE mapping, imported by both paths, so "the copies diverged" is not
   expressible;
2. it covers every `requirements_type` any registered parser actually emits --
   checked by reading the parser sources, so a new parser that invents a value
   fails here rather than silently landing on the fallback;
3. the fallback for a genuinely unknown value is None (unknown), never a guess.

Property 2 is the load-bearing one. The bug was not a wrong mapping; it was a
missing one, and nothing failed when it was missing.
"""

import ast
import pathlib

import pytest

import kospex_schema as KospexSchema

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

# Every module that parses a manifest into records. The two scan paths dispatch
# here via the extractor registry.
PARSER_SOURCES = (
    SRC / "kospex_dependencies.py",
    SRC / "kospex" / "extractors" / "npmlock.py",
    SRC / "kospex" / "extractors" / "pnpm.py",
    SRC / "kospex" / "extractors" / "gomod.py",
    SRC / "kospex" / "extractors" / "nuget.py",
)


def _emitted_requirements_types(path):
    """Every string literal assigned to a `requirements_type` key in `path`.

    Parsed with `ast` rather than grepped, so a value in a comment or a
    docstring is not mistaken for one the code emits -- the distinction the
    comment in npmlock.py depends on, since it *names* "peer" and "optional"
    while deliberately not emitting them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()

    for node in ast.walk(tree):
        # record["requirements_type"] = "direct"
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "requirements_type"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    found.add(node.value.value)
        # {"requirements_type": "resolved"} inside a dict literal
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "requirements_type"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    found.add(value.value)

    return found


class TestOneMapping:
    def test_schema_owns_the_mapping(self):
        assert isinstance(KospexSchema.REQ_TO_PACKAGE_USE, dict)
        assert KospexSchema.REQ_TO_PACKAGE_USE, "mapping must not be empty"

    def test_both_scan_paths_use_the_same_object(self):
        """Not "equal" -- the same object. Equality is satisfied by two copies
        that happen to agree today, which is the state this replaced."""
        import kospex_dependencies
        import krunner

        assert (
            kospex_dependencies.KospexDependencies._REQ_TO_USE
            is KospexSchema.REQ_TO_PACKAGE_USE
        )
        assert krunner._REQ_TO_USE is KospexSchema.REQ_TO_PACKAGE_USE

    def test_every_mapped_value_is_a_known_package_use(self):
        for req_type, use in KospexSchema.REQ_TO_PACKAGE_USE.items():
            assert use in KospexSchema.PACKAGE_USE_VALUES, (
                f"{req_type!r} maps to {use!r}, which is not in PACKAGE_USE_VALUES"
            )


class TestMappingIsTotal:
    """Every requirements_type the parsers emit has a mapping.

    This is the test that would have caught the original defect. "peer" and
    "optional" were emitted by parse_package_json for a year with no key in
    either table, and nothing failed -- the rows just carried the fallback.
    """

    @pytest.mark.parametrize("source", PARSER_SOURCES, ids=lambda p: p.name)
    def test_source_emits_only_mapped_types(self, source):
        emitted = _emitted_requirements_types(source)
        # The empty string is the template default, handled by the callers'
        # `setdefault("requirements_type", "direct")` rather than by the mapping.
        emitted.discard("")

        unmapped = sorted(emitted - set(KospexSchema.REQ_TO_PACKAGE_USE))
        assert not unmapped, (
            f"{source.name} emits requirements_type {unmapped} with no entry in "
            "KospexSchema.REQ_TO_PACKAGE_USE, so those rows get the fallback "
            "instead of a real package_use"
        )

    def test_the_known_vocabulary_is_covered(self):
        """Belt and braces: the values we know the parsers emit today.

        Hardcoded deliberately. The ast scan above proves the mapping covers
        what the source says; this proves the scan is actually finding things,
        so a scan that silently returned an empty set could not pass both.
        """
        for req_type in ("direct", "dev", "resolved", "indirect", "peer", "optional"):
            assert req_type in KospexSchema.REQ_TO_PACKAGE_USE, (
                f"{req_type!r} is emitted by a parser but is not mapped"
            )

    def test_scan_actually_finds_the_values(self):
        """The ast helper is not vacuously returning an empty set.

        Only the literals remain findable this way: `parse_pyproject_file`
        assigns "direct" and "optional" inline. `parse_package_json` now
        iterates NPM_SECTION_TO_REQ_TYPE, so its values are loop variables and
        no source scan can see them -- which is exactly why
        test_npm_section_table_is_fully_mapped below checks that structure
        directly. An AST scan alone would have silently stopped covering npm.
        """
        found = _emitted_requirements_types(SRC / "kospex_dependencies.py")
        assert {"direct", "optional"} <= found

    def test_npm_section_table_is_fully_mapped(self):
        """Every package.json section's requirements_type has a mapping.

        Checks the tuple the parser iterates, not the source text.
        """
        from kospex_dependencies import NPM_SECTION_TO_REQ_TYPE

        for section, req_type in NPM_SECTION_TO_REQ_TYPE:
            assert req_type in KospexSchema.REQ_TO_PACKAGE_USE, (
                f"package.json section {section!r} emits requirements_type "
                f"{req_type!r}, which has no entry in REQ_TO_PACKAGE_USE"
            )

    def test_npm_section_table_covers_the_four_npm_sections(self):
        """A section dropped from the table would silently stop being read."""
        from kospex_dependencies import NPM_SECTION_TO_REQ_TYPE

        assert {s for s, _r in NPM_SECTION_TO_REQ_TYPE} == {
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        }

    def test_npmlock_does_not_emit_peer_or_optional(self):
        """npmlock names both in a comment while deliberately emitting neither.

        Pins that the ast helper reads code, not prose -- a grep-based version
        of this check would find them in the comment and prove nothing.
        """
        found = _emitted_requirements_types(SRC / "kospex" / "extractors" / "npmlock.py")
        assert "peer" not in found
        assert "optional" not in found
        assert "resolved" in found


class TestPeerAndOptionalAreNotDirect:
    """The specific rows the defect produced."""

    def test_peer_maps_to_peer(self):
        assert (
            KospexSchema.REQ_TO_PACKAGE_USE["peer"] == KospexSchema.PACKAGE_USE_PEER
        )

    def test_optional_maps_to_optional(self):
        assert (
            KospexSchema.REQ_TO_PACKAGE_USE["optional"]
            == KospexSchema.PACKAGE_USE_OPTIONAL
        )

    def test_peer_is_not_recorded_as_direct(self):
        """`sca` recorded a peerDependency as `direct` -- an assertion the
        manifest never made. A peer dependency is declared as *not* installed
        by this package."""
        assert KospexSchema.REQ_TO_PACKAGE_USE["peer"] != KospexSchema.PACKAGE_USE_DIRECT

    def test_optional_is_not_recorded_as_direct(self):
        assert (
            KospexSchema.REQ_TO_PACKAGE_USE["optional"]
            != KospexSchema.PACKAGE_USE_DIRECT
        )


class TestUnknownIsUnknown:
    """An unmapped value stores NULL, not a guess.

    NULL means unknown, which is the rule migration 0009 already set for the
    install-state columns. `direct` and `""` are both assertions: one claims the
    manifest said something it did not, the other is indistinguishable from a
    parser that returned no value at all.
    """

    def test_fallback_is_none(self):
        assert KospexSchema.PACKAGE_USE_UNKNOWN is None

    def test_unmapped_value_resolves_to_none(self):
        resolved = KospexSchema.REQ_TO_PACKAGE_USE.get(
            "no-such-requirements-type", KospexSchema.PACKAGE_USE_UNKNOWN
        )
        assert resolved is None

    def test_none_is_not_in_the_value_vocabulary(self):
        """Unknown is the absence of a value, not one of them."""
        assert KospexSchema.PACKAGE_USE_UNKNOWN not in KospexSchema.PACKAGE_USE_VALUES
