"""Tests for the Gradle build-file reader (phase 2 of #229).

`build.gradle` (Groovy) and `build.gradle.kts` (Kotlin) are read, not run.
Each declaration the reader recognises is a `direct` row (or a `bom` row for
`platform(...)`); `project(...)` and `files(...)` are not packages; anything
else is unreadable and logged. A file with declarations and none readable
raises UnreadableManifest (decision 3 of
changes/202611-gradle-dependency-parsing.md).

Every form tested here was measured on the development estate first; the
measurement is in the design doc's "Phase 2 measurements" section.
"""
import logging

import pytest

from kospex.extractors.errors import UnreadableManifest
from kospex.extractors.gradle import extract_build_gradle


def _build(tmp_path, text, name="build.gradle.kts", directory="."):
    """Write a build file inside a one-module build (with a settings file)."""
    (tmp_path / "settings.gradle.kts").touch()
    folder = tmp_path / directory
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text)
    return str(path)


def _catalog(tmp_path, text):
    (tmp_path / "gradle").mkdir(exist_ok=True)
    (tmp_path / "gradle" / "libs.versions.toml").write_text(text)


def _versions(records):
    return {r["package_name"]: r["package_version"] for r in records}


def _row(records, name):
    found = [r for r in records if r["package_name"] == name]
    assert len(found) == 1, f"{name}: {found}"
    return found[0]


