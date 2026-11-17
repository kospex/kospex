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
from datetime import datetime, timezone
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

    def run(*args, listing=None, version_record=None, advisories=None):
        """`version_record` is deps.dev's answer for any exact version (None:
        404), and `advisories` maps advisory id -> deps.dev advisory record."""
        import kospex_cli

        real_release_list = KospexDependencies.release_list
        run.asked = []
        run.versions_asked = []

        def release_list(self, package_type, package_name, **kwargs):
            run.asked.append((package_type, package_name))
            run.kwargs = kwargs
            if listing is None:
                return None
            return real_release_list(_kd(package_type, package_name, listing),
                                     package_type, package_name, **kwargs)

        def deps_dev_status(self, package_type, package_name, version, cache=None):
            run.versions_asked.append((package_type, package_name, version, cache))
            return (version_record, 200) if version_record else (None, 404)

        def advisory(self, advisory_id):
            return (advisories or {}).get(advisory_id)

        monkeypatch.setattr(KospexDependencies, "release_list", release_list)
        monkeypatch.setattr(KospexDependencies, "deps_dev_status", deps_dev_status)
        monkeypatch.setattr(KospexDependencies, "advisory", advisory)
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


# ---------------------------------------------------------------------------
# release_summary(): first and latest releases, ages and counts.
# ---------------------------------------------------------------------------

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def _versions(*versions):
    """A deps.dev package record from dicts: version, published, default, deprecated."""
    return json.dumps({"versions": [
        {"versionKey": {"version": v["version"]},
         "publishedAt": v.get("published", ""),
         "isDefault": v.get("default", False),
         "isDeprecated": v.get("deprecated", False),
         "deprecatedReason": v.get("reason", "")}
        for v in versions]})


