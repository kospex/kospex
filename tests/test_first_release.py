"""A package's first release date, from deps.dev's version list (#390).

deps.dev has no "first release" field: it is the earliest `publishedAt` in the
package record. first_release() reads release_list() (#276), so it agrees with
versions_behind on what counts as a release. Two answers are reported: the
first published version of any kind, and the first stable release, because
the earliest version is often a pre-release.

Versions with no `publishedAt` are skipped and counted. numpy has 37 of them,
including 1.0.1.dev3460 -- older than its earliest dated version -- so the
date is the earliest *known* release, and `undated` says how sure that is.

Fixtures are real deps.dev records (tests/data/depsdev/). Expected values were
read from the fixtures outside kospex.
"""
import json
import pathlib
from urllib.parse import quote

import pytest
from click.testing import CliRunner

from kospex_dependencies import KospexDependencies

DATA = pathlib.Path(__file__).parent / "data" / "depsdev"
BASE = "https://api.deps.dev/v3alpha/systems"


class _Query:
    def __init__(self, responses):
        self.responses = responses

    def url_request_with_status(self, url, **_kwargs):
        return self.responses.get(url, (None, 404))

    def url_request(self, url, **_kwargs):
        return self.url_request_with_status(url)[0]


def _kd(system, name, listing):
    url = f"{BASE}/{system}/packages/{quote(name, safe='')}"
    return KospexDependencies(kospex_query=_Query({url: (listing, 200)}))


def _fixture_kd(system, name, fixture):
    return _kd(system, name, (DATA / fixture).read_text())


def _listing(*versions):
    """A deps.dev package record from (version, publishedAt, isDefault) tuples."""
    return json.dumps({"versions": [
        {"versionKey": {"version": v}, "publishedAt": d, "isDefault": default}
        for v, d, default in versions]})


class TestFirstRelease:

    def test_none_when_deps_dev_has_no_record(self):
        kd = KospexDependencies(kospex_query=_Query({}))
        assert kd.first_release("npm", "left-pad") is None

    def test_first_stable_skips_an_earlier_pre_release(self):
        kd = _kd("npm", "x", _listing(("0.1.0-alpha.1", "2010-01-01T00:00:00Z", False),
                                      ("0.1.0", "2011-01-01T00:00:00Z", False),
                                      ("1.0.0", "2012-01-01T00:00:00Z", True)))
        first = kd.first_release("npm", "x")

        assert first["first_published_version"] == "0.1.0-alpha.1"
        assert first["first_published"] == "2010-01-01T00:00:00Z"
        assert first["first_stable_version"] == "0.1.0"
        assert first["first_stable_published"] == "2011-01-01T00:00:00Z"
        assert first["versions"] == 3
        assert first["prereleases"] == 1
        assert first["undated"] == 0

    def test_undated_versions_are_skipped_and_counted(self):
        """An undated version sorts last in release_list() -- the oldest
        position -- and must not be reported as the first release."""
        kd = _kd("pypi", "requests", _listing(("2.23.0-py2.7", "", False),
                                              ("2.31.0", "2023-05-22T00:00:00Z", True),
                                              ("0.2.0", "2011-02-14T08:49:42Z", False)))
        first = kd.first_release("pypi", "requests")

        assert first["first_published_version"] == "0.2.0"
        assert first["first_stable_version"] == "0.2.0"
        assert first["undated"] == 1

    def test_only_pre_releases_has_no_first_stable(self):
        kd = _kd("npm", "x", _listing(("1.0.0-rc.1", "2020-01-01T00:00:00Z", True),
                                      ("1.0.0-beta.1", "2019-01-01T00:00:00Z", False)))
        first = kd.first_release("npm", "x")

        assert first["first_published_version"] == "1.0.0-beta.1"
        assert first["first_stable_version"] is None
        assert first["first_stable_published"] is None

    def test_nothing_dated_has_no_first_release(self):
        kd = _kd("pypi", "x", _listing(("1.0", "", True), ("0.9", "", False)))
        first = kd.first_release("pypi", "x")

        assert first["first_published"] is None
        assert first["first_published_version"] is None
        assert first["first_stable_published"] is None
        assert first["undated"] == 2

    def test_a_shared_timestamp_gives_the_lowest_version(self):
        same = "2011-01-01T00:00:00Z"
        kd = _kd("npm", "x", _listing(("0.1.10", same, False), ("0.1.9", same, False),
                                      ("1.0.0", "2012-01-01T00:00:00Z", True)))
        assert kd.first_release("npm", "x")["first_stable_version"] == "0.1.9"

    def test_numpy_reports_its_undated_versions(self):
        first = _fixture_kd("pypi", "numpy", "pypi__numpy.json").first_release("pypi", "numpy")

        assert first["first_stable_version"] == "1.3.0"
        assert first["first_stable_published"] == "2009-04-06T06:19:25Z"
        assert first["versions"] == 172
        assert first["undated"] == 37

    def test_react(self):
        first = _fixture_kd("npm", "react", "npm__react.json").first_release("npm", "react")

        assert first["first_published_version"] == "0.0.1"
        assert first["first_stable_published"] == "2011-10-26T17:46:22Z"


