"""`kospex advisory-history -package TYPE:NAME[@VERSION]`.

deps.dev (release_list) and OSV (OsvClient) are both faked; the pure logic has its own
tests in test_osv_history.py. Here: parsing, the flow, the table, -json, failures.
"""
import json

import pytest
from click.testing import CliRunner

from kospex.integrations.osv.client import OsvError
from kospex_dependencies import KospexDependencies


def rel(version, published="2022-01-01T00:00:00Z", pre=False, deprecated=False,
        default=False):
    return {"version": version, "published_at": published, "is_default": default,
            "is_prerelease": pre, "is_deprecated": deprecated, "deprecated_reason": ""}


class FakeOsv:
    """Stands in for OsvClient. `batch` maps an OSV-spelled version to its IDs, or None;
    `vulns` maps an ID to a record, or an Exception to raise."""

    batch = {}
    vulns = {}
    batch_error = None
    batch_calls = []
    vuln_calls = []
    init_kwargs = {}

    def __init__(self, *args, **kwargs):
        FakeOsv.init_kwargs = kwargs

    def querybatch(self, ecosystem, name, versions):
        FakeOsv.batch_calls.append((ecosystem, name, list(versions)))
        if FakeOsv.batch_error:
            raise FakeOsv.batch_error
        return {v: FakeOsv.batch.get(v, []) for v in versions}

    def vuln(self, osv_id):
        FakeOsv.vuln_calls.append(osv_id)
        found = FakeOsv.vulns.get(osv_id, {"id": osv_id})
        if isinstance(found, Exception):
            raise found
        return found


@pytest.fixture
def run(monkeypatch, tmp_path):
    from kospex.habitat_config import HabitatConfig

    home = tmp_path / "kospex_home"
    home.mkdir()
    monkeypatch.setenv("KOSPEX_HOME", str(home))
    monkeypatch.setenv("KOSPEX_DB", str(home / "kospex.db"))
    monkeypatch.delenv("KOSPEX_DEPS_PRIVATE", raising=False)
    monkeypatch.delenv("GOPRIVATE", raising=False)
    HabitatConfig.reset_instance()
    FakeOsv.batch, FakeOsv.vulns, FakeOsv.batch_error = {}, {}, None
    FakeOsv.batch_calls, FakeOsv.vuln_calls, FakeOsv.init_kwargs = [], [], {}

    def go(*args, releases=None, batch=None, vulns=None, batch_error=None):
        import kospex_cli

        FakeOsv.batch = batch or {}
        FakeOsv.vulns = vulns or {}
        FakeOsv.batch_error = batch_error
        asked = []

        def release_list(self, package_type, package_name, cache=None):
            asked.append((package_type, package_name))
            return releases

        monkeypatch.setattr(KospexDependencies, "release_list", release_list)
        monkeypatch.setattr(kospex_cli, "OsvClient", FakeOsv)
        result = CliRunner().invoke(kospex_cli.cli, ["advisory-history", *args])
        result.deps_asked = asked
        return result

    return go


DEMO = [rel("3.1.0b1", "2022-06-01T00:00:00Z", pre=True), rel("3.0.0", default=True),
        rel("2.1.0", "2021-06-01T00:00:00Z"), rel("2.0.0", "2021-01-01T00:00:00Z"),
        rel("1.0.0", "2020-01-01T00:00:00Z", deprecated=True)]