class TestReleaseSummary:

    def test_none_when_deps_dev_has_no_record(self):
        kd = KospexDependencies(kospex_query=_Query({}))
        assert kd.release_summary("npm", "left-pad", now=NOW) is None

    def test_latest_is_the_deps_dev_default(self):
        kd = _kd("pypi", "x", _versions(
            {"version": "0.1", "published": "2024-07-13T00:00:00Z"},
            {"version": "0.4.0", "published": "2026-11-16T00:00:00Z", "default": True}))
        summary = kd.release_summary("pypi", "x", now=NOW)

        assert summary["latest"]["version"] == "0.4.0"
        assert summary["latest"]["published_at"] == "2026-11-16T00:00:00Z"
        assert summary["first"]["version"] == "0.1"

    def test_a_backport_published_later_is_not_latest(self):
        """1.2.9 shipped after 2.0.0; the registry's default is still 2.0.0."""
        kd = _kd("pypi", "x", _versions(
            {"version": "2.0.0", "published": "2025-01-01T00:00:00Z", "default": True},
            {"version": "1.2.9", "published": "2025-06-01T00:00:00Z"}))

        assert kd.release_summary("pypi", "x", now=NOW)["latest"]["version"] == "2.0.0"

    def test_a_pre_release_default_falls_back_to_the_newest_release(self):
        """As versions_behind does: duckdb's default was once a nightly."""
        kd = _kd("npm", "x", _versions(
            {"version": "1.0.0", "published": "2025-01-01T00:00:00Z"},
            {"version": "1.1.0-dev.3", "published": "2025-02-01T00:00:00Z", "default": True}))
        summary = kd.release_summary("npm", "x", now=NOW)

        assert summary["latest"]["version"] == "1.0.0"
        assert summary["latest_any"]["version"] == "1.1.0-dev.3"

    def test_a_pre_release_default_falls_back_to_the_highest_release_not_the_newest(self):
        """netty publishes 4.2.x and 4.1.x together, and deps.dev's default is
        a 2015 alpha. 4.1.141 went out after 4.2.21; the latest is 4.2.21."""
        kd = _kd("maven", "io.netty:netty-handler", _versions(
            {"version": "5.0.0.Alpha2", "published": "2015-03-04T00:00:00Z", "default": True},
            {"version": "4.2.21.Final", "published": "2026-11-16T17:39:49Z"},
            {"version": "4.1.141.Final", "published": "2026-11-16T18:24:21Z"}))
        summary = kd.release_summary("maven", "io.netty:netty-handler", now=NOW)

        assert summary["latest"]["version"] == "4.2.21.Final"

    def test_latest_any_is_a_newer_pre_release_only(self):
        kd = _kd("pypi", "x", _versions(
            {"version": "4.0", "published": "2025-01-01T00:00:00Z", "default": True},
            {"version": "5.0rc1", "published": "2026-10-01T00:00:00Z"},
            {"version": "4.0b1", "published": "2024-06-01T00:00:00Z"}))
        summary = kd.release_summary("pypi", "x", now=NOW)

        assert summary["latest_any"]["version"] == "5.0rc1"
        assert summary["first_any"]["version"] == "4.0b1"
        assert summary["first"]["version"] == "4.0"

    def test_an_older_pre_release_is_not_latest_any(self):
        kd = _kd("pypi", "x", _versions(
            {"version": "4.0b1", "published": "2024-06-01T00:00:00Z"},
            {"version": "4.0", "published": "2025-01-01T00:00:00Z", "default": True}))

        assert kd.release_summary("pypi", "x", now=NOW)["latest_any"] is None

    def test_ages_are_whole_days_before_now(self):
        kd = _kd("pypi", "x", _versions(
            {"version": "0.1", "published": "2024-07-13T00:00:00Z"},
            {"version": "0.4.0", "published": "2026-11-16T00:00:00Z", "default": True}))
        summary = kd.release_summary("pypi", "x", now=NOW)

        assert summary["latest"]["age_days"] == 1
        assert summary["first"]["age_days"] == 857

    def test_releases_in_the_last_12_months_exclude_pre_releases(self):
        kd = _kd("pypi", "x", _versions(
            {"version": "1.0", "published": "2025-11-01T00:00:00Z"},   # 381 days
            {"version": "1.1", "published": "2025-11-18T00:00:00Z"},   # 364 days
            {"version": "1.2rc1", "published": "2026-03-01T00:00:00Z"},
            {"version": "1.2", "published": "2026-04-01T00:00:00Z", "default": True},
            {"version": "0.9", "published": ""}))

        assert kd.release_summary("pypi", "x", now=NOW)["releases_last_12_months"] == 2

    def test_deprecated_versions_are_counted_and_a_deprecated_latest_is_flagged(self):
        kd = _kd("npm", "x", _versions(
            {"version": "1.0.0", "published": "2020-01-01T00:00:00Z", "deprecated": True,
             "reason": "use 2.x"},
            {"version": "2.0.0", "published": "2021-01-01T00:00:00Z", "default": True,
             "deprecated": True, "reason": "package moved to @acme/x"}))
        summary = kd.release_summary("npm", "x", now=NOW)

        assert summary["deprecated"] == 2
        assert summary["latest"]["is_deprecated"] is True
        assert summary["latest"]["deprecated_reason"] == "package moved to @acme/x"

    def test_counts(self):
        kd = _kd("pypi", "x", _versions(
            {"version": "1.0", "published": "2025-01-01T00:00:00Z", "default": True},
            {"version": "1.1rc1", "published": "2025-02-01T00:00:00Z"},
            {"version": "0.1", "published": ""}))
        summary = kd.release_summary("pypi", "x", now=NOW)

        assert (summary["versions"], summary["prereleases"], summary["undated"]) == (3, 1, 1)

    def test_fresh_bypasses_the_url_cache(self):
        seen = {}

        class Query(_Query):
            def url_request_with_status(self, url, **kwargs):
                seen.update(kwargs)
                return super().url_request_with_status(url)

        url = f"{BASE}/pypi/packages/x"
        kd = KospexDependencies(kospex_query=Query({url: (_versions(
            {"version": "1.0", "published": "2025-01-01T00:00:00Z", "default": True}), 200)}))
        kd.release_summary("pypi", "x", fresh=True, now=NOW)

        assert seen["cache"] == 0


class TestAgeLabel:

    @pytest.mark.parametrize("days, label", [
        (0, "<1d"), (1, "1d"), (59, "59d"), (60, "2m"), (334, "11m"), (364, "1y"),
        (400, "1y 1m"), (857, "2y 4m"), (3650, "10y"), (None, "")])
    def test_label(self, days, label):
        import kospex_utils
        assert kospex_utils.age_label(days) == label


