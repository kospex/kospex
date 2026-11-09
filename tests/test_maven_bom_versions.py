"""Versions an imported Maven BOM supplies, looked up on deps.dev (tier 3).

See changes/202611-maven-bom-version-resolution.md. A blank-version row in a
pom that imports a BOM is looked up at the version the BOM manages; the
declaration stays blank and the looked-up version lands in resolved_version.
A failed BOM lookup leaves the row exactly as it was: no_version.
"""

import json

import pytest

import krunner
from kospex.extractors.registry import REGISTRY
from kospex_dependencies import KospexDependencies

NETTY_BOM = ("https://api.deps.dev/v3alpha/systems/maven/packages/"
             "io.netty%3Anetty-bom/versions/4.2.18.Final:requirements")
OTHER_BOM = ("https://api.deps.dev/v3alpha/systems/maven/packages/"
             "com.example%3Aother-bom/versions/1.0:requirements")


def _requirements(*managed):
    """Entries the BOM declares itself: deps.dev fills name and resolvedName."""
    return json.dumps({"maven": {"dependencyManagement": [
        _declared(name, version) for name, version in managed
    ]}})


def _declared(name, version):
    return {"name": name, "version": "${project.version}", "resolvedName": name,
            "resolvedVersion": version, "origin": "management"}


def _inherited(name, version):
    """An entry from a BOM this BOM imports, or from its parent: deps.dev
    leaves name blank and gives the artifact only as resolvedName."""
    return {"name": "", "version": "", "resolvedName": name,
            "resolvedVersion": version, "origin": "management"}


def _nested_import(name, version):
    """The coordinates of a BOM this BOM imports. Not a managed artifact."""
    return {"name": "", "version": "", "resolvedName": name,
            "resolvedVersion": version, "origin": "import"}


def _raw(*entries):
    return json.dumps({"maven": {"dependencyManagement": list(entries)}})


class _Query:
    """Serves canned (content, status) per URL and records what was asked."""

    def __init__(self, responses):
        self.responses = responses
        self.asked = []

    def url_request_with_status(self, url, **_kwargs):
        self.asked.append(url)
        return self.responses.get(url, (None, 404))


def _row(name, version="", req_type="direct", file_path="pom.xml",
         repo_id="github.com~acme~app"):
    return {"package_name": name, "package_version": version,
            "package_type": "maven", "requirements_type": req_type,
            "file_path": file_path, "_repo_id": repo_id}


def _bom(name="io.netty:netty-bom", version="4.2.18.Final", **kwargs):
    return _row(name, version, req_type="bom", **kwargs)


def _kdeps(responses):
    return KospexDependencies(kospex_query=_Query(responses))