class TestUsage:
    def test_requires_package(self, run):
        result = run()
        assert result.exit_code == 2
        assert "-package" in result.output

    def test_package_spec_forms(self, run):
        a = run("-package", "pypi:demo@2.1.0", "-json", releases=DEMO)
        b = run("-ecosystem", "pypi", "-package", "demo@2.1.0", "-json", releases=DEMO)
        assert a.exit_code == b.exit_code == 0
        assert a.deps_asked == b.deps_asked == [("pypi", "demo")]
        for result in (a, b):                                # the window starts at the request
            data = json.loads(result.stdout)
            assert data["start"] == "2.1.0"
            assert [v["version"] for v in data["versions"]] == ["2.1.0", "2.0.0", "1.0.0"]

    def test_unknown_type_lists_the_known_ones(self, run):
        result = run("-package", "nope:demo")
        assert result.exit_code == 2
        assert "pypi" in result.output and "maven" in result.output

    def test_empty_version_after_at_is_an_error(self, run):
        result = run("-package", "pypi:demo@")
        assert result.exit_code == 2 and "empty version" in result.output

    def test_private_package_refused_without_network(self, run, monkeypatch):
        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "maven:com.example")
        result = run("-package", "maven:com.example:core", releases=DEMO)
        assert result.exit_code == 1
        assert "private" in result.output
        assert result.deps_asked == [] and FakeOsv.batch_calls == []

    def test_help_no_longer_says_redesigned(self):
        import kospex_cli
        result = CliRunner().invoke(kospex_cli.cli, ["--help"])
        line = next(l for l in result.output.splitlines() if "advisory-history" in l)
        assert "redesigned" not in line


class TestFlow:
    def test_no_deps_dev_record_exits_1_without_osv_call(self, run):
        result = run("-package", "pypi:demo", releases=None)
        assert result.exit_code == 1
        assert result.deps_asked == [("pypi", "demo")]
        assert FakeOsv.batch_calls == []

    def test_no_deps_dev_record_writes_nothing_to_stdout_in_json_mode(self, run):
        result = run("-package", "pypi:demo", "-json", releases=None)
        assert result.exit_code == 1
        assert result.stdout == ""
        assert "no record" in result.stderr

    def test_unknown_start_version_is_a_usage_error_naming_it(self, run):
        result = run("-package", "pypi:demo@9.9.9", releases=DEMO)
        assert result.exit_code == 2 and "9.9.9" in result.output
        assert FakeOsv.batch_calls == []

    @pytest.mark.parametrize("spec,ecosystem,name", [
        ("npm:@babel/core", "npm", "@babel/core"),
        ("maven:org.apache.logging.log4j:log4j-core", "Maven",
         "org.apache.logging.log4j:log4j-core"),
    ])
    def test_scoped_npm_and_maven_names_reach_osv_intact(self, run, spec, ecosystem, name):
        result = run("-package", spec, releases=[rel("1.0.0", default=True), rel("0.9.0")])
        assert result.exit_code == 0, result.output
        assert FakeOsv.batch_calls[0][:2] == (ecosystem if ecosystem != "npm" else "npm", name)
        assert "1.0.0" in result.output

    def test_go_versions_sent_without_v_and_mapped_back(self, run):
        releases = [rel("v1.8.0", default=True), rel("v1.7.0")]
        result = run("-package", "go:github.com/gorilla/mux", releases=releases,
                     batch={"1.8.0": ["GO-1"]}, vulns={"GO-1": {"id": "GO-1"}})
        assert result.exit_code == 0, result.output
        assert FakeOsv.batch_calls[0][2] == ["1.8.0", "1.7.0"]
        assert FakeOsv.batch_calls[0][0] == "Go"
        assert "v1.8.0" in result.output           # shown as deps.dev spells it

    def test_records_go_through_the_url_cache(self, run):
        run("-package", "pypi:demo", releases=DEMO, batch={"3.0.0": ["A"]})
        assert callable(FakeOsv.init_kwargs.get("cached_get"))

    def test_each_distinct_record_is_fetched_once(self, run):
        run("-package", "pypi:demo", releases=DEMO,
            batch={"3.0.0": ["A", "B"], "2.1.0": ["A"], "2.0.0": ["B"]})
        assert sorted(FakeOsv.vuln_calls) == ["A", "B"]

    def test_querybatch_failure_exits_1_with_one_line_on_stderr(self, run):
        result = run("-package", "pypi:demo", releases=DEMO,
                     batch_error=OsvError("POST /querybatch answered HTTP 503"))
        assert result.exit_code == 1
        assert "OSV did not answer" in result.stderr
        assert result.stdout == ""

    def test_one_failed_vuln_fetch_marks_only_citing_releases_unknown(self, run):
        result = run("-package", "pypi:demo", "-json", releases=DEMO,
                     batch={"3.0.0": ["A"], "2.1.0": ["B"], "2.0.0": []},
                     vulns={"A": OsvError("boom"), "B": {"id": "B"}})
        got = {v["version"]: v["advisory_count"] for v in json.loads(result.stdout)["versions"]}
        assert got == {"3.0.0": None, "2.1.0": 1, "2.0.0": 0, "1.0.0": 0}


