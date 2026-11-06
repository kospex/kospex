"""Tests for the pure Maven pom.xml extractor.

Two tiers of version resolution are covered. Tier 1 reads only the file:
`<properties>` and the built-in `${project.*}` / `${project.parent.*}` values.
Tier 2 follows `<parent>` to a pom.xml inside the same repository and takes
its properties and `<dependencyManagement>`. A parent outside the repository,
or an imported BOM, is not fetched — those versions are recorded as "" and
enrichment reports them as `no_version`.

A file this extractor could not read raises UnreadableManifest rather than
returning []; the shared contract lives in
tests/test_extractor_unreadable_manifests.py.
"""
import pytest

from kospex.extractors.errors import UnreadableManifest
from kospex.extractors.maven import _template, extract_pom

NS = 'xmlns="http://maven.apache.org/POM/4.0.0"'


def _pom(body, ns=NS):
    return f"<project {ns}>\n<modelVersion>4.0.0</modelVersion>\n{body}\n</project>"


def _dep(group, artifact, version=None, scope=None, extra=""):
    parts = [f"<groupId>{group}</groupId>", f"<artifactId>{artifact}</artifactId>"]
    if version is not None:
        parts.append(f"<version>{version}</version>")
    if scope is not None:
        parts.append(f"<scope>{scope}</scope>")
    return "<dependency>" + "".join(parts) + extra + "</dependency>"


def _deps(*deps):
    return "<dependencies>" + "".join(deps) + "</dependencies>"


def _write(directory, content, name="pom.xml"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content)
    return str(path)


@pytest.fixture
def repo(tmp_path):
    """A directory that looks like a git checkout, so parents can be followed."""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def _versions(records):
    return {r["package_name"]: r["package_version"] for r in records}


class TestTemplateContract:
    def test_template_matches_get_package_template(self):
        from kospex_dependencies import KospexDependencies
        assert set(_template().keys()) == set(
            KospexDependencies().get_package_template().keys()
        )

    def test_advisory_columns_default_to_null_not_empty_string(self):
        """INTEGER columns: '' sorts above every integer in SQLite (#258)."""
        t = _template()
        assert t["advisories"] is None
        assert t["versions_behind"] is None


class TestExtract:
    def test_records_dependencies_as_group_colon_artifact(self, tmp_path):
        """deps.dev names a Maven package `groupId:artifactId`."""
        path = _write(tmp_path, _pom(_deps(
            _dep("junit", "junit", "4.13.2"),
            _dep("com.google.truth", "truth", "1.4.5"),
        )))

        assert _versions(extract_pom(path)) == {
            "junit:junit": "4.13.2", "com.google.truth:truth": "1.4.5",
        }

    def test_records_are_direct_with_the_full_template_shape(self, tmp_path):
        path = _write(tmp_path, _pom(_deps(_dep("junit", "junit", "4.13.2"))))

        record = extract_pom(path)[0]

        assert record["requirements_type"] == "direct"
        assert record["ecosystem"] == "maven"
        assert set(record.keys()) == set(_template().keys())

    def test_a_pom_without_a_namespace_is_read(self, tmp_path):
        """All 14 pom.xml files on the development estate carry the Maven
        namespace, but it is optional, and matching on it either way would
        report the other spelling as declaring nothing."""
        path = _write(tmp_path, _pom(_deps(_dep("junit", "junit", "4.13.2")), ns=""))

        assert _versions(extract_pom(path)) == {"junit:junit": "4.13.2"}

    def test_dependency_without_version_is_still_recorded(self, tmp_path):
        """24 of 51 dependencies on the estate omit <version>. Maven refuses
        to build such a pom unless a parent or BOM manages the version, so
        each one is a real declared dependency."""
        path = _write(tmp_path, _pom(_deps(_dep("junit", "junit"))))

        assert _versions(extract_pom(path)) == {"junit:junit": ""}

    def test_project_with_no_dependencies_returns_empty(self, tmp_path):
        """A parent or aggregator pom legitimately declares none."""
        assert extract_pom(_write(tmp_path, _pom("<packaging>pom</packaging>"))) == []

    def test_plugin_and_profile_dependencies_are_not_recorded(self, tmp_path):
        """Plugin dependencies are the build tool's classpath, and profile
        dependencies apply only when the profile is active."""
        path = _write(tmp_path, _pom(
            _deps(_dep("junit", "junit", "4.13.2"))
            + "<build><plugins><plugin><artifactId>maven-x-plugin</artifactId>"
            + _deps(_dep("org.ow2.asm", "asm", "9.7"))
            + "</plugin></plugins></build>"
            + "<profiles><profile><id>p</id>"
            + _deps(_dep("org.slf4j", "slf4j-api", "2.0.13"))
            + "</profile></profiles>"
        ))

        assert _versions(extract_pom(path)) == {"junit:junit": "4.13.2"}

    def test_managed_entries_that_are_not_imports_are_not_recorded(self, tmp_path):
        """<dependencyManagement> pins versions for children; it does not
        make this project depend on anything."""
        path = _write(tmp_path, _pom(
            "<dependencyManagement>" + _deps(_dep("junit", "junit", "4.13.2"))
            + "</dependencyManagement>"
        ))

        assert extract_pom(path) == []


