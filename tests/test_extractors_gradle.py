"""Tests for the pure Gradle version catalog extractor (phase 1 of #229).

A catalog (`gradle/libs.versions.toml`) is where a Gradle build sets the
versions its modules use. Each `[libraries]` and `[plugins]` entry is a row
with requirements_type `version-catalog`; `[bundles]` only group libraries and
produce none. See changes/202611-gradle-dependency-parsing.md.

A file this extractor could not read raises UnreadableManifest rather than
returning []; the shared contract lives in
tests/test_extractor_unreadable_manifests.py.
"""
import pytest

from kospex.extractors.errors import UnreadableManifest
from kospex.extractors.gradle import _template, extract_version_catalog


def _catalog(tmp_path, text, name="libs.versions.toml"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def _versions(records):
    return {r["package_name"]: r["package_version"] for r in records}


class TestTemplateContract:
    def test_template_matches_get_package_template(self):
        from kospex_dependencies import KospexDependencies
        assert set(_template()) == set(KospexDependencies().get_package_template())


class TestLibraryNotations:
    """Every library notation on the development estate (1,170 entries)."""

    def test_string_notation(self, tmp_path):
        path = _catalog(tmp_path, '[libraries]\nokio = "com.squareup.okio:okio:3.9.1"\n')
        assert _versions(extract_version_catalog(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_module_with_version(self, tmp_path):
        path = _catalog(tmp_path,
                        '[libraries]\nokio = { module = "com.squareup.okio:okio", version = "3.9.1" }\n')
        assert _versions(extract_version_catalog(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_module_with_version_ref(self, tmp_path):
        path = _catalog(tmp_path, '[versions]\nokio = "3.9.1"\n'
                        '[libraries]\nokio = { module = "com.squareup.okio:okio", version.ref = "okio" }\n')
        assert _versions(extract_version_catalog(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_group_and_name(self, tmp_path):
        path = _catalog(tmp_path, '[versions]\nokio = "3.9.1"\n'
                        '[libraries]\nokio = { group = "com.squareup.okio", name = "okio", '
                        'version.ref = "okio" }\n')
        assert _versions(extract_version_catalog(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_no_version_is_blank(self, tmp_path):
        # Supplied by a platform (BOM) in the build file: 27 entries on the estate.
        path = _catalog(tmp_path, '[libraries]\ncompose-ui = { module = "androidx.compose.ui:ui" }\n')
        assert _versions(extract_version_catalog(path)) == {"androidx.compose.ui:ui": ""}

    def test_refreshversions_placeholder_is_blank(self, tmp_path):
        # `_` means "see versions.properties" (the refreshVersions plugin), not
        # a version: deps.dev would be asked for version "_".
        path = _catalog(tmp_path,
                        '[libraries]\nappcompat = "androidx.appcompat:appcompat:_"\n')
        assert _versions(extract_version_catalog(path)) == {"androidx.appcompat:appcompat": ""}


class TestRichVersions:
    """`{ strictly, require, prefer, reject }`, in [versions] or inline."""

    @pytest.mark.parametrize("table,expected", [
        ('{ strictly = "2.1" }', "2.1"),
        ('{ require = "2.1" }', "2.1"),
        ('{ prefer = "2.1" }', "2.1"),
        ('{ strictly = "[2.0,3.0)", prefer = "2.1" }', "[2.0,3.0)"),
        ('{ require = "2.0", prefer = "2.1" }', "2.0"),
        ('{ reject = ["2.0"] }', ""),
    ])
    def test_inline(self, tmp_path, table, expected):
        path = _catalog(tmp_path, f'[libraries]\nx = {{ module = "g:a", version = {table} }}\n')
        assert _versions(extract_version_catalog(path)) == {"g:a": expected}

    def test_in_versions_section(self, tmp_path):
        path = _catalog(tmp_path, '[versions]\nx = { strictly = "2.1" }\n'
                        '[libraries]\nx = { module = "g:a", version.ref = "x" }\n')
        assert _versions(extract_version_catalog(path)) == {"g:a": "2.1"}


class TestPlugins:
    """A plugin is looked up as its marker artifact, `id:id.gradle.plugin`."""

    @pytest.mark.parametrize("entry", [
        '"org.jetbrains.kotlin.jvm:2.2.0"',
        '{ id = "org.jetbrains.kotlin.jvm", version = "2.2.0" }',
        '{ id = "org.jetbrains.kotlin.jvm", version.ref = "kotlin" }',
    ])
    def test_notations(self, tmp_path, entry):
        path = _catalog(tmp_path, f'[versions]\nkotlin = "2.2.0"\n[plugins]\nkotlin = {entry}\n')
        assert _versions(extract_version_catalog(path)) == {
            "org.jetbrains.kotlin.jvm:org.jetbrains.kotlin.jvm.gradle.plugin": "2.2.0"}

    def test_plugin_is_build_tooling(self, tmp_path):
        path = _catalog(tmp_path, '[plugins]\nkotlin = "org.jetbrains.kotlin.jvm:2.2.0"\n')
        (record,) = extract_version_catalog(path)
        assert record["declared_scope"] == "plugin"
        assert record["is_dev"] is True

    def test_plugin_without_version(self, tmp_path):
        path = _catalog(tmp_path, '[plugins]\nandroid = { id = "com.android.application" }\n')
        assert _versions(extract_version_catalog(path)) == {
            "com.android.application:com.android.application.gradle.plugin": ""}


class TestRecordShape:
    def test_library_fields(self, tmp_path):
        path = _catalog(tmp_path, '[libraries]\nokio = "com.squareup.okio:okio:3.9.1"\n')
        (record,) = extract_version_catalog(path)
        assert record["requirements_type"] == "version-catalog"
        assert record["ecosystem"] == "gradle"
        # A catalog doesn't say whether a library is for tests.
        assert record["is_dev"] is None
        assert record["declared_scope"] is None

    def test_bundles_are_not_rows(self, tmp_path):
        path = _catalog(tmp_path, '[libraries]\na = "g:a:1.0"\nb = "g:b:1.0"\n'
                        '[bundles]\nboth = ["a", "b"]\n')
        assert sorted(_versions(extract_version_catalog(path))) == ["g:a", "g:b"]

    def test_same_artifact_and_version_twice_is_one_row(self, tmp_path):
        # The primary key includes name and version, so two would collide.
        path = _catalog(tmp_path, '[libraries]\na = "g:a:1.0"\nalias-a = "g:a:1.0"\n')
        assert len(extract_version_catalog(path)) == 1

    def test_same_artifact_at_two_versions_is_two_rows(self, tmp_path):
        path = _catalog(tmp_path, '[libraries]\nold = "g:a:1.0"\nnew = "g:a:2.0"\n')
        assert sorted(r["package_version"] for r in extract_version_catalog(path)) == ["1.0", "2.0"]

    def test_metadata_section_is_accepted(self, tmp_path):
        path = _catalog(tmp_path, '[metadata]\nformat.version = "1.1"\n[libraries]\na = "g:a:1.0"\n')
        assert _versions(extract_version_catalog(path)) == {"g:a": "1.0"}


class TestGenuinelyEmpty:
    def test_versions_only(self, tmp_path):
        path = _catalog(tmp_path, '[versions]\nkotlin = "2.2.0"\n')
        assert extract_version_catalog(path) == []

    def test_empty_file(self, tmp_path):
        assert extract_version_catalog(_catalog(tmp_path, "")) == []


class TestUnreadable:
    def test_missing_file(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_version_catalog(str(tmp_path / "libs.versions.toml"))

    def test_invalid_toml(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_version_catalog(_catalog(tmp_path, "[libraries\n"))

    def test_not_utf8(self, tmp_path):
        path = tmp_path / "libs.versions.toml"
        path.write_bytes(b'[libraries]\na = "g:a:\xff"\n')
        with pytest.raises(UnreadableManifest):
            extract_version_catalog(str(path))

    def test_libraries_not_a_table(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_version_catalog(_catalog(tmp_path, 'libraries = "g:a:1.0"\n'))

    @pytest.mark.parametrize("entry", [
        '"g-a-1.0"',                                  # not group:artifact[:version]
        '{ version = "1.0" }',                       # no module, no group/name
        '{ group = "g", version = "1.0" }',          # group without name
        '{ module = "g", version = "1.0" }',         # module without artifact
        '42',
    ])
    def test_uninterpretable_library(self, tmp_path, entry):
        # Gradle refuses the whole catalog, so nothing is known about it.
        with pytest.raises(UnreadableManifest):
            extract_version_catalog(_catalog(tmp_path, f"[libraries]\nx = {entry}\n"))

    def test_missing_version_ref(self, tmp_path):
        # Gradle: "version reference 'nope' doesn't exist".
        with pytest.raises(UnreadableManifest):
            extract_version_catalog(_catalog(
                tmp_path, '[libraries]\nx = { module = "g:a", version.ref = "nope" }\n'))

    def test_plugin_without_id(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_version_catalog(_catalog(tmp_path, '[plugins]\nx = { version = "1.0" }\n'))
