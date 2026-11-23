"""`kospex sca -package TYPE:NAME@VERSION -osv`: the OSV block under deps.dev's output.

deps.dev and OSV are both faked. The OSV records are real (tests/data/osv/).
"""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from kospex.integrations.osv.client import OsvError
from kospex_dependencies import KospexDependencies

DATA = Path(__file__).parent / "data" / "osv"


def records(name):
    with open(DATA / f"{name}.json") as handle:
        return json.load(handle)["response"]["vulns"]


def entry(version):
    return {"version": version, "published_at": "2020-01-01T00:00:00Z", "age_days": 2000,
            "is_deprecated": False, "deprecated_reason": "", "is_prerelease": False}


def requested(version, advisories=()):
    return {**entry(version), "requested_as": version, "versions_behind": 3,
            "latest_version": "9.9.9", "behind": {}, "advisories": list(advisories),
            "advisory_ids": len(advisories)}


def deps_advisory(advisory_id, also=(), cves=()):
    return {"id": advisory_id, "also": list(also), "cves": list(cves),
            "title": f"title of {advisory_id}", "cvss3_score": 5.3, "url": ""}


class FakeOsv:
    """Stands in for OsvClient. `answer` is the list of records, or an OsvError to raise."""

    asked = []
    answer = []

    def __init__(self, *args, **kwargs):
        pass

    def query(self, ecosystem, name, version):
        FakeOsv.asked.append((ecosystem, name, version))
        if isinstance(FakeOsv.answer, Exception):
            raise FakeOsv.answer
        return FakeOsv.answer


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
    FakeOsv.asked = []
    FakeOsv.answer = []

    def go(*args, osv_records=(), version="2.25.0", deps=(), summary=True):
        import kospex_cli

        FakeOsv.answer = osv_records
        asked_deps = []

        def release_summary(self, package_type, package_name, fresh=False, version=None):
            asked_deps.append((package_type, package_name, version))
            if not summary:
                return None
            return {"first": entry("0.0.1"), "latest": entry("9.9.9"),
                    "first_any": entry("0.0.1"), "latest_any": None,
                    "versions": 10, "prereleases": 0, "deprecated": 0,
                    "releases_last_12_months": 1, "undated": 0,
                    "requested": requested(version, deps) if version else None}

        monkeypatch.setattr(KospexDependencies, "release_summary", release_summary)
        monkeypatch.setattr(kospex_cli, "OsvClient", FakeOsv)
        result = CliRunner().invoke(kospex_cli.cli, ["sca", *args])
        result.deps_asked = asked_deps
        return result

    return go


REQUESTS_DEPS = [
    deps_advisory("GHSA-9hjg-9r4m-mvj7", also=["PYSEC-2026-1872"], cves=["CVE-2024-47081"]),
    deps_advisory("GHSA-9wx4-h78v-vm56", also=["PYSEC-2026-1873"], cves=["CVE-2024-35195"]),
    deps_advisory("GHSA-gc5v-m9x4-r6x2", also=["PYSEC-2026-2275"], cves=["CVE-2026-25645"]),
    deps_advisory("GHSA-j8r2-6x86-q33q", also=["PYSEC-2023-74"], cves=["CVE-2023-32681"]),
]


class TestUsage:
    def test_osv_without_package_is_a_usage_error(self, run):
        result = run("-osv")
        assert result.exit_code == 2
        assert "-osv only applies to -package" in result.output
        assert FakeOsv.asked == []

    def test_osv_without_a_version_is_a_usage_error_before_anything_is_asked(self, run):
        result = run("-package", "pypi:requests", "-osv")

        assert result.exit_code == 2
        assert "-osv needs a version" in result.output
        assert result.deps_asked == []
        assert FakeOsv.asked == []

    def test_an_empty_version_is_still_the_existing_error(self, run):
        result = run("-package", "pypi:requests@", "-osv")
        assert result.exit_code == 2
        assert "empty version" in result.output

    def test_without_the_switch_osv_is_never_asked(self, run):
        result = run("-package", "pypi:requests@2.25.0", osv_records=records("pypi_requests_2.25.0"))
        assert result.exit_code == 0
        assert FakeOsv.asked == []
        assert "OSV" not in result.output