class TestOutput:
    def test_table_summary_and_next_release(self, run):
        result = run("-package", "pypi:demo", releases=DEMO,
                     batch={"2.0.0": ["A"], "2.1.0": ["A"], "1.0.0": ["B"], "3.0.0": None})
        out = result.output
        assert result.exit_code == 0, out
        for expected in ("3.0.0", "2021-01-01", "current", "deprecated", "?"):
            assert expected in out
        assert "3.1.0b1" not in out
        assert "1 pre-release" in out                     # excluded, and counted
        assert "3 of 3 stable releases (100%) have a known advisory" in out
        assert "2.x  2/2" in out and "1.x  1/1" in out
        assert 'No newer "clean" release than 3.0.0' in out
        assert "safe" not in out.lower()
        assert "Clean means no known advisories" in out

    def test_next_release_found(self, run):
        result = run("-package", "pypi:demo@2.0.0", releases=DEMO,
                     batch={"2.0.0": ["A"], "2.1.0": ["A"]})
        assert 'Next "clean" release with no known advisories: 3.0.0' in result.output

    def test_next_release_says_how_many_affected_versions_come_before_it(self, run):
        result = run("-package", "pypi:demo@2.0.0", releases=DEMO,
                     batch={"2.0.0": ["A"], "2.1.0": ["A"]})
        assert "1 version with a known advisory between 2.0.0 and 3.0.0" in result.output
        assert "crosses to 3.x" in result.output
        same_major = run("-package", "pypi:demo@2.0.0", "-json", releases=DEMO,
                         batch={"2.0.0": ["A"]})
        data = json.loads(same_major.stdout)["next_clean"]
        assert data["version"] == "2.1.0" and data["skipped_affected"] == 0
        assert "crosses" not in run("-package", "pypi:demo@2.0.0", releases=DEMO,
                                    batch={"2.0.0": ["A"]}).output

    def test_failed_record_fetch_is_not_described_as_an_incomplete_osv_answer(self, run):
        result = run("-package", "pypi:demo", releases=DEMO, batch={"3.0.0": ["A"]},
                     vulns={"A": OsvError("boom")})
        assert "could not all be read" in result.output
        assert "incomplete" not in result.output

    def test_every_later_release_affected(self, run):
        result = run("-package", "pypi:demo@2.0.0", releases=DEMO,
                     batch={"2.0.0": ["A"], "2.1.0": ["A"], "3.0.0": ["A"]})
        assert 'No newer "clean" release than 2.0.0' in result.output
        assert "2 versions with a known advisory after 2.0.0" in result.output

    def test_third_party_text_is_not_markup(self, run):
        result = run("-package", "pypi:demo", releases=[rel("[red]x[/red]1.0", default=True)])
        assert "[red]x[/red]1.0" in result.output

    def test_pre_flag_includes_prereleases(self, run):
        releases = [rel("3.0.0", default=True), rel("2.5.0b1", pre=True), rel("2.0.0")]
        assert "2.5.0b1" not in run("-package", "pypi:demo", releases=releases).output
        assert "2.5.0b1" in run("-package", "pypi:demo", "-pre", releases=releases).output