class TestScope:
    """declared_scope is the raw <scope>; docs/dependency-flags.md has the six
    values and why only `test` maps to is_dev."""

    def _record(self, tmp_path, scope):
        path = _write(tmp_path, _pom(_deps(_dep("g", "a", "1.0", scope=scope))))
        return extract_pom(path)[0]

    def test_test_scope_is_dev(self, tmp_path):
        record = self._record(tmp_path, "test")
        assert record["declared_scope"] == "test"
        assert record["is_dev"] is True

    @pytest.mark.parametrize("scope", ["compile", "provided", "runtime", "system"])
    def test_other_scopes_are_not_dev(self, tmp_path, scope):
        record = self._record(tmp_path, scope)
        assert record["declared_scope"] == scope
        assert record["is_dev"] is False

    def test_absent_scope_is_compile_but_stored_as_null(self, tmp_path):
        """Maven's default is compile, so it is not dev. The file did not say
        `compile`, and declared_scope stores what the file said."""
        record = self._record(tmp_path, None)
        assert record["declared_scope"] is None
        assert record["is_dev"] is False

    def test_optional_and_install_script_are_unknown(self, tmp_path):
        """Maven's <optional> means "not passed on to consumers", not "may not
        install", so it is not is_optional. Maven has no install scripts."""
        path = _write(tmp_path, _pom(_deps(
            _dep("g", "a", "1.0", extra="<optional>true</optional>"))))
        record = extract_pom(path)[0]
        assert record["is_optional"] is None
        assert record["runs_install_script"] is None


class TestBomImports:
    def test_bom_import_is_recorded(self, tmp_path):
        """An imported BOM is where missing versions come from, so it is the
        one <dependencyManagement> entry worth a row — 4 on the estate."""
        path = _write(tmp_path, _pom(
            "<dependencyManagement>" + _deps(
                _dep("io.netty", "netty-bom", "4.2.18.Final", scope="import",
                     extra="<type>pom</type>"),
                _dep("junit", "junit", "4.13.2"),
            ) + "</dependencyManagement>"
        ))

        records = extract_pom(path)

        assert _versions(records) == {"io.netty:netty-bom": "4.2.18.Final"}
        assert records[0]["requirements_type"] == "bom"
        assert records[0]["declared_scope"] == "import"
        assert records[0]["is_dev"] is None

    def test_bom_version_range_is_kept_verbatim(self, tmp_path):
        """Both okhttp copies on the estate import their BOM with a range."""
        path = _write(tmp_path, _pom(
            "<dependencyManagement>" + _deps(
                _dep("com.squareup.okhttp3", "okhttp-bom",
                     "[5.2.0-SNAPSHOT,6.0.0-SNAPSHOT)", scope="import",
                     extra="<type>pom</type>"),
            ) + "</dependencyManagement>"
        ))

        assert _versions(extract_pom(path)) == {
            "com.squareup.okhttp3:okhttp-bom": "[5.2.0-SNAPSHOT,6.0.0-SNAPSHOT)"}


