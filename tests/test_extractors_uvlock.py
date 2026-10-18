"""Tests for the uv.lock extractor.

Mirrors tests/test_extractors_pnpm.py: template contract, extract-level
tests, install state, and the error contract. Every raise-path fixture is
synthetic, because every uv.lock on the development estate is well-formed.

Each format gate below was checked against uv itself (0.11.11, `uv export
--frozen`) before it was written: uv refuses `version` other than the
integer 1, a non-integer `revision`, an empty file and UTF-16, and reads an
unknown `revision`, a missing `revision` and a UTF-8 BOM.
"""
import pytest

from kospex.extractors.errors import UnreadableManifest
from kospex.extractors.uvlock import _template, extract_uv_lock

HEADER = 'version = 1\nrevision = 3\nrequires-python = ">=3.9"\n'

#: A project `app` with one runtime dependency (which pulls a transitive),
#: an extra `socks`, and a `dev` group. Shaped on the real files: the
#: project is `source = { editable = "." }` and carries no version.
PROJECT = HEADER + '''
[[package]]
name = "app"
source = { editable = "." }
dependencies = [
    { name = "requests" },
]

[package.optional-dependencies]
socks = [
    { name = "pysocks" },
]

[package.dev-dependencies]
dev = [
    { name = "pytest" },
]

[package.metadata]
requires-dist = [
    { name = "requests", specifier = ">=2" },
    { name = "pysocks", marker = "extra == 'socks'" },
]
provides-extras = ["socks"]

[package.metadata.requires-dev]
dev = [{ name = "pytest" }]

[[package]]
name = "requests"
version = "2.32.3"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "urllib3" },
]

[[package]]
name = "urllib3"
version = "2.2.3"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pysocks"
version = "1.7.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pytest"
version = "8.3.3"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "pluggy" },
]

[[package]]
name = "pluggy"
version = "1.5.0"
source = { registry = "https://pypi.org/simple" }
'''


def _write(tmp_path, content, name="uv.lock"):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def _write_bytes(tmp_path, raw, name="uv.lock"):
    p = tmp_path / name
    p.write_bytes(raw)
    return str(p)


def _by_name(records):
    by = {r["package_name"]: r for r in records}
    assert len(by) == len(records), "a name was recorded twice"
    return by


class TestTemplateContract:
    """The local record template must stay in sync with KospexDependencies."""

    def test_template_matches_get_package_template(self):
        from kospex_dependencies import KospexDependencies
        assert set(_template().keys()) == set(
            KospexDependencies().get_package_template().keys()
        )

    def test_advisory_columns_default_to_null_not_empty_string(self):
        """`advisories` and `versions_behind` are INTEGER columns.

        SQLite sorts TEXT above every INTEGER, so '' > 0 is true and a row
        carrying '' counts as vulnerable (#258). The older templates use ''
        and this one must not add rows to that defect.
        """
        t = _template()
        assert t["advisories"] is None
        assert t["versions_behind"] is None