class TestLiteralCoordinates:

    def test_kotlin_dsl(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation("com.squareup.okio:okio:3.9.1")\n}\n')
        records = extract_build_gradle(path)
        assert _versions(records) == {"com.squareup.okio:okio": "3.9.1"}
        row = records[0]
        assert row["requirements_type"] == "direct"
        assert row["declared_scope"] == "implementation"
        assert row["is_dev"] is False
        assert row["ecosystem"] == "gradle"

    def test_groovy_without_parentheses(self, tmp_path):
        path = _build(tmp_path, "dependencies {\n    implementation 'com.squareup.okio:okio:3.9.1'\n"
                                '    api "com.google.guava:guava:33.0.0-jre"\n}\n',
                      name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {
            "com.squareup.okio:okio": "3.9.1", "com.google.guava:guava": "33.0.0-jre"}

    def test_groovy_several_arguments(self, tmp_path):
        path = _build(tmp_path, "dependencies {\n    implementation 'a.b:c:1.0', 'd.e:f:2.0'\n}\n",
                      name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0", "d.e:f": "2.0"}

    def test_groovy_map_notation(self, tmp_path):
        path = _build(tmp_path, "dependencies {\n    implementation group: 'a.b', name: 'c', "
                                "version: '1.0'\n}\n", name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0"}

    def test_kotlin_named_arguments(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation(group = "a.b", name = "c", '
                                'version = "1.0")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0"}

    def test_no_version(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation("a.b:c")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": ""}

    def test_classifier_and_extension_are_not_the_version(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation("a.b:c:1.0:sources")\n'
                                '    implementation("d.e:f:2.0@aar")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0", "d.e:f": "2.0"}

    def test_strictly_shorthand_is_stored_as_the_plain_version(self, tmp_path):
        # As the catalog stores `strictly` (phase 1): `1.2!!` would be sent to
        # deps.dev unchanged and fail.
        path = _build(tmp_path, 'dependencies {\n    implementation("a.b:c:1.2!!")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.2"}

    def test_dynamic_version_is_kept(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation("a.b:c:1.+")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.+"}

    def test_add_with_a_configuration_name(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    add("implementation", "a.b:c:1.0")\n}\n')
        row = _row(extract_build_gradle(path), "a.b:c")
        assert row["declared_scope"] == "implementation"

    def test_string_configuration_name(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    "testImplementation"("a.b:c:1.0")\n}\n')
        row = _row(extract_build_gradle(path), "a.b:c")
        assert row["declared_scope"] == "testImplementation"
        assert row["is_dev"] is True

    def test_trailing_closure_is_ignored(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation("a.b:c:1.0") {\n'
                                '        exclude(group = "x.y")\n    }\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0"}

    def test_groovy_closure_as_an_argument_is_ignored(self, tmp_path):
        path = _build(tmp_path, "dependencies {\n    implementation('a.b:c:1.0', { transitive = true })\n}\n",
                      name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0"}

    def test_kotlin_module(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation(kotlin("stdlib"))\n'
                                '    testImplementation(kotlin("test", "2.0.0"))\n}\n')
        assert _versions(extract_build_gradle(path)) == {
            "org.jetbrains.kotlin:kotlin-stdlib": "",
            "org.jetbrains.kotlin:kotlin-test": "2.0.0"}


class TestVariables:

    def test_from_gradle_properties(self, tmp_path):
        (tmp_path / "gradle.properties").write_text("okioVersion=3.9.1\n")
        path = _build(tmp_path, 'dependencies {\n    implementation("com.squareup.okio:okio:$okioVersion")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_module_properties_win_over_the_root(self, tmp_path):
        (tmp_path / "gradle.properties").write_text("v=1.0\n")
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "gradle.properties").write_text("v=2.0\n")
        path = _build(tmp_path, 'dependencies {\n    implementation("a.b:c:${v}")\n}\n', directory="app")
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "2.0"}

    def test_from_a_val_in_the_same_file(self, tmp_path):
        path = _build(tmp_path, 'val okio = "3.9.1"\ndependencies {\n'
                                '    implementation("com.squareup.okio:okio:$okio")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_from_the_root_build_files_ext_block(self, tmp_path):
        (tmp_path / "build.gradle").write_text("ext {\n    okio_version = '3.9.1'\n}\n")
        path = _build(tmp_path, "dependencies {\n    implementation \"com.squareup.okio:okio:$okio_version\"\n}\n",
                      name="build.gradle", directory="app")
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_root_project_ext_prefix(self, tmp_path):
        (tmp_path / "build.gradle").write_text("ext.okio_version = '3.9.1'\n")
        path = _build(tmp_path, "dependencies {\n    implementation \"com.squareup.okio:okio:${rootProject.ext.okio_version}\"\n}\n",
                      name="build.gradle", directory="app")
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_through_property(self, tmp_path):
        # Decision 4: val x = property("y"), resolved from gradle.properties.
        (tmp_path / "gradle.properties").write_text("coroutines_version=1.10.2\n")
        path = _build(tmp_path, 'val coroutinesVersion = property("coroutines_version")\n'
                                'dependencies {\n    implementation('
                                '"org.jetbrains.kotlinx:kotlinx-coroutines-core:$coroutinesVersion")\n}\n')
        assert _versions(extract_build_gradle(path)) == {
            "org.jetbrains.kotlinx:kotlinx-coroutines-core": "1.10.2"}

    def test_from_the_catalogs_versions(self, tmp_path):
        _catalog(tmp_path, '[versions]\nokio = "3.9.1"\n')
        path = _build(tmp_path, 'dependencies {\n    implementation('
                                '"com.squareup.okio:okio:${libs.versions.okio.get()}")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_a_variable_holding_the_whole_coordinate(self, tmp_path):
        path = _build(tmp_path, "dependencies {\n    def sidecar = \"a.b:c:1.0\"\n"
                                "    compileOnly(sidecar)\n}\n", name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0"}

    def test_an_unknown_version_is_kept_as_written(self, tmp_path):
        # Enrichment then reports unresolved_spec, as for ${revision} in a pom.
        path = _build(tmp_path, 'dependencies {\n    implementation("a.b:c:$nowhere")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "$nowhere"}

    def test_an_unknown_name_is_unreadable(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation("$group:c:1.0")\n'
                                '    implementation("a.b:d:1.0")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:d": "1.0"}


class TestCatalogReferences:

    CATALOG = ('[versions]\nokio = "3.9.1"\n'
               '[libraries]\nokio = { module = "com.squareup.okio:okio", version.ref = "okio" }\n'
               'okio-fakefilesystem = { module = "com.squareup.okio:okio-fakefilesystem", version.ref = "okio" }\n'
               'compose-ui = { module = "androidx.compose.ui:ui" }\n'
               '[bundles]\nokio-all = ["okio", "okio-fakefilesystem"]\n')

    def test_library(self, tmp_path):
        _catalog(tmp_path, self.CATALOG)
        path = _build(tmp_path, "dependencies {\n    implementation(libs.okio)\n}\n")
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_dashed_alias_is_a_dotted_accessor(self, tmp_path):
        _catalog(tmp_path, self.CATALOG)
        path = _build(tmp_path, "dependencies {\n    testImplementation(libs.okio.fakefilesystem)\n}\n")
        row = _row(extract_build_gradle(path), "com.squareup.okio:okio-fakefilesystem")
        assert row["package_version"] == "3.9.1"
        assert row["is_dev"] is True

    def test_bundle(self, tmp_path):
        _catalog(tmp_path, self.CATALOG)
        path = _build(tmp_path, "dependencies {\n    implementation(libs.bundles.okio.all)\n}\n")
        assert _versions(extract_build_gradle(path)) == {
            "com.squareup.okio:okio": "3.9.1", "com.squareup.okio:okio-fakefilesystem": "3.9.1"}

    def test_groovy(self, tmp_path):
        _catalog(tmp_path, self.CATALOG)
        path = _build(tmp_path, "dependencies {\n    implementation libs.okio\n}\n", name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_unknown_accessor_is_unreadable(self, tmp_path):
        _catalog(tmp_path, self.CATALOG)
        path = _build(tmp_path, "dependencies {\n    implementation(libs.okio)\n"
                                "    implementation(libs.nothing.here)\n}\n")
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_a_module_uses_the_builds_catalog(self, tmp_path):
        _catalog(tmp_path, self.CATALOG)
        path = _build(tmp_path, "dependencies {\n    implementation(libs.okio)\n}\n",
                      directory="feature/app")
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_a_catalog_in_a_subdirectory_without_settings_is_not_the_builds(self, tmp_path):
        # androidx car/app/app-samples: a gradle/ folder of its own, but no
        # settings file, so Gradle builds it with the root's catalog.
        _catalog(tmp_path, self.CATALOG)
        (tmp_path / "samples" / "gradle").mkdir(parents=True)
        (tmp_path / "samples" / "gradle" / "libs.versions.toml").write_text(
            '[libraries]\nokio = "com.squareup.okio:okio:1.0.0"\n')
        path = _build(tmp_path, "dependencies {\n    implementation(libs.okio)\n}\n",
                      directory="samples/app")
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "3.9.1"}

    def test_a_nested_build_uses_its_own_catalog(self, tmp_path):
        # maplibre-native: platform/android is a build of its own.
        _catalog(tmp_path, self.CATALOG)
        nested = tmp_path / "platform" / "android"
        (nested / "gradle").mkdir(parents=True)
        (nested / "settings.gradle.kts").touch()
        (nested / "gradle" / "libs.versions.toml").write_text(
            '[libraries]\nokio = "com.squareup.okio:okio:1.0.0"\n')
        path = _build(tmp_path, "dependencies {\n    implementation(libs.okio)\n}\n",
                      directory="platform/android/app")
        assert _versions(extract_build_gradle(path)) == {"com.squareup.okio:okio": "1.0.0"}

    def test_an_included_build_named_catalog(self, tmp_path):
        # okhttp's build-logic: from(files("../gradle/libs.versions.toml")).
        (tmp_path / ".git").mkdir()
        _catalog(tmp_path, self.CATALOG)
        logic = tmp_path / "build-logic"
        logic.mkdir()
        (logic / "settings.gradle.kts").write_text(
            'dependencyResolutionManagement {\n  versionCatalogs {\n    create("libs") {\n'
            '      from(files("../gradle/libs.versions.toml"))\n    }\n  }\n}\n')
        path = logic / "build.gradle.kts"
        path.write_text("dependencies {\n    implementation(libs.okio)\n}\n")
        assert _versions(extract_build_gradle(str(path))) == {"com.squareup.okio:okio": "3.9.1"}

    def test_a_named_catalog_outside_the_repository_is_not_read(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (tmp_path / "outside.toml").write_text('[libraries]\nokio = "com.squareup.okio:okio:9.9.9"\n')
        (repo / "settings.gradle.kts").write_text(
            'dependencyResolutionManagement { versionCatalogs { create("libs") {\n'
            '  from(files("../outside.toml")) } } }\n')
        path = repo / "build.gradle.kts"
        path.write_text('dependencies {\n    implementation(libs.okio)\n'
                        '    implementation("a.b:c:1.0")\n}\n')
        assert _versions(extract_build_gradle(str(path))) == {"a.b:c": "1.0"}

    def test_an_unreadable_catalog_leaves_references_unreadable(self, tmp_path):
        _catalog(tmp_path, "this is [not toml")
        path = _build(tmp_path, 'dependencies {\n    implementation(libs.okio)\n'
                                '    implementation("a.b:c:1.0")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0"}

    def test_a_blank_catalog_version_stays_blank(self, tmp_path):
        _catalog(tmp_path, self.CATALOG)
        path = _build(tmp_path, "dependencies {\n    implementation(libs.compose.ui)\n}\n")
        assert _versions(extract_build_gradle(path)) == {"androidx.compose.ui:ui": ""}


class TestPlatforms:

    def test_platform_is_a_bom_row(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n'
                                '    implementation(platform("androidx.compose:compose-bom:2025.01.00"))\n'
                                '    implementation("androidx.compose.ui:ui")\n}\n')
        records = extract_build_gradle(path)
        bom = _row(records, "androidx.compose:compose-bom")
        assert bom["requirements_type"] == "bom"
        assert bom["declared_scope"] == "platform"
        assert bom["is_dev"] is None
        assert _row(records, "androidx.compose.ui:ui")["package_version"] == ""

    def test_enforced_platform_from_the_catalog(self, tmp_path):
        _catalog(tmp_path, '[libraries]\nokhttp-bom = "com.squareup.okhttp3:okhttp-bom:5.0.0"\n')
        path = _build(tmp_path, "dependencies {\n    api(enforcedPlatform(libs.okhttp.bom))\n}\n")
        bom = _row(extract_build_gradle(path), "com.squareup.okhttp3:okhttp-bom")
        assert bom["requirements_type"] == "bom"
        assert bom["declared_scope"] == "enforcedPlatform"

    def test_a_platform_supplies_versions_in_the_same_file(self, tmp_path, monkeypatch):
        # The existing BOM lookup fills the blank sibling, unchanged.
        from kospex_dependencies import KospexDependencies
        path = _build(tmp_path, 'dependencies {\n'
                                '    implementation(platform("androidx.compose:compose-bom:2025.01.00"))\n'
                                '    implementation("androidx.compose.ui:ui")\n}\n')
        records = [{**r, "package_type": "maven", "_repo_id": "x", "file_path": "build.gradle.kts"}
                   for r in extract_build_gradle(path)]
        kdeps = KospexDependencies()
        monkeypatch.setattr(kdeps, "_bom_managed_versions",
                            lambda name, version: {"androidx.compose.ui:ui": "1.7.6"})
        found = kdeps.maven_bom_versions(records)
        ui = next(i for i, r in enumerate(records) if r["package_name"] == "androidx.compose.ui:ui")
        assert found == {ui: "1.7.6"}

    def test_a_platform_project_is_first_party(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation(platform(project(":bom")))\n'
                                '    implementation("a.b:c:1.0")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0"}


class TestNoRow:

    def test_project_references(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation(project(":core"))\n'
                                '    implementation(projects.feature)\n'
                                '    testImplementation(testFixtures(project(":core")))\n}\n')
        assert extract_build_gradle(path) == []

    def test_files_and_gradle_helpers(self, tmp_path, caplog):
        path = _build(tmp_path, 'dependencies {\n    implementation(files("libs/vendor.jar"))\n'
                                '    implementation(fileTree("libs"))\n'
                                '    implementation(gradleApi())\n    testImplementation(gradleTestKit())\n}\n')
        with caplog.at_level(logging.INFO, logger="extractors.gradle"):
            assert extract_build_gradle(path) == []
        # A checked-in jar is logged, not silently dropped (#398).
        assert "file dependency" in caplog.text

    def test_constraints_are_out_of_scope(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    constraints {\n'
                                '        implementation("a.b:c:2.0")\n    }\n'
                                '    implementation("a.b:d:1.0")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:d": "1.0"}

    def test_comments_are_not_declarations(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    // implementation("a.b:c:1.0")\n'
                                '    /* implementation("a.b:d:1.0") */\n'
                                '    implementation("a.b:e:1.0") // trailing\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:e": "1.0"}

    def test_a_url_in_a_string_is_not_a_comment(self, tmp_path):
        path = _build(tmp_path, 'repositories { maven { url = uri("https://repo.example.com/m2") } }\n'
                                'dependencies {\n    implementation("a.b:c:1.0")\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0"}


class TestScope:

    @pytest.mark.parametrize("configuration", [
        "testImplementation", "androidTestImplementation", "testFixturesApi",
        "debugTestImplementation", "kspAndroidTest", "kaptTest", "testRuntimeOnly",
    ])
    def test_test_configurations_are_dev(self, tmp_path, configuration):
        path = _build(tmp_path, f'dependencies {{\n    {configuration}("a.b:c:1.0")\n}}\n')
        assert _row(extract_build_gradle(path), "a.b:c")["is_dev"] is True

    @pytest.mark.parametrize("configuration", [
        "implementation", "api", "compileOnly", "runtimeOnly", "kapt", "ksp",
        "annotationProcessor", "docs", "lintChecks",
    ])
    def test_other_configurations_are_not(self, tmp_path, configuration):
        # compileOnly / kapt / ksp are build-time, not tests, as Maven's
        # `provided`; androidx's docs and lintChecks stay too (decision 3).
        path = _build(tmp_path, f'dependencies {{\n    {configuration}("a.b:c:1.0")\n}}\n')
        row = _row(extract_build_gradle(path), "a.b:c")
        assert row["is_dev"] is False
        assert row["declared_scope"] == configuration

    def test_buildscript_classpath_is_build_tooling(self, tmp_path):
        path = _build(tmp_path, 'buildscript {\n    dependencies {\n'
                                '        classpath("com.android.tools.build:gradle:8.5.0")\n    }\n}\n')
        row = _row(extract_build_gradle(path), "com.android.tools.build:gradle")
        assert row["declared_scope"] == "classpath"
        assert row["is_dev"] is True

    def test_declarations_in_subprojects_are_read(self, tmp_path):
        path = _build(tmp_path, 'subprojects {\n    dependencies {\n'
                                '        implementation("a.b:c:1.0")\n    }\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0"}

    def test_conditional_declarations_are_read(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    if (useOkio) {\n'
                                '        implementation("a.b:c:1.0")\n    } else {\n'
                                '        implementation("a.b:d:1.0")\n    }\n}\n')
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0", "a.b:d": "1.0"}

    def test_a_product_use_wins_over_a_test_use(self, tmp_path):
        # One row per (name, version), the primary key's pair.
        path = _build(tmp_path, 'dependencies {\n    testImplementation("a.b:c:1.0")\n'
                                '    implementation("a.b:c:1.0")\n}\n')
        records = extract_build_gradle(path)
        assert len(records) == 1
        assert records[0]["is_dev"] is False
        assert records[0]["declared_scope"] == "implementation"

    def test_two_versions_are_two_rows(self, tmp_path):
        path = _build(tmp_path, 'dependencies {\n    implementation("a.b:c:1.0")\n'
                                '    testImplementation("a.b:c:2.0")\n}\n')
        assert len(extract_build_gradle(path)) == 2


class TestKotlinMultiplatform:
    """Decision 2: declared_scope is <sourceSet>.<configuration>."""

    def test_source_set_dot_dependencies(self, tmp_path):
        path = _build(tmp_path, 'kotlin {\n    sourceSets {\n'
                                '        commonMain.dependencies {\n            api("a.b:c:1.0")\n        }\n'
                                '        commonTest.dependencies {\n            implementation("a.b:d:1.0")\n        }\n'
                                '    }\n}\n')
        records = extract_build_gradle(path)
        main, test = _row(records, "a.b:c"), _row(records, "a.b:d")
        assert (main["declared_scope"], main["is_dev"]) == ("commonMain.api", False)
        assert (test["declared_scope"], test["is_dev"]) == ("commonTest.implementation", True)

    def test_source_set_block(self, tmp_path):
        path = _build(tmp_path, 'kotlin {\n    sourceSets {\n        jvmTest {\n'
                                '            dependencies {\n                implementation("a.b:c:1.0")\n'
                                '            }\n        }\n    }\n}\n')
        row = _row(extract_build_gradle(path), "a.b:c")
        assert (row["declared_scope"], row["is_dev"]) == ("jvmTest.implementation", True)

    def test_val_by_getting(self, tmp_path):
        path = _build(tmp_path, 'kotlin {\n    sourceSets {\n        val androidMain by getting {\n'
                                '            dependencies {\n                implementation("a.b:c:1.0")\n'
                                '            }\n        }\n    }\n}\n')
        row = _row(extract_build_gradle(path), "a.b:c")
        assert (row["declared_scope"], row["is_dev"]) == ("androidMain.implementation", False)

    def test_named(self, tmp_path):
        path = _build(tmp_path, 'kotlin {\n    sourceSets {\n        named("androidUnitTest") {\n'
                                '            dependencies {\n                implementation("a.b:c:1.0")\n'
                                '            }\n        }\n    }\n}\n')
        row = _row(extract_build_gradle(path), "a.b:c")
        assert (row["declared_scope"], row["is_dev"]) == ("androidUnitTest.implementation", True)

    def test_jvm_test_suite(self, tmp_path):
        path = _build(tmp_path, 'testing {\n    suites {\n'
                                '        val test by getting(JvmTestSuite::class) {\n'
                                '            dependencies {\n                implementation("a.b:c:1.0")\n'
                                '            }\n        }\n    }\n}\n')
        row = _row(extract_build_gradle(path), "a.b:c")
        assert (row["declared_scope"], row["is_dev"]) == ("test.implementation", True)


class TestPlugins:
    """Decision 1: a row only for a plugin with a version."""

    def test_id_with_version(self, tmp_path):
        path = _build(tmp_path, 'plugins {\n    id("com.diffplug.spotless") version "7.0.2"\n}\n')
        row = _row(extract_build_gradle(path),
                   "com.diffplug.spotless:com.diffplug.spotless.gradle.plugin")
        assert row["package_version"] == "7.0.2"
        assert row["declared_scope"] == "plugin"
        assert row["is_dev"] is True
        assert row["requirements_type"] == "direct"

    def test_groovy_id_with_version(self, tmp_path):
        path = _build(tmp_path, "plugins {\n    id 'com.diffplug.spotless' version '7.0.2' apply false\n}\n",
                      name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {
            "com.diffplug.spotless:com.diffplug.spotless.gradle.plugin": "7.0.2"}

    def test_kotlin_plugin(self, tmp_path):
        path = _build(tmp_path, 'plugins {\n    kotlin("jvm") version "2.1.0"\n}\n')
        assert _versions(extract_build_gradle(path)) == {
            "org.jetbrains.kotlin.jvm:org.jetbrains.kotlin.jvm.gradle.plugin": "2.1.0"}

    def test_catalog_alias(self, tmp_path):
        _catalog(tmp_path, '[plugins]\nspotless = { id = "com.diffplug.spotless", version = "7.0.2" }\n')
        path = _build(tmp_path, "plugins {\n    alias(libs.plugins.spotless) apply false\n}\n")
        assert _versions(extract_build_gradle(path)) == {
            "com.diffplug.spotless:com.diffplug.spotless.gradle.plugin": "7.0.2"}

    def test_no_version_is_no_row(self, tmp_path):
        path = _build(tmp_path, 'plugins {\n    id("com.android.library")\n    `kotlin-dsl`\n    java\n}\n')
        assert extract_build_gradle(path) == []

    def test_gradle_plugin_definitions_are_not_applied_plugins(self, tmp_path):
        path = _build(tmp_path, 'gradlePlugin {\n    plugins {\n        create("x") {\n'
                                '            id = "com.example.x"\n        }\n    }\n}\n')
        assert extract_build_gradle(path) == []


class TestSideFilesStayInTheRepository:
    """CWE-59: a cloned repository can commit a symlink. Values read from
    side files end up in versions sent to deps.dev, so a side file that
    resolves outside the repository is not read."""

    def _repo(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / "settings.gradle.kts").touch()
        return repo

    def test_a_symlinked_gradle_properties_outside_is_not_read(self, tmp_path):
        repo = self._repo(tmp_path)
        (tmp_path / "home.properties").write_text("v=secret-token\n")
        (repo / "gradle.properties").symlink_to(tmp_path / "home.properties")
        path = repo / "build.gradle.kts"
        path.write_text('dependencies {\n    implementation("a.b:c:$v")\n}\n')
        assert _versions(extract_build_gradle(str(path))) == {"a.b:c": "$v"}

    def test_a_symlinked_root_build_file_outside_is_not_read(self, tmp_path):
        repo = self._repo(tmp_path)
        (tmp_path / "elsewhere.gradle").write_text("ext.v = 'secret-token'\n")
        (repo / "build.gradle").symlink_to(tmp_path / "elsewhere.gradle")
        path = repo / "app" / "build.gradle"
        path.parent.mkdir()
        path.write_text('dependencies {\n    implementation "a.b:c:$v"\n}\n')
        assert _versions(extract_build_gradle(str(path))) == {"a.b:c": "$v"}

    def test_a_symlinked_catalog_outside_is_not_read(self, tmp_path):
        repo = self._repo(tmp_path)
        (tmp_path / "outside.toml").write_text('[libraries]\nokio = "com.squareup.okio:okio:9.9.9"\n')
        (repo / "gradle").mkdir()
        (repo / "gradle" / "libs.versions.toml").symlink_to(tmp_path / "outside.toml")
        path = repo / "build.gradle.kts"
        path.write_text('dependencies {\n    implementation(libs.okio)\n'
                        '    implementation("a.b:c:1.0")\n}\n')
        assert _versions(extract_build_gradle(str(path))) == {"a.b:c": "1.0"}

    def test_a_named_catalog_through_a_symlinked_directory_is_not_read(self, tmp_path):
        repo = self._repo(tmp_path)
        (tmp_path / "out").mkdir()
        (tmp_path / "out" / "libs.versions.toml").write_text(
            '[libraries]\nokio = "com.squareup.okio:okio:9.9.9"\n')
        (repo / "link").symlink_to(tmp_path / "out")
        (repo / "settings.gradle.kts").write_text(
            'dependencyResolutionManagement { versionCatalogs { create("libs") {\n'
            '  from(files("link/libs.versions.toml")) } } }\n')
        path = repo / "build.gradle.kts"
        path.write_text('dependencies {\n    implementation(libs.okio)\n'
                        '    implementation("a.b:c:1.0")\n}\n')
        assert _versions(extract_build_gradle(str(path))) == {"a.b:c": "1.0"}

    def test_a_symlink_inside_the_repository_is_still_read(self, tmp_path):
        repo = self._repo(tmp_path)
        (repo / "shared.properties").write_text("v=1.0\n")
        (repo / "gradle.properties").symlink_to(repo / "shared.properties")
        path = repo / "build.gradle.kts"
        path.write_text('dependencies {\n    implementation("a.b:c:$v")\n}\n')
        assert _versions(extract_build_gradle(str(path))) == {"a.b:c": "1.0"}


class TestErrorContract:

    def test_missing_file(self, tmp_path):
        with pytest.raises(UnreadableManifest, match="not found"):
            extract_build_gradle(str(tmp_path / "build.gradle.kts"))

    def test_directory(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_build_gradle(str(tmp_path))

    def test_not_utf8(self, tmp_path):
        (tmp_path / "settings.gradle.kts").touch()
        path = tmp_path / "build.gradle.kts"
        path.write_bytes(b'dependencies { implementation("a:b:\xff") }')
        with pytest.raises(UnreadableManifest, match="UTF-8"):
            extract_build_gradle(str(path))

    def test_no_dependencies_block_is_empty(self, tmp_path):
        path = _build(tmp_path, 'plugins {\n    java\n}\ngroup = "com.example"\n')
        assert extract_build_gradle(path) == []

    def test_nothing_readable_raises(self, tmp_path):
        # Decision 3: reported as parse_error, not as `empty`.
        path = _build(tmp_path, 'dependencies {\n    implementation(project(":core"))\n'
                                '    implementation(intellijCore())\n}\n')
        with pytest.raises(UnreadableManifest, match="cannot read"):
            extract_build_gradle(path)

    def test_partly_readable_returns_rows_and_logs_the_rest(self, tmp_path, caplog):
        path = _build(tmp_path, 'dependencies {\n    implementation("a.b:c:1.0")\n'
                                '    implementation(intellijCore())\n}\n')
        with caplog.at_level(logging.INFO, logger="extractors.gradle"):
            assert _versions(extract_build_gradle(path)) == {"a.b:c": "1.0"}
        assert "intellijCore()" in caplog.text
        assert "build.gradle.kts:3" in caplog.text


class _CountingText(str):
    """A str that adds up how many characters its slices copy."""

    copied = 0

    def __getitem__(self, key):
        part = str.__getitem__(self, key)
        type(self).copied += len(part)
        return part


class TestResourceLimits:
    """A build file comes from a cloned third-party repository, so its size and
    shape are attacker-controlled (CWE-400). Real files are small: across 1,495
    on the development estate the largest is 36 KB, braces nest at most 8 deep,
    no string has more than 4 references and no assignment is longer than 125
    characters. Each limit below is far above those."""

    def test_an_expansion_that_multiplies_is_not_expanded(self, tmp_path):
        # 200 references to $a, each 200 references to a 100-character property:
        # 4 MB from a 1 KB file. Kept as written instead, like a pom's ${...}.
        (tmp_path / "gradle.properties").write_text("b=" + "9" * 100 + "\n")
        declared = "$a" * 200
        path = _build(tmp_path, f'def a = "{"$b" * 200}"\n'
                                f'dependencies {{\n    implementation "g:a:{declared}"\n}}\n',
                      name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {"g:a": declared}

    def test_each_name_is_worked_out_once(self, tmp_path, monkeypatch):
        # $a holds 200 references to $b, which holds 200 to an unknown name:
        # 80,000 lookups without memoising, a few hundred with it.
        from kospex.extractors import gradle
        calls = []
        original = gradle._Context.value

        def counting(self, expression, depth=0):
            calls.append(expression)
            return original(self, expression, depth)

        monkeypatch.setattr(gradle._Context, "value", counting)
        path = _build(tmp_path, f'def a = "{"$b" * 200}"\ndef b = "{"$c.x" * 200}"\n'
                                'dependencies {\n    implementation "g:a:$a"\n}\n',
                      name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {"g:a": "$a"}
        assert len(calls) < 2_000

    def test_a_value_within_the_limit_is_still_expanded(self, tmp_path):
        (tmp_path / "gradle.properties").write_text("v=" + "1" * 500 + "\n")
        path = _build(tmp_path, 'dependencies {\n    implementation "g:a:${v}.$v"\n}\n',
                      name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {"g:a": "1" * 500 + "." + "1" * 500}

    def test_blocks_nested_too_deep_are_unreadable(self, tmp_path, caplog):
        path = _build(tmp_path, "a {" * 200 + 'dependencies { implementation("x.y:deep:1.0") }'
                                + "}" * 200 + '\ndependencies { implementation("x.y:top:1.0") }\n')
        with caplog.at_level(logging.INFO, logger="extractors.gradle"):
            assert _versions(extract_build_gradle(path)) == {"x.y:top": "1.0"}
        assert "nested" in caplog.text

    def test_only_too_deep_declarations_raise(self, tmp_path):
        path = _build(tmp_path, "a {" * 200 + 'dependencies { implementation("x.y:deep:1.0") }'
                                + "}" * 200 + "\n")
        with pytest.raises(UnreadableManifest):
            extract_build_gradle(path)

    def test_ordinary_nesting_is_read(self, tmp_path):
        path = _build(tmp_path, "a {" * 10 + 'dependencies { implementation("x.y:z:1.0") }'
                                + "}" * 10 + "\n")
        assert _versions(extract_build_gradle(path)) == {"x.y:z": "1.0"}

    def test_nested_ext_blocks_are_scanned_once(self, monkeypatch):
        from kospex.extractors import gradle
        scanned = []
        original = gradle._close

        def counting(text, i):
            j = original(text, i)
            scanned.append(j - i)
            return j

        monkeypatch.setattr(gradle, "_close", counting)
        text = "ext {\n" * 300 + "x = '1'\n" + "}\n" * 300
        assert gradle._assignments(text) == {"x": "'1'"}
        assert sum(scanned) < 3 * len(text)

    def test_statements_copy_each_character_a_bounded_number_of_times(self):
        from kospex.extractors import gradle
        for text in ('    implementation "g:a:1.0"\n' * 2_000,   # many statements
                     "a,\n" * 2_000 + "b\n",                      # one continued statement
                     "x\n" + "\n" * 2_000 + ".y\n"):              # continued by a later `.`
            _CountingText.copied = 0
            gradle._statements(_CountingText(text), 0, len(text))
            assert _CountingText.copied < 3 * len(text), text[:20]

    def test_a_dot_after_blank_lines_continues_the_statement(self):
        from kospex.extractors import gradle
        text = "x\n\n\n  .y\nz\n"
        assert [s for _, s in gradle._statements(text, 0, len(text))] == ["x\n\n\n  .y", "z"]

    def test_a_build_file_over_the_size_limit_is_unreadable(self, tmp_path, monkeypatch):
        from kospex.extractors import gradle
        monkeypatch.setattr(gradle, "_MAX_FILE_BYTES", 100)
        path = _build(tmp_path, 'dependencies {\n    implementation("a.b:c:1.0")\n}\n' + " " * 100)
        with pytest.raises(UnreadableManifest, match="larger than"):
            extract_build_gradle(path)

    def test_a_gradle_properties_over_the_size_limit_is_not_read(self, tmp_path, monkeypatch):
        from kospex.extractors import gradle
        monkeypatch.setattr(gradle, "_MAX_FILE_BYTES", 100)
        (tmp_path / "gradle.properties").write_text("v=1.0\n" + "#" * 100 + "\n")
        path = _build(tmp_path, 'dependencies {\n    implementation "a.b:c:$v"\n}\n',
                      name="build.gradle")
        assert _versions(extract_build_gradle(path)) == {"a.b:c": "$v"}