class TestOutput:
    def test_the_osv_block_follows_deps_dev_and_asks_with_the_canonical_version(self, run):
        result = run("-package", "pypi:requests@2.25.0", "-osv", deps=REQUESTS_DEPS,
                     osv_records=records("pypi_requests_2.25.0"))

        assert result.exit_code == 0, result.output
        assert FakeOsv.asked == [("PyPI", "requests", "2.25.0")]
        out = result.output
        assert out.index("Releases: pypi requests") < out.index("Advisories: pypi requests")
        assert out.index("Advisories: pypi requests") < out.index("OSV advisories: pypi requests")
        assert "OSV: 4 advisories for pypi requests 2.25.0." in out
        assert "2.32.4" in out          # the fixed version, which deps.dev's table lacks
        assert "MEDIUM 5.3 (CVSS 3.1)" in out
        assert "Compared with deps.dev: 4 in both, 0 only in OSV, 0 only in deps.dev." in out

    def test_a_comparison_is_flagged_when_deps_dev_details_could_not_be_read(self, run):
        # advisories() fills in only id and `also` when its lookup fails.
        bare = [{"id": "GHSA-9hjg-9r4m-mvj7", "also": ["PYSEC-2026-1872"], "cves": [],
                 "title": "", "cvss3_score": None, "url": ""}]
        degraded = run("-package", "pypi:requests@2.25.0", "-osv", deps=bare,
                       osv_records=records("pypi_requests_2.25.0"))
        healthy = run("-package", "pypi:requests@2.25.0", "-osv", deps=REQUESTS_DEPS,
                      osv_records=records("pypi_requests_2.25.0"))

        assert "may overstate the differences" in " ".join(degraded.output.split())
        assert "may overstate" not in healthy.output

    def test_a_difference_is_named(self, run):
        result = run("-package", "pypi:requests@2.25.0", "-osv", deps=REQUESTS_DEPS[:3],
                     osv_records=records("pypi_requests_2.25.0"))

        assert "3 in both, 1 only in OSV, 0 only in deps.dev." in result.output
        assert "only in OSV: GHSA-j8r2-6x86-q33q" in result.output

    def test_a_v4_only_advisory_shows_the_label_and_a_note_when_unscored(self, run):
        recs = records("maven_log4j_core_2.14.1")
        result = run("-package", "maven:org.apache.logging.log4j:log4j-core@2.14.1", "-osv",
                     version="2.14.1", osv_records=recs)

        assert "MEDIUM (GitHub)" in result.output
        assert "CRITICAL 10.0 (CVSS 3.1)" in result.output
        assert "UNSCORED" not in result.output   # every v4-only record here has a label

    def test_unscored_is_explained(self, run):
        rec = json.loads(json.dumps(records("maven_log4j_core_2.14.1")[0]))
        rec["database_specific"].pop("severity", None)
        result = run("-package", "maven:org.apache.logging.log4j:log4j-core@2.14.1", "-osv",
                     version="2.14.1", osv_records=[rec])

        assert "UNSCORED (CVSS 4)" in result.output
        # The console wraps long lines, so compare with the whitespace collapsed.
        assert "kospex does not calculate" in " ".join(result.output.split())

    def test_a_withdrawn_advisory_is_shown_marked_and_not_counted(self, run):
        recs = records("pypi_requests_2.25.0")
        withdrawn = json.loads(json.dumps(recs[0]))
        withdrawn["id"], withdrawn["aliases"] = "GHSA-hand-built", []
        withdrawn["withdrawn"] = "2025-01-02T00:00:00Z"
        result = run("-package", "pypi:requests@2.25.0", "-osv", osv_records=recs + [withdrawn])

        assert "OSV: 4 advisories for pypi requests 2.25.0, and 1 withdrawn." in result.output
        assert "GHSA-hand-built" in result.output
        assert "(withdrawn 2025-01-02)" in result.output

    def test_no_advisories(self, run):
        result = run("-package", "pypi:requests@2.25.0", "-osv", osv_records=[])

        assert "OSV: 0 advisories for pypi requests 2.25.0." in result.output
        assert "OSV advisories:" not in result.output     # no empty table

    def test_several_fixed_versions_are_flagged_when_the_line_is_unknown(self, run):
        recs = records("maven_log4j_core_2.14.1")
        result = run("-package", "maven:org.apache.logging.log4j:log4j-core@9.9.9", "-osv",
                     version="9.9.9", osv_records=recs)

        assert "One fixed version per release line" in " ".join(result.output.split())

    def test_escape_sequences_in_osv_text_are_removed(self, run):
        rec = json.loads(json.dumps(records("pypi_requests_2.25.0")[0]))
        rec["summary"] = "bad\x1b]0;owned\x07 title"
        result = run("-package", "pypi:requests@2.25.0", "-osv", osv_records=[rec])

        assert "\x1b" not in result.output
        assert "\x07" not in result.output

    def test_rich_markup_in_osv_text_is_not_interpreted(self, run):
        rec = json.loads(json.dumps(records("pypi_requests_2.25.0")[0]))
        rec["summary"] = "see [bold red]this[/bold red]"
        result = run("-package", "pypi:requests@2.25.0", "-osv", osv_records=[rec])

        assert "[bold red]this[/bold red]" in result.output


class TestFailures:
    def test_osv_failing_leaves_deps_dev_output_and_the_exit_code(self, run):
        result = run("-package", "pypi:requests@2.25.0", "-osv", deps=REQUESTS_DEPS,
                     osv_records=OsvError("POST /query answered HTTP 503", status=503))

        assert result.exit_code == 0
        assert "Releases: pypi requests" in result.output
        assert "Advisories: pypi requests" in result.output
        assert "OSV did not answer: POST /query answered HTTP 503." in result.output
        assert "Compared with deps.dev" not in result.output

    def test_deps_dev_failing_means_osv_is_not_asked(self, run):
        result = run("-package", "pypi:no-such@1.0", "-osv", summary=False)

        assert result.exit_code == 1
        assert FakeOsv.asked == []

    def test_a_type_osv_has_no_ecosystem_for(self, run, monkeypatch):
        from kospex.integrations.osv import queries

        monkeypatch.setattr(queries, "ECOSYSTEMS", {})
        result = run("-package", "pypi:requests@2.25.0", "-osv")

        assert result.exit_code == 0
        assert "OSV has no ecosystem for pypi." in result.output
        assert FakeOsv.asked == []


class TestPrivacy:
    def test_a_private_package_is_never_sent_to_osv(self, run, monkeypatch):
        from kospex.habitat_config import HabitatConfig

        monkeypatch.setenv("KOSPEX_DEPS_PRIVATE", "maven:au.com.acme")
        HabitatConfig.reset_instance()

        result = run("-package", "maven:au.com.acme:internal-lib@1.0.0", "-osv", version="1.0.0")

        assert result.exit_code == 1
        assert "is private" in result.output
        assert FakeOsv.asked == []
        assert result.deps_asked == []

    def test_no_enrich_stops_osv_too(self, run):
        result = run("-package", "pypi:requests@2.25.0", "-osv", "-no-enrich")

        assert result.exit_code == 1
        assert FakeOsv.asked == []