class TestJson:
    def test_only_stdout_with_the_documented_keys(self, run):
        result = run("-package", "pypi:demo@2.1.0", "-json", releases=DEMO,
                     batch={"2.1.0": ["A"], "3.0.0": None},
                     vulns={"A": {"id": "A", "database_specific": {"severity": "HIGH"}}})
        data = json.loads(result.stdout)
        assert set(data) == {"package", "start", "limit", "include_prereleases",
                             "skipped_prereleases", "summary", "next_clean", "versions",
                             "sources"}
        assert data["package"] == {"type": "pypi", "name": "demo"}
        assert data["start"] == "2.1.0" and data["limit"] == 20
        assert data["skipped_prereleases"] == 1
        assert set(data["versions"][0]) == {"version", "published_at", "advisory_count",
                                            "advisory_ids", "worst_severity", "is_current",
                                            "is_deprecated", "is_prerelease"}
        by = {v["version"]: v for v in data["versions"]}
        assert by["2.1.0"]["advisory_ids"] == ["A"] and by["2.1.0"]["worst_severity"] == "HIGH"
        assert by["2.0.0"]["advisory_count"] == 0
        assert set(data["next_clean"]) == {"status", "version", "published_at",
                                           "skipped_unknown", "skipped_affected"}
        assert data["next_clean"]["skipped_unknown"] == ["3.0.0"]
        assert data["sources"]["osv"].endswith("/querybatch")
        assert "deps.dev" in data["sources"]["deps_dev"]


class TestOrder:
    def test_rows_follow_version_number_not_publish_date(self, run):
        # 2.25.5 is a backport published after 2.26.1, as log4j-core really did
        releases = [rel("2.25.5", "2026-07-01T00:00:00Z"),
                    rel("2.26.1", "2026-06-29T00:00:00Z", default=True),
                    rel("2.26.0", "2026-05-02T00:00:00Z")]
        result = run("-package", "pypi:demo", "-json", releases=releases)
        data = json.loads(result.stdout)
        assert [v["version"] for v in data["versions"]] == ["2.26.1", "2.26.0", "2.25.5"]


class TestAll:
    def test_all_lists_every_stable_release_newer_and_older_than_the_start(self, run):
        result = run("-package", "pypi:demo@2.0.0", "-all", "-json", releases=DEMO)
        data = json.loads(result.stdout)
        assert [v["version"] for v in data["versions"]] == ["3.0.0", "2.1.0", "2.0.0", "1.0.0"]
        assert data["limit"] is None
        assert [v["version"] for v in data["versions"] if v["is_current"]] == ["2.0.0"]

    def test_all_with_pre_includes_prereleases(self, run):
        result = run("-package", "pypi:demo@2.0.0", "-all", "-pre", "-json", releases=DEMO)
        assert json.loads(result.stdout)["versions"][0]["version"] == "3.1.0b1"

    def test_all_ignores_the_default_limit(self, run):
        releases = [rel(f"1.{i}.0", default=(i == 29)) for i in range(30)]
        result = run("-package", "pypi:demo", "-all", "-json", releases=releases)
        assert len(json.loads(result.stdout)["versions"]) == 30

    def test_all_with_an_explicit_limit_is_a_usage_error(self, run):
        result = run("-package", "pypi:demo", "-all", "-limit", "5", releases=DEMO)
        assert result.exit_code == 2 and "-all" in result.output
        assert FakeOsv.batch_calls == []

    def test_table_marks_the_next_release_without_known_advisories(self, run):
        result = run("-package", "pypi:demo@2.0.0", "-all", releases=DEMO,
                     batch={"2.0.0": ["A"], "2.1.0": ["A"]})
        line = next(l for l in result.output.splitlines() if "3.0.0" in l and "│" in l)
        assert "next clean" in line
        other = next(l for l in result.output.splitlines() if "2.1.0" in l and "│" in l)
        assert "next clean" not in other