class TestTier1Interpolation:
    def test_local_property(self, tmp_path):
        path = _write(tmp_path, _pom(
            "<properties><protobuf.version>4.31.1</protobuf.version></properties>"
            + _deps(_dep("com.google.protobuf", "protobuf-java", "${protobuf.version}"))
        ))

        assert _versions(extract_pom(path)) == {
            "com.google.protobuf:protobuf-java": "4.31.1"}

    def test_property_referring_to_a_property(self, tmp_path):
        path = _write(tmp_path, _pom(
            "<properties><base>1.2</base><full>${base}.3</full></properties>"
            + _deps(_dep("g", "a", "${full}"))
        ))

        assert _versions(extract_pom(path)) == {"g:a": "1.2.3"}

    def test_project_parent_version_from_the_files_own_parent_block(self, tmp_path):
        """All 5 unresolved properties on the estate are this one, and the
        file itself answers it — no parent pom needs to be read."""
        path = _write(tmp_path, _pom(
            "<parent><groupId>com.google.code.gson</groupId>"
            "<artifactId>gson-parent</artifactId><version>2.14.1-SNAPSHOT</version></parent>"
            "<artifactId>proto</artifactId>"
            + _deps(_dep("com.google.code.gson", "gson", "${project.parent.version}"))
        ))

        assert _versions(extract_pom(path)) == {
            "com.google.code.gson:gson": "2.14.1-SNAPSHOT"}

    def test_project_version_falls_back_to_the_parent_version(self, tmp_path):
        """A child that omits <version> inherits its parent's."""
        path = _write(tmp_path, _pom(
            "<parent><groupId>g</groupId><artifactId>p</artifactId>"
            "<version>3.0</version></parent><artifactId>child</artifactId>"
            + _deps(_dep("g", "sibling", "${project.version}"))
        ))

        assert _versions(extract_pom(path)) == {"g:sibling": "3.0"}

    def test_project_group_id_in_a_dependency_name(self, tmp_path):
        path = _write(tmp_path, _pom(
            "<groupId>org.acme</groupId><artifactId>app</artifactId><version>1.0</version>"
            + _deps(_dep("${project.groupId}", "lib", "${project.version}"))
        ))

        assert _versions(extract_pom(path)) == {"org.acme:lib": "1.0"}

    def test_unresolvable_property_is_kept_verbatim(self, tmp_path):
        """Kept, not blanked: enrichment then reports `unresolved_spec` and
        the row still says which property it was waiting on."""
        path = _write(tmp_path, _pom(_deps(_dep("g", "a", "${revision}"))))

        assert _versions(extract_pom(path)) == {"g:a": "${revision}"}

    def test_self_referencing_property_terminates(self, tmp_path):
        path = _write(tmp_path, _pom(
            "<properties><a>${a}</a></properties>" + _deps(_dep("g", "x", "${a}"))
        ))

        assert _versions(extract_pom(path)) == {"g:x": "${a}"}

    def test_exponential_property_expansion_is_refused(self, tmp_path):
        """CWE-776's mechanism without a DTD: each property doubles the one
        below it, so ten levels reach a thousand copies and thirty a billion.
        The input is attacker-influenced, so the expansion is capped and the
        declared text kept instead."""
        props = "<p0>xxxxxxxxxx</p0>" + "".join(
            f"<p{i}>${{p{i - 1}}}${{p{i - 1}}}</p{i}>" for i in range(1, 31))
        path = _write(tmp_path, _pom(
            f"<properties>{props}</properties>" + _deps(_dep("g", "a", "${p30}"))
        ))

        assert _versions(extract_pom(path)) == {"g:a": "${p30}"}

    def test_a_single_wide_pass_is_refused_before_it_is_built(self, tmp_path):
        """The cap must bound memory, not just the result. One pass that
        substitutes a large property into many references is a blow-up of
        its own: 250 references to a 200 KB property is 50 MB, built before
        a length check that runs afterwards. Peak allocation stays far below."""
        import tracemalloc

        big = "x" * 200_000
        refs = "${a}" * 250
        path = _write(tmp_path, _pom(
            f"<properties><a>{big}</a><refs>{refs}</refs></properties>"
            + _deps(_dep("g", "a", "${refs}"))
        ))

        tracemalloc.start()
        try:
            versions = _versions(extract_pom(path))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert versions == {"g:a": "${refs}"}
        assert peak < 10_000_000

    def test_an_oversized_declared_value_is_kept_verbatim(self, tmp_path):
        long_version = "${a}" * 300  # 1200 characters, past the cap as written
        path = _write(tmp_path, _pom(
            "<properties><a>1</a></properties>" + _deps(_dep("g", "a", long_version))
        ))

        assert _versions(extract_pom(path)) == {"g:a": long_version}