class TestManagedVersions:
    def test_blank_row_gets_the_bom_version(self):
        kdeps = _kdeps({NETTY_BOM: (_requirements(("io.netty:netty-handler", "4.2.18.Final")), 200)})
        records = [_row("io.netty:netty-handler"), _bom()]

        assert kdeps.maven_bom_versions(records) == {0: "4.2.18.Final"}

    def test_rows_with_a_version_are_left_alone(self):
        kdeps = _kdeps({NETTY_BOM: (_requirements(("io.netty:netty-handler", "4.2.18.Final")), 200)})
        records = [_row("io.netty:netty-handler", "4.1.0.Final"), _bom()]

        assert kdeps.maven_bom_versions(records) == {}

    def test_no_bom_means_no_request(self):
        kdeps = _kdeps({})
        records = [_row("io.netty:netty-handler")]

        assert kdeps.maven_bom_versions(records) == {}
        assert kdeps.kospex_query.asked == []

    def test_a_bom_only_applies_to_its_own_file(self):
        kdeps = _kdeps({NETTY_BOM: (_requirements(("io.netty:netty-handler", "4.2.18.Final")), 200)})
        records = [_row("io.netty:netty-handler", file_path="other/pom.xml"),
                   _bom(file_path="pom.xml")]

        assert kdeps.maven_bom_versions(records) == {}

    def test_a_bom_only_applies_to_its_own_repo(self):
        """osi pools every repository's records into one list."""
        kdeps = _kdeps({NETTY_BOM: (_requirements(("io.netty:netty-handler", "4.2.18.Final")), 200)})
        records = [_row("io.netty:netty-handler", repo_id="github.com~acme~two"),
                   _bom(repo_id="github.com~acme~one")]

        assert kdeps.maven_bom_versions(records) == {}

    def test_first_declared_bom_wins(self):
        """Maven's rule when two imports manage the same artifact."""
        kdeps = _kdeps({
            OTHER_BOM: (_requirements(("io.netty:netty-handler", "9.9.9")), 200),
            NETTY_BOM: (_requirements(("io.netty:netty-handler", "4.2.18.Final")), 200),
        })
        records = [_bom("com.example:other-bom", "1.0"), _bom(),
                   _row("io.netty:netty-handler")]

        assert kdeps.maven_bom_versions(records) == {2: "9.9.9"}

    def test_later_bom_fills_what_the_first_does_not_manage(self):
        kdeps = _kdeps({
            OTHER_BOM: (_requirements(("com.example:thing", "1.0")), 200),
            NETTY_BOM: (_requirements(("io.netty:netty-handler", "4.2.18.Final")), 200),
        })
        records = [_bom("com.example:other-bom", "1.0"), _bom(),
                   _row("io.netty:netty-handler")]

        assert kdeps.maven_bom_versions(records) == {2: "4.2.18.Final"}

    def test_each_bom_is_fetched_once_per_run(self):
        kdeps = _kdeps({NETTY_BOM: (_requirements(("io.netty:netty-handler", "4.2.18.Final")), 200)})
        records = [_bom(file_path="a/pom.xml"), _row("io.netty:netty-handler", file_path="a/pom.xml"),
                   _bom(file_path="b/pom.xml"), _row("io.netty:netty-handler", file_path="b/pom.xml")]

        assert kdeps.maven_bom_versions(records) == {1: "4.2.18.Final", 3: "4.2.18.Final"}
        assert kdeps.kospex_query.asked == [NETTY_BOM]

    def test_other_ecosystems_are_ignored(self):
        kdeps = _kdeps({})
        records = [{"package_name": "left-pad", "package_version": "",
                    "package_type": "npm", "requirements_type": "direct"}]

        assert kdeps.maven_bom_versions(records) == {}


class TestBomThatInheritsItsVersions:
    """A BOM that imports other BOMs or has a parent, as spring-boot-dependencies
    does: most of what it manages arrives with a blank name (1,157 of 1,607
    entries for 3.5.7), so the artifact is read from resolvedName."""

    def test_version_from_a_bom_it_imports(self):
        kdeps = _kdeps({NETTY_BOM: (_raw(_inherited("org.assertj:assertj-core", "3.27.6")), 200)})
        records = [_bom(), _row("org.assertj:assertj-core")]

        assert kdeps.maven_bom_versions(records) == {1: "3.27.6"}

    def test_a_nested_import_is_not_a_managed_version(self):
        kdeps = _kdeps({NETTY_BOM: (_raw(_nested_import("org.assertj:assertj-bom", "3.27.6")), 200)})
        records = [_bom(), _row("org.assertj:assertj-bom")]

        assert kdeps.maven_bom_versions(records) == {}

    def test_the_import_declaration_itself_is_skipped(self):
        # Named, but unresolved: the `scope=import` entry as written in the BOM.
        declaration = {"name": "org.assertj:assertj-bom", "version": "${assertj.version}",
                       "type": "pom", "scope": "import", "resolvedName": "",
                       "resolvedVersion": "", "origin": "management"}
        kdeps = _kdeps({NETTY_BOM: (_raw(declaration), 200)})
        records = [_bom(), _row("org.assertj:assertj-bom")]

        assert kdeps.maven_bom_versions(records) == {}

    def test_first_entry_for_an_artifact_wins(self):
        # Maven: a version the BOM declares beats one it imports. deps.dev
        # lists the BOM's own entries before the inherited ones.
        kdeps = _kdeps({NETTY_BOM: (_raw(_declared("io.netty:netty-handler", "4.2.18.Final"),
                                         _inherited("io.netty:netty-handler", "4.1.0.Final")), 200)})
        records = [_bom(), _row("io.netty:netty-handler")]

        assert kdeps.maven_bom_versions(records) == {1: "4.2.18.Final"}