class TestScaPackageReleases:
    """`kospex sca -package`: the Releases table."""

    def test_title_is_releases_and_shows_first_and_latest(self, run_sca):
        result = run_sca("-package", "pypi:x", listing=_listing(
            ("0.0.1", "2024-07-13T04:26:52Z", False),
            ("0.4.0", "2026-11-16T13:39:23Z", True)))

        assert result.exit_code == 0, result.output
        assert "Releases: pypi x" in result.output
        assert "First" in result.output and "0.0.1" in result.output
        assert "Latest" in result.output and "0.4.0" in result.output
        assert "Age" in result.output

    def test_any_rows_are_hidden_when_they_repeat(self, run_sca):
        result = run_sca("-package", "pypi:x", listing=_listing(
            ("0.0.1", "2024-07-13T04:26:52Z", False),
            ("0.4.0", "2026-11-16T13:39:23Z", True)))

        assert result.exit_code == 0, result.output
        assert "(any)" not in result.output

    def test_any_rows_shown_when_a_pre_release_differs(self, run_sca):
        result = run_sca("-package", "npm:x", listing=_listing(
            ("0.1.0-alpha.1", "2010-01-01T00:00:00Z", False),
            ("1.0.0", "2011-01-01T00:00:00Z", True),
            ("2.0.0-rc.1", "2012-01-01T00:00:00Z", False)))

        assert result.exit_code == 0, result.output
        assert "First (any)" in result.output and "0.1.0-alpha.1" in result.output
        assert "Latest (any)" in result.output and "2.0.0-rc.1" in result.output

    def test_summary_line(self, run_sca):
        result = run_sca("-package", "pypi:x", listing=_listing(
            ("1.0", "2015-01-01T00:00:00Z", True), ("1.1rc1", "2016-01-01T00:00:00Z", False)))

        assert result.exit_code == 0, result.output
        assert "2 versions, 1 pre-release, 0 deprecated" in result.output
        assert "releases in the last 12 months" in result.output

    def test_fetches_fresh(self, run_sca):
        run_sca("-package", "pypi:x", listing=_listing(("1.0", "2015-01-01T00:00:00Z", True)))

        assert run_sca.kwargs.get("cache") == 0


# ---------------------------------------------------------------------------
# -package TYPE:NAME@VERSION: one version placed among the releases.
# ---------------------------------------------------------------------------

ADVISORY_URL = "https://api.deps.dev/v3alpha/advisories"

THREE_RELEASES = _versions(
    {"version": "1.0.0", "published": "2020-01-01T00:00:00Z"},
    {"version": "1.1.0", "published": "2021-01-01T00:00:00Z"},
    {"version": "1.2.0", "published": "2022-01-01T00:00:00Z", "default": True})


def _version_record(version, published="2020-01-01T00:00:00Z", advisories=(), **extra):
    return {"versionKey": {"version": version}, "publishedAt": published,
            "advisoryKeys": [{"id": a} for a in advisories], **extra}


def _advisory(advisory_id, aliases=(), title="", score=0, vector=""):
    return {"advisoryKey": {"id": advisory_id}, "aliases": list(aliases),
            "title": title, "cvss3Score": score, "cvss3Vector": vector,
            "url": f"https://osv.dev/vulnerability/{advisory_id}"}


VECTOR = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"


class _RecordingQuery(_Query):
    def __init__(self, responses):
        super().__init__(responses)
        self.calls = []

    def url_request_with_status(self, url, **kwargs):
        self.calls.append((url, kwargs.get("cache")))
        return super().url_request_with_status(url, **kwargs)


def _kd_with_version(system, name, listing, version, record, advisories=None,
                     query_class=_Query):
    package = f"{BASE}/{system}/packages/{quote(name, safe='')}"
    responses = {package: (listing, 200)}
    if record is not None:
        responses[f"{package}/versions/{version}"] = (json.dumps(record), 200)
    for advisory_id, advisory in (advisories or {}).items():
        responses[f"{ADVISORY_URL}/{advisory_id}"] = (json.dumps(advisory), 200)
    return KospexDependencies(kospex_query=query_class(responses))