class TestExtract:

    def test_third_party_packages_are_recorded(self, tmp_path):
        records = extract_uv_lock(_write(tmp_path, PROJECT))
        by = _by_name(records)
        assert set(by) == {"requests", "urllib3", "pysocks", "pytest", "pluggy"}
        assert by["requests"]["package_version"] == "2.32.3"
        assert all(r["ecosystem"] == "pypi" for r in records)

    def test_the_project_itself_is_not_a_dependency(self, tmp_path):
        """An editable entry is the project, not its supply chain.

        On the estate all 13 entries lacking a version are editable or
        virtual, so excluding first-party is also what keeps every recorded
        row carrying a version.
        """
        records = extract_uv_lock(_write(tmp_path, PROJECT))
        assert len(records) == 5
        assert "app" not in {r["package_name"] for r in records}
        assert all(r["package_version"] for r in records)

    def test_virtual_and_workspace_members_are_not_dependencies(self, tmp_path):
        content = HEADER + '''
[manifest]
members = ["root", "member"]

[[package]]
name = "root"
source = { virtual = "." }
dependencies = [{ name = "member" }]

[[package]]
name = "member"
version = "0.1.0"
source = { editable = "packages/member" }
dependencies = [{ name = "idna" }]

[[package]]
name = "idna"
version = "3.10"
source = { registry = "https://pypi.org/simple" }
'''
        records = extract_uv_lock(_write(tmp_path, content))
        assert [r["package_name"] for r in records] == ["idna"]
        # Reached through a workspace member, which is first-party, so it is
        # one of the project's own direct dependencies.
        assert records[0]["requirements_type"] == "direct"

    def test_a_git_source_is_recorded(self, tmp_path):
        """A git dependency is third-party and asserts a name and version.

        deps.dev may not answer for it, which `resolution` records; dropping
        it would hide a dependency, the direction this inventory must not err.
        """
        content = HEADER + '''
[[package]]
name = "app"
source = { editable = "." }
dependencies = [{ name = "hypercorn" }]

[[package]]
name = "hypercorn"
version = "0.15.0"
source = { git = "https://github.com/acme/hypercorn?rev=x#d1719f8c" }
'''
        records = extract_uv_lock(_write(tmp_path, content))
        assert [(r["package_name"], r["package_version"]) for r in records] == [
            ("hypercorn", "0.15.0")]

    def test_two_versions_of_one_name_are_both_recorded(self, tmp_path):
        """uv forks resolution by marker, so a name can lock at two versions.

        A version-qualified edge is then how the graph says which one.
        """
        content = HEADER + '''
[[package]]
name = "app"
source = { editable = "." }
dependencies = [
    { name = "numpy", version = "1.26.4", source = { registry = "https://pypi.org/simple" }, marker = "python_full_version < '3.10'" },
]

[package.dev-dependencies]
dev = [
    { name = "numpy", version = "2.1.3", source = { registry = "https://pypi.org/simple" }, marker = "python_full_version >= '3.10'" },
]

[[package]]
name = "numpy"
version = "1.26.4"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "numpy"
version = "2.1.3"
source = { registry = "https://pypi.org/simple" }
'''
        records = extract_uv_lock(_write(tmp_path, content))
        by_version = {r["package_version"]: r for r in records}
        assert set(by_version) == {"1.26.4", "2.1.3"}
        assert by_version["1.26.4"]["requirements_type"] == "direct"
        assert by_version["1.26.4"]["is_dev"] is False
        assert by_version["2.1.3"]["requirements_type"] == "dev"
        assert by_version["2.1.3"]["is_dev"] is True


class TestRequirementsType:

    def test_direct_dev_optional_and_resolved(self, tmp_path):
        by = _by_name(extract_uv_lock(_write(tmp_path, PROJECT)))
        assert by["requests"]["requirements_type"] == "direct"
        assert by["pysocks"]["requirements_type"] == "optional"
        assert by["pytest"]["requirements_type"] == "dev"
        assert by["urllib3"]["requirements_type"] == "resolved"
        assert by["pluggy"]["requirements_type"] == "resolved"

    def test_every_value_is_one_the_schema_maps(self, tmp_path):
        """An unmapped requirements_type lands on a fallback package_use."""
        from kospex_schema import REQ_TO_PACKAGE_USE
        records = extract_uv_lock(_write(tmp_path, PROJECT))
        assert len(records) == 5
        for r in records:
            assert r["requirements_type"] in REQ_TO_PACKAGE_USE