@pytest.fixture
def run_sca(monkeypatch, tmp_path):
    """`kospex sca ARGS`, with deps.dev answering `listing` for any package
    (or nothing, when listing is None). Records which package was asked for."""
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    HabitatConfig.reset_instance()

    def run(*args, listing=None):
        import kospex_cli

        real_release_list = KospexDependencies.release_list
        run.asked = []

        def release_list(self, package_type, package_name):
            run.asked.append((package_type, package_name))
            if listing is None:
                return None
            return real_release_list(_kd(package_type, package_name, listing),
                                     package_type, package_name)

        monkeypatch.setattr(KospexDependencies, "release_list", release_list)
        return CliRunner().invoke(kospex_cli.cli, ["sca", *args])

    return run


class TestScaPackage:
    """`kospex sca -package TYPE:NAME` looks up one package instead of a file."""

    def test_prints_both_first_releases(self, run_sca):
        result = run_sca("-package", "npm:x", listing=_listing(
            ("0.1.0-alpha.1", "2010-01-01T00:00:00Z", False),
            ("0.1.0", "2011-01-01T00:00:00Z", True)))

        assert result.exit_code == 0, result.output
        assert run_sca.asked == [("npm", "x")]
        assert "0.1.0-alpha.1" in result.output
        assert "2010-01-01" in result.output
        assert "2011-01-01" in result.output

    def test_reports_undated_versions(self, run_sca):
        result = run_sca("-package", "pypi:x", listing=_listing(
            ("1.0", "2015-01-01T00:00:00Z", True), ("0.1", "", False), ("0.2", "", False)))

        assert result.exit_code == 0, result.output
        assert "There are 2 undated versions" in result.output
        assert "any of them may be older" in result.output

    def test_reports_a_single_undated_version(self, run_sca):
        result = run_sca("-package", "pypi:x", listing=_listing(
            ("1.0", "2015-01-01T00:00:00Z", True), ("0.1", "", False)))

        assert result.exit_code == 0, result.output
        assert "There is 1 undated version" in result.output
        assert "it may be older" in result.output
        assert "any of them" not in result.output

    def test_maven_name_keeps_its_colon(self, run_sca):
        result = run_sca("-package", "maven:io.netty:netty-handler", listing=_listing(
            ("4.0.0.Final", "2013-07-15T14:07:50Z", True)))

        assert result.exit_code == 0, result.output
        assert run_sca.asked == [("maven", "io.netty:netty-handler")]

    def test_type_is_case_insensitive(self, run_sca):
        result = run_sca("-package", "PyPI:x", listing=_listing(
            ("1.0", "2015-01-01T00:00:00Z", True)))

        assert result.exit_code == 0, result.output
        assert run_sca.asked == [("pypi", "x")]

    def test_accepts_every_registry_package_type(self, run_sca):
        """TYPE comes from the extractor registry, not a list in the CLI."""
        from kospex.extractors.registry import package_types

        for package_type in package_types():
            result = run_sca("-package", f"{package_type}:x", listing=_listing(
                ("1.0.0", "2015-01-01T00:00:00Z", True)))
            assert result.exit_code == 0, (package_type, result.output)

    def test_unknown_package_exits_1(self, run_sca):
        result = run_sca("-package", "npm:no-such-package")

        assert result.exit_code == 1
        assert "no-such-package" in result.output

    @pytest.mark.parametrize("spec", ["requests", "pypi:", ":requests", "conda:numpy"])
    def test_malformed_or_unknown_type_is_a_usage_error(self, run_sca, spec):
        result = run_sca("-package", spec)

        assert result.exit_code == 2
        assert run_sca.asked == []

    def test_cannot_be_combined_with_a_file(self, run_sca, tmp_path):
        manifest = tmp_path / "requirements.txt"
        manifest.write_text("requests==2.0.0\n")

        result = run_sca("-package", "pypi:requests", str(manifest))

        assert result.exit_code == 2
        assert run_sca.asked == []

    @pytest.mark.parametrize("option", [["-save"], ["-out", "x.csv"], ["-scope", "all"],
                                        ["-json"]])
    def test_file_only_options_are_refused(self, run_sca, option):
        result = run_sca("-package", "pypi:requests", *option)

        assert result.exit_code == 2
        assert option[0] in result.output
        assert run_sca.asked == []

    def test_first_release_is_not_a_command(self, run_sca):
        import kospex_cli

        assert "first-release" not in kospex_cli.cli.commands