class TestVersionDetail:

    def test_counts_releases_behind_from_the_same_listing(self):
        kd = _kd_with_version("pypi", "x", THREE_RELEASES, "1.0.0",
                              _version_record("1.0.0"))
        summary = kd.release_summary("pypi", "x", now=NOW, version="1.0.0")
        requested = summary["requested"]

        assert requested["version"] == "1.0.0"
        assert requested["versions_behind"] == 2
        assert requested["latest_version"] == "1.2.0"
        assert requested["published_at"] == "2020-01-01T00:00:00Z"
        assert requested["age_days"] == (NOW - datetime(2020, 1, 1, tzinfo=timezone.utc)).days

    def test_the_latest_release_is_zero_behind(self):
        kd = _kd_with_version("pypi", "x", THREE_RELEASES, "1.2.0",
                              _version_record("1.2.0", "2022-01-01T00:00:00Z"))

        assert kd.release_summary("pypi", "x", now=NOW,
                                  version="1.2.0")["requested"]["versions_behind"] == 0

    def test_deps_dev_spelling_is_used_and_the_request_kept(self):
        """pypi `1.0` is deps.dev's `1.0.0`; the count needs the canonical form."""
        kd = _kd_with_version("pypi", "x", THREE_RELEASES, "1.0",
                              _version_record("1.0.0"))
        requested = kd.release_summary("pypi", "x", now=NOW, version="1.0")["requested"]

        assert requested["version"] == "1.0.0"
        assert requested["requested_as"] == "1.0"
        assert requested["versions_behind"] == 2

    def test_a_pre_release_has_no_count(self):
        listing = _versions(
            {"version": "1.0.0", "published": "2020-01-01T00:00:00Z", "default": True},
            {"version": "2.0.0-rc.1", "published": "2021-01-01T00:00:00Z"})
        kd = _kd_with_version("npm", "x", listing, "2.0.0-rc.1",
                              _version_record("2.0.0-rc.1", "2021-01-01T00:00:00Z"))
        requested = kd.release_summary("npm", "x", now=NOW,
                                       version="2.0.0-rc.1")["requested"]

        assert requested["is_prerelease"] is True
        assert requested["versions_behind"] is None

    def test_an_unknown_version_is_none_with_its_status(self):
        kd = _kd_with_version("pypi", "x", THREE_RELEASES, "9.9.9", None)
        summary = kd.release_summary("pypi", "x", now=NOW, version="9.9.9")

        assert summary["requested"] is None
        assert summary["requested_status"] == 404
        assert summary["latest"]["version"] == "1.2.0"

    def test_no_version_no_requested_key(self):
        kd = _kd("pypi", "x", THREE_RELEASES)

        assert "requested" not in kd.release_summary("pypi", "x", now=NOW)

    def test_fresh_fetches_the_version_too(self):
        kd = _kd_with_version("pypi", "x", THREE_RELEASES, "1.0.0",
                              _version_record("1.0.0"), query_class=_RecordingQuery)
        kd.release_summary("pypi", "x", now=NOW, version="1.0.0", fresh=True)

        version_calls = [c for u, c in kd.kospex_query.calls if "/versions/" in u]
        assert version_calls == [0]

    def test_aliases_of_one_vulnerability_are_merged(self):
        """requests 2.25.0 lists GHSA-9hjg-9r4m-mvj7 and PYSEC-2026-1872, one bug."""
        record = _version_record("1.0.0", advisories=("GHSA-a", "GHSA-b", "PYSEC-a"))
        advisories = {
            "GHSA-a": _advisory("GHSA-a", ("CVE-2024-1", "PYSEC-a"), "Leak", 5.3, VECTOR),
            "GHSA-b": _advisory("GHSA-b", ("CVE-2024-2",), "Bypass", 7.5, VECTOR),
            "PYSEC-a": _advisory("PYSEC-a", ("CVE-2024-1", "GHSA-a")),
        }
        kd = _kd_with_version("pypi", "x", THREE_RELEASES, "1.0.0", record, advisories)
        requested = kd.release_summary("pypi", "x", now=NOW, version="1.0.0")["requested"]

        assert requested["advisory_ids"] == 3
        assert [(a["id"], a["also"], a["cves"]) for a in requested["advisories"]] == [
            ("GHSA-a", ["PYSEC-a"], ["CVE-2024-1"]),
            ("GHSA-b", [], ["CVE-2024-2"]),
        ]
        assert requested["advisories"][0]["cvss3_score"] == 5.3
        assert requested["advisories"][0]["title"] == "Leak"

    def test_a_zero_score_with_no_vector_is_no_score(self):
        """deps.dev sends cvss3Score 0 for an advisory scored only under CVSS 4."""
        record = _version_record("1.0.0", advisories=("GHSA-a",))
        advisories = {"GHSA-a": _advisory("GHSA-a", score=0, vector="")}
        kd = _kd_with_version("pypi", "x", THREE_RELEASES, "1.0.0", record, advisories)
        requested = kd.release_summary("pypi", "x", now=NOW, version="1.0.0")["requested"]

        assert requested["advisories"][0]["cvss3_score"] is None

    def test_a_failed_advisory_lookup_keeps_the_id(self):
        record = _version_record("1.0.0", advisories=("GHSA-a",))
        kd = _kd_with_version("pypi", "x", THREE_RELEASES, "1.0.0", record)
        requested = kd.release_summary("pypi", "x", now=NOW, version="1.0.0")["requested"]

        assert [a["id"] for a in requested["advisories"]] == ["GHSA-a"]
        assert requested["advisories"][0]["cvss3_score"] is None