class TestTier2ParentInRepo:
    PARENT = _pom(
        "<groupId>org.acme</groupId><artifactId>acme-parent</artifactId>"
        "<version>2.0</version><packaging>pom</packaging>"
        "<properties><junit.version>4.13.2</junit.version></properties>"
        "<dependencyManagement>" + _deps(
            _dep("junit", "junit", "${junit.version}"),
            _dep("com.google.truth", "truth", "1.4.5"),
            _dep("org.acme", "acme-core", "${project.version}"),
        ) + "</dependencyManagement>"
    )

    def _child(self, deps, parent_extra=""):
        return _pom(
            "<parent><groupId>org.acme</groupId><artifactId>acme-parent</artifactId>"
            f"<version>2.0</version>{parent_extra}</parent>"
            "<artifactId>acme-child</artifactId>" + _deps(*deps)
        )

    def test_missing_versions_come_from_the_parent_one_directory_up(self, repo):
        """The gson layout: 13 of the 24 missing versions on the estate."""
        _write(repo, self.PARENT)
        child = _write(repo / "child", self._child(
            [_dep("junit", "junit", scope="test"), _dep("com.google.truth", "truth")]))

        assert _versions(extract_pom(child)) == {
            "junit:junit": "4.13.2", "com.google.truth:truth": "1.4.5"}

    def test_parent_managed_project_version_is_the_childs(self, repo):
        """Maven interpolates after inheritance, so ${project.version} in the
        parent's management means the child's version."""
        _write(repo, self.PARENT)
        child = _write(repo / "child", _pom(
            "<parent><groupId>org.acme</groupId><artifactId>acme-parent</artifactId>"
            "<version>2.0</version></parent>"
            "<artifactId>acme-child</artifactId><version>2.1</version>"
            + _deps(_dep("org.acme", "acme-core"))
        ))

        assert _versions(extract_pom(child)) == {"org.acme:acme-core": "2.1"}

    def test_an_explicit_version_wins_over_management(self, repo):
        _write(repo, self.PARENT)
        child = _write(repo / "child", self._child([_dep("junit", "junit", "4.12")]))

        assert _versions(extract_pom(child)) == {"junit:junit": "4.12"}

    def test_child_property_overrides_the_parents(self, repo):
        _write(repo, self.PARENT)
        child = _write(repo / "child", _pom(
            "<parent><groupId>org.acme</groupId><artifactId>acme-parent</artifactId>"
            "<version>2.0</version></parent><artifactId>acme-child</artifactId>"
            "<properties><junit.version>4.11</junit.version></properties>"
            + _deps(_dep("junit", "junit"))
        ))

        assert _versions(extract_pom(child)) == {"junit:junit": "4.11"}

    def test_grandparent_chain_is_followed(self, repo):
        _write(repo, _pom(
            "<groupId>org.acme</groupId><artifactId>root</artifactId><version>1</version>"
            "<dependencyManagement>" + _deps(_dep("junit", "junit", "4.13.2"))
            + "</dependencyManagement>"
        ))
        _write(repo / "mid", _pom(
            "<parent><groupId>org.acme</groupId><artifactId>root</artifactId>"
            "<version>1</version></parent><artifactId>mid</artifactId>"
        ))
        child = _write(repo / "mid" / "leaf", _pom(
            "<parent><groupId>org.acme</groupId><artifactId>mid</artifactId>"
            "<version>1</version></parent><artifactId>leaf</artifactId>"
            + _deps(_dep("junit", "junit"))
        ))

        assert _versions(extract_pom(child)) == {"junit:junit": "4.13.2"}

    def test_nearer_ancestor_wins_over_a_further_one(self, repo):
        _write(repo, _pom(
            "<groupId>org.acme</groupId><artifactId>root</artifactId><version>1</version>"
            "<properties><v>root</v></properties>"
            "<dependencyManagement>" + _deps(_dep("junit", "junit", "4.13.2"))
            + "</dependencyManagement>"
        ))
        _write(repo / "mid", _pom(
            "<parent><groupId>org.acme</groupId><artifactId>root</artifactId>"
            "<version>1</version></parent><artifactId>mid</artifactId>"
            "<properties><v>mid</v></properties>"
            "<dependencyManagement>" + _deps(_dep("junit", "junit", "4.12"))
            + "</dependencyManagement>"
        ))
        child = _write(repo / "mid" / "leaf", _pom(
            "<parent><groupId>org.acme</groupId><artifactId>mid</artifactId>"
            "<version>1</version></parent><artifactId>leaf</artifactId>"
            + _deps(_dep("junit", "junit"), _dep("g", "a", "${v}"))
        ))

        assert _versions(extract_pom(child)) == {"junit:junit": "4.12", "g:a": "mid"}

    def test_explicit_relative_path_to_a_directory(self, repo):
        _write(repo / "build" / "parent", self.PARENT)
        child = _write(repo / "child", self._child(
            [_dep("junit", "junit")],
            parent_extra="<relativePath>../build/parent</relativePath>"))

        assert _versions(extract_pom(child)) == {"junit:junit": "4.13.2"}

    def test_empty_relative_path_means_do_not_look_locally(self, repo):
        """`<relativePath/>` is Maven's documented way to say the parent is
        only in a repository, even when a pom sits one directory up."""
        _write(repo, self.PARENT)
        child = _write(repo / "child", self._child(
            [_dep("junit", "junit")], parent_extra="<relativePath/>"))

        assert _versions(extract_pom(child)) == {"junit:junit": ""}

    def test_a_pom_that_is_not_the_named_parent_is_ignored(self, repo):
        """Maven checks the coordinates and falls back to a repository when
        the file at relativePath is some other project. So must this."""
        _write(repo, self.PARENT.replace("acme-parent", "something-else"))
        child = _write(repo / "child", self._child([_dep("junit", "junit")]))

        assert _versions(extract_pom(child)) == {"junit:junit": ""}

    def test_a_parent_outside_the_repository_is_not_read(self, tmp_path, repo):
        """relativePath comes from a cloned third-party repository; it must
        not be able to read files from elsewhere on the scanning host."""
        _write(tmp_path, self.PARENT)  # beside the repo, not inside it
        child = _write(repo, self._child(
            [_dep("junit", "junit")], parent_extra="<relativePath>../pom.xml</relativePath>"))

        assert _versions(extract_pom(child)) == {"junit:junit": ""}

    def test_a_symlinked_parent_leaving_the_repository_is_not_read(self, tmp_path, repo):
        outside = tmp_path / "outside"
        _write(outside, self.PARENT)
        (repo / "linked").symlink_to(outside, target_is_directory=True)
        child = _write(repo / "child", self._child(
            [_dep("junit", "junit")],
            parent_extra="<relativePath>../linked/pom.xml</relativePath>"))

        assert _versions(extract_pom(child)) == {"junit:junit": ""}

    def test_without_a_git_checkout_no_parent_is_followed(self, tmp_path):
        """No repository boundary can be established, so none is crossed."""
        _write(tmp_path, self.PARENT)
        child = _write(tmp_path / "child", self._child([_dep("junit", "junit")]))

        assert _versions(extract_pom(child)) == {"junit:junit": ""}

    def test_a_parent_cycle_terminates(self, repo):
        _write(repo / "a", _pom(
            "<parent><groupId>g</groupId><artifactId>b</artifactId><version>1</version>"
            "<relativePath>../b/pom.xml</relativePath></parent><artifactId>a</artifactId>"
            + _deps(_dep("junit", "junit"))
        ))
        _write(repo / "b", _pom(
            "<parent><groupId>g</groupId><artifactId>a</artifactId><version>1</version>"
            "<relativePath>../a/pom.xml</relativePath></parent><artifactId>b</artifactId>"
        ))

        assert _versions(extract_pom(str(repo / "a" / "pom.xml"))) == {"junit:junit": ""}

    def test_an_unreadable_parent_leaves_the_child_readable(self, repo):
        """The child is the file being scanned, and it was read. A broken
        parent costs only the versions it would have supplied."""
        _write(repo, "<project><broken>")
        child = _write(repo / "child", self._child([_dep("junit", "junit")]))

        assert _versions(extract_pom(child)) == {"junit:junit": ""}

    def test_a_parent_carrying_a_doctype_is_not_parsed(self, repo):
        _write(repo, self.PARENT.replace(
            "<project", '<!DOCTYPE project [<!ENTITY v "4.13.2">]>\n<project', 1)
            .replace("${junit.version}", "&v;"))
        child = _write(repo / "child", self._child([_dep("junit", "junit")]))

        assert _versions(extract_pom(child)) == {"junit:junit": ""}