class TestInstallState:
    """is_dev / is_optional come from walking the graph from the project.

    Without the walk only direct dependencies could be flagged, and the
    transitive layer -- most of every lockfile -- would be NULL.
    """

    def test_runtime_closure_is_neither_dev_nor_optional(self, tmp_path):
        by = _by_name(extract_uv_lock(_write(tmp_path, PROJECT)))
        for name in ("requests", "urllib3"):
            assert by[name]["is_dev"] is False, name
            assert by[name]["is_optional"] is False, name
            assert by[name]["declared_scope"] is None, name

    def test_dev_group_closure_is_dev(self, tmp_path):
        by = _by_name(extract_uv_lock(_write(tmp_path, PROJECT)))
        for name in ("pytest", "pluggy"):
            assert by[name]["is_dev"] is True, name
            assert by[name]["is_optional"] is False, name
            assert by[name]["declared_scope"] == "dev-dependencies:dev", name

    def test_extra_closure_is_optional_and_names_the_extra(self, tmp_path):
        by = _by_name(extract_uv_lock(_write(tmp_path, PROJECT)))
        assert by["pysocks"]["is_dev"] is False
        assert by["pysocks"]["is_optional"] is True
        assert by["pysocks"]["declared_scope"] == "optional-dependencies:socks"

    def test_install_scripts_are_unknown(self, tmp_path):
        """uv.lock says nothing about build or install hooks: NULL, not False."""
        records = extract_uv_lock(_write(tmp_path, PROJECT))
        assert len(records) == 5
        for r in records:
            assert r["runs_install_script"] is None

    def test_one_runtime_path_makes_a_package_non_dev(self, tmp_path):
        """Reached by both the dev group and the runtime closure: it installs."""
        content = PROJECT.replace(
            'dependencies = [\n    { name = "urllib3" },\n]',
            'dependencies = [\n    { name = "urllib3" },\n    { name = "pluggy" },\n]')
        by = _by_name(extract_uv_lock(_write(tmp_path, content)))
        assert by["pluggy"]["is_dev"] is False
        assert by["pluggy"]["is_optional"] is False
        assert by["pluggy"]["declared_scope"] is None

    def test_dev_and_extra_but_not_runtime(self, tmp_path):
        """npm's devOptional state: certainly not production, optionality unsaid."""
        content = PROJECT.replace(
            'socks = [\n    { name = "pysocks" },\n]',
            'socks = [\n    { name = "pysocks" },\n    { name = "pluggy" },\n]')
        by = _by_name(extract_uv_lock(_write(tmp_path, content)))
        assert by["pluggy"]["is_dev"] is True
        assert by["pluggy"]["is_optional"] is None
        assert by["pluggy"]["declared_scope"] == (
            "dev-dependencies:dev,optional-dependencies:socks")

    def test_an_extra_requested_on_an_edge_is_followed(self, tmp_path):
        """`{ name = "x", extra = ["socks"] }` installs x's socks extra too."""
        content = HEADER + '''
[[package]]
name = "app"
source = { editable = "." }
dependencies = [{ name = "requests", extra = ["socks"] }]

[[package]]
name = "requests"
version = "2.32.3"
source = { registry = "https://pypi.org/simple" }

[package.optional-dependencies]
socks = [{ name = "pysocks" }]
use-chardet = [{ name = "chardet" }]

[[package]]
name = "pysocks"
version = "1.7.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "chardet"
version = "5.2.0"
source = { registry = "https://pypi.org/simple" }
'''
        by = _by_name(extract_uv_lock(_write(tmp_path, content)))
        assert by["pysocks"]["is_dev"] is False
        assert by["pysocks"]["is_optional"] is False
        # Nothing requests use-chardet: locked, but on no install path.
        assert by["chardet"]["is_dev"] is None
        assert by["chardet"]["is_optional"] is None

    def test_a_package_on_no_path_is_unknown(self, tmp_path):
        """No first-party root reaches it, so the file does not say."""
        content = HEADER + '''
[[package]]
name = "idna"
version = "3.10"
source = { registry = "https://pypi.org/simple" }
'''
        (r,) = extract_uv_lock(_write(tmp_path, content))
        assert r["requirements_type"] == "resolved"
        assert r["is_dev"] is None
        assert r["is_optional"] is None
        assert r["declared_scope"] is None


class TestReturnsEmpty:
    """Read, well-formed, declares nothing: [] is true."""

    def test_no_package_array(self, tmp_path):
        assert extract_uv_lock(_write(tmp_path, HEADER)) == []

    def test_only_the_project_itself(self, tmp_path):
        content = HEADER + '''
[[package]]
name = "app"
version = "0.1.0"
source = { editable = "." }
'''
        assert extract_uv_lock(_write(tmp_path, content)) == []


class TestFormatVersions:

    @pytest.mark.parametrize("revision", [1, 2, 3])
    def test_known_revisions_parse(self, tmp_path, revision):
        content = PROJECT.replace("revision = 3", f"revision = {revision}")
        assert len(extract_uv_lock(_write(tmp_path, content))) == 5

    def test_an_unknown_revision_is_read_like_uv_reads_it(self, tmp_path):
        """uv reads `revision = 99`; refusing it would be a false positive.

        `revision` is uv's compatible bump within `version`, and uv exports
        such a file normally. Gating on it would turn every lockfile from the
        next uv into a parse_error that uv itself does not have.
        """
        content = PROJECT.replace("revision = 3", "revision = 99")
        assert len(extract_uv_lock(_write(tmp_path, content))) == 5

    def test_a_missing_revision_is_read(self, tmp_path):
        content = PROJECT.replace("revision = 3\n", "")
        assert len(extract_uv_lock(_write(tmp_path, content))) == 5