class TestSplitPackageVersion:

    @pytest.mark.parametrize("name, expected", [
        ("requests", ("requests", None)),
        ("requests@2.25.0", ("requests", "2.25.0")),
        ("@babel/core", ("@babel/core", None)),
        ("@babel/core@7.0.0", ("@babel/core", "7.0.0")),
        ("io.netty:netty-handler@4.1.100.Final", ("io.netty:netty-handler", "4.1.100.Final")),
        ("github.com/acme/mod@v1.2.3", ("github.com/acme/mod", "v1.2.3")),
        ("requests@", ("requests", "")),
    ])
    def test_split(self, name, expected):
        from kospex_cli import _split_package_version

        assert _split_package_version(name) == expected


class TestScaPackageVersion:
    """`kospex sca -package TYPE:NAME@VERSION`."""

    def test_the_version_gets_its_own_row_between_first_and_latest(self, run_sca):
        result = run_sca("-package", "pypi:x@1.1.0", listing=THREE_RELEASES,
                         version_record=_version_record("1.1.0", "2021-01-01T00:00:00Z"))

        assert result.exit_code == 0, result.output
        assert run_sca.asked == [("pypi", "x")]
        assert run_sca.versions_asked == [("pypi", "x", "1.1.0", 0)]
        lines = result.output.splitlines()
        first = next(i for i, l in enumerate(lines) if "First" in l)
        requested = next(i for i, l in enumerate(lines) if "Requested" in l)
        latest = next(i for i, l in enumerate(lines) if "Latest" in l)
        assert first < requested < latest
        assert "1.1.0" in lines[requested]
        assert "1.1.0 is 1 release behind the latest (1.2.0)." in result.output

    def test_the_latest_row_is_flagged_instead_of_repeated(self, run_sca):
        result = run_sca("-package", "pypi:x@1.2.0", listing=THREE_RELEASES,
                         version_record=_version_record("1.2.0", "2022-01-01T00:00:00Z"))

        assert result.exit_code == 0, result.output
        assert "Latest (requested)" in result.output
        assert "Requested " not in result.output
        assert "1.2.0 is the latest release." in result.output

    def test_the_first_row_is_flagged_instead_of_repeated(self, run_sca):
        result = run_sca("-package", "pypi:x@1.0.0", listing=THREE_RELEASES,
                         version_record=_version_record("1.0.0"))

        assert result.exit_code == 0, result.output
        assert "First (requested)" in result.output
        assert "1.0.0 is 2 releases behind the latest (1.2.0)." in result.output

    def test_a_pre_release_newer_than_latest_goes_after_it(self, run_sca):
        listing = _versions(
            {"version": "1.0.0", "published": "2020-01-01T00:00:00Z", "default": True},
            {"version": "2.0.0-rc.1", "published": "2021-01-01T00:00:00Z"},
            {"version": "2.0.0-rc.2", "published": "2022-01-01T00:00:00Z"})
        result = run_sca("-package", "npm:x@2.0.0-rc.1", listing=listing,
                         version_record=_version_record("2.0.0-rc.1",
                                                        "2021-01-01T00:00:00Z"))

        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        latest = next(i for i, l in enumerate(lines) if "Latest " in l and "(any)" not in l)
        requested = next(i for i, l in enumerate(lines) if "Requested" in l)
        latest_any = next(i for i, l in enumerate(lines) if "Latest (any)" in l)
        assert latest < requested < latest_any
        assert "is a pre-release, so releases behind is not counted" in result.output

    def test_advisories_are_listed_under_the_table(self, run_sca):
        record = _version_record("1.0.0", advisories=("GHSA-a", "PYSEC-a", "GHSA-b"))
        advisories = {
            "GHSA-a": _advisory("GHSA-a", ("CVE-2024-1", "PYSEC-a"), "Leak", 5.3, VECTOR),
            "GHSA-b": _advisory("GHSA-b", ("CVE-2024-2",), "Bypass", 0, ""),
        }
        result = run_sca("-package", "pypi:x@1.0.0", listing=THREE_RELEASES,
                         version_record=record, advisories=advisories)

        assert result.exit_code == 0, result.output
        out = result.output
        assert "2 known advisories for 1.0.0 (deps.dev lists 3 IDs" in out
        assert "Advisories: pypi x 1.0.0" in out
        assert out.index("Releases: pypi x") < out.index("Advisories: pypi x 1.0.0")
        for text in ("GHSA-a", "PYSEC-a", "CVE-2024-1", "5.3", "Leak", "GHSA-b", "Bypass"):
            assert text in out
        unscored = next(l for l in out.splitlines() if "GHSA-b" in l)
        assert "0.0" not in unscored and " - " in unscored
        assert "no CVSS 3 score" in out

    def test_no_advisories_says_so(self, run_sca):
        result = run_sca("-package", "pypi:x@1.0.0", listing=THREE_RELEASES,
                         version_record=_version_record("1.0.0"))

        assert result.exit_code == 0, result.output
        assert "deps.dev lists no known advisories for 1.0.0." in result.output
        assert "Advisories:" not in result.output

    def test_a_canonicalised_version_says_so(self, run_sca):
        result = run_sca("-package", "pypi:x@1.0", listing=THREE_RELEASES,
                         version_record=_version_record("1.0.0"))

        assert result.exit_code == 0, result.output
        assert "deps.dev lists 1.0 as 1.0.0." in result.output

    def test_a_deprecated_version_says_why(self, run_sca):
        result = run_sca("-package", "npm:x@1.1.0", listing=THREE_RELEASES,
                         version_record=_version_record(
                             "1.1.0", "2021-01-01T00:00:00Z", isDeprecated=True,
                             deprecatedReason="use 1.2"))

        assert result.exit_code == 0, result.output
        assert "1.1.0 is deprecated: use 1.2" in result.output

    def test_an_unknown_version_fails(self, run_sca):
        result = run_sca("-package", "pypi:x@9.9.9", listing=THREE_RELEASES)

        assert result.exit_code == 1
        assert "deps.dev has no version 9.9.9 of x (pypi)" in result.output
        assert "Releases:" not in result.output

    def test_an_empty_version_is_a_usage_error(self, run_sca):
        result = run_sca("-package", "pypi:x@", listing=THREE_RELEASES)

        assert result.exit_code == 2
        assert "empty version" in result.output
        assert run_sca.asked == []

    def test_an_npm_scope_is_not_a_version(self, run_sca):
        result = run_sca("-package", "npm:@acme/x", listing=THREE_RELEASES)

        assert result.exit_code == 0, result.output
        assert run_sca.asked == [("npm", "@acme/x")]
        assert run_sca.versions_asked == []

    def test_a_scoped_npm_name_with_a_version(self, run_sca):
        result = run_sca("-package", "npm:@acme/x@1.1.0", listing=THREE_RELEASES,
                         version_record=_version_record("1.1.0", "2021-01-01T00:00:00Z"))

        assert result.exit_code == 0, result.output
        assert run_sca.versions_asked == [("npm", "@acme/x", "1.1.0", 0)]