class TestFailuresLeaveTheRowBlank:
    """Decision 2: the version stays unknown, no new resolution value."""

    def test_snapshot_bom_is_not_looked_up(self):
        kdeps = _kdeps({})
        records = [_bom("com.squareup.okhttp3:okhttp-bom", "[5.2.0-SNAPSHOT,6.0.0-SNAPSHOT)"),
                   _row("com.squareup.okhttp3:okhttp")]

        assert kdeps.maven_bom_versions(records) == {}
        assert kdeps.kospex_query.asked == []

    @pytest.mark.parametrize("status", [404, 429, 500, None])
    def test_failed_bom_lookup(self, status):
        kdeps = _kdeps({NETTY_BOM: (None, status)})
        records = [_bom(), _row("io.netty:netty-handler")]

        assert kdeps.maven_bom_versions(records) == {}

    def test_artifact_not_in_the_bom(self):
        kdeps = _kdeps({NETTY_BOM: (_requirements(("io.netty:netty-buffer", "4.2.18.Final")), 200)})
        records = [_bom(), _row("com.example:unrelated")]

        assert kdeps.maven_bom_versions(records) == {}

    def test_unparseable_response(self):
        kdeps = _kdeps({NETTY_BOM: ("<html>not json</html>", 200)})
        records = [_bom(), _row("io.netty:netty-handler")]

        assert kdeps.maven_bom_versions(records) == {}

    def test_entry_without_a_resolved_version(self):
        kdeps = _kdeps({NETTY_BOM: (_requirements(("io.netty:netty-handler", "")), 200)})
        records = [_bom(), _row("io.netty:netty-handler")]

        assert kdeps.maven_bom_versions(records) == {}


def _capture_lookups(kdeps, monkeypatch):
    """Replace the per-package deps.dev lookup; return the (name, version) asked."""
    asked = []

    def _record(package_type, package_name, package_version):
        asked.append((package_name, package_version))
        resolution = "resolved" if package_version else "no_version"
        return {"package_name": package_name, "package_version": package_version,
                "package_type": package_type, "versions_behind": 0,
                "advisories": 0, "resolution": resolution}

    monkeypatch.setattr(kdeps, "depsdev_record", _record)
    return asked


class TestBothScanPaths:
    """Decision 1: declaration blank, looked-up version in resolved_version."""

    def _records(self):
        return [_row("io.netty:netty-handler"), _bom()]

    def _kdeps(self):
        return _kdeps({NETTY_BOM: (_requirements(("io.netty:netty-handler", "4.2.18.Final")), 200)})

    def test_osi(self, monkeypatch):
        kdeps = self._kdeps()
        asked = _capture_lookups(kdeps, monkeypatch)

        rows = krunner.enrich_dependency_records(self._records(), kdeps)

        assert ("io.netty:netty-handler", "4.2.18.Final") in asked
        handler = rows[0]
        assert handler["package_version"] == ""
        assert handler["resolved_version"] == "4.2.18.Final"
        assert handler["resolution"] == "resolved"

    def test_sca(self, monkeypatch):
        kdeps = self._kdeps()
        asked = _capture_lookups(kdeps, monkeypatch)
        extractor = next(e for e in REGISTRY if e.name == "maven-pom")

        rows = kdeps._enrich_dependency_records(self._records(), extractor)

        assert ("io.netty:netty-handler", "4.2.18.Final") in asked
        handler = rows[0]
        assert handler["package_version"] == ""
        assert handler["version_kind"] == "none"
        assert handler["resolved_version"] == "4.2.18.Final"
        assert handler["resolution"] == "resolved"

    def test_osi_failed_bom_is_no_version(self, monkeypatch):
        kdeps = _kdeps({NETTY_BOM: (None, 404)})
        asked = _capture_lookups(kdeps, monkeypatch)

        rows = krunner.enrich_dependency_records(self._records(), kdeps)

        assert ("io.netty:netty-handler", "") in asked
        assert rows[0]["resolution"] == "no_version"
        assert rows[0]["resolved_version"] == ""