class TestRaises:
    """Unreadable or uninterpretable: raise, never return [].

    Each is a perturbation of the valid PROJECT file, which still parses --
    asserted alongside, so a raise cannot be the fixture's fault.
    """

    def test_baseline_parses(self, tmp_path):
        assert len(extract_uv_lock(_write(tmp_path, PROJECT))) == 5

    def test_missing_file(self, tmp_path):
        with pytest.raises(UnreadableManifest, match="not found"):
            extract_uv_lock(str(tmp_path / "uv.lock"))

    def test_a_directory(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_uv_lock(str(tmp_path))

    def test_empty_file(self, tmp_path):
        """uv refuses a 0-byte uv.lock: nothing declared because nothing read."""
        with pytest.raises(UnreadableManifest, match="version"):
            extract_uv_lock(_write(tmp_path, ""))

    def test_malformed_toml(self, tmp_path):
        with pytest.raises(UnreadableManifest, match="invalid TOML"):
            extract_uv_lock(_write(tmp_path, PROJECT + "\n[[package]\n"))

    def test_truncated_bytes(self, tmp_path):
        raw = PROJECT.encode()
        cut = raw.index(b'version = "2.32.3"') + len(b'version = "2.3')
        with pytest.raises(UnreadableManifest, match="invalid TOML"):
            extract_uv_lock(_write_bytes(tmp_path, raw[:cut]))

    def test_not_utf8(self, tmp_path):
        with pytest.raises(UnreadableManifest):
            extract_uv_lock(_write_bytes(tmp_path, PROJECT.encode("utf-16")))

    @pytest.mark.parametrize("version", ["0", "2", '"1"', "true", "1.0"])
    def test_a_version_uv_refuses(self, tmp_path, version):
        """uv: "unsupported schema version (v2, but only v1 is supported)".

        A string or bool is refused by uv's own TOML typing, so it is not
        coerced here: refusing is the fail-closed direction.
        """
        content = PROJECT.replace("version = 1\n", f"version = {version}\n", 1)
        with pytest.raises(UnreadableManifest, match="version"):
            extract_uv_lock(_write(tmp_path, content))

    def test_no_version(self, tmp_path):
        content = PROJECT.replace("version = 1\n", "", 1)
        with pytest.raises(UnreadableManifest, match="version"):
            extract_uv_lock(_write(tmp_path, content))

    @pytest.mark.parametrize("revision", ['"3"', "true", "-1"])
    def test_a_revision_uv_refuses(self, tmp_path, revision):
        content = PROJECT.replace("revision = 3", f"revision = {revision}")
        with pytest.raises(UnreadableManifest, match="revision"):
            extract_uv_lock(_write(tmp_path, content))

    def test_package_is_not_an_array_of_tables(self, tmp_path):
        with pytest.raises(UnreadableManifest, match="package"):
            extract_uv_lock(_write(tmp_path, HEADER + 'package = "requests"\n'))

    def test_a_package_with_no_name(self, tmp_path):
        content = PROJECT.replace('name = "urllib3"\nversion', 'version', 1)
        with pytest.raises(UnreadableManifest, match="name"):
            extract_uv_lock(_write(tmp_path, content))

    def test_a_package_with_no_source(self, tmp_path):
        """2,434 of 2,434 real entries carry `source`; it decides first-party."""
        content = PROJECT.replace(
            'version = "2.2.3"\nsource = { registry = "https://pypi.org/simple" }',
            'version = "2.2.3"')
        with pytest.raises(UnreadableManifest, match="source"):
            extract_uv_lock(_write(tmp_path, content))

    def test_a_dependency_list_that_is_not_a_list(self, tmp_path):
        content = PROJECT.replace(
            'dependencies = [\n    { name = "urllib3" },\n]',
            'dependencies = "urllib3"')
        with pytest.raises(UnreadableManifest, match="dependencies"):
            extract_uv_lock(_write(tmp_path, content))

    def test_a_dependency_edge_with_no_name(self, tmp_path):
        content = PROJECT.replace('{ name = "urllib3" }', '{ marker = "x" }')
        with pytest.raises(UnreadableManifest, match="name"):
            extract_uv_lock(_write(tmp_path, content))


class TestByteOrderMark:

    def test_a_utf8_bom_is_read_because_uv_reads_it(self, tmp_path):
        """uv exports a BOM'd uv.lock normally, so refusing it is a false
        positive. Contrast pyproject.toml, where pip refuses the BOM and so
        does kospex.

        Built byte-wise: an escape for an invisible character in source
        becomes the character, and a check written that way reads as
        startswith("") in review.
        """
        bom = bytes([0xEF, 0xBB, 0xBF])
        path = _write_bytes(tmp_path, bom + PROJECT.encode())
        with open(path, "rb") as f:
            assert f.read(3) == bom
        assert len(extract_uv_lock(path)) == 5