class TestUnreadable:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_pom(str(tmp_path / "pom.xml"))

    def test_malformed_xml_raises(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_pom(_write(tmp_path, "<project><dependencies>"))

    def test_root_that_is_not_project_raises(self, tmp_path):
        """Any XML file named pom.xml would otherwise report as a project
        declaring nothing. All 14 on the estate have a <project> root."""
        with pytest.raises(UnreadableManifest, match="not <project>"):
            extract_pom(_write(tmp_path, "<settings></settings>"))

    def test_doctype_is_refused(self, tmp_path):
        """CWE-776. ElementTree expands internal entities, which need a DTD;
        no pom.xml on the estate carries a DOCTYPE."""
        with pytest.raises(UnreadableManifest, match="DOCTYPE"):
            extract_pom(_write(tmp_path,
                '<!DOCTYPE project [<!ENTITY a "aaaa">]>' + _pom("")))

    def test_dependency_without_artifact_id_raises(self, tmp_path):
        """Maven refuses such a pom outright. Skipping the entry would report
        the file as declaring fewer dependencies than it does."""
        with pytest.raises(UnreadableManifest, match="artifactId"):
            extract_pom(_write(tmp_path, _pom(
                "<dependencies><dependency><groupId>g</groupId></dependency></dependencies>")))
